"""Button platform for OpenIPC."""
import logging
import asyncio
import aiohttp
from datetime import datetime

from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory

from .const import (
    DOMAIN, 
    API_REBOOT, 
    RECORD_START, 
    RECORD_STOP,
    CONF_DEVICE_TYPE,
    DEVICE_TYPE_BEWARD,
    DEVICE_TYPE_VIVOTEK,
    DEVICE_TYPE_OPENIPC,
    LNPR_LIST,
    LNPR_EXPORT,
    LNPR_CLEAR_LOG,
    LNPR_CLEAR,
)

_LOGGER = logging.getLogger(__name__)

# One button per function; the duration comes from the select entities
RECORD_BUTTONS = (
    # name, unique id suffix, icon, HA media method (None = camera SD), send to Telegram
    ("Record (Camera SD)", "record_sd", "mdi:sd", None, False),
    ("Record (HA Media)", "record_ha", "mdi:home-assistant", "snapshots", False),
    ("Record (RTSP)", "record_rtsp", "mdi:video", "rtsp", False),
    ("Record + Telegram", "telegram", "mdi:telegram", "snapshots", True),
    ("Record + Telegram (RTSP)", "telegram_rtsp", "mdi:telegram", "rtsp", True),
)

# Per-duration buttons of earlier versions, removed from the entity registry
LEGACY_BUTTON_IDS = {
    f"{prefix}_{label}"
    for prefix in ("record_sd", "record_ha", "record_rtsp", "telegram", "telegram_rtsp")
    for label in ("15s", "30s", "1m", "3m", "5m", "10m")
} | {"qr_scan_15", "qr_scan_30", "qr_scan_60"} | {
    f"qr_mode_{mode}" for mode in ("single", "periodic", "continuous", "disabled")
}

