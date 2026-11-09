# AURUM — DEVELOPMENT HANDOFF

## Project Status

- Current phase — Phase 5 statistical tooling Q01 is implemented. Q02 paper account rules is next. D04 and B02 empirical work await authorized MGC.
- Current milestone — Q01 chronological validation and conditional Monte Carlo.
- Current branch — `main`.
- Last verified commit — the Q01 implementation commit containing this handoff.

## Current Objective

- Task ID — Q02.
- Objective — Implement a separate versioned LucidFlex paper account simulator with provider-verified rules, intraday equity breach, EOD trailing floors, funded/evaluation differences and payout effects.
- Acceptance criteria — Model the documented $25K profile and explicit variations without touching actual accounts. Track balance, equity, eligible EOD high, active/locked floor, remaining allowance, breaches, phase, position limits and relevant payout eligibility. Verify official rules and disclose any ambiguities. Tests cover boundaries and deterministic replay.

## Completed Work

- D01–D03 — Single-contract unadjusted MGC CSV import and replay, immutable raw provenance, reviewed dated windows and incremental 5m/15m/1h/4h/1d aggregates. Missing expected minutes block historical replay.
- S01–S03 — Confirmed structure, liquidity and imbalance lifecycle, mirrored paper strategy variants, point-in-time context, experimental scoring, exact sizing and persisted state transitions.
- B01 — Auditable minute fill model, stop-first ambiguous bars, adverse gap stops, costs, closed-trade equity, group metrics and reproducible persisted backtests. Only synthetic market fixtures have been evaluated.
- Q01 validation — `statistics.py` purges labels crossing split boundaries, applies embargo, selects on validation expectancy, reports only the selected holdout and limits walk-forward to pre-holdout data. It records block-bootstrap intervals, Holm-adjusted validation p-values, clustering, score strata and fixed-fill cost sensitivity.
- Q01 scenarios — `monte_carlo.py` implements shuffle, circular block resampling and assumed outcomes with seeded paths, equity/drawdown quantiles, losing streaks and generic fixed-floor survival. `research_reports.py` joins backtest trades to setup decisions and provenance. SQLite v3 persists reports. CLI commands are `validate`, `monte-carlo` and `report`.

## Work In Progress

- Current files — Q02 implementation is pending. Read specification section XIII and inspect `monte_carlo.py`, `execution.py` and `storage.py` for separable inputs and existing report persistence.
- Implemented foundations — Trade audit provides realized PnL and limited excursions. Generic Monte Carlo uses trade-close equity and a fixed floor. Neither substitutes for provider-specific intraday equity checks.
- Remaining portions — Verify official LucidFlex rule pages for effective phase, loss, scaling, consistency, payout, inactivity and session requirements. Implement an independent simulator with an explicit marked-equity timeline and sourced profiles. Add paper-only report and scenario support. D05 data adapters and live provider work follow.

## Important Decisions

- ADR-004–009 — Reviewed exchange sessions, actual evidence timestamps, separate qualification and conservative paper fills. Preserve source versions and structural risk.
- ADR-010 — Candidate comparison holds data and execution/cost assumptions fixed. Separate parameter configurations are disclosed. Only pre-holdout samples can rank a candidate. Cost sensitivity does not rerun fills. Caller controls preregistration.
- Survival — Q01's fixed floor is generic and uses trade closes. Q02 needs provider-specific EOD updates and observed intraday marks. Do not call an OHLC-only result proof of account compliance.
- Official Q02 evidence to inspect — Lucid Trading Help Center pages titled LucidFlex Drawdown, Evaluation Account, Funded Account, Customization, Scaling Plan, Payouts, Daily Loss Limit and Allowed Trading Times. Effective documentation can distinguish custom account settings.

## Known Issues

- DATA-001 — High, blocked. No authorized MGC history and production-reviewed schedule. No empirical strategy result exists.
- CAL-002/EVENT-001 — Effective exchange and economic schedules are caller supplied. Production feed integration is pending.
- DATA-002/DATA-003 — Sparse no-trade exports block replay. Import buffers the CSV and normalized bars.
- Provider-specific restrictions, streaming and external delivery are not implemented. The system is paper-only.

## Test Status

- Passed — `.venv/bin/python -m pytest -q` (105), Ruff lint and format, mypy (34 files), dependency and Git whitespace checks. Tests cover purged boundaries, holdout mutation independence, seed stability and persisted report CLI.
- Failed — None outstanding.
- Not run — Authorized real-data tests, statistical estimation on real trades, provider-specific account checks, live feed parity.
- Limitations — Tests and Monte Carlo scenarios are synthetic or conditional. The initial backtest metrics use closed-trade equity.

## Environment

- Runtime — Python 3.12.7, macOS arm64, SQLite 3.45.3. Requires SQLite 3.37+.
- Setup — `python3.12 -m venv .venv` then `.venv/bin/python -m pip install -e '.[dev]'`.
- Services — None. Keep authorized input in ignored `data/`.
- Missing configuration — Authorized MGC data and production-reviewed schedule for D04/B02.

## Immediate Next Actions

1. Verify the latest LucidFlex $25K official evaluation and funded rules. Save source links and dates and reconcile inconsistent language explicitly.
2. Implement versioned account profiles and deterministic observed-equity marks. Test inclusive hard breach, EOD updates/lock, phase differences, daily lockout and payout effects before exposing the simulator.
3. Add a saved paper-account report and documented data assumptions. Update tests and handoff, then advance the next unblocked phase.

## Do Not Repeat

- D01–D03, S01–S03, B01 and Q01 are verified. Reuse the existing input and report paths.
- Preserve the holdout. Keep synthetic and assumed outputs separate from observations.
- Keep actual orders, live credentials and brokerage-account connections out of AURUM.

## Continuation Instruction

Resume from the latest verified checkpoint. Inspect the referenced implementation, complete the next unblocked task, run relevant tests, and update the handoff.
