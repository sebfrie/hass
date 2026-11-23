"""Select platform for OpenIPC (durations, QR scanner mode)."""
import logging
from homeassistant.components.select import SelectEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Варианты длительности записи (в секундах)
RECORDING_DURATIONS = {
    "15 seconds": 15,
    "30 seconds": 30,
    "1 minute": 60,
    "2 minutes": 120,
    "3 minutes": 180,
    "5 minutes": 300,
    "10 minutes": 600,
    "15 minutes": 900,
    "30 minutes": 1800,
    "1 hour": 3600,
}

QR_SCAN_DURATIONS = {
    "15 seconds": 15,
    "30 seconds": 30,
    "1 minute": 60,
}

QR_MODES = {
    "Disabled": "DISABLED",
    "Single": "SINGLE",
    "Periodic": "PERIODIC",
    "Continuous": "CONTINUOUS",
}

async def async_setup_entry(hass, entry, async_add_entities):
    """Set up OpenIPC select entities."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    
    entities = [
        OpenIPCOptionSelect(
            coordinator, entry, "Recording Duration", "recording_duration",
            RECORDING_DURATIONS, "1 minute",
        ),
        OpenIPCOptionSelect(
            coordinator, entry, "QR Scan Duration", "qr_scan_duration",
            QR_SCAN_DURATIONS, "30 seconds", EntityCategory.CONFIG,
        ),
        OpenIPCQRModeSelect(
            coordinator, entry, "QR Mode", "qr_mode",
            QR_MODES, "Disabled", EntityCategory.CONFIG, "mdi:qrcode",
        ),
    ]
    
    async_add_entities(entities)

class OpenIPCOptionSelect(CoordinatorEntity, SelectEntity, RestoreEntity):
    """Option whose value is kept on the coordinator as `key` (read by the buttons)."""

    def __init__(
        self, coordinator, entry, name, key, values, default,
        entity_category=None, icon="mdi:timer",
    ):
        """Initialize the select entity."""
        super().__init__(coordinator)
        self.coordinator = coordinator
        self.entry = entry
        self._key = key
        self._values = values
        self._attr_name = f"{entry.data.get('name', 'OpenIPC')} {name}"
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_icon = icon
        self._attr_entity_category = entity_category
        self._attr_options = list(values)
        self._attr_current_option = default

    def _apply(self, value) -> None:
        setattr(self.coordinator, self._key, value)

    async def async_added_to_hass(self) -> None:
        """Restore last state."""
        await super().async_added_to_hass()
        
        # Восстанавливаем предыдущее состояние
        last_state = await self.async_get_last_state()
        if last_state and last_state.state in self._attr_options:
            self._attr_current_option = last_state.state
            _LOGGER.debug("Restored %s: %s", self._key, self._attr_current_option)
        self._apply(self._values[self._attr_current_option])

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        if option in self._attr_options:
            self._attr_current_option = option
            self.async_write_ha_state()
            
            _LOGGER.info("%s set to %s", self._key, option)
            self._apply(self._values[option])

    @property
    def current_option(self) -> str | None:
        """Return the selected option."""
        return self._attr_current_option

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


class OpenIPCQRModeSelect(OpenIPCOptionSelect):
    """QR scanner mode, applied to the coordinator's scanner when it has one."""

    def _apply(self, value) -> None:
        if self.coordinator.qr_scanner:
            from .qr_scanner import QRMode

            self.coordinator.qr_scanner.mode = QRMode[value]
