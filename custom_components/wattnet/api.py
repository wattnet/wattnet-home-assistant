"""Thin wrapper around the official async Wattnet client."""

from __future__ import annotations

from typing import Any, Mapping

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from wattnet.client import AsyncWattnetRawClient

from .const import CONF_CLIENT_ID, CONF_CLIENT_SECRET


def build_client(hass: HomeAssistant, data: Mapping[str, Any]) -> AsyncWattnetRawClient:
    """Create a client that shares Home Assistant's aiohttp session.

    Authentication is always the client id and secret of a Wattnet credential:
    the client exchanges them for a short-lived token and renews it by itself,
    so the entry never expires.
    """
    return AsyncWattnetRawClient(
        client_id=data[CONF_CLIENT_ID],
        client_secret=data[CONF_CLIENT_SECRET],
        session=async_get_clientsession(hass),
    )
