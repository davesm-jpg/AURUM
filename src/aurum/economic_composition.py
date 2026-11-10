"""Compose reviewed calendar snapshots without linking independent publications."""

from collections.abc import Sequence
from dataclasses import replace
from hashlib import sha256

from aurum.economic_events import (
    MAX_ECONOMIC_PUBLICATIONS,
    Announcement,
    AnnouncementOrigin,
    EconomicCalendar,
    EconomicPublication,
)
from aurum.exchange_calendar import TradingWindow
from aurum.market_data import canonical_json

ECONOMIC_COMPOSITION_VERSION = 1


def compose_economic_calendars(
    snapshots: Sequence[EconomicCalendar], *, source: str
) -> EconomicCalendar:
    if not 2 <= len(snapshots) <= MAX_ECONOMIC_PUBLICATIONS:
        raise ValueError(f"provide between 2 and {MAX_ECONOMIC_PUBLICATIONS} economic snapshots")
    if any(snapshot.publications for snapshot in snapshots):
        raise ValueError("compose the original publication snapshots directly")
    kinds = {snapshot.data_kind for snapshot in snapshots}
    kind = snapshots[0].data_kind
    if len(kinds) != 1 or kind is None:
        raise ValueError("economic snapshots must have one matching explicit source classification")

    events: list[Announcement] = []
    publications = []
    for snapshot in sorted(snapshots, key=lambda item: item.source):
        publications.append(
            EconomicPublication(
                snapshot.source,
                snapshot.version,
                _digest(snapshot.as_dict()),
                snapshot.coverage,
                snapshot.coverage_known_at,
                snapshot.coverage_complete,
                snapshot.captures,
            )
        )
        event_ids = {
            event.event_id: _digest((ECONOMIC_COMPOSITION_VERSION, snapshot.source, event.event_id))
            for event in snapshot.events
        }
        events.extend(
            replace(
                event,
                event_id=event_ids[event.event_id],
                supersedes=event_ids[event.supersedes] if event.supersedes else None,
                origin=AnnouncementOrigin(snapshot.source, event.event_id),
            )
            for event in snapshot.events
        )

    digest = _digest((source, kind, [publication.as_dict() for publication in publications]))
    return EconomicCalendar(
        source,
        f"economic-compose-v{ECONOMIC_COMPOSITION_VERSION}/{digest}",
        TradingWindow(
            min(snapshot.coverage.start for snapshot in snapshots),
            max(snapshot.coverage.end for snapshot in snapshots),
        ),
        min(snapshot.coverage_known_at for snapshot in snapshots),
        tuple(sorted(events, key=lambda event: (event.published_at, event.event_id))),
        coverage_complete=False,
        data_kind=kind,
        publications=tuple(publications),
    )


def _digest(value: object) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()
