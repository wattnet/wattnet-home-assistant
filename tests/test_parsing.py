"""Tests for the pure parsing helpers."""

from datetime import datetime, timedelta, timezone

from custom_components.wattnet.parsing import (
    latest_point,
    next_watermark,
    parse_points,
    to_hourly,
)

UTC = timezone.utc


def _record(series, **fields):
    return {"zone": "ES", "series": series, **fields}


def _series(valid, values, status="complete"):
    return {"valid": valid, "zone_status": status, "values": values}


def _quarters(hour, value, count=4):
    return [
        [f"2026-10-06T{hour:02d}:{m:02d}:00Z", value] for m in (0, 15, 30, 45)[:count]
    ]


def test_prefers_valid_over_provisional_for_same_timestamp():
    records = [
        _record(
            [
                _series(False, [["2026-10-06T10:00:00Z", 200.0]], "preview"),
                _series(True, [["2026-10-06T10:00:00Z", 180.0]]),
            ]
        )
    ]
    (point,) = parse_points(records)
    assert point.value == 180.0 and point.valid is True


def test_provisional_does_not_override_valid_regardless_of_order():
    records = [
        _record(
            [
                _series(True, [["2026-10-06T10:00:00Z", 180.0]]),
                _series(False, [["2026-10-06T10:00:00Z", 200.0]], "preview"),
            ]
        )
    ]
    (point,) = parse_points(records)
    assert point.value == 180.0 and point.valid is True


def test_match_filters_records():
    records = [
        _record(
            [_series(True, [["2026-10-06T10:00:00Z", 1.0]])], footprint_type="carbon"
        ),
        _record(
            [_series(True, [["2026-10-06T10:00:00Z", 2.0]])], footprint_type="water"
        ),
    ]
    (point,) = parse_points(records, {"footprint_type": "water"})
    assert point.value == 2.0


def test_naive_timestamps_are_utc_and_sorted():
    records = [
        _record(
            [
                _series(
                    True, [["2026-10-06T11:00:00", 2.0], ["2026-10-06T10:00:00", 1.0]]
                )
            ]
        )
    ]
    points = parse_points(records)
    assert [p.value for p in points] == [1.0, 2.0]
    assert points[0].timestamp.tzinfo is not None
    assert latest_point(points).value == 2.0


def test_hourly_aggregation_and_validity():
    records = [
        _record(
            [
                _series(True, _quarters(10, 100.0, count=2)),
                _series(False, _quarters(10, 200.0)[2:], "preview"),
            ]
        )
    ]
    (hour,) = to_hourly(parse_points(records))
    assert hour.mean == 150.0 and hour.minimum == 100.0 and hour.maximum == 200.0
    assert hour.valid is False  # one of its points is still provisional


def test_complete_consolidated_hour_is_valid():
    (hour,) = to_hourly(parse_points([_record([_series(True, _quarters(10, 100.0))])]))
    assert hour.valid is True


def test_incomplete_hour_is_not_valid_even_if_all_points_are():
    (hour,) = to_hourly(
        parse_points([_record([_series(True, _quarters(10, 100.0, count=2))])])
    )
    assert hour.valid is False
    assert hour.mean == 100.0


def test_watermark_stops_at_first_provisional_hour():
    records = [
        _record(
            [
                _series(True, _quarters(8, 1.0) + _quarters(9, 1.0)),
                _series(False, _quarters(10, 1.0), "preview"),
            ]
        )
    ]
    fallback = datetime(2026, 10, 1, tzinfo=UTC)
    mark = next_watermark(to_hourly(parse_points(records)), fallback)
    assert mark == datetime(2026, 10, 6, 10, tzinfo=UTC)


def test_watermark_stops_at_incomplete_hour():
    records = [
        _record([_series(True, _quarters(9, 1.0) + _quarters(10, 1.0, count=3))])
    ]
    fallback = datetime(2026, 10, 1, tzinfo=UTC)
    mark = next_watermark(to_hourly(parse_points(records)), fallback)
    assert mark == datetime(2026, 10, 6, 10, tzinfo=UTC)


