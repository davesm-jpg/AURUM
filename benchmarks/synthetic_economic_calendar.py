"""Measure point-in-time economic risk lookup on invented releases."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from random import Random
from time import perf_counter

from aurum.economic_events import Announcement, EconomicCalendar, EconomicSourceKind, EventRisk
from aurum.exchange_calendar import TradingWindow
from aurum.market_data import canonical_json
from aurum.timestamps import MINUTE

BASE = datetime(2020, 1, 1, tzinfo=UTC)
RELEASES = 3_000
QUERIES = 20_000
SEED = 41


def synthetic_calendar() -> EconomicCalendar:
    events = []
    for index in range(RELEASES):
        name = f"Synthetic release {index}"
        scheduled = BASE + timedelta(hours=index * 6)
        event_id = f"synthetic-{index}"
        events.append(Announcement(event_id, name, scheduled, scheduled - 90 * MINUTE))
        if index % 4 == 0:
            events.append(
                Announcement(
                    f"{event_id}-revision",
                    name,
                    scheduled + 10 * MINUTE,
                    scheduled - 30 * MINUTE,
                    actual_at=scheduled + 12 * MINUTE,
                    actual_known_at=scheduled + 14 * MINUTE,
                    supersedes=event_id,
                )
            )
        elif index % 9 == 0:
            events.append(
                Announcement(
                    f"{event_id}-cancel",
                    name,
                    scheduled,
                    scheduled - 5 * MINUTE,
                    supersedes=event_id,
                    cancelled=True,
                )
            )
    return EconomicCalendar(
        "Synthetic calendar benchmark",
        "fixture-v1",
        TradingWindow(BASE, BASE + timedelta(hours=RELEASES * 6)),
        BASE - 90 * MINUTE,
        tuple(events),
        coverage_complete=False,
        data_kind=EconomicSourceKind.SYNTHETIC,
    )


def main() -> None:
    setup_start = perf_counter()
    calendar = synthetic_calendar()
    setup_seconds = perf_counter() - setup_start
    random = Random(SEED)
    queries = [
        (
            BASE
            + MINUTE
            * (
                random.randrange(RELEASES) * 360 + random.randint(-25, 25)
                if index % 5 == 0
                else random.randrange(RELEASES * 360)
            ),
            random.choice((0, 5, 15, 30)),
        )
        for index in range(QUERIES)
    ]
    checksum = sha256()
    elevated = 0
    started = perf_counter()
    for at, buffer in queries:
        risk = calendar.assess(at, buffer)
        elevated += risk.risk is EventRisk.ELEVATED
        checksum.update(canonical_json(risk.as_dict()).encode() + b"\n")
    print(
        canonical_json(
            {
                "data_kind": "synthetic",
                "announcements": len(calendar.events),
                "queries": len(queries),
                "elevated": elevated,
                "seed": SEED,
                "construction_seconds": round(setup_seconds, 4),
                "assessment_seconds": round(perf_counter() - started, 4),
                "sha256": checksum.hexdigest(),
            }
        )
    )


if __name__ == "__main__":
    main()
