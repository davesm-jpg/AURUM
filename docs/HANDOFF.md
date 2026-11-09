# AURUM — DEVELOPMENT HANDOFF

## Project Status

- Current phase — Phase 4 backtest engine is implemented. Q01 statistical tooling is next. D04 and B02 require authorized historical MGC.
- Current milestone — B01 audited minute-level paper backtesting.
- Current branch — `main`.
- Last verified commit — the B01 implementation commit containing this handoff.

## Current Objective

- Task ID — Q01.
- Objective — Add chronological validation and held-out evaluation, dependence-aware resampling, walk-forward analysis and Monte Carlo scenarios on persisted backtests.
- Acceptance criteria — Never use the final holdout to select variants. Purge trades whose label interval crosses a split boundary and support an embargo. Record source, versions, dates, cost assumptions, exact samples and seeds. Report uncertainty and sample limits. Keep synthetic tests, conditional scenarios and historical conclusions distinct.

## Completed Work

- D01–D03 — Authorized-format single-contract MGC CSV import and replay, immutable raw provenance, dated windows and incremental 5m/15m/1h/4h/1d aggregation. Missing expected minutes block replay.
- S01–S02 — Confirmed pivots, BOS/MSS, liquidity proxies, sweeps and FVG/IFVG lifecycle in the shared causal evidence path. Structure/evidence manifests and ordered audit are stored in SQLite.
- S03 — Mirrored research variants, contextual measurements, event uncertainty, state transitions, structural paper sizing and experimental scoring. `strategy` and `strategy-events` persist and replay setup history.
- B01 execution — `execution.py` processes pending entries and existing positions before current close evidence. It rechecks whole-contract sizing at the eligible open, preserves structural brackets, models costs and flags ambiguous exits. Gap stops use the adverse open. Target gaps use the target. Default closure policy flattens at the scheduled window end. Forced end-of-data closes are identified.
- B01 persistence and metrics — `backtest`, `backtest-summary` and `backtest-events` store/retrieve versioned setup, order, trade and summary evidence. `performance.py` reports exact sample counts, realized net metrics, closed-trade equity/drawdown, excursions and breakdowns. Fixtures include a full synthetic end-to-end strategy and fill.

## Work In Progress

- Current files — Q01 has no implementation. Use `src/aurum/performance.py`, `backtesting.py`, `storage.py` and `tests/test_execution.py`.
- Implemented foundations — `DatasetStore.describe_backtest` returns dataset, strategy, cost and execution versions plus summary. `structure_events` yields ordered audit. `paper_trade_completed` contains entry and exit/recorded times, exchange date, costs, returns, regime and direction. `paper_setup_transition` contains signal time and score.
- Remaining portions — Extract typed observations, define comparable experiments and chronological boundaries, then implement resampling and scenario reporting. Q02 provider-specific account simulation requires current official verification. D05 adapter and contract work remains pending.

## Important Decisions

- ADR-004 through ADR-008 — Respect dated windows, full completed buckets, actual evidence availability and versioned rule/strategy context. The default USD 50 cap is gross with an optional net cap. Scores are experimental.
- ADR-009 — Separate chronological signal qualification from paper fills. Conservative OHLC fills do not infer intrabar order. The default is one position at a time. Fees and slippage are illustrative costs.
- Drawdown — Current performance uses closed-trade equity. The Wilson win-rate interval assumes independent outcomes and is descriptive. Q01 adds dependence-aware estimates. Q02 must separately model intraday breach where required.
- Reproducibility — Run manifests are content-addressed by input dataset and exact configs. Historical and synthetic labels are carried into each event.

## Known Issues

- DATA-001 — High, blocked. No authorized historical MGC sample or production-reviewed schedule was supplied. No empirical strategy result exists.
- CAL-002 and EVENT-001 — Reviewed exchange schedules and economic snapshots are supplied manually. Production feeds remain pending.
- DATA-002/DATA-003 — Sparse no-trade exports block replay. Import buffers source and normalized bars.
- Current execution assumes one dated contract, trade OHLCV and configured cash costs. Quote/tick replay and rollover require future adapter work.

## Test Status

- Passed — `.venv/bin/python -m pytest -q` (99), Ruff lint and format, mypy (30 files), dependency checks and Git whitespace checks.
- Failed — None outstanding.
- Not run — Real-data reconciliation, production providers, actual strategy performance, statistical validation or account simulation.
- Limitations — Synthetic input exercises fill and reporting logic only.

## Environment

- Runtime — Python 3.12.7 on macOS arm64 and SQLite 3.45.3. Requires SQLite 3.37+.
- Setup — `python3.12 -m venv .venv` and `.venv/bin/python -m pip install -e '.[dev]'`.
- Services — None. Keep authorized input in ignored `data/`.
- Missing configuration — Authorized MGC and reviewed exchange schedule for B02/D04. Q01 can be built and tested with labeled fixtures.

## Immediate Next Actions

1. Read specification sections XI–XIII and run the checkpoint tests. Define chronological splits using setup availability and outcome availability. Keep the final holdout completely separate from variant ranking.
2. Implement typed observation extraction, walk-forward selection, block-bootstrap uncertainty and scenario Monte Carlo. Test boundary leakage, zero trades, determinism and cost sensitivity.
3. Expose reproducible report commands and update docs. Verify current official Lucid evaluation/funded rules before implementing provider-specific Q02 defaults.

## Do Not Repeat

- D01–D03, S01–S03 and B01 are verified. Reuse shared replay, cost and audit logic.
- Do not optimize on final evaluation data or treat synthetic scenarios as observed wins.
- Keep real order execution and live brokerage credentials outside AURUM.

## Continuation Instruction

Resume from the latest verified checkpoint. Inspect the referenced implementation, complete the next unblocked task, run relevant tests, and update the handoff.
