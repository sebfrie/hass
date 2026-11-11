"""Firmware update platform for OpenIPC cameras."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import timedelta
from typing import Any

import aiohttp
import asyncssh
from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_SSH_HOST_KEY, DOMAIN

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(hours=6)

GITHUB_API = "https://api.github.com/repos/OpenIPC/firmware"
GITHUB_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "homeassistant-openipc",
}
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=20)
SSH_PORT = 22
SSH_TIMEOUT = 15
INSTALL_TIMEOUT = 900
MAX_NOTES_COMMITS = 40

BUILD_RE = re.compile(r"^nightly-(\d{8})-([0-9a-f]+)$")


def _build_date(build: str | None) -> str | None:
    match = BUILD_RE.match(build or "")
    return match.group(1) if match else None


def _build_sha(build: str | None) -> str | None:
    match = BUILD_RE.match(build or "")
    return match.group(2) if match else None


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the firmware update entity."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.is_beward or coordinator.is_vivotek:
        return
    async_add_entities([OpenIPCFirmwareUpdate(coordinator, entry)], True)


class OpenIPCFirmwareUpdate(CoordinatorEntity, UpdateEntity):
    """Firmware update entity backed by OpenIPC/firmware nightly builds."""

    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL | UpdateEntityFeature.RELEASE_NOTES
    )
    _attr_title = "OpenIPC firmware"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self.entry = entry
        self._attr_name = f"{entry.data.get('name', 'OpenIPC')} Firmware"
        self._attr_unique_id = f"{entry.entry_id}_firmware_update"
        self._installed: str | None = None
        self._latest: dict[str, Any] | None = None

    @property
    def device_info(self):
        return {"identifiers": {(DOMAIN, self.entry.entry_id)}}

    @property
    def should_poll(self) -> bool:
        # Release checks hit GitHub and SSH, so they run on their own interval
        return True

    @property
    def installed_version(self) -> str | None:
        if self._installed:
            return self._installed
        data = self.coordinator.data or {}
        return data.get("parsed", {}).get("firmware")

    @property
    def latest_version(self) -> str | None:
        if self._latest:
            return self._latest["tag_name"]
        return self.installed_version

    @property
    def release_url(self) -> str | None:
        return self._latest.get("html_url") if self._latest else None

    def version_is_newer(self, latest_version: str, installed_version: str) -> bool:
        if latest_version == installed_version:
            return False
        latest_date = _build_date(latest_version)
        installed_date = _build_date(installed_version)
        if latest_date and installed_date:
            return latest_date >= installed_date
        # Installed build is not a dated nightly (or unknown): offer the update
        return latest_date is not None

    async def async_update(self) -> None:
        """Check the installed and the latest firmware build."""
        try:
            self._installed = await self._read_installed_build()
        except Exception as err:
            _LOGGER.debug("Cannot read firmware build over SSH: %s", err)

        try:
            session = async_get_clientsession(self.hass)
            async with session.get(
                f"{GITHUB_API}/releases?per_page=30",
                headers=GITHUB_HEADERS,
                timeout=HTTP_TIMEOUT,
            ) as resp:
                resp.raise_for_status()
                releases = await resp.json()
            for release in releases:
                if BUILD_RE.match(release.get("tag_name", "")):
                    self._latest = release
                    break
        except Exception as err:
            _LOGGER.debug("Cannot fetch OpenIPC firmware releases: %s", err)

    async def async_release_notes(self) -> str | None:
        """Return commits between the installed and the latest build."""
        if not self._latest:
            return None
        latest_tag = self._latest["tag_name"]
        header = f"Build [{latest_tag}]({self._latest.get('html_url', '')})\n\n"
        old_sha = _build_sha(self._installed)
        new_sha = _build_sha(latest_tag)
        if not (old_sha and new_sha) or old_sha == new_sha:
            return header + (self._latest.get("body") or "")
        try:
            session = async_get_clientsession(self.hass)
            async with session.get(
                f"{GITHUB_API}/compare/{old_sha}...{new_sha}",
                headers=GITHUB_HEADERS,
                timeout=HTTP_TIMEOUT,
            ) as resp:
                resp.raise_for_status()
                compare = await resp.json()
        except Exception as err:
            _LOGGER.debug("Cannot fetch firmware changelog: %s", err)
            return header + (self._latest.get("body") or "")

        commits = compare.get("commits", [])
        lines = [
            f"- {c['commit']['message'].splitlines()[0]} ({c['sha'][:7]})"
            for c in reversed(commits[-MAX_NOTES_COMMITS:])
        ]
        total = compare.get("total_commits", len(commits))
        if total > len(lines):
            lines.append(
                f"- ... and {total - len(lines)} more, see [full comparison]"
                f"({compare.get('html_url', '')})"
            )
        return header + "\n".join(lines)

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        """Flash a build with sysupgrade; the camera reboots when done."""
        target = version or (self._latest or {}).get("tag_name")
        if not target or not BUILD_RE.match(target):
            raise HomeAssistantError("No OpenIPC firmware build available to install")

        self._attr_in_progress = True
        self.async_write_ha_state()
        try:
            async with await self._connect() as conn:
                try:
                    result = await asyncio.wait_for(
                        conn.run(f"sysupgrade --build={target}", check=False),
                        INSTALL_TIMEOUT,
                    )
                except (asyncssh.ConnectionLost, asyncssh.DisconnectError):
                    # The camera reboots into the new firmware, dropping the session
                    _LOGGER.info("Camera disconnected during upgrade (expected)")
                    return
                if result.exit_status not in (0, None):
                    raise HomeAssistantError(
                        f"sysupgrade failed: {(result.stderr or result.stdout or '').strip()[-300:]}"
                    )
        except (OSError, asyncssh.Error, asyncio.TimeoutError) as err:
            raise HomeAssistantError(f"Firmware update failed: {err}") from err
        finally:
            self._attr_in_progress = False
            self.async_write_ha_state()

    @property
    def _host_key_issue_id(self) -> str:
        return f"ssh_host_key_changed_{self.entry.entry_id}"

    async def _connect(self):
        """Open an SSH session, trusting only the host key seen on first contact.

        The first connection pins the camera's host key in the config entry
        (trust on first use). Afterwards a different key is refused and raised
        as a repair issue, so the root password is never sent to a device that
        merely took over the camera's address. The key survives a sysupgrade;
        a factory reset of the camera creates a new one.
        """
        host = self.coordinator.host
        pinned = self.entry.data.get(CONF_SSH_HOST_KEY)
        known_hosts = ([asyncssh.import_public_key(pinned)], [], []) if pinned else None
        try:
            conn = await asyncssh.connect(
                host,
                port=SSH_PORT,
                username=self.coordinator.username,
                password=self.coordinator.password,
                known_hosts=known_hosts,
                connect_timeout=SSH_TIMEOUT,
            )
        except asyncssh.HostKeyNotVerifiable as err:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self._host_key_issue_id,
                is_fixable=True,
                severity=ir.IssueSeverity.ERROR,
                translation_key="ssh_host_key_changed",
                translation_placeholders={"name": self.entry.title, "host": host},
                data={"entry_id": self.entry.entry_id},
            )
            raise HomeAssistantError(
                f"SSH host key of {host} does not match the pinned key, "
                "see Settings > System > Repairs"
            ) from err

        if pinned:
            ir.async_delete_issue(self.hass, DOMAIN, self._host_key_issue_id)
        elif (key := conn.get_server_host_key()) is not None:
            self.hass.config_entries.async_update_entry(
                self.entry,
                data={
                    **self.entry.data,
                    CONF_SSH_HOST_KEY: key.export_public_key().decode().strip(),
                },
            )
            _LOGGER.info("Pinned SSH host key of %s (%s)", host, key.get_algorithm())
        return conn

    async def _read_installed_build(self) -> str | None:
        async with await self._connect() as conn:
            result = await conn.run("cat /etc/os-release", check=False, timeout=SSH_TIMEOUT)
        for line in (result.stdout or "").splitlines():
            if line.startswith("BUILD_ID="):
                return line.split("=", 1)[1].strip().strip('"')
        return None

