import json
import subprocess
from dataclasses import replace
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from sys import executable

import pytest

from aurum.economic_composition import compose_economic_calendars
from aurum.economic_events import (
    Announcement,
    EconomicCalendar,
    EconomicCapture,
    EconomicSourceKind,
    EventRisk,
)
from aurum.exchange_calendar import TradingWindow
from aurum.market_data import canonical_json
from aurum.providers import FeedStatus
from aurum.runtime_profiles import ProviderProfile
from aurum.timestamps import MINUTE, minute_timestamp

START = minute_timestamp("2025-03-10T12:00Z")


def at(minute: int) -> datetime:
    return START + minute * MINUTE


@pytest.fixture
def synthetic_publications() -> tuple[EconomicCalendar, EconomicCalendar]:
    source_a = EconomicCalendar(
        "Synthetic publication A",
        "synthetic-a-v1",
        TradingWindow(at(0), at(40)),
        at(5),
        (
            Announcement("shared", "Synthetic initial time", at(20), at(5)),
            Announcement("changed", "Synthetic changed time", at(22), at(10), supersedes="shared"),
            Announcement(
                "cancelled",
                "Synthetic changed time",
                at(22),
                at(25),
                supersedes="changed",
                cancelled=True,
            ),
        ),
        coverage_complete=False,
        data_kind=EconomicSourceKind.SYNTHETIC,
        captures=tuple(
            EconomicCapture(f"synthetic-capture-{minute}", at(minute)) for minute in (5, 10, 25)
        ),
    )
    source_b = EconomicCalendar(
        "Synthetic publication B",
        "synthetic-b-v1",
        TradingWindow(at(15), at(60)),
        at(12),
        (
            Announcement(
                "shared",
                "Synthetic distinct release",
                at(20),
                at(12),
                actual_at=at(21),
                actual_known_at=at(23),
            ),
        ),
        data_kind=EconomicSourceKind.SYNTHETIC,
    )
    return source_a, source_b


def test_composed_publications_keep_causal_identity_and_provenance(
    tmp_path: Path, synthetic_publications: tuple[EconomicCalendar, EconomicCalendar]
) -> None:
    first, second = synthetic_publications
    source = "Synthetic publication bundle"
    bundle = compose_economic_calendars((second, first), source=source)
    assert bundle == compose_economic_calendars((first, second), source=source)
    assert bundle.coverage == TradingWindow(at(0), at(60))
    assert bundle.coverage_complete is False
    assert bundle.data_kind is EconomicSourceKind.SYNTHETIC
    assert bundle.version.startswith("economic-compose-v1/")
    for publication, original in zip(bundle.publications, synthetic_publications, strict=True):
        assert publication.source == original.source
        assert publication.captures == original.captures
        assert (
            publication.snapshot_sha256
            == sha256(canonical_json(original.as_dict()).encode()).hexdigest()
        )
    initial, revised, other, cancelled = bundle.events
    assert initial.origin is not None and other.origin is not None
    assert initial.origin.event_id == other.origin.event_id == "shared"
    assert initial.origin.source != other.origin.source
    assert initial.event_id != other.event_id
    assert revised.supersedes == initial.event_id
    assert cancelled.supersedes == revised.event_id
    assert bundle.assess(at(9), 15).event_ids == (initial.event_id,)
    assert bundle.assess(at(12), 15).event_ids == (revised.event_id, other.event_id)
    assert bundle.assess(at(20), 0).event_ids == (other.event_id,)
    assert bundle.assess(at(21), 0).risk is EventRisk.UNKNOWN
    assert bundle.assess(at(23), 2).event_ids == (revised.event_id, other.event_id)
    assert bundle.assess(at(25), 5).event_ids == (other.event_id,)
    assert bundle.assess(at(50), 5).risk is EventRisk.UNKNOWN
    updated = compose_economic_calendars(
        (first, replace(second, version="synthetic-b-v2")), source=source
    )
    assert updated.version != bundle.version
    assert tuple(item.event_id for item in updated.events) == tuple(
        item.event_id for item in bundle.events
    )

    paths = tuple(tmp_path / f"synthetic-publication-{index}.json" for index in range(2))
    for path, original in zip(paths, synthetic_publications, strict=True):
        path.write_text(json.dumps(original.as_dict()), encoding="utf-8")
    unused = tmp_path / "unused-synthetic.sqlite"
    output = subprocess.run(
        [
            str(Path(executable).with_name("aurum")),
            "--db",
            str(unused),
            "compose-economic-calendars",
            *(str(path) for path in paths),
            "--source",
            source,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert output.stdout == canonical_json(bundle.as_dict()) + "\n"
    assert EconomicCalendar.from_json(output.stdout) == bundle
    assert not unused.exists()

    profile = ProviderProfile(
        "Synthetic comparison fixture",
        "AURUM_SYNTHETIC_TEST_ENDPOINT",
        FeedStatus.SIMULATED,
        calendar_dataset_id="0" * 64,
        economic_calendar=json.loads(output.stdout),
    )
    assert profile.economic_events() == bundle
    with pytest.raises(ValueError, match="simulated provider"):
        replace(profile, source_status=FeedStatus.DELAYED).economic_events()


def test_composition_rejects_ambiguous_sources_and_cross_publication_revisions(
    synthetic_publications: tuple[EconomicCalendar, EconomicCalendar],
) -> None:
    first, second = synthetic_publications
    name = "Synthetic publication bundle"
    with pytest.raises(ValueError, match="matching explicit source classification"):
        compose_economic_calendars(
            (first, replace(second, data_kind=EconomicSourceKind.OBSERVED_PUBLICATION)), source=name
        )
    with pytest.raises(ValueError, match="matching explicit source classification"):
        compose_economic_calendars((first, replace(second, data_kind=None)), source=name)
    with pytest.raises(ValueError, match="distinct economic publications"):
        compose_economic_calendars(
            (first, replace(second, source=first.source.lower())), source=name
        )
    bundle = compose_economic_calendars(synthetic_publications, source=name)
    with pytest.raises(ValueError, match="original publication snapshots"):
        compose_economic_calendars((bundle, second), source=name)

    payload = json.loads(canonical_json(bundle.as_dict()))
    payload["events"][3]["supersedes"] = payload["events"][2]["event_id"]
    with pytest.raises(ValueError, match="another publication"):
        EconomicCalendar.from_dict(payload)
    payload = json.loads(canonical_json(bundle.as_dict()))
    del payload["events"][0]["origin"]
    with pytest.raises(ValueError, match="known publication origins"):
        EconomicCalendar.from_dict(payload)
