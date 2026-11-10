from datetime import datetime
from random import Random

import pytest

from aurum.economic_events import Announcement, EconomicCalendar, EventAssessment, EventRisk
from aurum.exchange_calendar import TradingWindow
from aurum.timestamps import MINUTE, minute_timestamp

BASE = minute_timestamp("2025-03-10T12:00Z")


def at(minute: int) -> datetime:
    return BASE + minute * MINUTE


def scan(calendar: EconomicCalendar, now: datetime, width: int) -> EventAssessment:
    lower, upper = now - width * MINUTE, now + width * MINUTE
    visible = tuple(event for event in calendar.events if event.published_at <= now)
    superseded = {event.supersedes for event in visible}
    included = tuple(
        event.event_id
        for event in visible
        if not event.cancelled
        and event.event_id not in superseded
        and lower <= event.available_release_at(now) <= upper
    )
    if included:
        return EventAssessment(EventRisk.ELEVATED, included)
    clear = (
        calendar.coverage_complete
        and calendar.coverage_known_at <= now
        and calendar.coverage.start <= lower <= upper < calendar.coverage.end
    )
    return EventAssessment(EventRisk.CLEAR if clear else EventRisk.UNKNOWN)


@pytest.mark.parametrize("complete", (False, True))
def test_economic_index_matches_publication_scan(complete: bool) -> None:
    rng = Random(77)
    events = [
        Announcement("synthetic-original", "Synthetic prior", at(3), at(0), at(3), at(5)),
        Announcement(
            "synthetic-cancel",
            "Synthetic prior",
            at(3),
            at(5),
            supersedes="synthetic-original",
            cancelled=True,
        ),
        Announcement(
            "synthetic-restored", "Synthetic restored", at(8), at(7), supersedes="synthetic-cancel"
        ),
        Announcement("synthetic-actual", "Synthetic actual", at(8), at(4), at(3), at(4)),
    ]
    for index in range(150):
        event_id = f"synthetic-generated-{index}"
        scheduled = rng.randrange(2, 95)
        published = scheduled - rng.randrange(1, 15)
        actual = scheduled + rng.randrange(-1, 8)
        actual_known = actual + rng.randrange(0, 5)
        events.append(
            Announcement(
                event_id,
                "Synthetic scheduled",
                at(scheduled),
                at(published),
                at(actual),
                at(actual_known),
            )
        )
        if index % 3 == 0:
            events.append(
                Announcement(
                    f"{event_id}-updated",
                    "Synthetic revision",
                    at(scheduled + rng.randrange(-1, 8)),
                    at(published + rng.randrange(1, 19)),
                    supersedes=event_id,
                    cancelled=index % 2 == 0,
                )
            )
    rng.shuffle(events)
    calendar = EconomicCalendar(
        "Synthetic randomized publication",
        "fixture-index",
        TradingWindow(at(-5), at(110)),
        at(-3),
        tuple(events),
        coverage_complete=complete,
    )
    for minute in range(-10, 121):
        for buffer in (0, 1, 5, 12):
            assert calendar.assess(at(minute), buffer) == scan(calendar, at(minute), buffer)
    assert "_release_index" not in calendar.as_dict()
    assert EconomicCalendar.from_dict(calendar.as_dict()) == calendar
