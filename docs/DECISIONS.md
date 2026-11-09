# Architecture decision records

## ADR-001 — Local vertical slice before service dependencies

- Date 2026-11-08.
- Context. The initial workspace was empty. Import and replay require deterministic offline verification.
- Alternatives. Start with a PostgreSQL/FastAPI deployment, use file-only persistence, or use a local transactional store.
- Decision. Use a Python 3.12+ package with SQLite v1 and a CLI for the first slice.
- Rationale. Transactions, indexes and versioned schema are available without external services.
- Consequences. This checkpoint has local deployment and SQLite's concurrency model. Multi-client deployment will require a service and storage decision.
- Files. `pyproject.toml`, `src/aurum/storage.py`, `src/aurum/cli.py`.

## ADR-002 — Caller-supplied reviewed schedule with bounded coverage

- Date 2026-11-08.
- Context. Weekly hours alone cannot explain holidays, early closes or halts and can mislabel feed gaps.
- Alternatives. Infer closures from observed candles, embed regular hours, or accept versioned explicit windows.
- Decision. Require ordered UTC-normalized open windows and explicit expected coverage. Omitted times inside coverage represent closures as attested by the snapshot. Pin timezone rules for naive CSV timestamps.
- Rationale. This makes the schedule assumption visible and reproducible, and detects missing first/last bars independently of observed bounds.
- Consequences. Snapshot quality must be reviewed externally. Future schedule work must distinguish exchange trading dates and preserve previously imported snapshots.
- Files. `src/aurum/exchange_calendar.py`, `src/aurum/timestamps.py`, `src/aurum/market_data.py`.

## ADR-003 — Immutable source identity and fail-closed replay

- Date 2026-11-08.
- Context. Dated and adjusted data can appear similar. Exports can be duplicated, incomplete or revised.
- Alternatives. Identify a series only by symbol/time, replace prior imports, or version each source/configuration combination.
- Decision. Require unadjusted dated MGC data for this path. Retain raw source by checksum, version provenance and store integer ticks. Reject malformed/duplicate/off-calendar input, sort and disclose disorder, and store gaps while blocking replay. Report zero volume without treating it alone as a gap.
- Rationale. The evidence needed for repeatable replay survives restarts and source revisions. Missing minutes cannot reach the research path.
- Consequences. Identical re-imports are idempotent. Correcting source or calendar inputs yields a new dataset. Sparse exports that omit no-trade minutes need a future explicit policy.
- Files. `src/aurum/instruments.py`, `src/aurum/market_data.py`, `src/aurum/storage.py`.

## ADR-004 — Date full sessions independently of import coverage

- Date 2026-11-09.
- Context. A single exchange trade date can begin on a previous civil day and contain several open intervals. Clipping the source windows at import boundaries would lose the information needed to anchor and complete aggregate bars.
- Alternatives. Infer the trade date from local time, store only clipped dated windows, or preserve full sessions with separately bounded coverage.
- Decision. Calendar schema v2 preserves full UTC-normalized open windows under an explicit exchange trade date. Use coverage to constrain expected source bars. Preserve the legacy format and JSON identity for existing datasets.
- Rationale. An authoritative schedule can assign holiday trade dates and exceptions. Full windows retain the information needed to distinguish partial coverage from a shortened exchange session.
- Consequences. The caller supplies and reviews the schedule. Dated snapshots add the trade date at replay from provenance without a database migration. A legacy dataset needs a dated snapshot before it can support exchange-session aggregation.
- Files. `src/aurum/exchange_calendar.py`, `src/aurum/cli.py`, `tests/test_exchange_calendar.py`, `tests/test_data_pipeline.py`.

## ADR-005 — Anchor aggregates to open windows and session close

- Date 2026-11-09.
- Context. Import coverage, early closes and halts can cut through nominal candle intervals.
- Alternatives. Use UTC clock buckets, use one clock anchored to the full session across a halt, or anchor intraday buckets to the open windows.
- Decision. Start 5m, 15m, 1h and 4h buckets at the full open-window start and reset at the next window. Publish the shortened final bucket at the window close when all of its expected minutes exist and flag it as shortened. Combine all open windows for daily bars. Require the scheduled closing minute and every expected open minute, independent of export coverage.
- Rationale. Bucket boundaries are reproducible across imports and intraday bars do not bridge a closure. The shortened flag lets downstream research select which bars qualify.
- Consequences. Chart boundaries may differ from vendor clock buckets, especially after a halt. Historical replay rejects a dataset with gaps. The incremental consumer rejects disordered input and suppresses any bucket missing an expected minute.
- Files. `src/aurum/candles.py`, `src/aurum/cli.py`, `tests/test_candles.py`.

## ADR-006 — Point-in-time pivots and structure breaks

- Date 2026-11-09.
- Context. Plausible pivot, break and MSS definitions can produce different histories. Research needs to reproduce each rule.
- Alternatives. Mark pivots retrospectively, use unconfirmed levels, or evaluate only completed candles against previously confirmed levels.
- Decision. Use strict local pivots with 3/3 internal and 5/5 external defaults. Equality inside a pivot window rejects a candidate. Separately confirmed pivots at equal prices receive equal labels. Compare the latest pair of highs and lows per scope. Before confirming new pivots, evaluate and consume previously confirmed levels once per rule. Buffered and displacement rules use a simple mean of the full prior true-range window. Label a qualifying single-direction break as MSS only with opposite prior structure and a close on the valid side of the latest opposing pivot. After MSS, keep the transition state until a high and low are confirmed strictly later.
- Rationale. Event timestamps, strict ordering and exact integer thresholds make outputs reconstructable and avoid future data and same-candle eligibility. A candle crossing both directions can report two BOS events without guessing their order.
- Consequences. This is rule version 1. Latest confirmed levels supersede older levels, and completed shortened bars count in the pivot and ATR windows. SQLite v2 adds atomic, idempotent structure-run persistence. The CLI defaults to close-based breaks and exposes each rule's thresholds.
- Files. `src/aurum/market_structure.py`, `src/aurum/storage.py`, `src/aurum/cli.py`, `tests/test_market_structure.py`.
