"""Constants for the Wattnet integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

DOMAIN = "wattnet"

CONF_EMAIL = "email"
CONF_PASSWORD = "password"  # nosec B105
CONF_ZONE = "zone"
CONF_ZONE_NAME = "zone_name"
CONF_DAYS = "days"
CONF_CONFIG_ENTRY = "config_entry"

SERVICE_REIMPORT = "reimport_statistics"
ATTRIBUTION = "Data provided by Wattnet"

CONF_SCOPE = "scope"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_BACKFILL_DAYS = "backfill_days"
CONF_SHOW_DASHBOARD = "show_dashboard"

SCOPES = ["life-cycle", "operational"]
DEFAULT_SCOPE = "life-cycle"
DEFAULT_UPDATE_INTERVAL = 15  # minutes, the resolution of the data
DEFAULT_BACKFILL_DAYS = 7

# Wattnet publishes one data point every 15 minutes.
RESOLUTION_MINUTES = 15
POINTS_PER_HOUR = 60 // RESOLUTION_MINUTES

# Max span requested from the API in one call while backfilling.
CHUNK_DAYS = 7
# How long the API may be unreachable before the sensors become unavailable
# and a repair issue is raised. Shorter outages keep serving the last data.
API_GRACE = timedelta(hours=1)
ISSUE_API_UNREACHABLE = "api_unreachable"

# Updates run at the same minutes of each hour, right on each slot of Wattnet's
# 15-minute resolution (second 0), so that the state written to the history
# lands as close as possible to the time of the data point. The point of the
# slot is usually not published yet at that exact moment, which the retries
# below take care of.
POLL_OFFSET_MINUTES = 0
# When the point of the slot that just started is not published yet, ask again
# every POLL_RETRY_DELAY seconds until POLL_RETRY_WINDOW after the slot.
POLL_RETRY_DELAY = 60
POLL_RETRY_WINDOW = timedelta(minutes=5)
UPDATE_INTERVALS = (15, 30, 60)  # minutes, must divide an hour

# When the home's grid consumption is available, the carbon and water totals are
# recomputed over this many days every time. Some meters publish their hourly
# consumption a day or two late, and a total computed before it arrived would
# otherwise stay wrong.
GRID_LOOKBACK = timedelta(days=3)

# Hours re-requested before the watermark as a safety margin.
WATERMARK_MARGIN_HOURS = 1

STORAGE_VERSION = 1


@dataclass(frozen=True)
class GridOutput:
    """What a per-kWh metric becomes when multiplied by the user's grid energy.

    The footprint of a kWh times the kWh the home drew from the grid in an hour
    is what that hour cost in carbon or water. Each hour adds to a running
    total, which is what the Energy dashboard reads as a cumulative statistic.
    """

    key: str  # statistic id suffix, e.g. "water_consumption"
    name: str  # human name used in the statistic metadata
    unit: str  # unit of the result, e.g. "L" (the metric unit without "/kWh")
    unit_class: str | None  # Home Assistant unit class, for unit conversion


@dataclass(frozen=True)
class Metric:
    """Describes one Wattnet metric exposed as a sensor and as statistics."""

    key: str
    method: str  # WattnetRawClient method name
    match: dict[str, Any]  # record-level filter applied to the response
    unit: str | None
    uses_scope: bool  # whether the user-selected scope applies
    params: dict[str, Any]  # fixed query params
    grid: GridOutput | None = None  # derived from the home's grid consumption


METRICS: tuple[Metric, ...] = (
    Metric(
        key="carbon_footprint",
        method="get_footprints",
        match={"footprint_type": "carbon"},
        unit="gCO2/kWh",
        uses_scope=True,
        params={"footprint_type": "carbon"},
        grid=GridOutput("carbon_emissions", "grid carbon emissions", "g", "mass"),
    ),
    Metric(
        key="water_footprint",
        method="get_footprints",
        match={"footprint_type": "water"},
        unit="L/kWh",
        uses_scope=True,
        params={"footprint_type": "water"},
        grid=GridOutput("water_consumption", "grid water consumption", "L", "volume"),
    ),
    Metric(
        key="water_impact",
        method="get_impacts",
        match={"impact_type": "water"},
        unit="stress-L/kWh",
        uses_scope=False,
        params={"impact_type": "water", "scope": "operational"},
        grid=GridOutput("water_impact", "grid water impact", "stress-L", None),
    ),
    Metric(
        key="environmental_score",
        method="get_environmental_score",
        match={},
        # A score from 0 to 100 that ranks zones against each other. It has no
        # unit, and no state class either, so its history is a band of values.
        unit=None,
        uses_scope=False,
        params={"scope": "operational"},
    ),
)
