"""The Wattnet integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .api import build_client
from .const import (
    CONF_CONFIG_ENTRY,
    CONF_DAYS,
    CONF_SHOW_DASHBOARD,
    DOMAIN,
    SERVICE_REIMPORT,
)
from .coordinator import WattnetCoordinator
from .validation import vol

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

REIMPORT_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_CONFIG_ENTRY): cv.string,
        vol.Optional(CONF_DAYS): vol.All(vol.Coerce(int), vol.Range(min=1, max=90)),
    }
)

type WattnetConfigEntry = ConfigEntry[WattnetCoordinator]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's actions."""

    async def _reimport(call: ServiceCall) -> None:
        entries = [
            entry
            for entry in hass.config_entries.async_loaded_entries(DOMAIN)
            if call.data.get(CONF_CONFIG_ENTRY) in (None, entry.entry_id)
        ]
        if not entries:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_loaded_entry"
            )
        for entry in entries:
            await entry.runtime_data.async_reimport(call.data.get(CONF_DAYS))

    hass.services.async_register(
        DOMAIN, SERVICE_REIMPORT, _reimport, schema=REIMPORT_SCHEMA
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: WattnetConfigEntry) -> bool:
    """Set up Wattnet from a config entry."""
    client = build_client(hass, entry.data)
    coordinator = WattnetCoordinator(hass, entry, client)
    await coordinator.async_load_watermarks()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(coordinator.async_start())

    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    if entry.options.get(CONF_SHOW_DASHBOARD, True):
        await _add_dashboard(hass, entry)
    return True


async def _add_dashboard(hass: HomeAssistant, entry: WattnetConfigEntry) -> None:
    """Show the zone in the dashboard, if this Home Assistant allows it."""
    try:
        from .panel import async_add_zone
    except ImportError:  # the Lovelace internals it relies on changed
        _LOGGER.warning("The Wattnet dashboard is not available in this version")
        return
    entry.async_on_unload(await async_add_zone(hass, entry.runtime_data))


async def async_unload_entry(hass: HomeAssistant, entry: WattnetConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload(hass: HomeAssistant, entry: WattnetConfigEntry) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
