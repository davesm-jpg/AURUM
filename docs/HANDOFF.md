# AURUM — DEVELOPMENT HANDOFF

## Project Status

- Current phase — Phase 2 market structure. D01–D03 and S01 are complete. D04 authorized source reconciliation remains blocked.
- Current milestone — S01 confirmed pivots, BOS/MSS and persisted structure evidence.
- Current branch — `main`.
- Last verified commit — `26e2ac9ea8639e88eeae1c99ad6cb671a293c948`.

## Current Objective

- Task ID — S02.
- Objective — Add causal liquidity proxies, sweeps, FVGs and IFVGs on the same aggregate-close path.
- Acceptance criteria — Versioned detectors preserve source evidence, emit only at actual confirmation, and record state changes without rewriting past events. Tests cover mirrored definitions, ties and boundaries, intrabar ambiguity, chronology, expiry and replay. Required mapping and lifecycle requirements are in sections VI and VII of the specification.

## Completed Work

- D01–D02 — The CLI validates, stores and replays dated unadjusted MGC 1m data with source bytes, immutable provenance, quality gates and full dated session windows. The older undated input and replay remain supported.
- D03 — The shared `CandleAggregator` produces completed 5m, 15m, 1h, 4h and daily bars on dated exchange boundaries. It flags scheduled shortened intraday bars and omits incomplete buckets.
- S01 — `src/aurum/market_structure.py` implements strict confirmed pivots, independent internal/external states, wick/close/ATR-buffered/displacement breaks and opposite-context MSS. It records pivot and confirmation times, source prices, prior ATR and body evidence, and the invalidation pivot for MSS.
- S01 persistence — `structure` builds an immutable run, `structure-events` reads ordered evidence. SQLite v2 migrates on write and retains existing read paths. The exact rule configuration, source dataset and implementation versions determine run identity. Writes are atomic and repeatable.
- S01 verification — `tests/test_market_structure.py` tests causal ordering, future mutations, both directions, ambiguous two-way wick breaks, prior ATR warmup, real CLI integration, migration and persistence rollback. A database from the original implementation retained identical replay and provenance after migration.

## Work In Progress

- Current files — S02 implementation is pending. Reuse `src/aurum/market_structure.py`, `src/aurum/candles.py` and `src/aurum/storage.py`. Do not alter recorded S01 event semantics.
- Implemented foundations — `StructureEngine.push` returns typed swing, trend and break events at aggregate closes. `CandleAggregator.consume` yields completed bars at requested intervals. Structure scope and pivot origin can feed a liquidity mapper after the confirming candle has been processed.
- Remaining portions — Implement imbalance definitions and lifecycle, then confirmed liquidity mapping and sweeps. Integrate replay and versioning. S03 follows with contextual filters, strategy state, sizing and scoring.

## Important Decisions

- ADR-004/005 — Use explicit exchange dates and full open windows. Clip only input coverage, restart intraday buckets at open-window boundaries and label scheduled shortened bars. Daily bars span every open window.
- ADR-006 — Strict pivots require every right bar, breaks use previously available pivots, and an MSS requires an opposing trend, a single direction and a valid structural invalidation. Do not assume intrabar order. Current defaults are internal 3/3, external 5/5 and close breaks.
- Database — SQLite is local. Runs and events are persisted in one transaction. Older datasets remain readable and migrate on the next write. Source timestamps, run dataset and timeframe identify aggregates without storing copies.

## Known Issues

- DATA-001 — High, blocked. No authorized MGC export and reviewed schedule were supplied for real-data checks.
- CAL-002 — Medium, open. The caller supplies exchange windows and confirms their effective coverage.
- DATA-002/DATA-003 — Medium, open. Sparse no-trade exports block replay and imports buffer the source and normalized bars.
- Live monitoring and execution modeling have yet to be implemented. The code is for paper research.

## Test Status

- Passed — `.venv/bin/python -m pytest -q` (66), `.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`, `.venv/bin/mypy .` (13 files), dependency checks and Git diff checks. Old SQLite migration passed byte comparisons and the SQLite integrity check.
- Failed — None outstanding.
- Not run — Authorized real-data reconciliation, production schedule validation, live provider parity and later strategy/backtest/simulation checks.
- Limitations — All test observations are synthetic. No trading performance has been measured.

## Environment

- Runtime — Python 3.12.7 on macOS arm64, SQLite 3.45.3. Requires SQLite 3.37+.
- Setup — `python3.12 -m venv .venv` and `.venv/bin/python -m pip install -e '.[dev]'`.
- Required services — None. Keep source files in ignored `data/`.
- Missing configuration — Authorized historical export and applicable reviewed schedule for D04.

## Immediate Next Actions

1. Rerun the checkpoint suite and read specification sections VI and VII. Start with the three-candle FVG rule, confirmation at candle C's close and mirrored inversion. Decide how to handle intervals separated by an exchange closure.
2. Add imbalance lifecycle events with first touch, partial/full fill, inversion, retest and reaction. Implement causal liquidity levels and reclaim detection. Newly confirmed levels cannot be swept by the bar that confirms them.
3. Add evidence replay and versioned persistence, exercise mirrored and anti-look-ahead fixtures, then update the handoff before starting S03.

## Do Not Repeat

- D01–D03 and S01 are verified. Keep the existing replay, aggregate and structure modes stable.
- Do not use synthetic data as historical evidence or make profitability claims.
- Do not infer hidden orders, holiday schedules or unavailable event order from candle data.

## Continuation Instruction

Resume from the latest verified checkpoint. Inspect the referenced implementation, complete the next unblocked task, run relevant tests, and update the handoff.
