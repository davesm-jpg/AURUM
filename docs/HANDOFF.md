# AURUM — DEVELOPMENT HANDOFF

## Project Status

- Current phase — Phase 1 data foundation is implemented through D03. Historical source reconciliation D04 is blocked and P0 market structure S01 is next.
- Current milestone — D03 incremental exchange-aligned candle aggregation.
- Current branch — `main`.
- Last verified commit — `69ad81a14d02d3c1b24b670508cddf094e707870`.

## Current Objective

- Task ID — S01.
- Objective — Detect confirmed pivots, internal/external structure, BOS and MSS incrementally from completed candles. Persist versioned evidence.
- Acceptance criteria — Use only information available at the event timestamp. Pivot and confirmation times are distinct. Structure events disclose their source candles, direction and prior context, rule configuration, displacement, break and invalidation evidence. Identical input prefixes produce identical events when future candles change. Persistent runs are reproducible.

## Completed Work

- D01 — CLI validates and stores dated unadjusted 1m MGC OHLCV and preserves the source bytes. Missing expected candles block replay. SQLite writes and idempotent imports are tested.
- D02 — Dated calendar snapshots preserve full open windows and explicitly assigned exchange trade dates. The importer clips expected bars to export coverage. Legacy datasets retain their IDs and candle-close output.
- D03 — `src/aurum/candles.py` aggregates completed 1-minute bars to 5m, 15m, 1h, 4h and daily through one incremental consumer. The CLI's `aggregate` command emits stable IDs, source-minute counts, trade date and a flag for scheduled shortened intraday buckets.
- D03 evidence — `tests/test_candles.py` verifies OHLCV, exact close availability, halts, export clipping, missing or rejected candles, DST, all supported intervals and CLI/incremental parity on synthetic input. See `docs/VALIDATION.md`.

## Work In Progress

- Current files — S01 implementation has not started. Reuse `CandleAggregator` and `AggregateCandle` in `src/aurum/candles.py`, and the immutable SQLite dataset and replay path in `src/aurum/storage.py`.
- Implemented foundations — The aggregator accepts strictly increasing 1m closes for a dated contract and returns higher-timeframe bars only at their scheduled close. Each aggregate can be traced to the dataset and its UTC opening boundary and timeframe.
- Remaining portions — Define versioned structure rules and causal event ordering, implement confirmed pivots and structure classification, then implement BOS/MSS and persist a versioned structure run. Do not mark S01 done until persistence and reproducibility are exercised.

## Important Decisions

- ADR-001 through ADR-003 — Use a local transactional SQLite store, immutable provenance and reviewed schedule coverage. Block historical replay on missing candles.
- ADR-004 — Separate full trading windows from export coverage and use explicit exchange trade dates.
- ADR-005 — Anchor intraday buckets at the open window, restart after closures, and label a scheduled shortened final bucket. Daily bars include every open window of the trade date. Omit incomplete buckets.

## Known Issues

- DATA-001 — High, blocked. Authorized real MGC history and a production-reviewed schedule are absent.
- CAL-002 — Medium, open. Exchange schedules and dates are caller supplied and independently reviewed.
- DATA-002 — Medium, open. A sparse export that omits no-trade minutes triggers the gap gate.
- DATA-003 — Medium, open. Import buffers source and normalized bars in memory.

## Test Status

- Passed — `.venv/bin/python -m pytest -q` (50), `.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`, `.venv/bin/mypy .` (11 files), dependency and diff checks.
- Failed — None outstanding.
- Not run — Authorized source reconciliation, live feed parity, strategy backtesting and paper simulations.
- Limitations — Parity compares a replayed fixture with incremental delivery of equivalent synthetic input. No live data connection or research result exists.

## Environment

- Runtime — Python 3.12.7, macOS arm64, SQLite 3.45.3. SQLite 3.37+ is required.
- Setup — `python3.12 -m venv .venv` and `.venv/bin/python -m pip install -e '.[dev]'`.
- Required services — None. Use ignored `data/` for local sources.
- Missing configuration — D04 needs an authorized CSV export and reviewed schedule for the same range. S01 can proceed with synthetic tests.

## Immediate Next Actions

1. Read the canonical pivots/BOS/MSS requirements and rerun the checkpoint suite. Define a pure incremental engine consuming a single timeframe and contract.
2. Implement confirmed internal and external swings and state classification with explicit tie handling and actual availability. Add prefix and future-mutation tests. Follow with qualifying breaks against previously confirmed levels and prior-context MSS.
3. Persist evidence and configuration in a versioned structure run and verify the normal replay entry point. Update documentation and handoff at each verified slice.

## Do Not Repeat

- D01–D03 are verified. Do not re-create the importer, calendar or aggregation paths.
- Keep chart and strategy consumers on completed bars and retain the `shortened` attribute.
- Keep empirical claims separate from synthetic fixtures.

## Continuation Instruction

Resume from the latest verified checkpoint. Inspect the referenced implementation, complete the next unblocked task, run relevant tests, and update the handoff.