async def async_setup_entry(hass, entry, async_add_entities):
    """Set up OpenIPC buttons."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    device_type = entry.data.get(CONF_DEVICE_TYPE, "openipc")
    
    entities = []
    
    # Стандартные кнопки для всех типов
    entities.extend([
        OpenIPCButton(coordinator, entry, "Reboot", "reboot", API_REBOOT, "mdi:restart"),
        OpenIPCButton(coordinator, entry, "Start Recording (Camera SD)", "record_start", RECORD_START, "mdi:record-rec"),
        OpenIPCButton(coordinator, entry, "Stop Recording", "record_stop", RECORD_STOP, "mdi:stop"),
    ])
    
    entities.extend(
        OpenIPCRecordButton(coordinator, entry, *spec) for spec in RECORD_BUTTONS
    )

    registry = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            reg_entry.domain == "button"
            and reg_entry.unique_id.removeprefix(f"{entry.entry_id}_") in LEGACY_BUTTON_IDS
        ):
            registry.async_remove(reg_entry.entity_id)

    # QR-код кнопки для всех камер
    entities.extend([
        OpenIPCQRScanButton(coordinator, entry),
        OpenIPCQRStopButton(coordinator, entry),
    ])
    
    # Специфичные кнопки для Beward
    if device_type == DEVICE_TYPE_BEWARD and coordinator.beward:
        entities.extend([
            BewardOpenDoorButton(coordinator, entry, 1, "Main Door"),
            BewardOpenDoorButton(coordinator, entry, 2, "Secondary Door"),
            # LNPR кнопки для Beward
            BewardLNPRListButton(coordinator, entry),
            BewardLNPREmptyButton(coordinator, entry),
            BewardLNPREventsButton(coordinator, entry),
            BewardLNPREventsClearButton(coordinator, entry),
        ])
        _LOGGER.info("✅ Added Beward-specific buttons for %s", entry.data.get('name'))
    
    # Специфичные кнопки для Vivotek
    elif device_type == DEVICE_TYPE_VIVOTEK and coordinator.vivotek:
        entities.extend([
            VivotekRebootButton(coordinator, entry),
            # PTZ кнопки для Vivotek
            VivotekPTZUpButton(coordinator, entry),
            VivotekPTZDownButton(coordinator, entry),
            VivotekPTZLeftButton(coordinator, entry),
            VivotekPTZRightButton(coordinator, entry),
            VivotekPTZUpLeftButton(coordinator, entry),
            VivotekPTZUpRightButton(coordinator, entry),
            VivotekPTZDownLeftButton(coordinator, entry),
            VivotekPTZDownRightButton(coordinator, entry),
            VivotekPTZStopButton(coordinator, entry),
            VivotekPTZHomeButton(coordinator, entry),
            VivotekPTZZoomInButton(coordinator, entry),
            VivotekPTZZoomOutButton(coordinator, entry),
            VivotekPTZPreset1Button(coordinator, entry),
            VivotekPTZPreset2Button(coordinator, entry),
            VivotekPTZPreset3Button(coordinator, entry),
            VivotekPTZPreset4Button(coordinator, entry),
            VivotekPTZSetPresetButton(coordinator, entry),
            VivotekPTZRefreshButton(coordinator, entry),
        ])
        _LOGGER.info("✅ Added Vivotek PTZ buttons for %s", entry.data.get('name'))
    
    async_add_entities(entities)


class OpenIPCButton(CoordinatorEntity, ButtonEntity):
    """Representation of an OpenIPC button."""

    def __init__(self, coordinator, entry, name, button_id, api_command, icon):
        """Initialize the button."""
        super().__init__(coordinator)
        self.coordinator = coordinator
        self.entry = entry
        self.button_id = button_id
        self.api_command = api_command
        self._attr_name = f"{entry.data.get('name', 'OpenIPC')} {name}"
        self._attr_unique_id = f"{entry.entry_id}_{button_id}"
        self._attr_icon = icon

    async def async_press(self) -> None:
        """Handle the button press."""
        _LOGGER.debug("Pressing button %s for camera %s", self.button_id, self.entry.data.get('name'))
        
        if self.button_id == "record_start":
            await self.coordinator.async_start_recording()
        elif self.button_id == "record_stop":
            await self.coordinator.async_stop_recording()
        else:
            await self.coordinator.async_send_command(self.api_command)

    @property
    def device_info(self):
        """Return device info."""
        parsed = self.coordinator.data.get("parsed", {})
        return {
            "identifiers": {(DOMAIN, self.entry.entry_id)},
            "name": self.entry.data.get("name", "OpenIPC Camera"),
            "manufacturer": "OpenIPC",
            "model": parsed.get("model", "Camera"),
        }


class OpenIPCRecordButton(OpenIPCButton):
    """Record for the duration chosen in the Recording Duration select."""

    def __init__(self, coordinator, entry, name, button_id, icon, method, telegram):
        """Initialize the button."""
        super().__init__(coordinator, entry, name, button_id, None, icon)
        self.method = method
        self.telegram = telegram

    async def async_press(self) -> None:
        """Handle the button press."""
        duration = self.coordinator.recording_duration
        name = self.entry.data.get("name")
        _LOGGER.info("%s: %s for %d seconds", name, self.button_id, duration)

        if self.telegram:
            await self.coordinator.async_record_and_send_telegram(
                duration,
                method=self.method,
                caption=f"📹 Recording from {name}\n⏱ {duration} seconds",
            )
        elif self.method is None:
            await self.coordinator.async_start_timed_recording(duration, save_to_ha=False)
        else:
            await self.coordinator.async_start_timed_recording(
                duration, save_to_ha=True, method=self.method
            )


# ==================== QR Code Buttons ====================

class OpenIPCQRScanButton(CoordinatorEntity, ButtonEntity):
    """Button to start a QR scan for the duration chosen in the QR Scan Duration select."""

    def __init__(self, coordinator, entry):
        """Initialize the button."""
        super().__init__(coordinator)
        self.coordinator = coordinator
        self.entry = entry
        self._attr_name = f"{entry.data.get('name', 'OpenIPC')} QR Scan"
        self._attr_unique_id = f"{entry.entry_id}_qr_scan"
        self._attr_icon = "mdi:qrcode-scan"
        self._attr_entity_category = EntityCategory.CONFIG

    async def async_press(self) -> None:
        """Handle the button press."""
        duration = self.coordinator.qr_scan_duration
        _LOGGER.info("📸 Manual QR scan triggered for %s (%ds)", self.entry.data.get("name"), duration)

        if self.coordinator.qr_scanner:
            await self.coordinator.qr_scanner.async_activate(
                reason=f"manual_{duration}s",
                timeout=duration
            )

            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": f"📸 QR Scan Activated - {self.entry.data.get('name')}",
                    "message": f"QR scanning enabled for {duration} seconds",
                    "notification_id": f"openipc_qr_scan_{self.entry.entry_id}"
                }
            )

    @property
    def device_info(self):
        """Return device info."""
        parsed = self.coordinator.data.get("parsed", {})
        return {
            "identifiers": {(DOMAIN, self.entry.entry_id)},
            "name": self.entry.data.get("name", "OpenIPC Camera"),
            "manufacturer": "OpenIPC",
            "model": parsed.get("model", "Camera"),
        }


class OpenIPCQRStopButton(CoordinatorEntity, ButtonEntity):
    """Button to stop QR scanning."""

    def __init__(self, coordinator, entry):
        """Initialize the button."""
        super().__init__(coordinator)
        self.coordinator = coordinator
        self.entry = entry
        self._attr_name = f"{entry.data.get('name', 'OpenIPC')} QR Stop"
        self._attr_unique_id = f"{entry.entry_id}_qr_stop"
        self._attr_icon = "mdi:stop"
        self._attr_entity_category = EntityCategory.CONFIG

    async def async_press(self) -> None:
        """Handle the button press."""
        _LOGGER.info(f"🛑 QR scanning stopped for {self.entry.data.get('name')}")
        
        if hasattr(self.coordinator, 'qr_scanner'):
            await self.coordinator.qr_scanner.async_deactivate()
            
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": f"🛑 QR Scan Stopped - {self.entry.data.get('name')}",
                    "message": "QR сканирование остановлено",
                    "notification_id": f"openipc_qr_stop_{self.entry.entry_id}"
                }
            )

    @property
    def device_info(self):
        """Return device info."""
        parsed = self.coordinator.data.get("parsed", {})
        return {
            "identifiers": {(DOMAIN, self.entry.entry_id)},
            "name": self.entry.data.get("name", "OpenIPC Camera"),
            "manufacturer": "OpenIPC",
            "model": parsed.get("model", "Camera"),
        }


# ==================== Beward Specific Buttons ====================

class BewardOpenDoorButton(CoordinatorEntity, ButtonEntity):
    """Button to open Beward door."""

    def __init__(self, coordinator, entry, relay_id: int, name_suffix: str):
        """Initialize the button."""
        super().__init__(coordinator)
        self.coordinator = coordinator
        self.entry = entry
        self.relay_id = relay_id
        self._attr_name = f"{entry.data.get('name', 'Beward')} Open {name_suffix}"
        self._attr_unique_id = f"{entry.entry_id}_beward_open_door_{relay_id}"
        self._attr_icon = "mdi:door-open"

    async def async_press(self) -> None:
        """Handle the button press."""
        if self.coordinator.beward:
            _LOGGER.info("🚪 Opening Beward door (relay %d)", self.relay_id)
            await self.coordinator.beward.async_open_door(main=(self.relay_id == 1))
        else:
            _LOGGER.error("Beward device not available")

    @property
    def device_info(self):
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self.entry.entry_id)},
            "name": self.entry.data.get("name", "Beward Doorbell"),
            "manufacturer": "Beward",
            "model": "DS07P-LP",
        }


class BewardLNPRListButton(CoordinatorEntity, ButtonEntity):
    """Button to get LNPR list (whitelist)."""

    def __init__(self, coordinator, entry):
        """Initialize the button."""
        super().__init__(coordinator)
        self.coordinator = coordinator
        self.entry = entry
        self._attr_name = f"{entry.data.get('name', 'Beward')} Get Plates List"
        self._attr_unique_id = f"{entry.entry_id}_beward_lnpr_list"
        self._attr_icon = "mdi:format-list-numbered"
        self._attr_entity_category = EntityCategory.CONFIG

    async def async_press(self) -> None:
        """Handle the button press."""
        if not self.coordinator.beward:
            _LOGGER.error("Beward device not available")
            return
        
        _LOGGER.info("📋 Getting LNPR whitelist from %s", self.entry.data.get('name'))
        
        try:
            url = f"http://{self.coordinator.beward.host}{LNPR_LIST}"
            async with self.coordinator.session.get(url, auth=self.coordinator.auth) as response:
                if response.status == 200:
                    text = await response.text()
                    
                    plates = []
                    lines = text.strip().split('\n')
                    for line in lines:
                        if line.startswith('Number'):
                            plates.append(line)
                    
                    message = f"📋 **Найденные номера:**\n\n"
                    if plates:
                        for plate in plates:
                            message += f"• {plate}\n"
                        message += f"\nВсего: {len(plates)} номеров"
                    else:
                        message += "Список пуст"
                    
                    await self.hass.services.async_call(
                        "persistent_notification",
                        "create",
                        {
                            "title": f"LNPR Whitelist - {self.entry.data.get('name')}",
                            "message": message,
                            "notification_id": f"openipc_lnpr_list_{self.entry.entry_id}"
                        }
                    )
                else:
                    _LOGGER.error("Failed to get LNPR list: HTTP %d", response.status)
        except Exception as err:
            _LOGGER.error("Error getting LNPR list: %s", err)

    @property
    def device_info(self):
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self.entry.entry_id)},
            "name": self.entry.data.get("name", "Beward Doorbell"),
            "manufacturer": "Beward",
            "model": "DS07P-LP",
        }


class BewardLNPREmptyButton(CoordinatorEntity, ButtonEntity):
    """Button to clear LNPR whitelist."""

    def __init__(self, coordinator, entry):
        """Initialize the button."""
        super().__init__(coordinator)
        self.coordinator = coordinator
        self.entry = entry
        self._attr_name = f"{entry.data.get('name', 'Beward')} Clear Plates List"
        self._attr_unique_id = f"{entry.entry_id}_beward_lnpr_clear"
        self._attr_icon = "mdi:delete-sweep"
        self._attr_entity_category = EntityCategory.CONFIG

    async def async_press(self) -> None:
        """Handle the button press."""
        if not self.coordinator.beward:
            _LOGGER.error("Beward device not available")
            return
        
        _LOGGER.info("🧹 Clearing LNPR whitelist for %s", self.entry.data.get('name'))
        
        try:
            url = f"http://{self.coordinator.beward.host}{LNPR_CLEAR}"
            async with self.coordinator.session.get(url, auth=self.coordinator.auth) as response:
                if response.status == 200:
                    await self.hass.services.async_call(
                        "persistent_notification",
                        "create",
                        {
                            "title": f"✅ LNPR - {self.entry.data.get('name')}",
                            "message": "Список разрешенных номеров успешно очищен",
                            "notification_id": f"openipc_lnpr_clear_{self.entry.entry_id}"
                        }
                    )
                else:
                    _LOGGER.error("Failed to clear LNPR list: HTTP %d", response.status)
        except Exception as err:
            _LOGGER.error("Error clearing LNPR list: %s", err)

    @property
    def device_info(self):
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self.entry.entry_id)},
            "name": self.entry.data.get("name", "Beward Doorbell"),
            "manufacturer": "Beward",
            "model": "DS07P-LP",
        }


class BewardLNPREventsButton(CoordinatorEntity, ButtonEntity):
    """Button to export LNPR events log."""

    def __init__(self, coordinator, entry):
        """Initialize the button."""
        super().__init__(coordinator)
        self.coordinator = coordinator
        self.entry = entry
        self._attr_name = f"{entry.data.get('name', 'Beward')} Export LNPR Events"
        self._attr_unique_id = f"{entry.entry_id}_beward_lnpr_export"
        self._attr_icon = "mdi:export"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_press(self) -> None:
        """Handle the button press."""
        if not self.coordinator.beward:
            _LOGGER.error("Beward device not available")
            return
        
        _LOGGER.info("📊 Exporting LNPR events from %s", self.entry.data.get('name'))
        
        from datetime import datetime, timedelta
        
        end_date = datetime.now()
        start_date = end_date - timedelta(days=7)
        
        start_str = start_date.strftime("%Y-%m-%d %H:%M:%S")
        end_str = end_date.strftime("%Y-%m-%d %H:%M:%S")
        
        try:
            url = f"http://{self.coordinator.beward.host}{LNPR_EXPORT}&begin={start_str}&end={end_str}"
            async with self.coordinator.session.get(url, auth=self.coordinator.auth) as response:
                if response.status == 200:
                    text = await response.text()
                    
                    filename = f"/config/lnpr_events_{self.entry.entry_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
                    with open(filename, 'w') as f:
                        f.write(text)
                    
                    await self.hass.services.async_call(
                        "persistent_notification",
                        "create",
                        {
                            "title": f"📊 LNPR Events - {self.entry.data.get('name')}",
                            "message": f"✅ Экспорт завершен\n\n"
                                      f"📁 Файл: {filename}\n"
                                      f"📅 Период: {start_str} - {end_str}\n"
                                      f"Размер: {len(text)} байт",
                            "notification_id": f"openipc_lnpr_export_{self.entry.entry_id}"
                        }
                    )
                else:
                    _LOGGER.error("Failed to export LNPR events: HTTP %d", response.status)
        except Exception as err:
            _LOGGER.error("Error exporting LNPR events: %s", err)

    @property
    def device_info(self):
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self.entry.entry_id)},
            "name": self.entry.data.get("name", "Beward Doorbell"),
            "manufacturer": "Beward",
            "model": "DS07P-LP",
        }


class BewardLNPREventsClearButton(CoordinatorEntity, ButtonEntity):
    """Button to clear LNPR events log."""

    def __init__(self, coordinator, entry):
        """Initialize the button."""
        super().__init__(coordinator)
        self.coordinator = coordinator
        self.entry = entry
        self._attr_name = f"{entry.data.get('name', 'Beward')} Clear LNPR Events"
        self._attr_unique_id = f"{entry.entry_id}_beward_lnpr_events_clear"
        self._attr_icon = "mdi:delete"
        self._attr_entity_category = EntityCategory.CONFIG

    async def async_press(self) -> None:
        """Handle the button press."""
        if not self.coordinator.beward:
            _LOGGER.error("Beward device not available")
            return
        
        _LOGGER.info("🧹 Clearing LNPR events log for %s", self.entry.data.get('name'))
        
        try:
            url = f"http://{self.coordinator.beward.host}{LNPR_CLEAR_LOG}"
            async with self.coordinator.session.get(url, auth=self.coordinator.auth) as response:
                if response.status == 200:
                    await self.hass.services.async_call(
                        "persistent_notification",
                        "create",
                        {
                            "title": f"✅ LNPR - {self.entry.data.get('name')}",
                            "message": "Журнал событий LNPR успешно очищен",
                            "notification_id": f"openipc_lnpr_events_clear_{self.entry.entry_id}"
                        }
                    )
                else:
                    _LOGGER.error("Failed to clear LNPR events: HTTP %d", response.status)
        except Exception as err:
            _LOGGER.error("Error clearing LNPR events: %s", err)

    @property
    def device_info(self):
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self.entry.entry_id)},
            "name": self.entry.data.get("name", "Beward Doorbell"),
            "manufacturer": "Beward",
            "model": "DS07P-LP",
        }


# ==================== Vivotek Specific Buttons ====================

class VivotekRebootButton(CoordinatorEntity, ButtonEntity):
    """Button to reboot Vivotek camera."""

    def __init__(self, coordinator, entry):
        """Initialize the button."""
        super().__init__(coordinator)
        self.coordinator = coordinator
        self.entry = entry
        self._attr_name = f"{entry.data.get('name', 'Vivotek')} Reboot"
        self._attr_unique_id = f"{entry.entry_id}_vivotek_reboot"
        self._attr_icon = "mdi:restart"

    async def async_press(self) -> None:
        """Handle the button press."""
        if self.coordinator.vivotek:
            _LOGGER.info("🔄 Rebooting Vivotek camera")
            await self.coordinator.async_send_command("/cgi-bin/reboot.cgi")
        else:
            _LOGGER.error("Vivotek device not available")

    @property
    def device_info(self):
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self.entry.entry_id)},
            "name": self.entry.data.get("name", "Vivotek Camera"),
            "manufacturer": "Vivotek",
            "model": "SD9364-EHL",
        }


# ==================== Vivotek PTZ Buttons ====================

class BaseVivotekPTZButton(CoordinatorEntity, ButtonEntity):
    """Base class for Vivotek PTZ buttons."""

    def __init__(self, coordinator, entry, name, button_id, icon):
        """Initialize the button."""
        super().__init__(coordinator)
        self.coordinator = coordinator
        self.entry = entry
        self._attr_name = f"{entry.data.get('name', 'Vivotek')} {name}"
        self._attr_unique_id = f"{entry.entry_id}_{button_id}"
        self._attr_icon = icon

    @property
    def device_info(self):
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self.entry.entry_id)},
            "name": self.entry.data.get("name", "Vivotek Camera"),
            "manufacturer": "Vivotek",
            "model": "SD9364-EHL",
        }


class VivotekPTZUpButton(BaseVivotekPTZButton):
    """PTZ Up button."""
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "PTZ Up", "ptz_up", "mdi:arrow-up")
    async def async_press(self) -> None:
        if self.coordinator.vivotek and self.coordinator.vivotek.ptz:
            await self.coordinator.vivotek.ptz.async_move("up", 50)


class VivotekPTZDownButton(BaseVivotekPTZButton):
    """PTZ Down button."""
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "PTZ Down", "ptz_down", "mdi:arrow-down")
    async def async_press(self) -> None:
        if self.coordinator.vivotek and self.coordinator.vivotek.ptz:
            await self.coordinator.vivotek.ptz.async_move("down", 50)


class VivotekPTZLeftButton(BaseVivotekPTZButton):
    """PTZ Left button."""
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "PTZ Left", "ptz_left", "mdi:arrow-left")
    async def async_press(self) -> None:
        if self.coordinator.vivotek and self.coordinator.vivotek.ptz:
            await self.coordinator.vivotek.ptz.async_move("left", 50)


class VivotekPTZRightButton(BaseVivotekPTZButton):
    """PTZ Right button."""
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "PTZ Right", "ptz_right", "mdi:arrow-right")
    async def async_press(self) -> None:
        if self.coordinator.vivotek and self.coordinator.vivotek.ptz:
            await self.coordinator.vivotek.ptz.async_move("right", 50)


class VivotekPTZUpLeftButton(BaseVivotekPTZButton):
    """PTZ Up-Left button."""
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "PTZ Up-Left", "ptz_up_left", "mdi:arrow-top-left")
    async def async_press(self) -> None:
        if self.coordinator.vivotek and self.coordinator.vivotek.ptz:
            await self.coordinator.vivotek.ptz.async_move("up-left", 50)


class VivotekPTZUpRightButton(BaseVivotekPTZButton):
    """PTZ Up-Right button."""
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "PTZ Up-Right", "ptz_up_right", "mdi:arrow-top-right")
    async def async_press(self) -> None:
        if self.coordinator.vivotek and self.coordinator.vivotek.ptz:
            await self.coordinator.vivotek.ptz.async_move("up-right", 50)


class VivotekPTZDownLeftButton(BaseVivotekPTZButton):
    """PTZ Down-Left button."""
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "PTZ Down-Left", "ptz_down_left", "mdi:arrow-bottom-left")
    async def async_press(self) -> None:
        if self.coordinator.vivotek and self.coordinator.vivotek.ptz:
            await self.coordinator.vivotek.ptz.async_move("down-left", 50)


class VivotekPTZDownRightButton(BaseVivotekPTZButton):
    """PTZ Down-Right button."""
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "PTZ Down-Right", "ptz_down_right", "mdi:arrow-bottom-right")
    async def async_press(self) -> None:
        if self.coordinator.vivotek and self.coordinator.vivotek.ptz:
            await self.coordinator.vivotek.ptz.async_move("down-right", 50)


class VivotekPTZStopButton(BaseVivotekPTZButton):
    """PTZ Stop button."""
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "PTZ Stop", "ptz_stop", "mdi:stop")
    async def async_press(self) -> None:
        if self.coordinator.vivotek and self.coordinator.vivotek.ptz:
            await self.coordinator.vivotek.ptz.async_stop()


class VivotekPTZHomeButton(BaseVivotekPTZButton):
    """PTZ Home button."""
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "PTZ Home", "ptz_home", "mdi:home")
    async def async_press(self) -> None:
        if self.coordinator.vivotek and self.coordinator.vivotek.ptz:
            await self.coordinator.vivotek.ptz.async_goto_preset(1)


class VivotekPTZZoomInButton(BaseVivotekPTZButton):
    """PTZ Zoom In button."""
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "PTZ Zoom In", "ptz_zoom_in", "mdi:plus")
    async def async_press(self) -> None:
        if self.coordinator.vivotek and self.coordinator.vivotek.ptz:
            await self.coordinator.vivotek.ptz.async_move("in", 50)


class VivotekPTZZoomOutButton(BaseVivotekPTZButton):
    """PTZ Zoom Out button."""
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "PTZ Zoom Out", "ptz_zoom_out", "mdi:minus")
    async def async_press(self) -> None:
        if self.coordinator.vivotek and self.coordinator.vivotek.ptz:
            await self.coordinator.vivotek.ptz.async_move("out", 50)


class VivotekPTZPreset1Button(BaseVivotekPTZButton):
    """PTZ Preset 1 button."""
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "PTZ Preset 1", "ptz_preset_1", "mdi:numeric-1-circle")
    async def async_press(self) -> None:
        if self.coordinator.vivotek and self.coordinator.vivotek.ptz:
            await self.coordinator.vivotek.ptz.async_goto_preset(1)


class VivotekPTZPreset2Button(BaseVivotekPTZButton):
    """PTZ Preset 2 button."""
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "PTZ Preset 2", "ptz_preset_2", "mdi:numeric-2-circle")
    async def async_press(self) -> None:
        if self.coordinator.vivotek and self.coordinator.vivotek.ptz:
            await self.coordinator.vivotek.ptz.async_goto_preset(2)


class VivotekPTZPreset3Button(BaseVivotekPTZButton):
    """PTZ Preset 3 button."""
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "PTZ Preset 3", "ptz_preset_3", "mdi:numeric-3-circle")
    async def async_press(self) -> None:
        if self.coordinator.vivotek and self.coordinator.vivotek.ptz:
            await self.coordinator.vivotek.ptz.async_goto_preset(3)


class VivotekPTZPreset4Button(BaseVivotekPTZButton):
    """PTZ Preset 4 button."""
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "PTZ Preset 4", "ptz_preset_4", "mdi:numeric-4-circle")
    async def async_press(self) -> None:
        if self.coordinator.vivotek and self.coordinator.vivotek.ptz:
            await self.coordinator.vivotek.ptz.async_goto_preset(4)


class VivotekPTZSetPresetButton(BaseVivotekPTZButton):
    """Button to set current position as preset."""
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "Set Preset", "ptz_set_preset", "mdi:map-marker-plus")
        self._attr_entity_category = EntityCategory.CONFIG

    async def async_press(self) -> None:
        if self.coordinator.vivotek and self.coordinator.vivotek.ptz:
            # Находим первый свободный номер пресета
            presets = await self.coordinator.vivotek.ptz.async_get_presets()
            preset_id = 1
            while preset_id in presets:
                preset_id += 1
            if preset_id <= 256:
                await self.coordinator.vivotek.ptz.async_set_preset(preset_id, f"Preset {preset_id}")
                _LOGGER.info(f"✅ Set preset {preset_id} for {self.entry.data.get('name')}")


class VivotekPTZRefreshButton(BaseVivotekPTZButton):
    """Button to refresh PTZ status."""
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "Refresh PTZ", "ptz_refresh", "mdi:refresh")
        self._attr_entity_category = EntityCategory.CONFIG

    async def async_press(self) -> None:
        if self.coordinator.vivotek and self.coordinator.vivotek.ptz:
            await self.coordinator.vivotek.ptz.async_get_presets()
            _LOGGER.info(f"🔄 Refreshed PTZ presets for {self.entry.data.get('name')}")