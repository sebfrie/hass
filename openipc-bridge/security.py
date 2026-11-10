"""Security helpers for the OpenIPC Bridge add-on.

* Authentication gate for the Flask app (the add-on publishes port 5000 on the
  host network, so every route must be protected).
* Path / name / host validation helpers shared by server.py and recording_api.py.
* Log redaction for credentials embedded in URLs.
"""

import base64
import hmac
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import threading
import time
from typing import Optional
from urllib.parse import urlparse

from flask import Response, request

logger = logging.getLogger(__name__)

ADDON_OPTIONS_FILE = "/data/options.json"

# Recordings/snapshots/exports live in the HA media share, NOT in /config/www:
# Home Assistant serves /config/www unauthenticated at /local/.
MEDIA_ROOT = "/media/openipc"
LEGACY_WWW = "/config/www"
# Private scratch space for debug dumps (never web-served)
DEBUG_DIR = "/data/debug"

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PUBLIC_PATHS = {"/health"}

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")
_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)*$")
_URL_CREDS_RE = re.compile(r"(\b[a-zA-Z][a-zA-Z0-9+.\-]*://)[^@\s/]+@")
_LANG_RE = re.compile(r"^[A-Za-z]{2,3}([\-_][A-Za-z0-9]{2,4})?$")

_FAIL_WINDOW = 60
_FAIL_LIMIT = 10
_failures = {}
_failures_lock = threading.Lock()


# ==================== VALIDATION HELPERS ====================

def safe_name(value) -> Optional[str]:
    """Return value if it is a plain file/dir name (no separators, no '..')."""
    if not isinstance(value, str) or ".." in value or not _NAME_RE.match(value):
        return None
    return value


def valid_lang(value) -> Optional[str]:
    """Return a language code such as 'ru' or 'en-US', else None."""
    if isinstance(value, str) and _LANG_RE.match(value):
        return value
    return None


def resolve_within(base: str, *parts: str) -> Optional[str]:
    """Join parts onto base and return the real path only if it stays inside base."""
    try:
        base_real = os.path.realpath(base)
        target = os.path.realpath(os.path.join(base_real, *parts))
        if os.path.commonpath([base_real, target]) != base_real:
            return None
        return target
    except (ValueError, OSError):
        return None


