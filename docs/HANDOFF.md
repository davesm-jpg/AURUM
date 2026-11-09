# AURUM — DEVELOPMENT HANDOFF

## Project Status

- Current phase — Phase 2 market evidence is implemented. Phase 3 paper strategy S03 is the highest-priority unblocked research slice. D04 still needs an authorized source.
- Current milestone — S02 causal liquidity, sweep, FVG and IFVG replay.
- Current branch — `main`.
- Last verified commit — the S02 implementation commit containing this handoff.

## Current Objective

- Task ID — S03.
- Objective — Build the versioned paper setup engine on completed bars and typed evidence. Include contextual features, mirrored state transitions, risk feasibility, scoring and persistence.
- Acceptance criteria — All conditions are evaluated at their actual availability. The initial sweep → MSS → IFVG → reaction chain is mirrored and explainable, and experiment variants run independently. The strategy stores expiry, invalidation, state and configuration. Risk uses exact tick sizing and whole contracts. Context and score explain unknown inputs. Persist setup evidence with restart and replay tests.

## Completed Work

- D01–D03 — Local CSV import and replay, explicit dated exchange windows and incremental completed 5m/15m/1h/4h/daily OHLCV. Raw data and versioned provenance are immutable. Missing expected minutes block replay. Older undated replay retains its event shape.
- S01 — Causal confirmed pivots, internal/external structure, four BOS variants and qualifying MSS. The CLI builds deterministic, atomic SQLite runs. Prior schema migration preserves source datasets.
- S02 — `EvidenceEngine` combines structure, price-derived liquidity and gaps on the same completed-bar stream. Mapping covers confirmed swings, equal/repeated levels, rolling boundaries, completed day/week and Asia/London extremes. The lifecycle distinguishes touch, penetration, reclaim, timeout, follow-through, invalidation and expiry. An earlier valid sweep can link to an MSS with elapsed bars and time.
- S02 gaps — `ImbalanceEngine` detects strict adjacent three-candle FVGs and records formation, availability, depth, inversion, retest, reaction and expiry. It keeps original zone evidence. Consecutive closing invalidation and width thresholds are configurable.
- S02 persistence — `aurum structure DATASET_ID --evidence` stores extra versioned evidence in existing run tables. `structure-events` reads chronological events. The source is labeled synthetic or historical throughout.

## Work In Progress

- Current files for S03 — `src/aurum/evidence.py`, `candles.py`, `market_structure.py`, `sessions.py` and `storage.py`. Strategy and context modules are pending.
- Implemented portions — `EvidenceEngine.push` emits typed events at analysis closes. It owns selected and daily aggregators. `SweepStructureLink` includes the earlier eligible sweep and causal MSS. Gap and liquidity events carry stable IDs, source boundaries and availability.
- Remaining portions — Expose the analysis bar and event batch without changing current outputs. Build point-in-time higher-timeframe, premium/discount, regime, session and event-risk context, then implement strategy states, score, sizing and persisted paper research. No live account work is in scope.

## Important Decisions

- ADR-004/005 — Full dated windows control boundaries. Intraday aggregation restarts after closures and labels scheduled shortened buckets. Daily aggregation requires all open windows.
- ADR-006 — Breaks precede new pivots on each close. Structure version 1 uses strict extrema and prior ATR for volatility filters. Two-way wick breaks have no inferred intrabar order.
- ADR-007 — Price proxies publish after the selected close, require complete source windows and cannot be swept by their confirming bar. Gap tests are adjacent. An inverse zone starts only when the original invalidates. OHLCV measures sweep observation time, with no knowledge of exact time beyond the level.
- S02 parameters — Initial regional windows use Tokyo 09:00–15:00 and London 08:00–12:00 local. Full weeks need Chicago Sunday-to-Sunday coverage. They are explicit research assumptions.

## Known Issues

- DATA-001 — High, blocked. No authorized MGC sample and applicable production-reviewed schedule were supplied.
- CAL-002 — Medium, open. Effective exchange windows and dates are caller-reviewed inputs.
- DATA-002/DATA-003 — Medium, open. Sparse minute exports fail the gap gate. Import buffers the source and bars.
- No economic calendar provider, live market feed, paper execution or measured strategy performance exists.

## Test Status

- Passed — `.venv/bin/python -m pytest -q` (82), Ruff lint and format, mypy (21 files), dependency and Git whitespace checks. Tests include evidence replay through the CLI and earlier SQLite migration.
- Failed — None outstanding.
- Not run — Real-data reconciliation, live parity, strategy backtest or account simulation.
- Limitations — Every market test and exception fixture is synthetic. No empirical trading conclusion follows.

## Environment

- Runtime — Python 3.12.7 on macOS arm64, SQLite 3.45.3. Requires SQLite 3.37+.
- Setup — `python3.12 -m venv .venv`, then `.venv/bin/python -m pip install -e '.[dev]'`.
- Services — None for the CLI. Store authorized source files under ignored `data/`.
- Missing configuration — Authorized market history, reviewed production schedule and reputable scheduled economic feed. S03 can proceed with synthetic and explicitly unknown context.

## Immediate Next Actions

1. Rerun the suite and read specification sections VIII–X. Add the evidence frame interface with the completed analysis bar and batched events. Preserve `EvidenceEngine.push` output for existing consumers.
2. Implement exact tick-based sizing and point-in-time context. Define mirrored setup transitions and choose expiration, invalidation and retest timing rules. Preserve variant selection and versioned assumptions.
3. Exercise synthetic setups with matched and failed conditions, risk and chronology, then persist versioned strategy runs. Update the handoff with what was actually verified.

## Do Not Repeat

- D01–D03 and S01–S02 are implemented and verified. Reuse existing data, time, volatility and evidence paths.
- Do not infer hidden liquidity, intrabar trade order, holiday schedules or future event data.
- Keep research alerts and hypothetical sizing separate from actual order execution.

## Continuation Instruction

Resume from the latest verified checkpoint. Inspect the referenced implementation, complete the next unblocked task, run relevant tests, and update the handoff.
