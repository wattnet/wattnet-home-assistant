"""Tests for the client factory."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from wattnet.client import AsyncWattnetRawClient
from wattnet.client.async_auth import AsyncCredentialsTokenProvider

from custom_components.wattnet.api import build_client
from custom_components.wattnet.const import CONF_EMAIL, CONF_PASSWORD


async def test_build_client_uses_credentials_and_shares_ha_session(hass: HomeAssistant):
    client = build_client(hass, {CONF_EMAIL: "a@b.c", CONF_PASSWORD: "pw"})
    assert isinstance(client, AsyncWattnetRawClient)
    # E-mail and password mean the client renews its own short-lived token.
    assert isinstance(client._auth, AsyncCredentialsTokenProvider)
    assert client._session is not None and not client._owns_session