def _address_allowed(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if (ip.is_loopback or ip.is_link_local or ip.is_multicast
            or ip.is_unspecified or ip.is_reserved):
        return False
    return ip.is_private


def valid_camera_host(value) -> Optional[str]:
    """Validate a camera host: a LAN IP literal or a hostname resolving to LAN IPs.

    Loopback, link-local (cloud metadata), multicast and public addresses are
    rejected so that camera settings cannot be abused for SSRF.
    """
    if not isinstance(value, str):
        return None
    host = value.strip()
    if not host or len(host) > 253:
        return None
    try:
        ipaddress.ip_address(host)
        return host if _address_allowed(host) else None
    except ValueError:
        pass
    if host.lower() == "localhost" or not _HOSTNAME_RE.match(host):
        return None
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return None
    if infos and all(_address_allowed(i[4][0]) for i in infos):
        return host
    return None


def redact(text) -> str:
    """Mask user:password@ in URLs (e.g. rtsp://user:pass@host) before logging."""
    return _URL_CREDS_RE.sub(r"\1***@", str(text))


# ==================== STORAGE ====================

def _merge_move(src: str, dst: str):
    if os.path.isdir(src) and not os.path.islink(src):
        os.makedirs(dst, exist_ok=True)
        for entry in os.listdir(src):
            _merge_move(os.path.join(src, entry), os.path.join(dst, entry))
        try:
            os.rmdir(src)
        except OSError:
            pass
    elif not os.path.exists(dst):
        shutil.move(src, dst)


def migrate_legacy_media():
    """Move media (and delete debug dumps) out of the publicly served /config/www."""
    import glob

    for sub in ("recordings", "snapshots", "exports"):
        legacy = os.path.join(LEGACY_WWW, sub)
        if not os.path.isdir(legacy) or os.path.islink(legacy):
            continue
        try:
            logger.info("📦 Moving %s to %s (no longer publicly served)", legacy, MEDIA_ROOT)
            _merge_move(legacy, os.path.join(MEDIA_ROOT, sub))
        except Exception as e:
            logger.error("❌ Could not migrate %s: %s", legacy, e)

    for pattern in ("qr_debug_*.jpg", "qr_marked_*.jpg", "snapshot_debug_*.jpg", "tts_debug_*"):
        for path in glob.glob(os.path.join(LEGACY_WWW, pattern)):
            try:
                os.remove(path)
            except OSError:
                pass


# ==================== AUTHENTICATION ====================

def _load_access_password() -> str:
    try:
        with open(ADDON_OPTIONS_FILE, "r", encoding="utf-8") as f:
            return str(json.load(f).get("access_password") or "")
    except (OSError, ValueError):
        return os.environ.get("OPENIPC_ACCESS_PASSWORD", "")


def _is_trusted_peer() -> bool:
    """Loopback peers (HA Core on the host network, add-on internal calls) and
    the Supervisor ingress gateway are trusted. Requests that went through a
    reverse proxy are never treated as loopback."""
    addr = request.remote_addr or ""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return not any(
            h in request.headers
            for h in ("X-Forwarded-For", "X-Real-IP", "Forwarded", "X-Forwarded-Host")
        )
    return str(ip) == "172.30.32.2" and "X-Ingress-Path" in request.headers


def _supplied_secret() -> str:
    scheme, _, value = request.headers.get("Authorization", "").partition(" ")
    scheme = scheme.lower()
    value = value.strip()
    if scheme == "bearer":
        return value
    if scheme == "basic":
        try:
            decoded = base64.b64decode(value, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return ""
        return decoded.partition(":")[2]
    return ""


def _locked_out(addr: str) -> bool:
    now = time.time()
    with _failures_lock:
        recent = [t for t in _failures.get(addr, []) if now - t < _FAIL_WINDOW]
        if recent:
            _failures[addr] = recent
        else:
            _failures.pop(addr, None)
        return len(recent) >= _FAIL_LIMIT


def _record_failure(addr: str):
    with _failures_lock:
        _failures.setdefault(addr, []).append(time.time())


def _cross_site_write() -> bool:
    """Block browser-driven cross-site writes (CSRF), also from trusted peers."""
    if request.method in SAFE_METHODS:
        return False
    if request.headers.get("Sec-Fetch-Site") == "cross-site":
        return True
    origin = request.headers.get("Origin")
    if origin:
        if origin == "null":
            return True
        return urlparse(origin).netloc.lower() != (request.host or "").lower()
    return False


def init_auth(app):
    """Install the authentication gate and hardening headers on the Flask app."""
    password = _load_access_password()
    if not password:
        logger.warning(
            "⚠️ Add-on option 'access_password' is not set: the web UI and API "
            "are only reachable from the Home Assistant host itself. "
            "Set it in the add-on configuration to enable remote access."
        )

    @app.before_request
    def _require_auth():
        if request.path in PUBLIC_PATHS:
            return None
        if _cross_site_write():
            return Response("Cross-site request blocked", 403, mimetype="text/plain")
        if _is_trusted_peer():
            return None

        addr = request.remote_addr or "unknown"
        if not password:
            return Response(
                "Access is disabled. Set the 'access_password' option of the "
                "OpenIPC Bridge add-on.",
                503, mimetype="text/plain",
            )
        if _locked_out(addr):
            return Response("Too many failed attempts", 429, mimetype="text/plain",
                            headers={"Retry-After": str(_FAIL_WINDOW)})
        supplied = _supplied_secret()
        if supplied and hmac.compare_digest(supplied.encode(), password.encode()):
            return None
        if request.headers.get("Authorization"):
            _record_failure(addr)
        return Response(
            "Authentication required", 401, mimetype="text/plain",
            headers={"WWW-Authenticate": 'Basic realm="OpenIPC Bridge", charset="UTF-8"'},
        )

    @app.after_request
    def _harden_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if response.mimetype == "application/json":
            response.headers.setdefault("Cache-Control", "no-store")
        return response
