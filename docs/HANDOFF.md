# AURUM — DEVELOPMENT HANDOFF

## Project Status

- Current phase — Historical adapters and contract mapping D05 are complete. L01 normalized monitoring is the next implementation slice. D04 and B02 await authorized historical data.
- Current milestone — D05 Parquet, HTTPS history, reviewed contracts and continuous research mapping.
- Current branch — `main`.
- Last verified commit — the D05 implementation commit containing this handoff.

## Current Objective

- Task ID — L01.
- Objective — Build the persistent, paper-only monitoring path for normalized minute closes and share the strategy code with replay.
- Acceptance criteria — Preserve source status and raw evidence. Detect duplicates, gaps, stale data and disconnects. Store progress and recover on restart. Gate paper alerts until continuity and freshness are verified. Historical and incremental equivalent inputs yield matching research events. Test against local synthetic transport and keep external verification separate.

## Completed Work

- D01–D03 — Immutable raw datasets, validated MGC minute replay, dated sessions and completed higher-timeframe aggregation.
- S01–S03 — Causal pivots, structure, liquidity and gap lifecycles, configurable strategy, context, risk and persisted paper setup transitions.
- B01 — Auditable fills and costs, conservative OHLC ambiguity, net metrics and closed-trade equity. Real performance has not been measured.
- Q01–Q02 — Chronological selection and holdout, conditional Monte Carlo, and separate versioned account ledger. LucidFlex rule sources and open assumptions are in `docs/VALIDATION.md`.
- D05 adapters — `providers.py` exposes local/authorized HTTPS sources. `parquet_rows.py` validates native timestamps without precision loss. Shared parsing keeps legacy CSV IDs stable and preserves source bytes.
- D05 contract research — `contracts` reads reviewed effective listings and causal activity. `continuous` stores explicit series plans. `continuous-bars` streams unadjusted or forward-adjusted research values alongside each original dated candle.

## Work In Progress

- Current files — L01 code is pending. Reuse `providers.py`, `exchange_calendar.py`, `strategy.py`, `storage.py` and the typed candle model.
- Implemented foundations — `FeedStatus` distinguishes real time, delayed, historical, simulated and unavailable. The minute strategy consumer is `StrategyReplay.push`. Calendar v2 gives full sessions and clipped open windows. SQLite v3 uses transactional migrations and immutable research reports.
- Remaining portions — Define normalized WebSocket event and source identity, durable gap/restart behavior and health status. Implement a paper-only in-app outbox and optional delivery adapters. Validate live behavior only when authorized data and service configuration are available.

## Important Decisions

- ADR-004–009 — Use actual availability, explicit sessions and a shared causal path. Preserve structural risk and conservative paper execution.
- ADR-010/011 — Protect final holdout and separate provider-specific marked equity from generic trade-close scenarios.
- ADR-012 — Preserve legacy CSV IDs, unify source row checks and keep adjusted research prices separate. Continuous rolls must be explicit and use completed overlapping references.
- Provider architecture — Only authorized data sources may be connected. An actual monitoring adapter must distinguish delayed and unavailable from real time and must suppress signals on data integrity failure.

## Known Issues

- DATA-001 — High, blocked. No authorized historical MGC dataset and reviewed production schedule. No empirical strategy results.
- CAL-002/EVENT-001 — Caller-reviewed exchange and economic schedule snapshots. Production feed integration is pending.
- DATA-002/DATA-003 — Sparse exports block replay. Imports buffer raw files and bars.
- ACCOUNT-001 — Provider unrealized treatment and optional daily loss limit need explicit scenario assumptions.
- HTTPS history expects an already normalized export. Backtests currently use one dated contract. No external delivery or live account execution exists.

## Test Status

- Passed — `.venv/bin/python -m pytest -q` (123 with Parquet installed), Ruff lint and format, mypy (43 files), dependency and Git whitespace checks.
- Failed — None outstanding.
- Not run — Authorized source reconciliation, empirical market backtests, production feed or notification delivery.
- Limitations — Adapter and rollover fixtures are synthetic. HTTPS was verified with a fake transport, not an external provider.

## Environment

- Runtime — Python 3.12.7 on macOS arm64 and SQLite 3.45.3. SQLite 3.37+ required.
- Setup — `python3.12 -m venv .venv` then `.venv/bin/python -m pip install -e '.[dev,parquet]'`. Parquet extra pins PyArrow 26.0.1.
- Services — None required for current CLI. Keep data in ignored `data/`.
- Missing configuration — Authorized MGC history, applicable production calendar, authorized normalized streaming endpoint and optional channel credentials.

## Immediate Next Actions

1. Run the checkpoint suite and review specification sections XIV–XV and XIX. Define an incremental monitored feed using reviewed session windows and immutable source identity.
2. Persist accepted and pending candles and enough state to recover a restart. Add explicit gap, duplicate conflict, disconnect, freshness and known-closure behavior. Exercise synthetic local replay and transport.
3. Persist in-app research alerts and add optional external notification components without sending messages during tests. Record exact outstanding production verification.

## Do Not Repeat

- D01–D03, D05, S01–S03, B01, Q01 and Q02 are verified. Reuse the normalized strategy and data path.
- Keep synthetic, adjusted and historical paper results labeled. Do not infer live access or provider authorization from local tests.
- Keep real-money execution and brokerage credentials out of AURUM.

## Continuation Instruction

Resume from the latest verified checkpoint. Inspect the referenced implementation, complete the next unblocked task, run relevant tests, and update the handoff.
