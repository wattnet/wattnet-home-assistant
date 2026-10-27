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
from pytest_homeassistant_custom_component.common import MockConfigEntry
from wattnet.client.exceptions import (
    AuthenticationError,
    WattnetAPIError,
    WattnetError,
)

from custom_components.wattnet.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_ZONE,
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
