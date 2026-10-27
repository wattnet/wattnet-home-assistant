"""Diagnostics for the Wattnet integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import WattnetConfigEntry
from .const import CONF_CLIENT_ID, CONF_CLIENT_SECRET

TO_REDACT = {CONF_CLIENT_ID, CONF_CLIENT_SECRET}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: WattnetConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry, without credentials."""
    coordinator = entry.runtime_data
    consolidated = coordinator.consolidated_until
    return {
        "entry": {
            "title": entry.title,
            "data": async_redact_data(entry.data, TO_REDACT),
            "options": dict(entry.options),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval_minutes": coordinator.poll_interval,
            "watermarks": dict(coordinator._watermarks),
            "consolidated_until": consolidated.isoformat() if consolidated else None,
        },
        "latest": {
            key: (
                None
                if point is None
                else {
                    "value": point.value,
                    "valid": point.valid,
                    "zone_status": point.zone_status,
                    "timestamp": point.timestamp.isoformat(),
                }
            )
            for key, point in (coordinator.data or {}).items()
        },
    }
