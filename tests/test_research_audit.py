"""Research journal reconstruction uses explicitly synthetic source data only."""

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
from sys import executable
from threading import Event
from typing import Any

import pytest
from test_market_structure import (
    CONTRACT,
    START,
    TIMEFRAME,
    prepare_synthetic_print_replay,
    prepare_synthetic_research_sample,
)

from aurum.economic_events import Announcement, EconomicCalendar, EconomicSourceKind
from aurum.evidence import DEFAULT_EVIDENCE_CONFIG
from aurum.exchange_calendar import SessionCalendar, TradingSession, TradingWindow
from aurum.execution import DEFAULT_EXECUTION_CONFIG
from aurum.market_context import DEFAULT_CONTEXT_CONFIG, Regime, ResearchSession
from aurum.market_data import DataKind, ImportConfig, TimestampLabel, canonical_json, prepare_csv
from aurum.market_structure import BreakMode, BreakRule, PivotRule, Scope, StructureConfig
from aurum.paper_risk import TargetMode
from aurum.research_audit import verify_research_run
from aurum.research_replay import ResearchReplay
from aurum.storage import DatasetStore
from aurum.storage_admin import backup_store, verify_store
from aurum.strategy import DEFAULT_STRATEGY_CONFIG, Variant
from aurum.timestamps import MINUTE

STRUCTURE = StructureConfig(PivotRule(1, 1))
STRATEGY = replace(DEFAULT_STRATEGY_CONFIG, variant=Variant.MSS)
# Captured from the production constructors at 8b8ae96, before their extraction.
BASELINES = {
    "structure": (
        "53a918e2e7ba18f4ca8a605bd6c4e9da82fdb4522a42c8216822e4ebd476a4ba",
        79,
        "d6c8fd0a50c471def5b28af3b2e5fea7eaf5f25ad3198d170490531356149b18",
    ),
    "evidence": (
        "6a339579e7160ed1039d4a563f6e329e17a06e3a62fad337dba0a40de1b15046",
        521,
        "d6d7c6ec21334204df991e87756e16359d63cf3abb8aeecbbe0f266b5d3ad519",
    ),
    "strategy": (
        "fc491d6bdef408b7b7feec238b29727873ba899268b5abf424359c0bc3220187",
        15,
        "541f0a8fdf79b24aa776c1f0a7e80603fed6f8323d0b88b483e2091fda98ca04",
    ),
    "backtest": (
        "65a5d22e51736fb659ddb60da3e421374fc71ea03aa2e7560acb46d8d06f06ee",
        31,
        "6d5df2ad67f05eedc38519cd667d3d594796a9d3709f8952bf5e019df2d28ece",
    ),
}


def build(store: DatasetStore, dataset: str, kind: str) -> str:
    if kind in ("structure", "evidence"):
        return store.build_structure_run(
            dataset,
            TIMEFRAME,
            STRUCTURE,
            evidence=DEFAULT_EVIDENCE_CONFIG if kind == "evidence" else None,
        )
    return store.build_strategy_run(
        dataset,
        TIMEFRAME,
        STRUCTURE,
        STRATEGY,
        execution=DEFAULT_EXECUTION_CONFIG if kind == "backtest" else None,
    )


@pytest.mark.parametrize("kind", tuple(BASELINES))
def test_replay_extraction_preserves_original_ids_and_every_synthetic_event(
    tmp_path: Path, kind: str
) -> None:
    store, dataset, _ = prepare_synthetic_research_sample(tmp_path)
    run_id = build(store, dataset, kind)
    expected_id, expected_count, expected_digest = BASELINES[kind]
    assert run_id == expected_id
    events = [entry for page in store.structure_payload_batches(run_id) for entry in page]
    assert len(events) == expected_count
    assert sha256(canonical_json(events).encode()).hexdigest() == expected_digest
    before = sha256(store.path.read_bytes()).hexdigest()
    report = verify_research_run(store, run_id)
    assert report["verified"] is True and report["event_count"] == expected_count
    assert report["data_kind"] == "synthetic"
    assert report["input_validation"] == "preserved_source_reconstruction"
    assert sha256(store.path.read_bytes()).hexdigest() == before


