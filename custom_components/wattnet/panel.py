"""Registers the Wattnet dashboard in Home Assistant, read-only.

It is the dashboard of the zones set up in this installation, with the Wattnet
logo in the sidebar.

Home Assistant keeps the dashboards it knows in ``hass.data`` and shows each in
the sidebar through a panel. This registers one the way it does for a dashboard
defined in YAML. These are internals of the Lovelace integration rather than a
published API, so everything is checked and the dashboard is simply skipped if
a Home Assistant release changes them.

The sidebar icon is the Wattnet logo, added to Home Assistant as a custom icon
set by a small script, which is the documented way to have an icon of your own.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.const import (
    EVENT_LOVELACE_UPDATED,
    LOVELACE_DATA,
    MODE_YAML,
)
from homeassistant.components.lovelace.dashboard import LovelaceConfig
from homeassistant.core import HomeAssistant
from homeassistant.util.hass_dict import HassKey

from .const import DOMAIN
from .coordinator import WattnetCoordinator
from .dashboard import (
    DASHBOARD_TITLE,
    ICON,
    WattnetDashboardConfig,
    to_json,
)

_LOGGER = logging.getLogger(__name__)

DASHBOARD_URL_PATH = "wattnet"
ICON_SCRIPT = f"/{DOMAIN}_static/wattnet-icons.js"
MANAGER: HassKey[WattnetDashboards] = HassKey(f"{DOMAIN}_dashboards")


class GeneratedLovelace(LovelaceConfig):
    """A dashboard that is generated, not stored."""

    def __init__(
        self,
        hass: HomeAssistant,
        url_path: str,
        title: str,
        build: Callable[[], Awaitable[dict[str, Any]]],
    ) -> None:
        """Describe the dashboard the way Settings > Dashboards lists it."""
        super().__init__(
            hass,
            url_path,
            {
                "mode": MODE_YAML,
                "title": title,
                "icon": ICON,
                "show_in_sidebar": True,
                "require_admin": False,
            },
        )
        self._build = build

    @property
    def mode(self) -> str:
        """Return the mode: generated dashboards are read-only, like YAML ones."""
        return str(MODE_YAML)

    async def async_get_info(self) -> dict[str, Any]:
        """Return what Home Assistant shows about the dashboard."""
        config = await self._build()
        return {"mode": self.mode, "views": len(config["views"])}

    async def async_load(self, force: bool) -> dict[str, Any]:
        """Build the configuration."""
        return await self._build()

    async def async_json(self, force: bool) -> Any:
        """Build the configuration as JSON."""
        return to_json(await self._build())


class WattnetDashboards:
    """Keeps the dashboard in the sidebar while a loaded zone asks for it."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Start with no zones and nothing registered."""
        self.hass = hass
        self._zones: dict[str, WattnetCoordinator] = {}
        self._registered = False
        self._script_ready = False

    @property
    def coordinators(self) -> list[WattnetCoordinator]:
        """Return the zones the dashboard shows."""
        return list(self._zones.values())

    async def async_add(self, coordinator: WattnetCoordinator) -> Callable[[], None]:
        """Show a zone; return the function that stops showing it."""
        entry_id = coordinator.config_entry.entry_id
        self._zones[entry_id] = coordinator
        if not self._registered:
            await self._async_register()
        self._changed()

        def _remove() -> None:
            if self._zones.pop(entry_id, None) is not None:
                self._unregister_if_empty()
                self._changed()

        return _remove

    async def _async_register(self) -> None:
        data = self.hass.data.get(LOVELACE_DATA)
        if data is None:
            _LOGGER.debug("Lovelace is not loaded, so there is no dashboard to add")
            return
        if DASHBOARD_URL_PATH in data.dashboards or frontend.async_panel_exists(
            self.hass, DASHBOARD_URL_PATH
        ):
            _LOGGER.debug("There already is a panel at %s", DASHBOARD_URL_PATH)
            return
        await self._async_add_icon_script()
        builder = WattnetDashboardConfig(self.hass, lambda: self.coordinators)
        data.dashboards[DASHBOARD_URL_PATH] = GeneratedLovelace(
            self.hass, DASHBOARD_URL_PATH, DASHBOARD_TITLE, builder.async_build
        )
        try:
            frontend.async_register_built_in_panel(
                self.hass,
                "lovelace",
                sidebar_title=DASHBOARD_TITLE,
                sidebar_icon=ICON,
                frontend_url_path=DASHBOARD_URL_PATH,
                require_admin=False,
                show_in_sidebar=True,
                config={"mode": MODE_YAML},
            )
        except ValueError:
            data.dashboards.pop(DASHBOARD_URL_PATH, None)
            return
        self._registered = True

    async def _async_add_icon_script(self) -> None:
        """Serve and load the script that adds the Wattnet logo as an icon."""
        if self._script_ready or self.hass.http is None:
            return
        folder = Path(__file__).parent / "frontend"
        await self.hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    ICON_SCRIPT, str(folder / "wattnet-icons.js"), cache_headers=False
                )
            ]
        )
        # The file has no cache headers, so browsers may keep an old copy of it.
        # A version in the URL makes them fetch it again when the script changes.
        version = (folder / "wattnet-icons.js").stat().st_mtime_ns
        frontend.add_extra_js_url(self.hass, f"{ICON_SCRIPT}?v={version}")
        self._script_ready = True

    def _unregister_if_empty(self) -> None:
        if self._zones or not self._registered:
            return
        frontend.async_remove_panel(self.hass, DASHBOARD_URL_PATH)
        data = self.hass.data.get(LOVELACE_DATA)
        if data is not None and isinstance(
            data.dashboards.get(DASHBOARD_URL_PATH), GeneratedLovelace
        ):
            data.dashboards.pop(DASHBOARD_URL_PATH)
        self._registered = False

    def _changed(self) -> None:
        """Tell open dashboards to reload, as they list the zones."""
        self.hass.bus.async_fire(
            EVENT_LOVELACE_UPDATED, {"url_path": DASHBOARD_URL_PATH}
        )


async def async_add_zone(
    hass: HomeAssistant, coordinator: WattnetCoordinator
) -> Callable[[], None]:
    """Show a zone in the Wattnet dashboard; return the function that removes it."""
    manager = hass.data.get(MANAGER)
    if manager is None:
        manager = hass.data[MANAGER] = WattnetDashboards(hass)
    return await manager.async_add(coordinator)
