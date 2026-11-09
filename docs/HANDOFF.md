# AURUM — DEVELOPMENT HANDOFF

## Project Status

- Current phase — Phase 1, repository and data foundation.
- Current milestone — D01 CSV import/replay and D02 dated exchange calendars are complete. D03 aggregation is next. D04 reconciliation with an authorized export remains blocked.
- Current branch — `main`.
- Last verified commit — `1eaad0e9c0986f769a40232b20d40af6989765a7`. A handoff-only update may be ahead of the code checkpoint.

## Current Objective

- Task ID — D03.
- Objective — Aggregate validated 1-minute candles into exchange-aware 5m, 15m, 1h, 4h and daily candles through a shared incremental path.
- Acceptance criteria — Each published aggregate is complete and available only at its close. Calendar v2 supplies exchange trade dates and stable full session boundaries. Intraday buckets respect session windows and closures. Daily bars combine all expected open windows of a trade date. Partial export coverage and missing source candles cannot produce complete-looking aggregates. Replay and equivalent streaming inputs produce identical results. Document and test the chosen interval alignment and shortened-bucket policy.

## Completed Work

- D01 — The CLI validates one dated unadjusted MGC contract with 1-minute OHLCV per CSV, preserves original bytes and immutable provenance in SQLite, and replays chronological candle-close events. Missing candles block replay. Source validation covers contract, prices, volume, calendar and UTC timestamps. See `src/aurum/market_data.py`, `src/aurum/storage.py` and `tests/test_data_pipeline.py`.
- D02 — Calendar schema v2 groups full, UTC-normalized open windows by an explicit exchange trade date. The importer intersects the windows with dataset coverage and stores the full schedule. The CLI derives `trade_date` from each candle open for dated datasets. Legacy snapshots retain their serialized form and event shape. SQLite schema v1 required no migration.
- D02 verification — `tests/test_exchange_calendar.py` and the integration tests cover evening opens, maintenance, weekends, split sessions, early closes, holiday closures, DST divergence, coverage clipping and dated replay. An SQLite dataset created by the D01 CLI had identical inspect, replay and re-import output after D02.

## Work In Progress

- Current files — D03 has no implementation. Start with `src/aurum/exchange_calendar.py`, `src/aurum/market_data.py` and `src/aurum/storage.py`. The planned aggregation module is `src/aurum/candles.py`.
- Implemented foundations — `SessionCalendar.from_dict` reconstructs a stored calendar from dataset provenance. `sessions` holds full windows with their exchange dates for v2, and `windows` holds the importable intersections. `session_at` looks up the dated session for a covered candle open. `DatasetStore.replay` yields validated 1-minute `Candle` objects with `opened_at` and `available_at`.
- Remaining portions — Set an explicit bucket policy, build the incremental aggregation and its replay entry point, then verify gap handling, session and bucket boundaries, close-time availability and parity. Structure detection starts after D03.

## Important Decisions

- ADR-001 — Use local transactional SQLite for the data foundation.
- ADR-002 and ADR-003 — Require independently reviewed calendars, preserve source bytes and versioned identity, and block incomplete replay.
- ADR-004 — Preserve full session windows separately from clipped import coverage. Use explicit exchange trade dates without inferring them from civil dates or generic weekly rules. Keep the legacy calendar and replay format unchanged. Read the ADR before choosing aggregation boundaries.
- Calendar authority — Regular hours and exchange trade-date conventions were checked against CME. Effective holiday and halt windows are supplied by the caller. See the source links in `docs/VALIDATION.md`.

## Known Issues

- DATA-001 — High, blocked. The workspace has no authorized MGC export or production-reviewed schedule for reconciliation.
- CAL-002 — Medium, open. The caller verifies effective schedule coverage and trade dates. The CLI currently ingests the supplied snapshot.
- DATA-002 — Medium, open. A vendor export that omits no-trade minutes triggers gap detection under the current coverage rule.
- DATA-003 — Medium, open. Imports buffer CSV bytes and normalized candles. Use bounded exports.

## Test Status

- Passed — `.venv/bin/python -m pytest -q` (43), `.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`, `.venv/bin/mypy .` (9 files) and `.venv/bin/python -m pip check`. Manual `cmp` checks confirmed compatibility with the previous checkpoint.
- Failed — No outstanding failures.
- Not run — Authorized historical reconciliation, production schedule validation, aggregate replay/stream parity and all later strategy, backtest and monitoring checks.
- Limitations — Market observations and calendar exceptions in fixtures are synthetic. No strategy results or trade outcomes have been measured.

## Environment

- Runtime — Python 3.12.7 on macOS arm64, SQLite 3.45.3. SQLite 3.37+ is required.
- Setup — `python3.12 -m venv .venv` and `.venv/bin/python -m pip install -e '.[dev]'`.
- Required services — None for import and replay. Store local source files in the ignored `data/` directory.
- Missing configuration — An authorized export and reviewed schedule for D04. Neither is required to test D03 with labeled synthetic fixtures.

## Immediate Next Actions

1. Run the checkpoint suite. Read the D03 acceptance and inspect `TradingSession`, `SessionCalendar.session_at`, `Candle` and the replay iterator. Use the full v2 session windows for bucket anchoring.
2. Specify interval alignment and the treatment of a final shortened intraday bucket. Separately model a scheduled early close and an export ending before a scheduled close. Decide how daily aggregation waits for every expected open minute across a halt.
3. Implement a shared incremental consumer with tests for OHLCV, availability, missing input, duplicate or out-of-order delivery, coverage edges, DST, closures and replay/stream equivalence. Keep the existing raw replay output stable. Update the handoff after verification.

## Do Not Repeat

- Repository initialization, the D01 data path and D02 dated calendar work are verified.
- Avoid guessing exchange closures from absent bars or adding dates to legacy datasets from UTC or Chicago civil dates.
- Keep synthetic fixtures and unverified account assumptions separate from empirical claims.

## Continuation Instruction

Resume from the latest verified checkpoint. Inspect the referenced implementation, complete the next unblocked task, run relevant tests, and update the handoff.
