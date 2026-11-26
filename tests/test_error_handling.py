"""Failures of the API, of the credentials and of Home Assistant internals."""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from homeassistant.components.recorder import Recorder
from homeassistant.config_entries import ConfigEntryState, ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry
from wattnet.client.exceptions import (
    AuthenticationError,
    WattnetAPIError,
    WattnetError,
)

from custom_components.wattnet.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    DOMAIN,
    METRICS,
)
from custom_components.wattnet.parsing import HourlyPoint

from .conftest import FakeClient, make_record, make_series, quarters

UTC = timezone.utc
CREDS = {CONF_CLIENT_ID: "id", CONF_CLIENT_SECRET: "secret"}


async def _setup(
    hass: HomeAssistant, entry: MockConfigEntry, wait_for_downloads: bool = True
) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    # A download that was made to hang never ends: do not wait for it.
    await hass.async_block_till_done(wait_background_tasks=wait_for_downloads)


def _window_data(fake_client: FakeClient) -> None:
    fake_client.window = {
        "get_footprints:carbon": [
            make_record(
                [make_series(True, quarters(9, 100.0))], footprint_type="carbon"
            )
        ]
    }


def _fail_only_the_window(fake_client: FakeClient, error: Exception) -> None:
    """Make the history requests fail while the latest value keeps working."""
    original = fake_client.get_footprints

    async def failing(**kwargs):
        if "start" in kwargs:
            raise error
        return await original(**kwargs)

    fake_client.get_footprints = failing  # type: ignore[method-assign]


# -- the update ---------------------------------------------------------------


