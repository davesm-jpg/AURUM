"""Reproduce finalized research journals through the shared replay factory."""

import json
from collections.abc import Iterator
from datetime import datetime
from hashlib import sha256
from itertools import zip_longest

from aurum.dataset_audit import verify_dataset
from aurum.economic_events import EconomicSourceKind
from aurum.exchange_calendar import SessionCalendar, TradingWindow
from aurum.instruments import MgcContract
from aurum.market_data import DataKind, canonical_json
from aurum.research_replay import ResearchReplay
from aurum.storage import DatasetStore

RESEARCH_AUDIT_VERSION = 1


def _journal(store: DatasetStore, run_id: str, through: int) -> Iterator[tuple[int, str]]:
    after = 0
    while after < through:
        with store.connection() as db:
            rows = db.execute(
                """SELECT sequence, payload FROM structure_events
                   WHERE run_id = ? AND sequence > ? AND sequence <= ?
                   ORDER BY sequence LIMIT 1000""",
                (run_id, after, through),
            ).fetchall()
        if not rows:
            return
        for row in rows:
            yield row["sequence"], row["payload"]
        after = rows[-1]["sequence"]


def _bounds(store: DatasetStore, run_id: str) -> tuple[str, str, int, int]:
    with store.connection() as db:
        db.execute("BEGIN")
        schema = db.execute("PRAGMA user_version").fetchone()[0]
        if schema < 2:
            raise ValueError("store has no persisted research runs")
        run = db.execute(
            "SELECT definition, dataset_id FROM structure_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if run is None:
            raise ValueError(f"unknown research run: {run_id}")
        bounds = db.execute(
            "SELECT count(*) AS count, coalesce(max(sequence), 0) AS through "
            "FROM structure_events WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return run["definition"], run["dataset_id"], bounds["count"], bounds["through"]


def verify_research_run(
    store: DatasetStore, run_id: str, *, verify_source: bool = True
) -> dict[str, object]:
    """Audit a full finalized run, never invoked by configuration selection.

    Input/journal rows are application-immutable. Bounded reads release locks
    between pages; verify a private backup for an isolated database snapshot.
    Store-wide audits may skip repeated source reconstruction only after the
    corresponding dataset has already passed its independent source audit.
    """
    captured = _bounds(store, run_id)
    definition, dataset_id, count, through = captured
    manifest = json.loads(definition)
    if not isinstance(manifest, dict) or canonical_json(manifest) != definition:
        raise ValueError("research run definition is not a canonical object")
    if sha256(definition.encode()).hexdigest() != run_id:
        raise ValueError("research run identity differs from its frozen definition")
    if manifest.get("dataset_id") != dataset_id:
        raise ValueError("research run dataset differs from its definition")
    if count != through:
        raise ValueError("research journal sequence is not contiguous")
    replay = ResearchReplay.from_manifest(manifest)
    if verify_source:
        verify_dataset(store, dataset_id)
    dataset = store.describe(dataset_id)
    if not dataset["replayable"]:
        raise ValueError("research replay is blocked by input quality findings")
    provenance = dataset["provenance"]
    if (
        replay.economic is not None
        and replay.economic.data_kind is EconomicSourceKind.SYNTHETIC
        and provenance["data_kind"] != DataKind.SYNTHETIC
    ):
        raise ValueError("synthetic economic snapshots require synthetic research data")
    calendar = SessionCalendar.from_dict(provenance["calendar"])
    contract = MgcContract(str(provenance["contract"]))
    span = TradingWindow(
        datetime.fromisoformat(dataset["first_open"]),
        datetime.fromisoformat(dataset["last_close"]),
    )
    minutes = (candle for batch in store.replay_batches(dataset_id, span) for candle in batch)
    lookup = store.trade_print_lookup(dataset_id) if replay.resolve_with_trade_prints else None
    reconstructed = replay.events(calendar, contract, minutes, trade_prints=lookup)
    digest = sha256()
    compared = 0
    for sequence, (saved, event) in enumerate(
        zip_longest(_journal(store, run_id, through), reconstructed), start=1
    ):
        if saved is None:
            raise ValueError(f"research journal is missing reconstructed event {sequence}")
        if event is None:
            raise ValueError(f"research journal has an extra event at sequence {sequence}")
        actual_sequence, payload = saved
        if actual_sequence != sequence:
            raise ValueError(f"research journal sequence differs at {sequence}")
        expected = canonical_json(event.as_dict())
        # The producer stores canonical serialization. Exact equality also
        # rejects duplicate keys whose SQL and Python interpretations can differ.
        if payload != expected:
            raise ValueError(f"research journal differs from reconstruction at sequence {sequence}")
        digest.update(f"{sequence}\n{expected}\n".encode())
        compared += 1
    if compared != count or _bounds(store, run_id) != captured:
        raise ValueError("research journal bound changed during verification; retry the audit")
    return {
        "research_audit_version": RESEARCH_AUDIT_VERSION,
        "run_id": run_id,
        "dataset_id": dataset_id,
        "contract": contract.code,
        "data_kind": provenance["data_kind"],
        "timeframe": replay.timeframe,
        "event_count": compared,
        "canonical_journal_sha256": digest.hexdigest(),
        "verified": True,
        "scope": "full_finalized_research_journal_reconstruction",
        "input_validation": (
            "preserved_source_reconstruction" if verify_source else "stored_normalized_rows_only"
        ),
        "limitations": [
            "Integrity does not establish empirical strategy performance.",
            "Full-run audit metadata must not be used for holdout configuration selection.",
            "Analysis reports, monitor journals and forward outcomes are outside this audit.",
        ],
    }