def test_watermark_advances_when_all_consolidated():
    records = [_record([_series(True, _quarters(9, 1.0))])]
    fallback = datetime(2026, 10, 1, tzinfo=UTC)
    mark = next_watermark(to_hourly(parse_points(records)), fallback)
    assert mark == datetime(2026, 10, 6, 10, tzinfo=UTC)


def test_watermark_keeps_fallback_without_data():
    fallback = datetime(2026, 10, 1, tzinfo=UTC)
    assert next_watermark([], fallback) == fallback


def test_reconsolidation_replaces_provisional_hour():
    """A later fetch of the same hour flips the watermark forward."""
    first = to_hourly(
        parse_points([_record([_series(False, _quarters(10, 200.0), "preview")])])
    )
    second = to_hourly(parse_points([_record([_series(True, _quarters(10, 180.0))])]))
    fallback = datetime(2026, 10, 1, tzinfo=UTC)
    assert next_watermark(first, fallback) == first[0].start
    assert next_watermark(second, fallback) == second[0].start + timedelta(hours=1)
    assert second[0].mean == 180.0


# -- grid consumption x footprint --------------------------------------------


def _hp(hour: int, mean: float, valid: bool = True):
    from custom_components.wattnet.parsing import HourlyPoint

    start = datetime(2026, 10, 6, hour, tzinfo=UTC)
    return HourlyPoint(start, mean, mean, mean, valid)


def _h(hour: int) -> datetime:
    return datetime(2026, 10, 6, hour, tzinfo=UTC)


def test_cumulative_total_adds_each_hours_product():
    from custom_components.wattnet.parsing import derive_cumulative

    out = derive_cumulative(
        [_hp(9, 100.0), _hp(10, 200.0), _hp(11, 50.0)],
        {_h(9): 2.0, _h(10): 1.0, _h(11): 4.0},
        base=0.0,
        last_complete_hour=_h(11),
    )
    assert out == [(_h(9), 200.0), (_h(10), 400.0), (_h(11), 600.0)]


def test_cumulative_total_continues_from_the_base():
    from custom_components.wattnet.parsing import derive_cumulative

    out = derive_cumulative(
        [_hp(10, 10.0)], {_h(10): 3.0}, base=1000.0, last_complete_hour=_h(10)
    )
    assert out == [(_h(10), 1030.0)]


def test_the_unfinished_hour_is_left_out():
    from custom_components.wattnet.parsing import derive_cumulative

    out = derive_cumulative(
        [_hp(9, 10.0), _hp(10, 10.0), _hp(11, 10.0)],
        {_h(9): 1.0, _h(10): 1.0, _h(11): 1.0},
        base=0.0,
        last_complete_hour=_h(10),
    )
    assert [hour for hour, _ in out] == [_h(9), _h(10)]


def test_an_hour_without_consumption_keeps_the_total():
    from custom_components.wattnet.parsing import derive_cumulative

    out = derive_cumulative(
        [_hp(9, 10.0), _hp(10, 10.0)],
        {_h(9): 1.0},
        base=0.0,
        last_complete_hour=_h(10),
    )
    assert out == [(_h(9), 10.0), (_h(10), 10.0)]


def test_a_gap_in_the_footprint_adds_nothing_but_does_not_stop_the_total():
    from custom_components.wattnet.parsing import derive_cumulative

    out = derive_cumulative(
        [_hp(9, 10.0), _hp(11, 10.0)],  # no footprint for 10:00
        {_h(9): 1.0, _h(10): 5.0, _h(11): 1.0},
        base=0.0,
        last_complete_hour=_h(11),
    )
    assert out == [(_h(9), 10.0), (_h(10), 10.0), (_h(11), 20.0)]


def test_nothing_is_derived_without_footprints_or_complete_hours():
    from custom_components.wattnet.parsing import derive_cumulative

    assert derive_cumulative([], {_h(9): 1.0}, 0.0, _h(12)) == []
    assert derive_cumulative([_hp(11, 1.0)], {_h(11): 1.0}, 0.0, _h(10)) == []
