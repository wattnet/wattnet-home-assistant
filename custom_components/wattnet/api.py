"""Thin wrapper around the official async Wattnet client."""

from __future__ import annotations

from typing import Any, Mapping

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from wattnet.client import AsyncWattnetRawClient

from .const import CONF_EMAIL, CONF_PASSWORD


def build_client(hass: HomeAssistant, data: Mapping[str, Any]) -> AsyncWattnetRawClient:
    """Create a client that shares Home Assistant's aiohttp session.

    Authentication is always e-mail and password: the client exchanges them for
    a short-lived token and renews it by itself, so the entry never expires.
    """
    return AsyncWattnetRawClient(
        email=data[CONF_EMAIL],
        password=data[CONF_PASSWORD],
        session=async_get_clientsession(hass),
    )
