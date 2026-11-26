"""Data update coordinator for Wattnet.

Each update does two things:

1. Fetch the latest point of every metric (a small request) for the sensors.
2. In the background, re-fetch the window starting at the stored watermark and
   import it as external statistics. The watermark is the first hour that is
   not yet consolidated, so provisional hours are overwritten on later updates
   until the API reports them consolidated.

The statistics sync runs in the background so that the potentially large first
history download never delays the setup of the integration. Updates are
scheduled at fixed minutes of the hour, right after Wattnet publishes data.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    statistics_during_period,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_call_later, async_track_utc_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from wattnet.client import AsyncWattnetRawClient
from wattnet.client.exceptions import AuthenticationError, WattnetAPIError, WattnetError

from .const import (
    ACCESS_LIMIT_CODES,
    API_GRACE,
    CHUNK_DAYS,
    CONF_BACKFILL_DAYS,
    CONF_SCOPE,
    CONF_UPDATE_INTERVAL,
    CONF_ZONE,
    CONF_ZONE_NAME,
    DEFAULT_BACKFILL_DAYS,
    DEFAULT_SCOPE,
    DEFAULT_UPDATE_INTERVAL,
    DEVELOPER_PORTAL_URL,
    DOMAIN,
    FIRST_SYNC_SLACK,
    FREE_HISTORY_DAYS,
    GRID_LOOKBACK,
    HISTORY_LIMIT_CODE,
    ISSUE_API_UNREACHABLE,
    ISSUE_HISTORY_LIMITED,
    METRICS,
    POLL_OFFSET_MINUTES,
    POLL_RETRY_DELAY,
    POLL_RETRY_WINDOW,
    RESOLUTION_MINUTES,
    STORAGE_VERSION,
    WATERMARK_MARGIN_HOURS,
    Metric,
)
from .parsing import (
    HourlyPoint,
    Point,
    derive_cumulative,
    latest_point,
    next_watermark,
    parse_points,
    to_hourly,
)

_LOGGER = logging.getLogger(__name__)


class AccessLimitExceeded(UpdateFailed):
    """The query goes beyond the access level of the credential (403 with a code).

    It is an ``UpdateFailed``, never ``ConfigEntryAuthFailed``: signing in again
    with the same credential would not change it.
    """

    def __init__(self, message: str, code: str) -> None:
        """Keep the code the API gave, to tell which limit it was."""
        super().__init__(message)
        self.code = code


def ceil_hour(moment: datetime) -> datetime:
    """Round up to the next full hour (unchanged when already on one)."""
    floor = moment.replace(minute=0, second=0, microsecond=0)
    return floor if floor == moment else floor + timedelta(hours=1)


def oldest_allowed_start(now: datetime, days: int) -> datetime:
    """First hour a query of the last ``days`` days may start at.

    It is inside the history the API allows even when its clock is a bit ahead.
    """
    return ceil_hour(now - timedelta(days=days) + FIRST_SYNC_SLACK)


def statistic_id(zone: str, metric: Metric, scope: str) -> str:
    """Build a valid external statistic id (lowercase, digits, underscore)."""
    parts = [zone, metric.key]
    if metric.uses_scope:
        parts.append(scope)
    return f"{DOMAIN}:" + "_".join(parts).lower().replace("-", "_")


def grid_statistic_id(zone: str, metric: Metric, scope: str) -> str | None:
    """Id of the statistic derived from the home's grid consumption, if any."""
    if metric.grid is None:
        return None
    parts = [zone, "grid", metric.grid.key]
    if metric.uses_scope:
        parts.append(scope)
    return f"{DOMAIN}:" + "_".join(parts).lower().replace("-", "_")


