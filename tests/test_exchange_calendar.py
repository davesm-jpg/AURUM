import json
from datetime import date

import pytest

from aurum.exchange_calendar import SessionCalendar
from aurum.timestamps import minute_timestamp, source_timezone


def interval(start: str, end: str) -> dict[str, str]:
    return {"start": start, "end": end}


def session(trade_date: str, *windows: dict[str, str]) -> dict[str, object]:
    return {"trade_date": trade_date, "windows": windows}


def synthetic_snapshot(coverage: dict[str, str], *sessions: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 2,
        "instrument": "MGC",
        "source": "synthetic calendar fixture",
        "version": "fixture-revision-1",
        "coverage": coverage,
        "sessions": list(sessions),
    }


def calendar_for(coverage: dict[str, str], *sessions: dict[str, object]) -> SessionCalendar:
    return SessionCalendar.from_json(json.dumps(synthetic_snapshot(coverage, *sessions)))


def test_evening_trade_date_maintenance_and_weekend() -> None:
    calendar = calendar_for(
        interval("2025-03-07T15:59-06:00", "2025-03-10T17:01-05:00"),
        session("2025-03-07", interval("2025-03-06T17:00-06:00", "2025-03-07T16:00-06:00")),
        session("2025-03-10", interval("2025-03-09T17:00-05:00", "2025-03-10T16:00-05:00")),
        session("2025-03-11", interval("2025-03-10T17:00-05:00", "2025-03-11T16:00-05:00")),
    )
    for stamp, trade_date in (
        ("2025-03-07T15:59-06:00", date(2025, 3, 7)),
        ("2025-03-09T17:00-05:00", date(2025, 3, 10)),
        ("2025-03-10T15:59-05:00", date(2025, 3, 10)),
        ("2025-03-10T17:00-05:00", date(2025, 3, 11)),
    ):
        matched = calendar.session_at(minute_timestamp(stamp))
        assert matched is not None
        assert matched.trade_date == trade_date

    for stamp in (
        "2025-03-07T15:58-06:00",
        "2025-03-07T16:00-06:00",
        "2025-03-08T12:00-06:00",
        "2025-03-09T16:59-05:00",
        "2025-03-10T16:00-05:00",
        "2025-03-10T16:59-05:00",
        "2025-03-10T17:01-05:00",
    ):
        start = minute_timestamp(stamp)
        assert not calendar.permits(start)
        assert calendar.session_at(start) is None


@pytest.mark.parametrize(
    ("sunday_open", "trade_date", "utc_hour", "prague_hour"),
    [
        ("2025-03-02T17:00-06:00", "2025-03-03", 23, 0),
        ("2025-03-09T17:00-05:00", "2025-03-10", 22, 23),
        ("2025-03-30T17:00-05:00", "2025-03-31", 22, 0),
    ],
)
def test_chicago_exchange_open_through_us_and_europe_dst(
    sunday_open: str, trade_date: str, utc_hour: int, prague_hour: int
) -> None:
    close = f"{trade_date}T16:00{sunday_open[-6:]}"
    window = interval(sunday_open, close)
    calendar = calendar_for(window, session(trade_date, window))
    opened = minute_timestamp(sunday_open)
    matched = calendar.session_at(opened)
    assert matched is not None
    assert matched.start.hour == utc_hour
    assert opened.astimezone(source_timezone("America/Chicago")).hour == 17
    assert opened.astimezone(source_timezone("America/New_York")).hour == 18
    assert opened.astimezone(source_timezone("Europe/Prague")).hour == prague_hour
    assert matched.trade_date.isoformat() == trade_date
    assert SessionCalendar.from_dict(calendar.as_dict()) == calendar


def test_holiday_early_close_and_split_session() -> None:
    # Exception times and trade-date assignments in this case are synthetic.
    calendar = calendar_for(
        interval("2025-03-18T22:00Z", "2025-03-21T20:00Z"),
        session(
            "2025-03-19",
            interval("2025-03-18T22:00Z", "2025-03-19T14:00Z"),
            interval("2025-03-19T14:30Z", "2025-03-19T16:30Z"),
        ),
        session("2025-03-21", interval("2025-03-20T22:00Z", "2025-03-21T21:00Z")),
    )
    before = calendar.session_at(minute_timestamp("2025-03-19T13:59Z"))
    after = calendar.session_at(minute_timestamp("2025-03-19T14:30Z"))
    assert before is not None
    assert before is after
    assert before.trade_date == date(2025, 3, 19)
    assert not calendar.permits(minute_timestamp("2025-03-19T14:00Z"))
    assert not calendar.permits(minute_timestamp("2025-03-19T16:30Z"))
    assert not calendar.permits(minute_timestamp("2025-03-20T14:00Z"))
    assert calendar.permits(minute_timestamp("2025-03-20T22:00Z"))


def test_coverage_inside_halt_is_closed() -> None:
    calendar = calendar_for(
        interval("2025-03-19T14:00Z", "2025-03-19T14:30Z"),
        session(
            "2025-03-19",
            interval("2025-03-18T22:00Z", "2025-03-19T14:00Z"),
            interval("2025-03-19T14:30Z", "2025-03-19T16:30Z"),
        ),
    )
    assert calendar.windows == ()
    assert calendar.session_at(minute_timestamp("2025-03-19T14:01Z")) is None


@pytest.mark.parametrize("schema_version", [1, 3, "2", 2.0])
def test_rejects_unsupported_snapshot_schema(schema_version: object) -> None:
    document = synthetic_snapshot(interval("2025-03-19T12:00Z", "2025-03-19T18:00Z"))
    document["schema_version"] = schema_version
    with pytest.raises(ValueError, match="schema_version 2"):
        SessionCalendar.from_dict(document)


@pytest.mark.parametrize(
    ("sessions", "message"),
    [
        ((session("2025-03-19"),), "requires at least one"),
        (
            (session("20250319", interval("2025-03-19T12:00Z", "2025-03-19T13:00Z")),),
            "YYYY-MM-DD",
        ),
        (
            (session("2025-03-18", interval("2025-03-18T12:00Z", "2025-03-18T13:00Z")),),
            "intersect coverage",
        ),
        (
            (
                session(
                    "2025-03-19",
                    interval("2025-03-19T12:00Z", "2025-03-19T13:00Z"),
                    interval("2025-03-19T13:00Z", "2025-03-19T14:00Z"),
                ),
            ),
            "separated by a closure",
        ),
        (
            (
                session("2025-03-19", interval("2025-03-19T12:00Z", "2025-03-19T13:00Z")),
                session("2025-03-19", interval("2025-03-19T14:00Z", "2025-03-19T15:00Z")),
            ),
            "trade dates must be unique",
        ),
        (
            (
                session("2025-03-19", interval("2025-03-19T12:00Z", "2025-03-19T15:00Z")),
                session("2025-03-20", interval("2025-03-19T14:00Z", "2025-03-19T16:00Z")),
            ),
            "chronological and disjoint",
        ),
    ],
)
def test_rejects_inconsistent_sessions(
    sessions: tuple[dict[str, object], ...], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        calendar_for(interval("2025-03-19T12:00Z", "2025-03-19T18:00Z"), *sessions)
