"""Tests for the client factory."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from wattnet.client import AsyncWattnetRawClient
from wattnet.client.async_auth import AsyncClientCredentialsTokenProvider

from custom_components.wattnet.api import build_client
from custom_components.wattnet.const import CONF_CLIENT_ID, CONF_CLIENT_SECRET


async def test_build_client_uses_credentials_and_shares_ha_session(hass: HomeAssistant):
    client = build_client(hass, {CONF_CLIENT_ID: "id", CONF_CLIENT_SECRET: "secret"})
    assert isinstance(client, AsyncWattnetRawClient)
    # A client id and secret mean the client renews its own short-lived token.
    assert isinstance(client._auth, AsyncClientCredentialsTokenProvider)
    assert client._session is not None and not client._owns_session
