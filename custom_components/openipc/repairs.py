"""Repairs for OpenIPC."""
from __future__ import annotations

from typing import Any

from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.core import HomeAssistant

from .const import CONF_SSH_HOST_KEY


class ForgetSshHostKeyFlow(ConfirmRepairFlow):
    """Drop the pinned SSH host key; the next connection pins the current one."""

    def __init__(self, entry_id: str) -> None:
        """Initialize the flow."""
        self._entry_id = entry_id

    async def async_step_confirm(self, user_input: dict[str, str] | None = None):
        """Forget the pinned key once the user confirms."""
        if user_input is not None and (
            entry := self.hass.config_entries.async_get_entry(self._entry_id)
        ):
            data = dict(entry.data)
            data.pop(CONF_SSH_HOST_KEY, None)
            self.hass.config_entries.async_update_entry(entry, data=data)
        return await super().async_step_confirm(user_input)


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """Create the fix flow for a changed SSH host key."""
    return ForgetSshHostKeyFlow(str((data or {}).get("entry_id", "")))
