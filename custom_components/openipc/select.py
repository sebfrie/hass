"""Select platform for OpenIPC (recording duration)."""
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

async def async_setup_entry(hass, entry, async_add_entities):
    """Set up OpenIPC select entities."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    
    entities = [
        OpenIPCDurationSelect(
            coordinator, entry, "Recording Duration", "recording_duration",
            RECORDING_DURATIONS, "1 minute",
        ),
        OpenIPCDurationSelect(
            coordinator, entry, "QR Scan Duration", "qr_scan_duration",
            QR_SCAN_DURATIONS, "30 seconds", EntityCategory.CONFIG,
        ),
    ]
    
    async_add_entities(entities)

class OpenIPCDurationSelect(CoordinatorEntity, SelectEntity, RestoreEntity):
    """Duration used by the record / QR scan buttons, kept on the coordinator as `key`."""

    def __init__(self, coordinator, entry, name, key, durations, default, entity_category=None):
        """Initialize the select entity."""
        super().__init__(coordinator)
        self.coordinator = coordinator
        self.entry = entry
        self._key = key
        self._durations = durations
        self._attr_name = f"{entry.data.get('name', 'OpenIPC')} {name}"
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_icon = "mdi:timer"
        self._attr_entity_category = entity_category
        self._attr_options = list(durations)
        self._attr_current_option = default

    async def async_added_to_hass(self) -> None:
        """Restore last state."""
        await super().async_added_to_hass()
        
        # Восстанавливаем предыдущее состояние
        last_state = await self.async_get_last_state()
        if last_state and last_state.state in self._attr_options:
            self._attr_current_option = last_state.state
            _LOGGER.debug("Restored %s: %s", self._key, self._attr_current_option)
        setattr(self.coordinator, self._key, self._durations[self._attr_current_option])

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        if option in self._attr_options:
            self._attr_current_option = option
            self.async_write_ha_state()
            
            duration = self._durations[option]
            _LOGGER.info("%s set to %s (%d seconds)", self._key, option, duration)
            setattr(self.coordinator, self._key, duration)

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