def test_nondefault_nested_rules_and_economic_snapshot_roundtrip(tmp_path: Path) -> None:
    store, dataset, sessions = prepare_synthetic_research_sample(tmp_path)
    window = TradingWindow(sessions[0].start, sessions[-1].end)
    economic = EconomicCalendar(
        "synthetic journal audit",
        "synthetic-v1",
        window,
        window.start,
        (Announcement("synthetic-event", "Synthetic release", sessions[1].start, window.start),),
        data_kind=EconomicSourceKind.SYNTHETIC,
    )
    strategy = replace(
        STRATEGY,
        scope=Scope.EXTERNAL,
        allowed_sessions=(ResearchSession.NEW_YORK,),
        allowed_regimes=(Regime.RANGE,),
        risk=replace(STRATEGY.risk, target_mode=TargetMode.STRUCTURAL),
    )
    structure = replace(STRUCTURE, breaks=BreakRule(BreakMode.WICK))
    context = replace(DEFAULT_CONTEXT_CONFIG, event_buffer_minutes=7)
    run_id = store.build_strategy_run(
        dataset,
        TIMEFRAME,
        structure,
        strategy,
        context=context,
        economic=economic,
        execution=DEFAULT_EXECUTION_CONFIG,
    )
    manifest = store.backtest_manifest(run_id)
    restored = ResearchReplay.from_manifest(manifest)
    assert canonical_json(restored.manifest()) == canonical_json(manifest)
    assert restored.strategy is not None
    assert restored.strategy.scope is Scope.EXTERNAL
    assert restored.strategy.allowed_sessions == (ResearchSession.NEW_YORK,)
    assert verify_research_run(store, run_id)["verified"] is True


@pytest.mark.parametrize(
    "field",
    (
        "aggregation_version",
        "structure_version",
        "evidence_version",
        "context_version",
        "strategy_version",
        "backtest_version",
        "execution_version",
        "performance_version",
    ),
)
def test_saved_rules_require_original_versions(tmp_path: Path, field: str) -> None:
    store, dataset, _ = prepare_synthetic_research_sample(tmp_path)
    run = build(store, dataset, "backtest")
    manifest = store.backtest_manifest(run)
    manifest[field] = 999
    with pytest.raises(ValueError, match=f"original {field}"):
        ResearchReplay.from_manifest(manifest)


@pytest.mark.parametrize("mutation", ("missing", "unknown", "ordering"))
def test_incomplete_or_unsupported_manifests_do_not_silently_use_defaults(
    tmp_path: Path, mutation: str
) -> None:
    store, dataset, _ = prepare_synthetic_research_sample(tmp_path)
    run = build(store, dataset, "backtest")
    manifest = store.backtest_manifest(run)
    if mutation == "missing":
        strategy = manifest["strategy_config"]
        assert isinstance(strategy, dict)
        strategy.pop("minimum_score")
    elif mutation == "unknown":
        manifest["unsupported_setting"] = True
    else:
        manifest["intrabar_ordering"] = "unimplemented_ordering"
    with pytest.raises(ValueError, match="saved research definition"):
        ResearchReplay.from_manifest(manifest)


