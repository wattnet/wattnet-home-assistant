"""The registration of the dashboard in Home Assistant."""

from __future__ import annotations

import importlib
import json
import sys
from unittest.mock import patch

import orjson
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wattnet import validation
from custom_components.wattnet.panel import MANAGER, async_add_zone

from .conftest import FakeClient


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await async_setup_component(hass, "frontend", {})
    assert await async_setup_component(hass, "lovelace", {})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_the_generated_dashboard_answers_like_any_dashboard_of_home_assistant(
    hass: HomeAssistant, fake_client: FakeClient, config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    dashboard = hass.data[LOVELACE_DATA].dashboards["wattnet"]

    assert dashboard.mode == "yaml"  # read-only, like a dashboard in YAML
    assert dashboard.url_path == "wattnet"
    assert await dashboard.async_get_info() == {"mode": "yaml", "views": 1}
    config = await dashboard.async_load(False)
    assert config["title"] == "Wattnet" and len(config["views"]) == 1
    body = orjson.dumps(await dashboard.async_json(False))  # what is served
    assert json.loads(body) == config


async def test_removing_a_zone_twice_is_harmless(
    hass: HomeAssistant, fake_client: FakeClient, config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    remove = await async_add_zone(hass, config_entry.runtime_data)  # same zone again
    remove()  # the zone leaves, so the dashboard goes
    assert "wattnet" not in hass.data[LOVELACE_DATA].dashboards
    remove()  # nothing left to remove: no error, nothing changes
    assert hass.data[MANAGER].coordinators == []
    # The entry's own removal, on unload, finds nothing to do either.
    assert await hass.config_entries.async_unload(config_entry.entry_id)


async def test_a_panel_that_already_exists_is_left_alone(
    hass: HomeAssistant, fake_client: FakeClient, config_entry: MockConfigEntry
) -> None:
    assert await async_setup_component(hass, "frontend", {})
    assert await async_setup_component(hass, "lovelace", {})
    someone_elses = object()
    hass.data[LOVELACE_DATA].dashboards["wattnet"] = someone_elses  # type: ignore[assignment]
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert hass.data[LOVELACE_DATA].dashboards["wattnet"] is someone_elses
    assert hass.data[MANAGER].coordinators  # the zone is known, just not shown
    # Unloading does not take away what is not ours.
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert hass.data[LOVELACE_DATA].dashboards["wattnet"] is someone_elses


async def test_a_refused_panel_leaves_nothing_behind(
    hass: HomeAssistant, fake_client: FakeClient, config_entry: MockConfigEntry
) -> None:
    assert await async_setup_component(hass, "frontend", {})
    assert await async_setup_component(hass, "lovelace", {})
    config_entry.add_to_hass(hass)
    with patch(
        "custom_components.wattnet.panel.frontend.async_register_built_in_panel",
        side_effect=ValueError("not unique"),
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)

    assert "wattnet" not in hass.data[LOVELACE_DATA].dashboards
    assert not hass.data[MANAGER]._registered


async def test_a_dashboard_replaced_by_someone_else_is_not_removed_on_unload(
    hass: HomeAssistant, fake_client: FakeClient, config_entry: MockConfigEntry
) -> None:
    await _setup(hass, config_entry)
    replacement = object()
    hass.data[LOVELACE_DATA].dashboards["wattnet"] = replacement  # type: ignore[assignment]

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert hass.data[LOVELACE_DATA].dashboards["wattnet"] is replacement


def test_without_probatio_the_older_schema_library_is_used():
    # Home Assistant before the switch to probatio only has voluptuous. Both offer
    # the same API, which is all the integration needs from either.
    import probatio

    try:
        with patch.dict(sys.modules, {"probatio": None}):
            importlib.reload(validation)
            assert validation.vol is not probatio  # the fallback was taken
            schema = validation.vol.Schema({validation.vol.Required("zone"): str})
            assert schema({"zone": "ES"}) == {"zone": "ES"}
    finally:
        importlib.reload(validation)
    assert validation.vol is probatio  # back to the one Home Assistant ships
