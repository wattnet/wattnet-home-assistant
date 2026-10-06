"""Pure helpers to turn Wattnet API payloads into hourly statistics.

Nothing here imports Home Assistant so it can be unit tested in isolation.

The API groups points in ``series`` keyed by ``(valid, zone_status)``. A point
with ``valid=True`` is consolidated and immutable, one with ``valid=False`` is
provisional and can be revised later, so the same hour must be requested again
until it comes back consolidated.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from .const import POINTS_PER_HOUR


@dataclass(frozen=True)
class Point:
    """A single timestamped value with its consolidation state."""

    timestamp: datetime
    value: float
    valid: bool
    zone_status: str


@dataclass(frozen=True)
class HourlyPoint:
    """Values of one UTC hour.

    ``valid`` is true only when the hour is complete (it has every expected
    point) and every point is itself consolidated.
    """

    start: datetime
    mean: float
    minimum: float
    maximum: float
    valid: bool


def _parse_ts(raw: Any) -> datetime:
    """Parse an ISO timestamp (or pass a datetime through) as aware UTC."""
    if isinstance(raw, datetime):
        ts = raw
    else:
        text = str(raw)
        ts = datetime.fromisoformat(
            text[:-1] + "+00:00" if text.endswith("Z") else text
        )
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def parse_points(
    records: Iterable[Mapping[str, Any]], match: Mapping[str, Any] | None = None
) -> list[Point]:
    """Flatten API records into points, keeping only records matching ``match``.

    ``match`` filters on record-level fields such as ``footprint_type`` or
    ``scope``. Duplicated timestamps (same hour present in a provisional and a
    consolidated series) are resolved preferring ``valid=True``.
    """
    by_ts: dict[datetime, Point] = {}
    for record in records:
        if match and any(record.get(k) != v for k, v in match.items()):
            continue
        for series in record.get("series", []):
            valid = bool(series.get("valid", False))
            status = str(series.get("zone_status", "missing"))
            for raw_ts, value in series.get("values", []):
                if value is None:
                    continue
                ts = _parse_ts(raw_ts)
                current = by_ts.get(ts)
                if current is not None and current.valid and not valid:
                    continue
                by_ts[ts] = Point(ts, float(value), valid, status)
    return [by_ts[ts] for ts in sorted(by_ts)]


def latest_point(points: list[Point]) -> Point | None:
    """Return the most recent point, if any."""
    return points[-1] if points else None


def to_hourly(
    points: Iterable[Point], expected_per_hour: int = POINTS_PER_HOUR
) -> list[HourlyPoint]:
    """Aggregate points into UTC hours (statistics need hour-aligned starts).

    Wattnet publishes one point every 15 minutes. An hour with fewer than
    ``expected_per_hour`` points (the current hour, or a gap) is never
    reported as consolidated, otherwise its partial mean would be frozen.
    """
    buckets: dict[datetime, list[Point]] = defaultdict(list)
    for point in points:
        buckets[point.timestamp.replace(minute=0, second=0, microsecond=0)].append(
            point
        )
    hourly = []
    for start in sorted(buckets):
        values = [p.value for p in buckets[start]]
        hourly.append(
            HourlyPoint(
                start=start,
                mean=sum(values) / len(values),
                minimum=min(values),
                maximum=max(values),
                valid=len(values) >= expected_per_hour
                and all(p.valid for p in buckets[start]),
            )
        )
    return hourly


def derive_cumulative(
    hourly: list[HourlyPoint],
    consumption: Mapping[datetime, float],
    base: float,
    last_complete_hour: datetime,
) -> list[tuple[datetime, float]]:
    """Return the running total of ``consumption x footprint`` for each hour.

    ``hourly`` holds the footprint per kWh of each hour and ``consumption`` the
    kWh the home drew from the grid in each hour. The result has one running
    total per hour, so each row is the previous one plus that hour's product.

    - Hours after ``last_complete_hour`` are left out: the consumption of an
      hour that has not finished is partial and would be frozen.
    - An hour with no consumption adds nothing. An hour in the middle with no
      footprint also adds nothing, so the total stays continuous instead of the
      following hours being lost behind a gap in Wattnet's data.
    - Hours before the first footprint, and after the last, are not produced.
    """
    by_start = {point.start: point for point in hourly}
    usable = [start for start in by_start if start <= last_complete_hour]
    if not usable:
        return []
    first, last = min(usable), max(usable)
    out: list[tuple[datetime, float]] = []
    total = base
    hour = first
    while hour <= last:
        point = by_start.get(hour)
        if point is not None:
            total += consumption.get(hour, 0.0) * point.mean
        out.append((hour, total))
        hour += timedelta(hours=1)
    return out


def next_watermark(hourly: list[HourlyPoint], fallback: datetime) -> datetime:
    """Where the next request window must start.

    It is the first hour not yet consolidated, so provisional hours are fetched
    and overwritten until the API flags them ``valid``. When every hour is
    consolidated it is the hour after the last one. Without data it stays at
    ``fallback``.
    """
    if not hourly:
        return fallback
    for point in hourly:
        if not point.valid:
            return point.start
    return hourly[-1].start + timedelta(hours=1)
