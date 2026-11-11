"""OpenIPC audio handling for TTS."""
import json
import logging
import re
import subprocess
import os
from pathlib import Path
from typing import Optional

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

_LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,3}([-_][A-Za-z0-9]{2,4})?$")

_GTTS_SCRIPT = """
import json, sys
try:
    from gtts import gTTS
    params = json.load(sys.stdin)
    gTTS(text=params["text"], lang=params["lang"], slow=False).save(params["output"])
    print("SUCCESS")
except Exception as e:
    print(f"ERROR: {e}")
    sys.exit(1)
"""

class OpenIPCAudio:
    """Class to handle audio playback on OpenIPC cameras."""

    def __init__(self, hass: HomeAssistant, host: str, username: str, password: str, coordinator=None):
        """Initialize OpenIPC audio."""
        self.hass = hass
        self.host = host
        self.username = username
        self.password = password
        self.coordinator = coordinator
        self._available = False

    async def async_test_connection(self) -> bool:
        """Test connection to audio endpoint."""
        try:
            auth = aiohttp.BasicAuth(self.username, self.password)
            url = f"http://{self.host}/play_audio"
            session = async_get_clientsession(self.hass)
            async with session.head(url, auth=auth, timeout=aiohttp.ClientTimeout(total=5)) as response:
                self._available = response.status in [200, 401]
                return self._available
        except Exception as err:
            _LOGGER.debug("OpenIPC audio endpoint not available: %s", err)
            self._available = False
            return False

    async def async_play_pcm(self, pcm_file_path: str) -> bool:
        """Play PCM file on OpenIPC camera."""
        if not os.path.exists(pcm_file_path):
            _LOGGER.error("PCM file not found: %s", pcm_file_path)
            return False

        _LOGGER.info("🔊 Playing PCM file on OpenIPC camera: %s", pcm_file_path)

        # Sent with HA's HTTP session: the password stays out of the process list
        data = await self.hass.async_add_executor_job(Path(pcm_file_path).read_bytes)
        session = async_get_clientsession(self.hass)
        try:
            async with session.post(
                f"http://{self.host}/play_audio",
                data=data,
                auth=aiohttp.BasicAuth(self.username, self.password),
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status < 400:
                    _LOGGER.info("✅ PCM played successfully on OpenIPC camera")
                    return True
                _LOGGER.error("❌ Failed to play PCM: HTTP %s", response.status)
                return False
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.error("Error playing PCM: %s", err)
            return False

    async def async_generate_and_play_tts(self, message: str, language: str = "ru") -> bool:
        """
        Generate TTS using gTTS (via add-on or locally) and play it.
        Returns True if successful, False otherwise.
        """
        # Сначала пробуем использовать аддон
        if self.coordinator and self.coordinator.use_addon and self.coordinator.addon.available:
            try:
                _LOGGER.info(f"Using add-on for TTS: '{message}'")
                camera_name = self.coordinator.recorder.camera_name if hasattr(self.coordinator, 'recorder') else "camera"
                
                # Отправляем TTS через addon
                success = await self.coordinator.addon.async_tts(camera_name, message, language)
                if success:
                    _LOGGER.info("✅ TTS via add-on successful")
                    return True
                else:
                    _LOGGER.warning("Add-on TTS failed, falling back to local")
            except Exception as err:
                _LOGGER.error(f"Add-on TTS error: {err}, falling back to local")
        
        # Fallback на локальный метод
        import tempfile
        import os
        
        with tempfile.NamedTemporaryFile(suffix='.pcm', delete=False) as tmp:
            pcm_path = tmp.name
        
        try:
            success = await self.hass.async_add_executor_job(
                self._generate_tts_sync, message, language, pcm_path
            )
            
            if not success:
                _LOGGER.error("Failed to generate TTS locally")
                return False
            
            return await self.async_play_pcm(pcm_path)
            
        finally:
            if os.path.exists(pcm_path):
                os.unlink(pcm_path)

    def _generate_tts_sync(self, message: str, language: str, output_path: str) -> bool:
        """Synchronous method to generate TTS using gTTS and convert to PCM."""
        import subprocess
        import tempfile
        
        if not isinstance(language, str) or not _LANGUAGE_RE.match(language):
            _LOGGER.error("Invalid TTS language: %r", language)
            return False
        
        temp_mp3 = tempfile.NamedTemporaryFile(suffix='.mp3', delete=False).name
        
        try:
            # Static script: user data is passed as JSON on stdin, never as code
            result = subprocess.run(
                ["python3", "-c", _GTTS_SCRIPT],
                input=json.dumps(
                    {"text": message, "lang": language, "output": temp_mp3}
                ),
                capture_output=True, text=True, timeout=30
            )
            
            if result.returncode != 0:
                _LOGGER.error("gTTS error: %s", result.stderr)
                return False
            
            ffmpeg_cmd = [
                "ffmpeg", "-y", "-i", temp_mp3,
                "-ar", "8000", "-ac", "1", "-f", "s16le",
                output_path
            ]
            result = subprocess.run(
                ffmpeg_cmd,
                capture_output=True, text=True, timeout=30
            )
            
            if result.returncode != 0:
                _LOGGER.error("ffmpeg error: %s", result.stderr)
                return False
            
            return True
            
        except Exception as e:
            _LOGGER.error("Error generating TTS: %s", e)
            return False
            
        finally:
            if os.path.exists(temp_mp3):
                os.unlink(temp_mp3)

    @property
    def is_available(self) -> bool:
        """Return True if audio endpoint is available."""
        return self._available
        