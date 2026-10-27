"""Tests for the statistics derived from the home's grid consumption."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from homeassistant.components.energy.data import async_get_manager
from homeassistant.components.energy.validate import async_validate
from homeassistant.components.recorder import Recorder, get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    list_statistic_ids,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.wattnet.const import METRICS
from custom_components.wattnet.coordinator import grid_statistic_id

from .conftest import FakeClient, make_record, make_series, quarters

NOW = "2026-10-06 12:30:00+00:00"
CARBON = "wattnet:es_grid_carbon_emissions_life_cycle"
WATER = "wattnet:es_grid_water_consumption_life_cycle"
IMPACT = "wattnet:es_grid_water_impact"
UTC = timezone.utc


def _h(hour: int, day: int = 6) -> datetime:
    return datetime(2026, 10, day, hour, tzinfo=UTC)


def _metric(key: str):
    return next(m for m in METRICS if m.key == key)


async def _enable_energy(hass: HomeAssistant, *meters: str) -> None:
    assert await async_setup_component(hass, "energy", {})
    manager = await async_get_manager(hass)
    await manager.async_update(
        {
            "energy_sources": [
                {"type": "grid", "stat_energy_from": meter, "cost_adjustment_day": 0}
                for meter in meters
            ]
        }
    )


async def _seed_meter(
    hass: HomeAssistant,
    stat_id: str,
    totals: dict[datetime, float],
    unit: str = "kWh",
) -> None:
    """Import a cumulative energy meter, like the Energy dashboard reads."""
    meta = StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=stat_id,
        source="recorder",
        statistic_id=stat_id,
        unit_class="energy",
        unit_of_measurement=unit,
    )
    async_import_statistics(
        hass,
        meta,
        [StatisticData(start=h, state=t, sum=t) for h, t in sorted(totals.items())],
    )
    await async_wait_recording_done(hass)


async def _sums(hass: HomeAssistant, stat_id: str) -> dict[datetime, float]:
    await async_wait_recording_done(hass)
    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        _h(0, day=1),
        None,
        {stat_id},
        "hour",
        None,
        {"sum"},
    )
    return {
        datetime.fromtimestamp(r["start"], UTC): r["sum"] for r in rows.get(stat_id, [])
    }


def _window(carbon, water=None, impact=None):
    """Window payloads for the three metrics that have a grid total."""
    window = {
        "get_footprints:carbon": [
            make_record(
                [make_series(True, carbon[0], "complete")] + carbon[1],
                footprint_type="carbon",
            )
        ]
    }
    if water is not None:
        window["get_footprints:water"] = [
            make_record([make_series(True, water)], footprint_type="water")
        ]
    if impact is not None:
        window["get_impacts:water"] = [
            make_record([make_series(True, impact)], impact_type="water")
        ]
    return window


async def _setup(hass, config_entry):
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


# -- the maths, end to end -----------------------------------------------------


async def test_carbon_water_and_impact_are_consumption_times_footprint(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    freezer,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    freezer.move_to(NOW)
    await _enable_energy(hass, "sensor.grid_energy")
    # The meter reads 100 kWh at 08:00, then 2, 1 and 4 kWh in hours 09, 10 and 11.
    await _seed_meter(
        hass,
        "sensor.grid_energy",
        {_h(8): 100.0, _h(9): 102.0, _h(10): 103.0, _h(11): 107.0},
    )
    fake_client.window = _window(
        carbon=(quarters(9, 100.0) + quarters(10, 200.0) + quarters(11, 50.0), []),
        water=quarters(9, 1.0) + quarters(10, 2.0) + quarters(11, 3.0),
        impact=quarters(9, 10.0) + quarters(10, 10.0) + quarters(11, 10.0),
    )
    await _setup(hass, config_entry)

    # gCO2/kWh x kWh, accumulated: 2x100, +1x200, +4x50.
    assert await _sums(hass, CARBON) == {_h(9): 200.0, _h(10): 400.0, _h(11): 600.0}
    # L/kWh x kWh: 2x1, +1x2, +4x3.
    assert await _sums(hass, WATER) == {_h(9): 2.0, _h(10): 4.0, _h(11): 16.0}
    # stress-L/kWh x kWh: (2+1+4) x 10.
    assert await _sums(hass, IMPACT) == {_h(9): 20.0, _h(10): 30.0, _h(11): 70.0}


async def test_the_hour_in_progress_is_not_counted(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    freezer,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    freezer.move_to(NOW)  # 12:30: hour 12 has not finished
    await _enable_energy(hass, "sensor.grid_energy")
    await _seed_meter(
        hass,
        "sensor.grid_energy",
        {_h(10): 100.0, _h(11): 101.0, _h(12): 150.0},
    )
    fake_client.window = _window(carbon=(quarters(11, 10.0) + quarters(12, 10.0), []))
    await _setup(hass, config_entry)

    assert list(await _sums(hass, CARBON)) == [_h(11)]


async def test_meters_are_summed_and_units_converted_to_kwh(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    freezer,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    freezer.move_to(NOW)
    await _enable_energy(hass, "sensor.tariff_a", "sensor.tariff_b")
    await _seed_meter(hass, "sensor.tariff_a", {_h(8): 0.0, _h(9): 1.0})
    # The second meter counts in Wh: 500 Wh is 0.5 kWh.
    await _seed_meter(hass, "sensor.tariff_b", {_h(8): 0.0, _h(9): 500.0}, unit="Wh")
    fake_client.window = _window(carbon=(quarters(9, 100.0), []))
    await _setup(hass, config_entry)

    assert await _sums(hass, CARBON) == {_h(9): 150.0}  # (1 + 0.5) kWh x 100


# -- corrections --------------------------------------------------------------


async def test_a_consolidated_hour_replaces_the_provisional_total_and_later_hours(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    freezer,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    freezer.move_to(NOW)
    await _enable_energy(hass, "sensor.grid_energy")
    await _seed_meter(
        hass,
        "sensor.grid_energy",
        {_h(9): 100.0, _h(10): 101.0, _h(11): 103.0, _h(12): 106.0},
    )
    # Hour 11 is provisional (200) at first; 10 was already consolidated (100).
    fake_client.window = _window(
        carbon=(
            quarters(10, 100.0),
            [make_series(False, quarters(11, 200.0), "preview")],
        )
    )
    await _setup(hass, config_entry)
    assert await _sums(hass, CARBON) == {_h(10): 100.0, _h(11): 500.0}

    # Wattnet consolidates hour 11 at 180 and publishes hour 12 (150); hour 12 of
    # the home is complete a bit later.
    freezer.move_to("2026-10-06 13:30:00+00:00")
    fake_client.window = _window(
        carbon=(
            quarters(10, 100.0) + quarters(11, 180.0) + quarters(12, 150.0),
            [],
        )
    )
    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)

    # 10: 1 kWh x 100 = 100. 11: 2 x 180 = 360 -> 460. 12: 3 x 150 = 450 -> 910.
    assert await _sums(hass, CARBON) == {
        _h(10): 100.0,
        _h(11): 460.0,
        _h(12): 910.0,
    }


async def test_the_total_continues_from_what_was_already_imported(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    freezer,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    freezer.move_to("2026-10-06 11:30:00+00:00")
    await _enable_energy(hass, "sensor.grid_energy")
    await _seed_meter(
        hass,
        "sensor.grid_energy",
        {_h(8): 0.0, _h(9): 1.0, _h(10): 3.0, _h(11): 6.0},
    )
    fake_client.window = _window(carbon=(quarters(9, 10.0) + quarters(10, 10.0), []))
    await _setup(hass, config_entry)
    assert await _sums(hass, CARBON) == {_h(9): 10.0, _h(10): 30.0}

    # Next cycles: all consolidated, so the watermark moves on and only the
    # lookback window is re-read. The total must carry on, not restart at zero.
    freezer.move_to("2026-10-06 12:30:00+00:00")
    fake_client.window = _window(
        carbon=(quarters(9, 10.0) + quarters(10, 10.0) + quarters(11, 10.0), [])
    )
    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert await _sums(hass, CARBON) == {_h(9): 10.0, _h(10): 30.0, _h(11): 60.0}


async def test_late_consumption_is_picked_up_within_the_lookback(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    freezer,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    freezer.move_to(NOW)
    await _enable_energy(hass, "sensor.grid_energy")
    # The meter reported nothing yet for hours 9 and 10.
    await _seed_meter(hass, "sensor.grid_energy", {_h(8): 0.0})
    fake_client.window = _window(carbon=(quarters(9, 10.0) + quarters(10, 10.0), []))
    await _setup(hass, config_entry)
    assert await _sums(hass, CARBON) == {_h(9): 0.0, _h(10): 0.0}

    # A day later its data arrives, for the hours that were already consolidated.
    await _seed_meter(hass, "sensor.grid_energy", {_h(9): 5.0, _h(10): 8.0})
    freezer.move_to("2026-10-07 12:30:00+00:00")
    fake_client.window = _window(carbon=(quarters(9, 10.0) + quarters(10, 10.0), []))
    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert await _sums(hass, CARBON) == {_h(9): 50.0, _h(10): 80.0}


# -- when there is nothing to compute ------------------------------------------


async def test_nothing_is_derived_when_the_energy_dashboard_has_no_grid(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    freezer,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    freezer.move_to(NOW)
    assert await async_setup_component(hass, "energy", {})  # configured by nobody
    fake_client.window = _window(carbon=(quarters(9, 10.0), []))
    await _setup(hass, config_entry)
    await async_wait_recording_done(hass)

    ids = await get_instance(hass).async_add_executor_job(list_statistic_ids, hass)
    names = {i["statistic_id"] for i in ids}
    assert "wattnet:es_carbon_footprint_life_cycle" in names  # the footprint itself
    assert not {CARBON, WATER, IMPACT} & names


async def test_nothing_is_derived_when_the_energy_integration_is_not_loaded(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    freezer,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    freezer.move_to(NOW)
    fake_client.window = _window(carbon=(quarters(9, 10.0), []))
    await _setup(hass, config_entry)
    assert "energy" not in hass.config.components
    assert config_entry.runtime_data.consolidated_until is not None  # still works


async def test_both_grid_formats_of_home_assistant_are_read(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    from custom_components.wattnet.coordinator import WattnetCoordinator

    config_entry.add_to_hass(hass)
    coordinator = WattnetCoordinator(hass, config_entry, AsyncMock())
    hass.config.components.add("energy")
    manager = AsyncMock()
    manager.data = {
        "energy_sources": [
            {"type": "solar", "stat_energy_from": "sensor.solar"},
            # Recent format: one meter per grid connection.
            {"type": "grid", "stat_energy_from": "sensor.new", "stat_energy_to": None},
            # Older format: a list of meters.
            {
                "type": "grid",
                "flow_from": [
                    {"stat_energy_from": "sensor.old_a"},
                    {"stat_energy_from": "sensor.old_b"},
                    {"stat_energy_from": "sensor.new"},  # duplicate
                ],
                "flow_to": [],
            },
            # Export-only grid: no import meter.
            {"type": "grid", "stat_energy_from": None, "stat_energy_to": "sensor.out"},
        ]
    }
    with patch(
        "homeassistant.components.energy.data.async_get_manager",
        AsyncMock(return_value=manager),
    ):
        ids = await coordinator.async_grid_statistic_ids()
    assert ids == ["sensor.new", "sensor.old_a", "sensor.old_b"]


def test_statistic_ids_of_the_derived_totals():
    assert grid_statistic_id("ES", _metric("carbon_footprint"), "life-cycle") == CARBON
    assert grid_statistic_id("ES", _metric("water_footprint"), "operational") == (
        "wattnet:es_grid_water_consumption_operational"
    )
    assert grid_statistic_id("ES", _metric("water_impact"), "life-cycle") == IMPACT
    assert grid_statistic_id("ES", _metric("environmental_score"), "x") is None


# -- the Energy dashboard accepts it --------------------------------------------


async def test_the_water_total_is_valid_as_a_water_source_of_the_energy_dashboard(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    freezer,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    freezer.move_to(NOW)
    await _enable_energy(hass, "sensor.grid_energy")
    await _seed_meter(hass, "sensor.grid_energy", {_h(8): 0.0, _h(9): 2.0})
    fake_client.window = _window(carbon=(quarters(9, 10.0), []), water=quarters(9, 3.0))
    await _setup(hass, config_entry)
    await async_wait_recording_done(hass)

    manager = await async_get_manager(hass)
    await manager.async_update(
        {
            "energy_sources": [
                {
                    "type": "grid",
                    "stat_energy_from": "sensor.grid_energy",
                    "cost_adjustment_day": 0,
                },
                {
                    "type": "water",
                    "stat_energy_from": WATER,
                    "stat_cost": None,
                    "entity_energy_price": None,
                    "number_energy_price": None,
                },
            ]
        }
    )
    result = (await async_validate(hass)).as_dict()

    water_source = result["energy_sources"][1]
    assert water_source == [], water_source
    # The metadata the panel needs: a cumulative statistic in litres.
    ids = await get_instance(hass).async_add_executor_job(list_statistic_ids, hass)
    meta = next(i for i in ids if i["statistic_id"] == WATER)
    assert meta["has_sum"] is True
    assert meta["statistics_unit_of_measurement"] == "L"
    assert meta["unit_class"] == "volume"
