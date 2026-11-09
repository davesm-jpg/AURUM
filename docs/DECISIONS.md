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
