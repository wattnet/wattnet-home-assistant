"""Config flow for the Wattnet integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from wattnet.client.exceptions import (
    AuthenticationError,
    ConfigurationError,
    WattnetAPIError,
    WattnetError,
)

from .api import build_client
from .const import (
    CONF_BACKFILL_DAYS,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_SCOPE,
    CONF_SHOW_DASHBOARD,
    CONF_UPDATE_INTERVAL,
    CONF_ZONE,
    CONF_ZONE_NAME,
    DEFAULT_BACKFILL_DAYS,
    DEFAULT_SCOPE,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    SCOPES,
    UPDATE_INTERVALS,
)
from .validation import vol

_LOGGER = logging.getLogger(__name__)

AUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.EMAIL, autocomplete="username"
            )
        ),
        vol.Required(CONF_PASSWORD): selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.PASSWORD,
                autocomplete="current-password",
            )
        ),
    }
)


def entry_title(code: str, name: str) -> str:
    """Title of an entry, e.g. ``Wattnet - Spain (ES)``."""
    return f"Wattnet - {name} ({code})"


async def _validate(
    hass: HomeAssistant, auth: Mapping[str, Any], lat: float, lon: float
) -> tuple[dict[str, str], str | None]:
    """Check credentials, list zones and guess the zone of the home location.

    Returns ``{zone code: full name}`` sorted by name.
    """
    client = build_client(hass, auth)
    zones = {
        z["zone"]: z.get("full_name") or z["zone"] for z in await client.get_zones()
    }
    zones = dict(sorted(zones.items(), key=lambda item: item[1]))
    guess = None
    try:
        records = await client.get_footprints(lat=lat, lon=lon, footprint_type="carbon")
        guess = records[0]["zone"] if records else None
    except WattnetAPIError:
        guess = None
    return zones, guess


def _zone_selector(zones: Mapping[str, str]) -> selector.SelectSelector:
    """Dropdown of zones labelled ``Spain (ES)``."""
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[
                selector.SelectOptionDict(value=code, label=f"{name} ({code})")
                for code, name in zones.items()
            ],
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


class WattnetConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Wattnet."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow."""
        self._auth: dict[str, Any] = {}
        self._zones: dict[str, str] = {}
        self._guess: str | None = None

    async def _async_check(self, auth: dict[str, Any]) -> str | None:
        """Validate auth against the API; return an error key or None."""
        try:
            self._zones, self._guess = await _validate(
                self.hass, auth, self.hass.config.latitude, self.hass.config.longitude
            )
        except (AuthenticationError, ConfigurationError):
            return "invalid_auth"
        except WattnetAPIError as err:
            return "invalid_auth" if err.status_code in (401, 403) else "cannot_connect"
        except WattnetError:
            return "cannot_connect"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error validating Wattnet credentials")
            return "unknown"
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the e-mail and password of the Wattnet account."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors_key = await self._async_check(user_input)
            if errors_key is None:
                self._auth = dict(user_input)
                return await self.async_step_zone()
            errors["base"] = errors_key
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(AUTH_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_zone(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the zone to monitor."""
        if user_input is not None:
            zone = user_input[CONF_ZONE]
            await self.async_set_unique_id(zone)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=entry_title(zone, self._zones.get(zone, zone)),
                data={
                    **self._auth,
                    CONF_ZONE: zone,
                    CONF_ZONE_NAME: self._zones.get(zone, zone),
                },
                options={CONF_SCOPE: user_input[CONF_SCOPE]},
            )
        # Preselect the zone containing the home location, when there is one.
        zone_key = (
            vol.Required(CONF_ZONE, default=self._guess)
            if self._guess in self._zones
            else vol.Required(CONF_ZONE)
        )
        schema = vol.Schema(
            {
                zone_key: _zone_selector(self._zones),
                vol.Required(
                    CONF_SCOPE, default=DEFAULT_SCOPE
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=SCOPES,
                        translation_key=CONF_SCOPE,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="zone", data_schema=schema)

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for new credentials."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            errors_key = await self._async_check(user_input)
            if errors_key is None:
                return self.async_update_reload_and_abort(
                    entry, data={**entry.data, **user_input}
                )
            errors["base"] = errors_key
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self.add_suggested_values_to_schema(
                AUTH_SCHEMA, user_input or {CONF_EMAIL: entry.data.get(CONF_EMAIL)}
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the monitored zone without removing the entry."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if not self._zones:
            error = await self._async_check(dict(entry.data))
            if error:
                return self.async_abort(reason=error)
        if user_input is not None:
            zone = user_input[CONF_ZONE]
            if any(
                other.unique_id == zone and other.entry_id != entry.entry_id
                for other in self._async_current_entries()
            ):
                errors["base"] = "already_configured"
            else:
                name = self._zones.get(zone, zone)
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=zone,
                    title=entry_title(zone, name),
                    data_updates={CONF_ZONE: zone, CONF_ZONE_NAME: name},
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_ZONE, default=entry.data[CONF_ZONE]): _zone_selector(
                    self._zones
                )
            }
        )
        return self.async_show_form(
            step_id="reconfigure", data_schema=schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return WattnetOptionsFlow()


class WattnetOptionsFlow(OptionsFlow):
    """Options: scope, polling interval and backfill depth."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    **user_input,
                    CONF_UPDATE_INTERVAL: int(user_input[CONF_UPDATE_INTERVAL]),
                }
            )
        opts = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCOPE, default=opts.get(CONF_SCOPE, DEFAULT_SCOPE)
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=SCOPES,
                        translation_key=CONF_SCOPE,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_UPDATE_INTERVAL,
                    default=str(
                        opts.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
                    ),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[str(minutes) for minutes in UPDATE_INTERVALS],
                        translation_key=CONF_UPDATE_INTERVAL,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_SHOW_DASHBOARD,
                    default=opts.get(CONF_SHOW_DASHBOARD, True),
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_BACKFILL_DAYS,
                    default=opts.get(CONF_BACKFILL_DAYS, DEFAULT_BACKFILL_DAYS),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=90, step=1, unit_of_measurement="d"
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
