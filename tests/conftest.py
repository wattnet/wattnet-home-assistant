"""Shared fixtures for the Wattnet integration tests."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from wattnet.client.exceptions import WattnetAPIError

from custom_components.wattnet.const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_SCOPE,
    CONF_ZONE,
    CONF_ZONE_NAME,
    DOMAIN,
)

ZONE = "ES"


def make_record(series: list[dict[str, Any]], **fields: Any) -> dict[str, Any]:
    """Build one API record (zone + series) like `/footprints` returns."""
    return {"zone": ZONE, "series": series, **fields}


def make_series(
    valid: bool, values: list[list[Any]], status: str = "complete"
) -> dict[str, Any]:
    """Build one `(valid, zone_status)` series."""
    return {"valid": valid, "zone_status": status, "values": values}


class FakeClient:
    """Stands in for `AsyncWattnetRawClient`, serving configurable payloads.

    ``latest`` answers calls without ``start``/``end`` (sensor values) and
    ``window`` answers windowed calls (statistics). Both map a method name and
    the record-level filter to a list of records.
    """

    def __init__(self) -> None:
        self.latest: dict[str, list[dict[str, Any]]] = {}
        self.window: dict[str, list[dict[str, Any]]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.error: Exception | None = None
        # Zone Wattnet resolves for the home coordinates; None means no zone (404).
        self.location_zone: str | None = None

    def _key(self, method: str, kwargs: dict[str, Any]) -> str:
        kind = kwargs.get("footprint_type") or kwargs.get("impact_type")
        return f"{method}:{kind}" if kind else method

    async def _serve(self, method: str, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append((method, kwargs))
        if self.error is not None:
            raise self.error
        windowed = "start" in kwargs
        source = self.window if windowed else self.latest
        return source.get(self._key(method, kwargs), [])

    async def get_zones(self) -> list[dict[str, Any]]:
        return [
            {"zone": "ES", "full_name": "Spain"},
            {"zone": "FR", "full_name": "France"},
        ]

    async def get_footprints(self, **kwargs: Any) -> list[dict[str, Any]]:
        if "lat" in kwargs:  # zone lookup by coordinates
            self.calls.append(("get_footprints", kwargs))
            if self.location_zone is None:
                raise WattnetAPIError("no zone", status_code=404)
            return [{"zone": self.location_zone, "series": []}]
        return await self._serve("get_footprints", **kwargs)

    async def get_impacts(self, **kwargs: Any) -> list[dict[str, Any]]:
        return await self._serve("get_impacts", **kwargs)

    async def get_environmental_score(self, **kwargs: Any) -> list[dict[str, Any]]:
        return await self._serve("get_environmental_score", **kwargs)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow Home Assistant to load `custom_components/wattnet`."""


@pytest.fixture(autouse=True)
def _recorder(recorder_mock) -> None:
    """The integration depends on the recorder, so always provide one."""


@pytest.fixture
def fake_client() -> FakeClient:
    """A fake API client, patched in wherever the integration builds one."""
    client = FakeClient()
    with (
        patch("custom_components.wattnet.build_client", return_value=client),
        patch(
            "custom_components.wattnet.config_flow.build_client", return_value=client
        ),
    ):
        yield client


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """A configured Wattnet entry for zone ES."""
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=ZONE,
        title="Wattnet - Spain (ES)",
        data={
            CONF_CLIENT_ID: "id",
            CONF_CLIENT_SECRET: "secret",
            CONF_ZONE: ZONE,
            CONF_ZONE_NAME: "Spain",
        },
        options={CONF_SCOPE: "life-cycle"},
    )


def hour(h: int, day: int = 6) -> str:
    """ISO timestamp for an hour of 2026-10-<day> (UTC)."""
    return datetime(2026, 10, day, h, tzinfo=timezone.utc).isoformat()


def quarters(h: int, value: float, day: int = 6, count: int = 4) -> list[list[Any]]:
    """The 15-minute points of one hour, as the API returns them."""
    return [
        [datetime(2026, 10, day, h, m, tzinfo=timezone.utc).isoformat(), value]
        for m in (0, 15, 30, 45)[:count]
    ]