@pytest.mark.parametrize(
    "corruption", ("event", "summary", "first", "last", "gap", "extra", "duplicate-key")
)
def test_journal_corruption_is_rejected_without_mutating_or_repairing_records(
    tmp_path: Path, corruption: str
) -> None:
    store, dataset, _ = prepare_synthetic_research_sample(tmp_path)
    run = build(store, dataset, "backtest")
    with store.connection(write=True) as db:
        rows = db.execute(
            "SELECT sequence, payload FROM structure_events WHERE run_id = ? ORDER BY sequence",
            (run,),
        ).fetchall()
        if corruption == "duplicate-key":
            # SQL json_extract can select the first value while Python selects
            # the last. Equality after JSON decoding must not hide ambiguity.
            payload_text = '{"event":"synthetic_misleading",' + rows[0]["payload"][1:]
            db.execute(
                "UPDATE structure_events SET payload = ? WHERE run_id = ? AND sequence = 1",
                (payload_text, run),
            )
        elif corruption in ("event", "summary"):
            row = rows[0] if corruption == "event" else rows[-1]
            payload = json.loads(row["payload"])
            if corruption == "event":
                payload["confirmed_at"] = "2099-01-01T00:00:00+00:00"
            else:
                payload["performance"]["net_profit_cents"] += 1_000_000
            db.execute(
                "UPDATE structure_events SET payload = ? WHERE run_id = ? AND sequence = ?",
                (canonical_json(payload), run, row["sequence"]),
            )
        elif corruption == "extra":
            db.execute(
                "INSERT INTO structure_events VALUES (?, ?, ?)",
                (run, rows[-1]["sequence"] + 1, '{"event":"synthetic_extra"}'),
            )
        else:
            selected = (
                rows[0] if corruption == "first" else rows[-1] if corruption == "last" else rows[2]
            )
            db.execute(
                "DELETE FROM structure_events WHERE run_id = ? AND sequence = ?",
                (run, selected["sequence"]),
            )
    before = sha256(store.path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="research journal"):
        verify_research_run(store, run)
    assert sha256(store.path.read_bytes()).hexdigest() == before


def test_run_and_source_identity_fail_closed(tmp_path: Path) -> None:
    store, dataset, _ = prepare_synthetic_research_sample(tmp_path)
    run = build(store, dataset, "backtest")
    with store.connection(write=True) as db:
        raw = store.backtest_manifest(run)
        execution = raw["execution_config"]
        assert isinstance(execution, dict)
        execution["initial_equity_cents"] += 1
        db.execute(
            "UPDATE structure_runs SET definition = ? WHERE id = ?", (canonical_json(raw), run)
        )
    with pytest.raises(ValueError, match="identity differs"):
        verify_research_run(store, run)


def test_source_reconstruction_is_required_before_claiming_full_verification(
    tmp_path: Path,
) -> None:
    store, dataset, _ = prepare_synthetic_research_sample(tmp_path)
    run = build(store, dataset, "structure")
    with store.connection(write=True) as db:
        db.execute("UPDATE candles SET volume = volume + 1 WHERE dataset_id = ?", (dataset,))
    with pytest.raises(ValueError, match="normalized candle differs"):
        verify_research_run(store, run)


def test_cli_and_private_backup_use_explicit_research_audit(tmp_path: Path) -> None:
    store, dataset, _ = prepare_synthetic_research_sample(tmp_path)
    run = build(store, dataset, "backtest")
    command = [str(Path(executable).with_name("aurum")), "--db", str(store.path)]
    before = store.path.read_bytes()
    output = subprocess.run(
        [*command, "verify-run", run], check=True, capture_output=True, text=True
    )
    assert json.loads(output.stdout) == verify_research_run(store, run)
    assert store.path.read_bytes() == before
    summary = verify_store(store, verify_research=True)
    normalized = summary["normalized"]
    assert isinstance(normalized, dict) and normalized["research_runs_verified"] == 1
    destination = tmp_path / "synthetic-verified-research.sqlite"
    verified = backup_store(store, destination, verify_research=True)
    normalized = verified["normalized"]
    assert isinstance(normalized, dict)
    assert normalized["research_events_verified"] == BASELINES["backtest"][1]
    with store.connection(write=True) as db:
        db.execute("DELETE FROM structure_events WHERE run_id = ? AND sequence = 1", (run,))
    # Input normalization alone retains its existing, explicitly narrower scope.
    normalized = verify_store(store, verify_normalized=True)["normalized"]
    assert isinstance(normalized, dict) and normalized["datasets_verified"] == 1
    rejected = tmp_path / "synthetic-rejected-research.sqlite"
    with pytest.raises(ValueError, match="failed journal audit"):
        backup_store(store, rejected, verify_research=True)
    assert not rejected.exists() and not list(tmp_path.glob(".aurum-backup-*"))