async def test_a_generic_client_error_makes_setup_retry(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    fake_client.error = WattnetError("boom")  # not an API error, e.g. a bad payload
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


# -- the history download, which runs in the background -----------------------


async def test_losing_access_during_the_history_download_asks_to_sign_in_again(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    _fail_only_the_window(fake_client, AuthenticationError("expired"))
    await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED  # the sensors still work
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_a_failing_history_download_is_only_a_warning_and_is_retried(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _fail_only_the_window(fake_client, WattnetAPIError("down", status_code=503))
    with caplog.at_level(logging.WARNING):
        await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    assert "Could not sync Wattnet statistics, will retry" in caplog.text
    assert config_entry.runtime_data.consolidated_until is None  # nothing imported


async def _hang_the_first_download(fake_client: FakeClient) -> asyncio.Event:
    """Make the first history request wait until the returned event is set."""
    gate = asyncio.Event()
    original = fake_client.get_footprints
    first = {"pending": True}

    async def slow(**kwargs):
        if "start" in kwargs and first["pending"]:
            first["pending"] = False
            await gate.wait()
        return await original(**kwargs)

    fake_client.get_footprints = slow  # type: ignore[method-assign]
    return gate


async def test_unloading_the_entry_cancels_a_download_that_is_running(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    await _hang_the_first_download(fake_client)
    await _setup(hass, config_entry, wait_for_downloads=False)
    task = config_entry.runtime_data._sync_task
    assert task is not None and not task.done()

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert task.cancelled()


async def test_a_reimport_cancels_a_download_that_is_still_running(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    await _hang_the_first_download(fake_client)
    _window_data(fake_client)
    await _setup(hass, config_entry, wait_for_downloads=False)
    coordinator = config_entry.runtime_data
    stuck = coordinator._sync_task
    assert stuck is not None and not stuck.done()

    await coordinator.async_reimport(1)

    assert stuck.cancelled()  # the old one is dropped, the new one runs to the end
    assert coordinator.consolidated_until is not None


# -- the grid totals, called with what cannot be derived ----------------------


async def test_grid_totals_are_not_derived_for_a_metric_without_a_grid_output(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    await _setup(hass, config_entry)
    score = next(m for m in METRICS if m.key == "environmental_score")
    assert score.grid is None
    now = datetime(2026, 10, 6, 12, 30, tzinfo=UTC)
    with patch(
        "custom_components.wattnet.coordinator.async_add_external_statistics"
    ) as add:
        await config_entry.runtime_data._async_sync_grid(score, [], now, ["sensor.g"])
    add.assert_not_called()


async def test_grid_totals_wait_until_the_first_hour_has_finished(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    await _setup(hass, config_entry)
    carbon = next(m for m in METRICS if m.key == "carbon_footprint")
    now = datetime(2026, 10, 6, 12, 30, tzinfo=UTC)
    in_progress = [
        HourlyPoint(datetime(2026, 10, 6, 12, tzinfo=UTC), 100.0, 100.0, 100.0, False)
    ]
    with patch(
        "custom_components.wattnet.coordinator.async_add_external_statistics"
    ) as add:
        await config_entry.runtime_data._async_sync_grid(
            carbon, in_progress, now, ["sensor.g"]
        )
    add.assert_not_called()


# -- the config flow ----------------------------------------------------------


async def _start(hass: HomeAssistant) -> ConfigFlowResult:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    return await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)


async def test_any_other_wattnet_error_while_signing_in_is_a_connection_error(
    hass: HomeAssistant, fake_client: FakeClient
) -> None:
    async def boom():
        raise WattnetError("unexpected payload")

    fake_client.get_zones = boom  # type: ignore[method-assign]
    assert (await _start(hass))["errors"] == {"base": "cannot_connect"}


async def test_an_unexpected_error_while_signing_in_is_logged_and_called_unknown(
    hass: HomeAssistant,
    fake_client: FakeClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def boom():
        raise RuntimeError("something else")

    fake_client.get_zones = boom  # type: ignore[method-assign]
    result = await _start(hass)
    assert result["errors"] == {"base": "unknown"}
    assert "Unexpected error validating Wattnet credentials" in caplog.text


async def test_reconfigure_aborts_when_the_stored_credentials_no_longer_work(
    hass: HomeAssistant, fake_client: FakeClient, config_entry: MockConfigEntry
) -> None:
    async def denied():
        raise AuthenticationError("secret regenerated")

    fake_client.get_zones = denied  # type: ignore[method-assign]
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["type"] == "abort" and result["reason"] == "invalid_auth"


# -- Home Assistant internals --------------------------------------------------


async def test_the_integration_still_works_when_the_dashboard_module_cannot_load(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # What an import error of the Lovelace internals it relies on looks like.
    with patch.dict(sys.modules, {"custom_components.wattnet.panel": None}):
        with caplog.at_level(logging.WARNING):
            await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    assert "The Wattnet dashboard is not available in this version" in caplog.text
    assert hass.states.get("sensor.wattnet_spain_es_carbon_footprint") is not None


# -- the limits of the access level of the credential -------------------------

NOW = "2026-10-06 12:30:00+00:00"
OLDEST = datetime(2026, 9, 29, 13, tzinfo=UTC)  # 7 days back, first full hour inside


def _limit(code: str) -> WattnetAPIError:
    return WattnetAPIError("beyond the access level", status_code=403, code=code)


def _refuse_windows_before(
    fake_client: FakeClient, oldest: datetime, code: str
) -> None:
    """Refuse, as the API does for a Free credential, history older than ``oldest``."""
    original = fake_client.get_footprints

    async def limited(**kwargs):
        if "start" in kwargs and kwargs["start"] < oldest:
            fake_client.calls.append(("get_footprints", kwargs))
            raise _limit(code)
        return await original(**kwargs)

    fake_client.get_footprints = limited  # type: ignore[method-assign]


def _carbon_starts(fake_client: FakeClient) -> list[datetime]:
    return [
        c["start"]
        for m, c in fake_client.calls
        if m == "get_footprints"
        and c.get("footprint_type") == "carbon"
        and "start" in c
    ]


async def test_an_access_level_limit_does_not_ask_to_sign_in_again(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    fake_client.error = _limit("endpoint_group_not_allowed")
    await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []


async def test_a_403_without_a_limit_code_still_asks_to_sign_in_again(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
) -> None:
    fake_client.error = WattnetAPIError("forbidden", status_code=403)
    await _setup(hass, config_entry)

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_history_beyond_the_access_level_is_asked_again_from_the_oldest_hour(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    freezer,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    freezer.move_to(NOW)
    _refuse_windows_before(fake_client, OLDEST, "history_limit_exceeded")
    await _setup(hass, config_entry)
    fake_client.calls.clear()

    # A reimport of 30 days with a credential that reaches 7.
    with caplog.at_level(logging.WARNING):
        await hass.services.async_call(
            DOMAIN, "reimport_statistics", {"days": 30}, blocking=True
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    starts = _carbon_starts(fake_client)
    # The chunks older than 7 days are refused; the one that reaches the limit is
    # asked again from the oldest hour allowed, and the newer ones go through.
    assert OLDEST in starts
    assert all(s >= OLDEST for s in starts[starts.index(OLDEST) :])
    # One warning per refused metric (the fake refuses the two footprints), for the
    # window that crosses the limit; the windows wholly older are only debug.
    assert caplog.text.count("does not reach") == 2
    assert "Skipping" not in caplog.text
    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []

    # Repairs shows it, with the oldest hour the credential reaches.
    issue = ir.async_get(hass).async_get_issue(
        DOMAIN, f"history_limited_{config_entry.entry_id}"
    )
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.translation_placeholders == {
        "zone": "Spain (ES)",
        "oldest": "2026-09-29 13:00 UTC",
    }
    assert issue.learn_more_url == "https://developers.wattnet.eu"

    # A reimport within the access level clears it, and so does removing the entry.
    await hass.services.async_call(
        DOMAIN, "reimport_statistics", {"days": 3}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    issue_id = f"history_limited_{config_entry.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    await hass.services.async_call(
        DOMAIN, "reimport_statistics", {"days": 30}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_a_window_refused_by_another_limit_is_skipped(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    freezer,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    freezer.move_to(NOW)
    _fail_only_the_window(fake_client, _limit("range_limit_exceeded"))
    with caplog.at_level(logging.WARNING):
        await _setup(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    assert "Skipping carbon_footprint" in caplog.text
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []
    assert config_entry.runtime_data.consolidated_until is None  # nothing imported


async def test_history_still_refused_from_the_oldest_hour_is_skipped(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    freezer,
    fake_client: FakeClient,
    config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A level that reaches less than the Free one: asked again once, then skipped."""
    freezer.move_to(NOW)
    _fail_only_the_window(fake_client, _limit("history_limit_exceeded"))
    await _setup(hass, config_entry)
    fake_client.calls.clear()

    with caplog.at_level(logging.WARNING):
        await hass.services.async_call(
            DOMAIN, "reimport_statistics", {"days": 10}, blocking=True
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    assert "does not reach" in caplog.text
    assert "Skipping carbon_footprint" in caplog.text
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []


def test_ceil_hour_keeps_a_full_hour() -> None:
    from custom_components.wattnet.coordinator import ceil_hour

    assert ceil_hour(OLDEST) == OLDEST
    assert ceil_hour(datetime(2026, 9, 29, 12, 35, tzinfo=UTC)) == OLDEST
