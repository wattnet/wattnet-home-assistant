"""Tests for the coordinator: sensor values and statistics reconsolidation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from homeassistant.components.recorder import Recorder, get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir  # noqa: F401
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)
from wattnet.client.exceptions import AuthenticationError, WattnetAPIError

from custom_components.wattnet.const import METRICS
from custom_components.wattnet.coordinator import statistic_id

from .conftest import FakeClient, hour, make_record, make_series, quarters

CARBON_ID = "wattnet:es_carbon_footprint_life_cycle"
NOW = "2026-10-06 12:30:00+00:00"


def _carbon(records: list[dict]) -> dict[str, list[dict]]:
    return {"get_footprints:carbon": records}


async def _stats(hass: HomeAssistant, stat_id: str) -> dict[datetime, float]:
    await async_wait_recording_done(hass)
    result = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2026, 10, 1, tzinfo=timezone.utc),
        None,
        {stat_id},
        "hour",
        None,
        {"mean"},
    )
    return {
        datetime.fromtimestamp(row["start"], timezone.utc): row["mean"]
        for row in result.get(stat_id, [])
    }


async def test_statistic_id_is_valid_for_scoped_and_unscoped_metrics():
    ids = {m.key: statistic_id("ES", m, "life-cycle") for m in METRICS}
    assert ids["carbon_footprint"] == CARBON_ID
    assert ids["water_impact"] == "wattnet:es_water_impact"
    for value in ids.values():
        assert value == value.lower() and "-" not in value


async def test_sensors_expose_latest_point_and_validity(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    freezer,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    freezer.move_to(NOW)
    fake_client.latest = _carbon(
        [
            make_record(
                [make_series(False, [[hour(11), 210.5]], "preview")],
                footprint_type="carbon",
            )
        ]
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    state = hass.states.get("sensor.wattnet_spain_es_carbon_footprint")
    assert state is not None
    assert float(state.state) == 210.5
    assert state.attributes["valid"] is False
    assert state.attributes["zone_status"] == "preview"
    assert state.attributes["unit_of_measurement"] == "gCO2/kWh"
    # No data for the other metrics: the sensors exist but are unknown.
    assert (
        hass.states.get("sensor.wattnet_spain_es_environmental_score").state
        == "unknown"
    )


async def test_provisional_hours_are_overwritten_when_consolidated(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    freezer,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    freezer.move_to(NOW)
    fake_client.window = _carbon(
        [
            make_record(
                [
                    make_series(True, [*quarters(9, 100.0), *quarters(10, 110.0)]),
                    make_series(False, [*quarters(11, 200.0)], "preview"),
                ],
                footprint_type="carbon",
            )
        ]
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    first = await _stats(hass, CARBON_ID)
    assert first[datetime(2026, 10, 6, 11, tzinfo=timezone.utc)] == 200.0

    # Wattnet revises hour 11 and consolidates it, and publishes hour 12.
    fake_client.window = _carbon(
        [
            make_record(
                [
                    make_series(
                        True,
                        [
                            *quarters(10, 110.0),
                            *quarters(11, 180.0),
                            *quarters(12, 175.0),
                        ],
                    )
                ],
                footprint_type="carbon",
            )
        ]
    )
    freezer.tick(timedelta(hours=1))
    coordinator = config_entry.runtime_data
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)

    second = await _stats(hass, CARBON_ID)
    assert second[datetime(2026, 10, 6, 11, tzinfo=timezone.utc)] == 180.0
    assert second[datetime(2026, 10, 6, 12, tzinfo=timezone.utc)] == 175.0
    assert second[datetime(2026, 10, 6, 9, tzinfo=timezone.utc)] == 100.0
    # Everything is consolidated: the next window starts after the last hour.
    assert coordinator._watermarks[CARBON_ID].startswith("2026-10-06T13:00")


async def test_next_window_starts_at_first_provisional_hour(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    freezer,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    freezer.move_to(NOW)
    fake_client.window = _carbon(
        [
            make_record(
                [
                    make_series(True, [*quarters(9, 100.0)]),
                    make_series(False, [*quarters(10, 120.0)], "preview"),
                ],
                footprint_type="carbon",
            )
        ]
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    coordinator = config_entry.runtime_data
    assert coordinator._watermarks[CARBON_ID].startswith("2026-10-06T10:00")

    fake_client.calls.clear()
    freezer.tick(timedelta(minutes=30))
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    windowed = [
        kwargs
        for method, kwargs in fake_client.calls
        if method == "get_footprints"
        and kwargs.get("footprint_type") == "carbon"
        and "start" in kwargs
    ]
    # Starts one hour of safety margin before the first provisional hour.
    assert windowed[0]["start"] == datetime(2026, 10, 6, 9, tzinfo=timezone.utc)


async def test_first_run_backfills_configured_days(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    freezer,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    freezer.move_to(NOW)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    starts = [
        kwargs["start"]
        for method, kwargs in fake_client.calls
        if method == "get_footprints"
        and kwargs.get("footprint_type") == "carbon"
        and "start" in kwargs
    ]
    # 7 days, starting at the first full hour inside them (12:30 + 5 min, rounded
    # up), so a Free credential, which reaches 7 days back, is never refused.
    assert starts == [datetime(2026, 9, 29, 13, tzinfo=timezone.utc)]


async def test_watermark_survives_restart(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    freezer,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    freezer.move_to(NOW)
    fake_client.window = _carbon(
        [
            make_record(
                [make_series(True, [*quarters(9, 100.0), *quarters(10, 101.0)])],
                footprint_type="carbon",
            )
        ]
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert await hass.config_entries.async_unload(config_entry.entry_id)

    fake_client.calls.clear()
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    windowed = [
        kwargs["start"]
        for method, kwargs in fake_client.calls
        if method == "get_footprints"
        and kwargs.get("footprint_type") == "carbon"
        and "start" in kwargs
    ]
    # Resumes from the stored watermark (11:00) minus the margin, not 7 days ago.
    assert windowed == [datetime(2026, 10, 6, 10, tzinfo=timezone.utc)]


async def test_auth_failure_triggers_reauth(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    fake_client.error = AuthenticationError("expired")
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler("wattnet")
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_http_401_also_triggers_reauth(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    fake_client.error = WattnetAPIError("nope", status_code=401)
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    assert hass.config_entries.flow.async_progress_by_handler("wattnet")


async def test_api_error_makes_setup_retry(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    fake_client.error = WattnetAPIError("boom", status_code=503)
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_404_means_no_data_not_an_error(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    fake_client.error = WattnetAPIError("none", status_code=404)
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert config_entry.state is ConfigEntryState.LOADED
    assert (
        hass.states.get("sensor.wattnet_spain_es_carbon_footprint").state == "unknown"
    )


async def test_incomplete_current_hour_stays_provisional(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    freezer,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    freezer.move_to(NOW)
    # Hour 12 only has its first two 15-minute points, both already consolidated.
    fake_client.window = _carbon(
        [
            make_record(
                [
                    make_series(
                        True,
                        [*quarters(11, 100.0), *quarters(12, 200.0, count=2)],
                    )
                ],
                footprint_type="carbon",
            )
        ]
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    coordinator = config_entry.runtime_data
    # The watermark does not move past the incomplete hour...
    assert coordinator._watermarks[CARBON_ID].startswith("2026-10-06T12:00")

    # ...so when the rest of the hour arrives it is re-imported with its final mean.
    fake_client.window = _carbon(
        [
            make_record(
                [make_series(True, [*quarters(12, 300.0)])],
                footprint_type="carbon",
            )
        ]
    )
    freezer.tick(timedelta(hours=1))
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)

    stats = await _stats(hass, CARBON_ID)
    assert stats[datetime(2026, 10, 6, 12, tzinfo=timezone.utc)] == 300.0
    assert coordinator._watermarks[CARBON_ID].startswith("2026-10-06T13:00")


async def test_quarter_hour_points_are_averaged_per_hour(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    freezer,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    freezer.move_to(NOW)
    values = [100.0, 120.0, 140.0, 160.0]
    points = [
        [datetime(2026, 10, 6, 9, m, tzinfo=timezone.utc).isoformat(), v]
        for m, v in zip((0, 15, 30, 45), values, strict=True)
    ]
    fake_client.window = _carbon(
        [make_record([make_series(True, points)], footprint_type="carbon")]
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    stats = await _stats(hass, CARBON_ID)
    assert stats[datetime(2026, 10, 6, 9, tzinfo=timezone.utc)] == 130.0


async def test_consolidated_until_sensor_and_diagnostics(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    freezer,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    freezer.move_to(NOW)
    fake_client.window = _carbon(
        [
            make_record(
                [
                    make_series(True, quarters(9, 100.0)),
                    make_series(False, quarters(10, 120.0), "preview"),
                ],
                footprint_type="carbon",
            )
        ]
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    # Only the carbon metric has data, so it is the only watermark.
    state = hass.states.get("sensor.wattnet_spain_es_statistics_consolidated_until")
    assert state.state == "2026-10-06T10:00:00+00:00"

    from custom_components.wattnet.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    diag = await async_get_config_entry_diagnostics(hass, config_entry)
    assert diag["entry"]["data"]["client_id"] == "**REDACTED**"
    assert diag["entry"]["data"]["client_secret"] == "**REDACTED**"
    assert diag["entry"]["data"]["zone"] == "ES"
    assert diag["coordinator"]["consolidated_until"] == "2026-10-06T10:00:00+00:00"
    assert diag["coordinator"]["watermarks"][CARBON_ID].startswith("2026-10-06T10:00")


async def test_entities_have_attribution_and_zone_device(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    from homeassistant.helpers import device_registry as dr

    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    state = hass.states.get("sensor.wattnet_spain_es_carbon_footprint")
    assert state.attributes["attribution"] == "Data provided by Wattnet"
    device = dr.async_get(hass).async_get_device_by_identifier(
        ("wattnet", "ES"), config_entry.entry_id
    )
    assert device.name == "Wattnet - Spain (ES)"


async def test_queries_all_metrics_each_update(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    methods = {
        (m, c.get("footprint_type") or c.get("impact_type"))
        for m, c in fake_client.calls
    }
    assert methods == {
        ("get_footprints", "carbon"),
        ("get_footprints", "water"),
        ("get_impacts", "water"),
        ("get_environmental_score", None),
    }


async def test_changing_scope_starts_a_fresh_history(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    freezer,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    freezer.move_to(NOW)
    fake_client.window = _carbon(
        [
            make_record(
                [make_series(True, quarters(9, 100.0) + quarters(10, 100.0))],
                footprint_type="carbon",
            )
        ]
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert CARBON_ID in config_entry.runtime_data._watermarks

    fake_client.calls.clear()
    hass.config_entries.async_update_entry(
        config_entry, options={**config_entry.options, "scope": "operational"}
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    starts = [
        c["start"]
        for m, c in fake_client.calls
        if m == "get_footprints"
        and c.get("scope") == "operational"
        and "start" in c
        and c.get("footprint_type") == "carbon"
    ]
    # The operational statistic has no watermark, so it backfills the full window
    # instead of resuming where the life-cycle one stopped.
    assert starts[0] == datetime(2026, 9, 29, 13, tzinfo=timezone.utc)


async def test_reimport_action_replaces_history(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    freezer,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    freezer.move_to(NOW)
    fake_client.window = _carbon(
        [make_record([make_series(True, quarters(9, 100.0))], footprint_type="carbon")]
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert (await _stats(hass, CARBON_ID))[
        datetime(2026, 10, 6, 9, tzinfo=timezone.utc)
    ] == 100.0

    # Wattnet corrected an already consolidated hour: only a re-import sees it.
    fake_client.window = _carbon(
        [make_record([make_series(True, quarters(9, 90.0))], footprint_type="carbon")]
    )
    fake_client.calls.clear()
    await hass.services.async_call(
        "wattnet", "reimport_statistics", {"days": 2}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    windowed = [
        c["start"]
        for m, c in fake_client.calls
        if m == "get_footprints"
        and c.get("footprint_type") == "carbon"
        and "start" in c
    ]
    # 2 days back, from the first full hour inside them.
    assert windowed == [datetime(2026, 10, 4, 13, tzinfo=timezone.utc)]
    assert (await _stats(hass, CARBON_ID))[
        datetime(2026, 10, 6, 9, tzinfo=timezone.utc)
    ] == 90.0
    # The configured backfill is restored for later cycles.
    assert config_entry.runtime_data._backfill_days == 7


async def test_reimport_action_fails_without_a_loaded_entry(hass: HomeAssistant):
    from homeassistant.exceptions import ServiceValidationError

    assert await async_setup_component(hass, "wattnet", {})
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "wattnet", "reimport_statistics", {}, blocking=True
        )


# -- scheduling, outages and non-blocking setup ----------------------------


def test_poll_minutes_follow_the_publication_slots():
    from custom_components.wattnet.coordinator import poll_minutes

    assert poll_minutes(15) == [0, 15, 30, 45]
    assert poll_minutes(30) == [0, 30]
    assert poll_minutes(60) == [0]


async def test_updates_run_on_the_quarter_hours_not_at_an_interval(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    freezer,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    freezer.move_to("2026-10-06 11:50:30+00:00")
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert config_entry.runtime_data.update_interval is None

    def latest_calls() -> int:
        return sum(
            1
            for m, c in fake_client.calls
            if m == "get_footprints" and "start" not in c
        )

    before = latest_calls()

    # 11:59 is not a quarter: nothing happens.
    freezer.move_to("2026-10-06 11:59:00+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert latest_calls() == before

    # 12:00:00 is, and every entry updates at the same second.
    freezer.move_to("2026-10-06 12:00:00+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert latest_calls() > before


async def test_the_update_on_the_quarter_waits_for_the_point_that_is_not_out_yet(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    freezer,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    freezer.move_to("2026-10-06 11:50:30+00:00")
    fake_client.latest = _carbon(
        [
            make_record(
                [make_series(True, [["2026-10-06T11:45:00+00:00", 100.0]])],
                footprint_type="carbon",
            )
        ]
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    entity = "sensor.wattnet_spain_es_carbon_footprint"

    # At 12:00 the point of 12:00 is not published: the update finds 11:45 again.
    freezer.move_to("2026-10-06 12:00:00+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert (
        hass.states.get(entity)
        .attributes["data_timestamp"]
        .startswith("2026-10-06T11:45")
    )

    # It is published a minute later, and the next retry picks it up.
    fake_client.latest = _carbon(
        [
            make_record(
                [make_series(True, [["2026-10-06T12:00:00+00:00", 120.0]])],
                footprint_type="carbon",
            )
        ]
    )
    freezer.move_to("2026-10-06 12:01:00+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    state = hass.states.get(entity)
    assert state.state == "120.0"
    assert state.attributes["data_timestamp"].startswith("2026-10-06T12:00")


async def test_outage_under_an_hour_keeps_the_last_data(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    freezer,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    freezer.move_to(NOW)
    fake_client.latest = _carbon(
        [make_record([make_series(True, [[hour(11), 210.5]])], footprint_type="carbon")]
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator = config_entry.runtime_data
    entity = "sensor.wattnet_spain_es_carbon_footprint"
    assert hass.states.get(entity).state == "210.5"

    fake_client.error = WattnetAPIError("down", status_code=503)
    freezer.tick(timedelta(minutes=59))
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert coordinator.last_update_success
    assert hass.states.get(entity).state == "210.5"  # still served
    assert not ir.async_get(hass).async_get_issue(
        "wattnet", f"api_unreachable_{config_entry.entry_id}"
    )


async def test_outage_over_an_hour_makes_entities_unavailable_and_raises_issue(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    freezer,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    freezer.move_to(NOW)
    fake_client.latest = _carbon(
        [make_record([make_series(True, [[hour(11), 210.5]])], footprint_type="carbon")]
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator = config_entry.runtime_data
    entity = "sensor.wattnet_spain_es_carbon_footprint"
    issue_id = f"api_unreachable_{config_entry.entry_id}"

    fake_client.error = WattnetAPIError("down", status_code=503)
    freezer.tick(timedelta(hours=1, minutes=1))
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert not coordinator.last_update_success
    assert hass.states.get(entity).state == "unavailable"
    issue = ir.async_get(hass).async_get_issue("wattnet", issue_id)
    assert issue is not None
    assert issue.translation_placeholders == {"zone": "Spain (ES)"}

    # It recovers by itself and the issue goes away.
    fake_client.error = None
    freezer.tick(timedelta(minutes=15))
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(entity).state == "210.5"
    assert ir.async_get(hass).async_get_issue("wattnet", issue_id) is None


async def test_auth_error_is_never_tolerated(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    fake_client.error = AuthenticationError("expired")
    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    flows = hass.config_entries.flow.async_progress_by_handler("wattnet")
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_setup_does_not_wait_for_the_history_download(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    freezer,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    import asyncio

    freezer.move_to(NOW)
    gate = asyncio.Event()
    original = fake_client.get_footprints

    async def slow(**kwargs):
        if "start" in kwargs:
            await gate.wait()  # the history download hangs
        return await original(**kwargs)

    fake_client.get_footprints = slow  # type: ignore[method-assign]
    fake_client.window = _carbon(
        [make_record([make_series(True, quarters(9, 100.0))], footprint_type="carbon")]
    )
    fake_client.latest = _carbon(
        [make_record([make_series(True, [[hour(11), 210.5]])], footprint_type="carbon")]
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    # Setup finished and the sensor has its value while the download is stuck.
    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.wattnet_spain_es_carbon_footprint").state == "210.5"
    assert config_entry.runtime_data.consolidated_until is None

    gate.set()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert config_entry.runtime_data.consolidated_until is not None


async def test_sensors_do_not_feed_the_recorder_statistics(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    state = hass.states.get("sensor.wattnet_spain_es_carbon_footprint")
    assert "state_class" not in state.attributes


async def test_no_sensor_feeds_the_recorder_statistics_and_the_score_has_no_unit(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    for metric in METRICS:
        attrs = hass.states.get(f"sensor.wattnet_spain_es_{metric.key}").attributes
        # A state class would make the recorder compile uncorrected statistics
        # next to the consolidated wattnet:* ones.
        assert "state_class" not in attrs, metric.key
        assert ("unit_of_measurement" in attrs) == (metric.unit is not None)
    # The score has neither, so its history is a band of values, not a line.
    score = hass.states.get("sensor.wattnet_spain_es_environmental_score")
    assert "unit_of_measurement" not in score.attributes
    assert "state_class" not in score.attributes


async def test_stopping_home_assistant_stops_the_schedule_without_errors(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    from homeassistant.const import EVENT_HOMEASSISTANT_STOP

    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done(wait_background_tasks=True)
    # Unloading afterwards must not trip over the listener that already fired.
    assert await hass.config_entries.async_unload(config_entry.entry_id)

    assert "Unable to remove unknown" not in caplog.text


# -- waiting for the point of the current slot --------------------------------


def _latest_carbon(timestamp: str, value: float) -> dict[str, list[dict]]:
    return _carbon(
        [
            make_record(
                [make_series(True, [[timestamp, value]])], footprint_type="carbon"
            )
        ]
    )


def _latest_calls(fake_client: FakeClient) -> int:
    return sum(
        1
        for m, c in fake_client.calls
        if m == "get_footprints"
        and c.get("footprint_type") == "carbon"
        and "start" not in c
    )


async def _setup_at(
    hass: HomeAssistant,
    freezer,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
    now: str,
    newest: str,
) -> None:
    freezer.move_to(now)
    fake_client.latest = _latest_carbon(newest, 100.0)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_retries_every_minute_until_the_slot_is_published(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    freezer,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    # At 12:01 the newest point is still the one of 11:45: the 12:00 one is late.
    await _setup_at(
        hass,
        freezer,
        fake_client,
        config_entry,
        "2026-10-06 12:01:30+00:00",
        "2026-10-06T11:45:00+00:00",
    )
    entity = "sensor.wattnet_spain_es_carbon_footprint"
    assert (
        hass.states.get(entity)
        .attributes["data_timestamp"]
        .startswith("2026-10-06T11:45")
    )
    after_setup = _latest_calls(fake_client)

    # A minute later it is still not there: it asks again.
    freezer.move_to("2026-10-06 12:02:30+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _latest_calls(fake_client) == after_setup + 1

    # Then Wattnet publishes it: the state is updated and the retries stop.
    fake_client.latest = _latest_carbon("2026-10-06T12:00:00+00:00", 120.0)
    freezer.move_to("2026-10-06 12:03:30+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    state = hass.states.get(entity)
    assert state.state == "120.0"
    assert state.attributes["data_timestamp"].startswith("2026-10-06T12:00")

    calls = _latest_calls(fake_client)
    freezer.move_to("2026-10-06 12:04:30+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _latest_calls(fake_client) == calls  # nothing more scheduled


async def test_gives_up_retrying_after_five_minutes(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    freezer,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    await _setup_at(
        hass,
        freezer,
        fake_client,
        config_entry,
        "2026-10-06 12:01:30+00:00",
        "2026-10-06T11:45:00+00:00",
    )
    # Never published: one retry per minute, and it stops at 12:05.
    for minute in range(2, 9):
        freezer.move_to(f"2026-10-06 12:{minute:02d}:30+00:00")
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)
    # setup, then retries at 12:02, 12:03, 12:04 and 12:05 only: the one at 12:05
    # finds the five-minute window closed and does not schedule another.
    assert _latest_calls(fake_client) == 5


async def test_does_not_retry_when_the_slot_is_already_published(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    freezer,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    await _setup_at(
        hass,
        freezer,
        fake_client,
        config_entry,
        "2026-10-06 12:01:30+00:00",
        "2026-10-06T12:00:00+00:00",
    )
    calls = _latest_calls(fake_client)
    freezer.move_to("2026-10-06 12:03:30+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _latest_calls(fake_client) == calls


async def test_does_not_retry_when_data_is_much_older_or_missing(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    freezer,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    # Two slots behind is a longer delay, not a point about to appear.
    await _setup_at(
        hass,
        freezer,
        fake_client,
        config_entry,
        "2026-10-06 12:01:30+00:00",
        "2026-10-06T11:30:00+00:00",
    )
    calls = _latest_calls(fake_client)
    freezer.move_to("2026-10-06 12:03:30+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _latest_calls(fake_client) == calls


async def test_a_retry_is_cancelled_when_the_entry_is_unloaded(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    freezer,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    await _setup_at(
        hass,
        freezer,
        fake_client,
        config_entry,
        "2026-10-06 12:01:30+00:00",
        "2026-10-06T11:45:00+00:00",
    )
    assert config_entry.runtime_data._retry_unsub is not None
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    calls = _latest_calls(fake_client)

    freezer.move_to("2026-10-06 12:02:30+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _latest_calls(fake_client) == calls