def poll_minutes(interval: int) -> list[int]:
    """Minutes of the hour at which to update, ``POLL_OFFSET_MINUTES`` after a slot.

    Every 15 minutes gives 0, 15, 30 and 45. Every hour gives 0.
    """
    return [(POLL_OFFSET_MINUTES + i * interval) % 60 for i in range(60 // interval)]


class WattnetCoordinator(DataUpdateCoordinator[dict[str, Point | None]]):
    """Fetch Wattnet data and keep the imported statistics consolidated."""

    config_entry: ConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: AsyncWattnetRawClient
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{entry.data[CONF_ZONE]}",
            update_interval=None,  # scheduled at fixed minutes, see async_start
        )
        options = entry.options
        self.client = client
        self.zone: str = entry.data[CONF_ZONE]
        self.zone_name: str = entry.data.get(CONF_ZONE_NAME, self.zone)
        self.scope: str = options.get(CONF_SCOPE, DEFAULT_SCOPE)
        self.poll_interval: int = int(
            options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
        )
        self._backfill_days: int = options.get(
            CONF_BACKFILL_DAYS, DEFAULT_BACKFILL_DAYS
        )
        self._store: Store[dict[str, str]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self._watermarks: dict[str, str] = {}
        self._sync_lock = asyncio.Lock()
        self._sync_task: asyncio.Task[None] | None = None
        self._last_success: datetime | None = None
        self._retry_unsub: CALLBACK_TYPE | None = None
        self._issue_id = f"{ISSUE_API_UNREACHABLE}_{entry.entry_id}"
        self._history_issue_id = f"{ISSUE_HISTORY_LIMITED}_{entry.entry_id}"
        # Oldest hour the last import could reach when the access level cut it.
        self._history_limited_from: datetime | None = None

    # -- lifecycle -------------------------------------------------------------

    async def async_load_watermarks(self) -> None:
        """Load persisted watermarks (called once before the first refresh)."""
        self._watermarks = await self._store.async_load() or {}

    def async_start(self) -> CALLBACK_TYPE:
        """Schedule updates at fixed minutes; returns the function to cancel them."""

        async def _scheduled(now: datetime) -> None:
            await self.async_refresh()

        unsub = async_track_utc_time_change(
            self.hass,
            _scheduled,
            minute=poll_minutes(self.poll_interval),
            second=0,
        )

        stopped = False

        def _stop(event: Event | None = None) -> None:
            nonlocal stopped
            if stopped:
                return
            stopped = True
            unsub()
            self._cancel_retry()
            if event is None:  # a fired listen_once listener is already removed
                unsub_stop()
            if self._sync_task and not self._sync_task.done():
                self._sync_task.cancel()

        # Also stop when Home Assistant shuts down (the entry is not unloaded then).
        unsub_stop = self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _stop)

        def _unload() -> None:
            _stop()
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
            ir.async_delete_issue(self.hass, DOMAIN, self._history_issue_id)

        return _unload

    @property
    def consolidated_until(self) -> datetime | None:
        """First hour not yet consolidated, across all metrics (the oldest one)."""
        marks = [
            datetime.fromisoformat(self._watermarks[self._mark_key(m)])
            for m in METRICS
            if self._mark_key(m) in self._watermarks
        ]
        return min(marks) if marks else None

    def _mark_key(self, metric: Metric) -> str:
        """Watermark key: the statistic id, so a new zone or scope starts fresh."""
        return statistic_id(self.zone, metric, self.scope)

    # -- fetching ------------------------------------------------------------

    def _query_params(self, metric: Metric) -> dict[str, Any]:
        params = {"zone": self.zone, **metric.params}
        if metric.uses_scope:
            params["scope"] = self.scope
        return params

    async def _fetch(
        self,
        metric: Metric,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Call the async client. A 404 means there is no data for the window."""
        method = getattr(self.client, metric.method)
        params = self._query_params(metric)
        if start is not None and end is not None:
            params.update(start=start, end=end)
        try:
            records: list[dict[str, Any]] = await method(**params)
            return records
        except AuthenticationError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except WattnetAPIError as err:
            if err.status_code == 403 and err.code in ACCESS_LIMIT_CODES:
                raise AccessLimitExceeded(
                    f"Beyond the access level of the credential: {err}", err.code
                ) from err
            if err.status_code in (401, 403):
                raise ConfigEntryAuthFailed(str(err)) from err
            if err.status_code == 404:
                _LOGGER.debug("No %s data for zone %s", metric.key, self.zone)
                return []
            raise UpdateFailed(f"Wattnet API error: {err}") from err
        except WattnetError as err:
            raise UpdateFailed(f"Wattnet error: {err}") from err

    async def _fetch_window(
        self, metric: Metric, start: datetime, end: datetime, now: datetime
    ) -> list[dict[str, Any]]:
        """Fetch a window of history, within the access level of the credential.

        A window that starts before the history the access level reaches (Home
        Assistant was off for long, or a reimport of many days with a Free
        credential) is asked again from the oldest hour allowed. A window refused
        for any other limit of the access level is skipped with a warning.
        """
        try:
            return await self._fetch(metric, start, end)
        except AccessLimitExceeded as err:
            oldest = oldest_allowed_start(now, FREE_HISTORY_DAYS)
            if err.code == HISTORY_LIMIT_CODE and end <= oldest:
                # Wholly older than the access level reaches: the window that
                # crosses the limit tells it once, below.
                _LOGGER.debug("No %s before %s for this access level", metric.key, end)
                self._history_limited_from = oldest
                return []
            if err.code == HISTORY_LIMIT_CODE and start < oldest:
                _LOGGER.warning(
                    "The access level of the credential does not reach %s for %s: "
                    "importing from %s",
                    start,
                    metric.key,
                    oldest,
                )
                self._history_limited_from = oldest
                return await self._fetch_window(metric, oldest, end, now)
            _LOGGER.warning(
                "Skipping %s from %s to %s: %s", metric.key, start, end, err
            )
            return []

    async def _async_update_data(self) -> dict[str, Point | None]:
        now = datetime.now(timezone.utc)
        self._cancel_retry()
        try:
            # Metrics are independent, so query them concurrently.
            points = await asyncio.gather(
                *(self._async_latest(metric) for metric in METRICS)
            )
        except UpdateFailed as err:
            return self._tolerate_outage(now, err)
        self._last_success = now
        ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
        self._start_statistics_sync()
        self._retry_if_slot_not_published(now, points)
        return {m.key: p for m, p in zip(METRICS, points, strict=True)}

    def _cancel_retry(self) -> None:
        if self._retry_unsub is not None:
            self._retry_unsub()
            self._retry_unsub = None

    def _retry_if_slot_not_published(
        self, now: datetime, points: list[Point | None]
    ) -> None:
        """Ask again soon when the point of the current slot is not out yet.

        The scheduled update runs right on each 15-minute slot, when the point
        of that slot is usually not published yet, so the newest one is still
        the one of the previous slot. Retrying every minute for a few
        minutes puts the new value in the history right when it is available,
        instead of a whole interval later. It only applies when exactly one slot
        is missing: older or absent data would be a longer problem, and
        retrying would only add requests.
        """
        slot = now.replace(
            minute=now.minute - now.minute % RESOLUTION_MINUTES,
            second=0,
            microsecond=0,
        )
        stamps = [p.timestamp for p in points if p is not None]
        if not stamps or now - slot >= POLL_RETRY_WINDOW:
            return
        if max(stamps) != slot - timedelta(minutes=RESOLUTION_MINUTES):
            return

        async def _retry(_: datetime) -> None:
            self._retry_unsub = None
            await self.async_refresh()

        self._retry_unsub = async_call_later(self.hass, POLL_RETRY_DELAY, _retry)

    async def _async_latest(self, metric: Metric) -> Point | None:
        records = await self._fetch(metric)
        return latest_point(parse_points(records, metric.match))

    def _tolerate_outage(
        self, now: datetime, err: UpdateFailed
    ) -> dict[str, Point | None]:
        """Keep serving the last data for ``API_GRACE``, then fail loudly."""
        since = self._last_success
        if self.data is not None and since is not None and now - since < API_GRACE:
            _LOGGER.warning(
                "Wattnet is unreachable, keeping the last data (%s): %s",
                now - since,
                err,
            )
            return self.data
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_API_UNREACHABLE,
            translation_placeholders={"zone": f"{self.zone_name} ({self.zone})"},
        )
        raise err

    # -- statistics ----------------------------------------------------------

    def _start_statistics_sync(self) -> None:
        """Sync statistics in the background, one run at a time."""
        if self._sync_task is not None and not self._sync_task.done():
            return
        self._sync_task = self.config_entry.async_create_background_task(
            self.hass, self._async_background_sync(), f"{DOMAIN} statistics sync"
        )

    async def _async_background_sync(self) -> None:
        try:
            await self._async_sync_all()
        except ConfigEntryAuthFailed:
            self.config_entry.async_start_reauth(self.hass)
        except UpdateFailed as err:
            _LOGGER.warning("Could not sync Wattnet statistics, will retry: %s", err)
        else:
            self.async_update_listeners()  # the consolidation marker may have moved

    async def _async_sync_all(self, days: int | None = None) -> None:
        async with self._sync_lock:
            now = datetime.now(timezone.utc)
            grid_ids = await self.async_grid_statistic_ids()
            self._history_limited_from = None
            await asyncio.gather(
                *(self._async_sync_statistics(m, now, days, grid_ids) for m in METRICS)
            )
            await self._store.async_save(self._watermarks)
            if self._history_limited_from is not None:
                self._raise_history_issue(self._history_limited_from)

    def _raise_history_issue(self, oldest: datetime) -> None:
        """Show in Repairs that the access level cut the history imported."""
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._history_issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_HISTORY_LIMITED,
            translation_placeholders={
                "zone": f"{self.zone_name} ({self.zone})",
                "oldest": oldest.strftime("%Y-%m-%d %H:%M UTC"),
            },
            learn_more_url=DEVELOPER_PORTAL_URL,
        )

    async def async_reimport(self, days: int | None = None) -> None:
        """Forget the watermarks and import the last ``days`` days again."""
        if self._sync_task is not None and not self._sync_task.done():
            self._sync_task.cancel()
            await asyncio.gather(self._sync_task, return_exceptions=True)
        self._watermarks = {}
        # A new import decides again whether the history is cut.
        ir.async_delete_issue(self.hass, DOMAIN, self._history_issue_id)
        await self._async_sync_all(days)
        self.async_update_listeners()

    def _window_start(
        self, metric: Metric, now: datetime, days: int | None = None
    ) -> datetime:
        raw = self._watermarks.get(self._mark_key(metric))
        if not raw:
            # Nothing imported yet: the window of the last days, starting inside
            # the history the access level allows (no margin before it).
            return oldest_allowed_start(now, days or self._backfill_days)
        mark = datetime.fromisoformat(raw) - timedelta(hours=WATERMARK_MARGIN_HOURS)
        return mark.replace(minute=0, second=0, microsecond=0)

    async def _async_sync_statistics(
        self,
        metric: Metric,
        now: datetime,
        days: int | None = None,
        grid_ids: list[str] | None = None,
    ) -> None:
        """Re-import everything from the watermark and advance it."""
        start = self._window_start(metric, now, days)
        if metric.grid is not None and grid_ids:
            # Also recompute the last days of the grid totals: the consumption
            # of the home may have been published late.
            lookback = (now - GRID_LOOKBACK).replace(minute=0, second=0, microsecond=0)
            start = min(start, lookback)
        records: list[dict[str, Any]] = []
        cursor = start
        while cursor < now:
            chunk_end = min(cursor + timedelta(days=CHUNK_DAYS), now)
            records.extend(await self._fetch_window(metric, cursor, chunk_end, now))
            cursor = chunk_end

        # Parse once so a point repeated across chunk boundaries is not counted twice.
        hourly = to_hourly(parse_points(records, metric.match))
        if not hourly:
            return
        self._import(metric, hourly)
        if metric.grid is not None and grid_ids:
            await self._async_sync_grid(metric, hourly, now, grid_ids)
        mark = next_watermark(hourly, start)
        self._watermarks[self._mark_key(metric)] = mark.isoformat()

    # -- grid consumption x footprint --------------------------------------------

    async def async_grid_statistic_ids(self) -> list[str]:
        """Return the statistics of the energy the home draws from the grid.

        They come from the Energy dashboard. The list is empty when it is not set
        up or has no grid source.
        """
        if "energy" not in self.hass.config.components:
            return []
        from homeassistant.components.energy.data import async_get_manager

        preferences = (await async_get_manager(self.hass)).data
        if not preferences:
            return []
        ids: list[str] = []
        for source in preferences.get("energy_sources", []):
            if source["type"] != "grid":
                continue
            grid: Mapping[str, Any] = source
            # Recent Home Assistant: one meter per grid connection. Older: a list
            # of meters in "flow_from". Read both.
            stat_ids = [grid.get("stat_energy_from")]
            stat_ids += [
                flow.get("stat_energy_from") for flow in grid.get("flow_from", [])
            ]
            ids += [s for s in stat_ids if s and s not in ids]
        return ids

    async def _async_grid_consumption(
        self, stat_ids: list[str], start: datetime, end: datetime
    ) -> dict[datetime, float]:
        """Return the kWh drawn from the grid in each hour of ``[start, end)``."""
        rows = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            start,
            end,
            set(stat_ids),
            "hour",
            {"energy": "kWh"},
            {"change"},
        )
        consumption: dict[datetime, float] = {}
        for series in rows.values():
            for row in series:
                hour = datetime.fromtimestamp(row["start"], timezone.utc)
                consumption[hour] = consumption.get(hour, 0.0) + (
                    row.get("change") or 0.0
                )
        return consumption

    async def _async_running_total(self, stat_id: str, hour: datetime) -> float:
        """Return the running total of a derived statistic at ``hour`` (0 if none)."""
        rows = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            hour,
            hour + timedelta(hours=1),
            {stat_id},
            "hour",
            None,
            {"sum"},
        )
        series = rows.get(stat_id) or []
        return float(series[0].get("sum") or 0.0) if series else 0.0

    async def _async_sync_grid(
        self,
        metric: Metric,
        hourly: list[HourlyPoint],
        now: datetime,
        grid_ids: list[str],
    ) -> None:
        """Import ``grid consumption x footprint`` as a running total per hour."""
        grid = metric.grid
        stat_id = grid_statistic_id(self.zone, metric, self.scope)
        if grid is None or stat_id is None:
            return
        last_complete = now.replace(minute=0, second=0, microsecond=0) - timedelta(
            hours=1
        )
        first = min(point.start for point in hourly)
        if first > last_complete:
            return
        consumption = await self._async_grid_consumption(
            grid_ids, first, last_complete + timedelta(hours=1)
        )
        base = await self._async_running_total(stat_id, first - timedelta(hours=1))
        # Not empty: the first hour is complete, so at least that one is derived.
        totals = derive_cumulative(hourly, consumption, base, last_complete)
        meta = StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=f"Wattnet - {self.zone_name} ({self.zone}) {grid.name}",
            source=DOMAIN,
            statistic_id=stat_id,
            unit_class=grid.unit_class,
            unit_of_measurement=grid.unit,
        )
        async_add_external_statistics(
            self.hass,
            meta,
            [
                StatisticData(start=hour, state=total, sum=total)
                for hour, total in totals
            ],
        )

    def _import(self, metric: Metric, hourly: list[HourlyPoint]) -> None:
        meta = StatisticMetaData(
            mean_type=StatisticMeanType.ARITHMETIC,
            has_sum=False,
            name=(
                f"Wattnet - {self.zone_name} ({self.zone}) "
                f"{metric.key.replace('_', ' ')}"
            ),
            source=DOMAIN,
            statistic_id=statistic_id(self.zone, metric, self.scope),
            unit_class=None,
            unit_of_measurement=metric.unit,
        )
        stats = [
            StatisticData(start=p.start, mean=p.mean, min=p.minimum, max=p.maximum)
            for p in hourly
        ]
        async_add_external_statistics(self.hass, meta, stats)