def test_reconstruction_uses_ordered_prints_for_the_original_backtest_variant(
    tmp_path: Path,
) -> None:
    store, _, dataset = prepare_synthetic_print_replay(tmp_path)
    run = store.build_strategy_run(
        dataset,
        TIMEFRAME,
        STRUCTURE,
        STRATEGY,
        execution=DEFAULT_EXECUTION_CONFIG,
        resolve_with_trade_prints=True,
    )
    assert verify_research_run(store, run)["verified"] is True


def test_reconstruction_releases_readers_during_computation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, dataset, _ = prepare_synthetic_research_sample(tmp_path)
    run = build(store, dataset, "backtest")
    paused, resume = Event(), Event()
    original = ResearchReplay.events

    def hold(self: ResearchReplay, *args: Any, **kwargs: Any) -> Any:
        for event in original(self, *args, **kwargs):
            if not paused.is_set():
                paused.set()
                assert resume.wait(5)
            yield event

    monkeypatch.setattr(ResearchReplay, "events", hold)
    with ThreadPoolExecutor(1) as executor:
        future = executor.submit(verify_research_run, store, run)
        try:
            assert paused.wait(5)
            store.save_analysis("synthetic-independent-write", {}, {})
        finally:
            resume.set()
        assert future.result(timeout=5)["verified"] is True


@pytest.mark.parametrize("minutes", (1, 5030), ids=("empty-journal", "multiple-pages"))
def test_empty_journal_and_bounded_pages_reconstruct(tmp_path: Path, minutes: int) -> None:
    source = tmp_path / "synthetic-audit-pages.csv"
    rows = ["timestamp,contract,open,high,low,close,volume"]
    sessions = []
    for offset in range(0, minutes, 1000):
        start = START + timedelta(days=offset // 1000)
        length = min(1000, minutes - offset)
        window = TradingWindow(start, start + length * MINUTE)
        sessions.append(TradingSession(start.date(), (window,)))
        for minute in range(length):
            bar = (offset + minute) // 5
            price = 29000 + bar // 10 + (10 if bar % 2 else 0)
            rows.append(
                f"{(start + minute * MINUTE).isoformat()},{CONTRACT.code},"
                f"{price / 10:.1f},{(price + 2) / 10:.1f},{(price - 2) / 10:.1f},{price / 10:.1f},2"
            )
    source.write_text("\n".join(rows) + "\n")
    span = TradingWindow(sessions[0].start, sessions[-1].end)
    calendar = SessionCalendar(
        "synthetic audit window",
        "synthetic-v1",
        span,
        sessions=tuple(sessions),
    )
    config = ImportConfig("synthetic", CONTRACT, "UTC", TimestampLabel.OPEN, DataKind.SYNTHETIC)
    store = DatasetStore(tmp_path / "synthetic-audit-pages.sqlite")
    dataset = store.save(prepare_csv(source, config, calendar))
    run = store.build_structure_run(dataset, TIMEFRAME, STRUCTURE)
    report = verify_research_run(store, run)
    assert report["verified"] is True
    if minutes == 1:
        assert report["event_count"] == 0
    else:
        count = report["event_count"]
        assert isinstance(count, int) and count > 1000


def test_journal_append_during_reconstruction_requires_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, dataset, _ = prepare_synthetic_research_sample(tmp_path)
    run = build(store, dataset, "backtest")
    original = ResearchReplay.events
    inserted = False

    def changed(self: ResearchReplay, *args: Any, **kwargs: Any) -> Any:
        nonlocal inserted
        for event in original(self, *args, **kwargs):
            if not inserted:
                with store.connection(write=True) as db:
                    db.execute(
                        "INSERT INTO structure_events VALUES (?, ?, ?)",
                        (
                            run,
                            BASELINES["backtest"][1] + 1,
                            '{"event":"synthetic_concurrent_append"}',
                        ),
                    )
                inserted = True
            yield event

    monkeypatch.setattr(ResearchReplay, "events", changed)
    with pytest.raises(ValueError, match="bound changed"):
        verify_research_run(store, run)
