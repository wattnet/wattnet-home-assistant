"""Tests for the config, reauth and options flows."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from wattnet.client.exceptions import AuthenticationError, WattnetAPIError

from custom_components.wattnet.const import (
    CONF_BACKFILL_DAYS,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_SCOPE,
    CONF_SHOW_DASHBOARD,
    CONF_UPDATE_INTERVAL,
    CONF_ZONE,
    CONF_ZONE_NAME,
    DOMAIN,
)
from custom_components.wattnet.validation import vol

from .conftest import FakeClient


async def _start(hass: HomeAssistant, user_input: dict):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(result["flow_id"], user_input)


CREDS = {CONF_EMAIL: "a@b.c", CONF_PASSWORD: "pw"}


async def test_full_flow(hass: HomeAssistant, fake_client: FakeClient):
    result = await _start(hass, CREDS)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "zone"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ZONE: "FR", CONF_SCOPE: "operational"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Wattnet - France (FR)"
    assert result["data"] == {**CREDS, CONF_ZONE: "FR", CONF_ZONE_NAME: "France"}
    assert result["options"] == {CONF_SCOPE: "operational"}


async def test_user_step_asks_only_for_email_and_password(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert list(result["data_schema"].schema) == [CONF_EMAIL, CONF_PASSWORD]


async def test_invalid_auth(hass: HomeAssistant, fake_client: FakeClient):
    async def boom():
        raise AuthenticationError("bad")

    fake_client.get_zones = boom  # type: ignore[method-assign]
    result = await _start(hass, CREDS)
    assert result["errors"] == {"base": "invalid_auth"}


async def test_cannot_connect(hass: HomeAssistant, fake_client: FakeClient):
    async def boom():
        raise WattnetAPIError("down", status_code=503)

    fake_client.get_zones = boom  # type: ignore[method-assign]
    result = await _start(hass, CREDS)
    assert result["errors"] == {"base": "cannot_connect"}


async def test_zone_already_configured(
    hass: HomeAssistant, fake_client: FakeClient, config_entry: MockConfigEntry
):
    config_entry.add_to_hass(hass)
    result = await _start(hass, CREDS)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ZONE: "ES", CONF_SCOPE: "life-cycle"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_prefills_email_and_updates_password(
    hass: HomeAssistant, fake_client: FakeClient, config_entry: MockConfigEntry
):
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    schema = result["data_schema"].schema
    email_key = next(k for k in schema if k == CONF_EMAIL)
    assert email_key.description == {"suggested_value": "a@b.c"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: "a@b.c", CONF_PASSWORD: "new-pw"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data == {
        CONF_EMAIL: "a@b.c",
        CONF_PASSWORD: "new-pw",
        CONF_ZONE: "ES",
        CONF_ZONE_NAME: "Spain",
    }


async def test_reauth_rejects_wrong_password(
    hass: HomeAssistant, fake_client: FakeClient, config_entry: MockConfigEntry
):
    async def boom():
        raise AuthenticationError("bad")

    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    fake_client.get_zones = boom  # type: ignore[method-assign]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: "a@b.c", CONF_PASSWORD: "wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}


async def test_options_flow(
    hass: HomeAssistant, fake_client: FakeClient, config_entry: MockConfigEntry
):
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCOPE: "operational",
            CONF_UPDATE_INTERVAL: "30",
            CONF_SHOW_DASHBOARD: False,
            CONF_BACKFILL_DAYS: 3,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {
        CONF_SCOPE: "operational",
        CONF_UPDATE_INTERVAL: 30,  # stored as a number
        CONF_SHOW_DASHBOARD: False,
        CONF_BACKFILL_DAYS: 3,
    }


async def test_the_dashboard_option_is_on_by_default(
    hass: HomeAssistant, fake_client: FakeClient, config_entry: MockConfigEntry
):
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    key = next(k for k in result["data_schema"].schema if k == CONF_SHOW_DASHBOARD)
    assert key.default() is True


async def test_zone_dropdown_is_labelled_with_full_name(
    hass: HomeAssistant, fake_client: FakeClient
):
    result = await _start(hass, CREDS)
    selector = result["data_schema"].schema[CONF_ZONE]
    options = selector.config["options"]
    assert {"value": "ES", "label": "Spain (ES)"} in options
    assert {"value": "FR", "label": "France (FR)"} in options


async def test_reconfigure_changes_zone_and_title(
    hass: HomeAssistant, fake_client: FakeClient, config_entry: MockConfigEntry
):
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ZONE: "FR"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.unique_id == "FR"
    assert config_entry.title == "Wattnet - France (FR)"
    assert config_entry.data[CONF_ZONE] == "FR"
    assert config_entry.data[CONF_ZONE_NAME] == "France"
    assert config_entry.data[CONF_EMAIL] == "a@b.c"  # credentials are kept


async def test_reconfigure_rejects_a_zone_that_is_already_configured(
    hass: HomeAssistant, fake_client: FakeClient, config_entry: MockConfigEntry
):
    config_entry.add_to_hass(hass)
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="FR",
        data={**config_entry.data, CONF_ZONE: "FR"},
    ).add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ZONE: "FR"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "already_configured"}


def _zone_default(result):
    key = next(k for k in result["data_schema"].schema if k == CONF_ZONE)
    return key.default() if callable(key.default) else key.default


async def test_zone_is_preselected_from_the_home_location(
    hass: HomeAssistant, fake_client: FakeClient
):
    hass.config.latitude, hass.config.longitude = 48.8566, 2.3522
    fake_client.location_zone = "FR"
    result = await _start(hass, CREDS)

    assert _zone_default(result) == "FR"
    lookup = [kw for m, kw in fake_client.calls if "lat" in kw]
    assert lookup == [{"lat": 48.8566, "lon": 2.3522, "footprint_type": "carbon"}]


async def test_zone_has_no_default_when_home_is_outside_every_zone(
    hass: HomeAssistant, fake_client: FakeClient
):
    # Wattnet answers 404 for coordinates that are in no zone (e.g. HA's default
    # location in California): the user must choose, nothing is preselected.
    fake_client.location_zone = None
    result = await _start(hass, CREDS)

    assert result["step_id"] == "zone"
    assert _zone_default(result) is vol.UNDEFINED
    # ...and the flow still completes once a zone is picked.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ZONE: "ES", CONF_SCOPE: "life-cycle"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_guessed_zone_missing_from_the_list_is_not_preselected(
    hass: HomeAssistant, fake_client: FakeClient
):
    fake_client.location_zone = "XX"  # not an offered zone
    result = await _start(hass, CREDS)
    assert _zone_default(result) is vol.UNDEFINED
