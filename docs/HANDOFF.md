# AURUM — DEVELOPMENT HANDOFF

## Project Status

- Current phase — Phase 3 paper setup qualification is implemented. Phase 4 backtest engine B01 is unblocked using synthetic fixtures. D04 and B02 real-data validation need an authorized source.
- Current milestone — S03 context, exact sizing and versioned paper strategy replay.
- Current branch — `main`.
- Last verified commit — the S03 implementation commit containing this handoff.

## Current Objective

- Task ID — B01.
- Objective — Simulate minute-level paper fills and outcomes from the same causal strategy replay, with versioned costs, audit evidence and performance statistics.
- Acceptance criteria — A setup can fill only at a later eligible minute open under the chosen latency rule. Open gaps, stops, targets, ambiguous OHLC ordering, costs, exchange windows and end of data have explicit policies. Fills preserve the structural stop and whole-contract risk constraints. Replays are reproducible and include sample-aware net statistics. No empirical historical conclusions are permitted without B02.

## Completed Work

- D01–D03 — Import and replay validated unadjusted MGC 1m CSV, retain exact source and immutable provenance, use reviewed dated exchange sessions and aggregate completed 5m/15m/1h/4h/daily candles. Missing expected candles block replay.
- S01–S02 — Causal confirmed pivots, BOS/MSS, price proxies, sweeps and FVG/IFVG lifecycle on a shared incremental evidence stream. SQLite retains versioned manifests and ordered events. Existing source databases migrate on write.
- S03 strategy — `strategy.py` implements seven independent variants, setup expiry/invalidation, strictly earlier sweep, MSS, qualifying zone and later retest/reaction, configurable filters and an experimental 100-point score. Every transition records source evidence and context. It emits hypothetical plans only.
- S03 risk/context — `paper_risk.py` uses integer ticks/cents, whole contracts, a USD 50 gross cap by default, optional all-in cap, costs and separate structural versus fixed targets. `market_context.py` provides completed 15m/1h/4h context, confirmed structural range, regime features and regional time windows. `economic_events.py` supports point-in-time reviewed snapshots and reports unknown without coverage.
- S03 persistence — `strategy DATASET_ID` writes atomic versioned transitions, and `strategy-events RUN_ID` reads them. Unfinished historical state can be reconstructed by replaying its input. Live checkpointing is pending.

## Work In Progress

- Current files — Backtest implementation is pending. Reuse `StrategyReplay.push`, `SetupEvent`, `RiskPlan`, `Candle`, `SessionCalendar` and the existing data store. Start with a pure execution module and synthetic fixtures.
- Implemented foundations — Each minute can yield paper setup signals after its close. Signals include direction, stop, target, quantity, actual availability, context and score. The store has a shared private `_persist_replay` path for immutable manifests and ordered events.
- Remaining portions — Define minute fill rules and paper costs, then implement and verify the simulator and metrics. Add CLI integration and persisted audit output. Statistical tools follow B01. D05 adapter/contract work and B02 actual history remain separate.

## Important Decisions

- ADR-004/005 — Exchange trade dates and full windows are distinct from dataset coverage. Intraday buckets restart after closures. Daily bars use every expected open window.
- ADR-006/007 — Evaluate previously known levels before publishing new pivots or proxies. Preserve gap/sweep confirmation and ordering explicitly. OHLCV cannot show hidden liquidity or intrabar order.
- ADR-008 — Allow same-close MSS and zone evidence, require a later retest/reaction, and preserve the outermost structural stop with configured tick buffer. Qualification is separate from fills and experimental scores are not probabilities.
- Costs — Illustrative USD 1.60 commission and one round-trip slippage tick per contract. The optional net cap is separate from the default gross cap.

## Known Issues

- DATA-001 — High, blocked. No authorized MGC export and production-reviewed schedule are available.
- CAL-002, DATA-002 and DATA-003 — Reviewed calendar is caller supplied. Sparse no-trade exports block replay. Imports buffer source bytes and candles.
- EVENT-001 — Event coverage is unknown without a reviewed snapshot. An authorized production feed is pending.
- Live market, delivery and actual account execution are absent. Only paper research is within scope.

## Test Status

- Passed — `.venv/bin/python -m pytest -q` (90), Ruff lint and format, mypy (26 files), dependency and Git whitespace checks. The tests include mirrored strategy CLI replay, prefix stability, risk and event-calendar validation.
- Failed — None outstanding.
- Not run — Authorized historical reconciliation, live feed parity, empirical strategy outcomes, backtests or account simulation.
- Limitations — All tests use synthetic market data or explicit unknown context.

## Environment

- Runtime — Python 3.12.7 on macOS arm64, SQLite 3.45.3. Requires SQLite 3.37+.
- Setup — `python3.12 -m venv .venv`, then `.venv/bin/python -m pip install -e '.[dev]'`.
- Required services — None for current CLI. Keep authorized input under ignored `data/`.
- Missing configuration — Authorized history and reviewed effective exchange schedule for D04/B02. B01 can be developed and tested offline.

## Immediate Next Actions

1. Run the checkpoint suite and read specification sections XI–XII. Build a pure execution engine receiving completed minutes and paper signals in chronology. Process any prior orders at the eligible minute open before observing current-minute strategy events.
2. Test gap entries and exits, both stop and target in a bar, missed entries across scheduled closures, costs, risk caps, marking and terminal outcomes. Document close-based outcome timestamps and excursion assumptions.
3. Implement sample-aware performance and persistent backtest evidence, then run the CLI end to end on synthetic inputs. Update the roadmap and handoff before moving to statistical validation.

## Do Not Repeat

- D01–D03 and S01–S03 are verified. Reuse the shared evidence and strategy paths.
- Keep synthetic replay separate from real performance. Preserve structural stops and apply conservative simulated fills.
- Do not infer calendar status, unseen data or real provider access from local tests.

## Continuation Instruction

Resume from the latest verified checkpoint. Inspect the referenced implementation, complete the next unblocked task, run relevant tests, and update the handoff.
