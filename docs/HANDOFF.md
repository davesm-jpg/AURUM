# AURUM — DEVELOPMENT HANDOFF

## Project Status

- Current phase — Phase 5 statistical tooling and paper account simulation are implemented. D05 data adapters and contract management is next. Real-data tasks D04 and B02 await authorized MGC.
- Current milestone — Q02 versioned LucidFlex paper ledger and trailing drawdown scenarios.
- Current branch — `main`.
- Last verified commit — `2102bf5243aa32e19fe01df755c747b38b388c9f`.

## Current Objective

- Task ID — D05.
- Objective — Add interchangeable historical adapters and reviewed dated contract metadata, with explicit roll rules and research price basis.
- Acceptance criteria — CSV IDs and original behavior stay stable. Parquet and authorized normalized historical sources share exact timestamp, contract and OHLCV validation and raw provenance. Track listing and expiry from reviewed metadata. Select rolls from time-available observations and preserve dated prices alongside any adjusted series.

## Completed Work

- D01–D03 — Unadjusted MGC CSV import and replay, immutable raw provenance, dated session windows and completed 5m/15m/1h/4h/1d aggregation. Missing expected candles gate replay.
- S01–S03 — Confirmed pivots, causal structure/evidence, mirrored strategy state, point-in-time context, experimental scoring and paper sizing. Immutable runs are available through CLI.
- B01 — Audited paper fills from eligible minute opens, costs, conservative OHLC ambiguity, net metrics and closed-trade equity.
- Q01 — Chronological purging and embargo, validation selection, held-out reporting, walk-forward, block resampling and assumed scenario simulation. SQLite v3 persists analysis definitions and results.
- Q02 — `account_simulation.py` uses reviewed dated session bounds and explicit marked equity. It supports evaluation/funded phases, EOD loss trail, inclusive hard breach, daily lockout, consistency, scaling, inactivity and hypothetical payout. `account` persists paper ledgers. Monte Carlo can reuse the loss trail with explicit trades per session and trade-close-only assumptions.
- Q02 verification — Official source links and rule interpretations are in `docs/VALIDATION.md`. The public drawdown page does not resolve unrealized PnL measurement, so the conservative default equity metric is configurable. DLL amount is caller supplied.

## Work In Progress

- Current files — D05 has no implementation. Start with `market_data.py`, `exchange_calendar.py`, `instruments.py` and existing `DatasetStore` tests.
- Implemented foundations — `prepare_csv` normalizes and validates source bytes. Dataset IDs hash exact source/config/calendar. Storage supports dated unadjusted 1m candles for a single contract. Existing run consumers expect the current normalized type and gate.
- Remaining portions — Add Parquet and authorized historical adapter boundaries, preserve file format and provenance, then add versioned contract inventory and point-in-time roll planning. Document any connector that requires subscription or credentials. L01 service and U01 interface remain roadmap work.

## Important Decisions

- ADR-004/005 — Preserve full exchange windows, use explicit trade dates and distinguish coverage from session boundaries. Publish only completed aggregates.
- ADR-006 through ADR-009 — Use shared causal evidence and paper setup logic, exact sizing, actual availability and conservative fill assumptions.
- ADR-010 — Keep held-out samples out of selection and label conditional paths distinctly.
- ADR-011 — Separate account marks and hypothetical payouts from strategy and execution. Use explicit session boundaries and disclose uncertainty about provider mark treatment and optional intraday payouts.
- Data policy — Do not silently splice or substitute contracts. An adjusted value is for research. Execution references use dated unadjusted source ticks.

## Known Issues

- DATA-001 — High, blocked. No authorized historical MGC export and matching production-reviewed schedule were supplied. No empirical strategy result exists.
- CAL-002/EVENT-001 — Exchange and economic schedule inputs are reviewed and supplied by the caller.
- DATA-002/DATA-003 — Sparse exports gate replay and imports buffer raw bytes and bars.
- ACCOUNT-001 — Provider unrealized measurement and optional DLL amount are not fully verified. Use explicit model assumptions.
- Live feeds, external delivery and dashboards are pending. Real order execution is outside scope.

## Test Status

- Passed — `.venv/bin/python -m pytest -q` (115), Ruff lint and format, mypy (37 files), dependency and Git whitespace checks. Tests exercise account floor, eligibility, phase, payout, survival and CLI.
- Failed — None outstanding.
- Not run — Real-data reconciliation, empirical strategy performance or live provider integration.
- Limitations — Market inputs, account marks and scenario outcomes in tests are synthetic or hypothetical.

## Environment

- Runtime — Python 3.12.7 on macOS arm64, SQLite 3.45.3. SQLite 3.37+ is required.
- Setup — `python3.12 -m venv .venv` then `.venv/bin/python -m pip install -e '.[dev]'`.
- Services — None for CLI. Keep authorized input in ignored `data/`.
- Missing configuration — Authorized MGC export and matching reviewed schedule. No broker credentials are needed or permitted.

## Immediate Next Actions

1. Rerun tests and read specification sections III–IV. Refactor the parser boundary so CSV and Parquet use the same validation and preserve the original CSV identity. Verify malformed, mixed and gap cases through both entry points.
2. Add reviewed contract metadata and causal rollover conditions based on expiration and time-available volume or open interest. Use source versions, explicit dates and deterministic outcomes.
3. Build an explicit continuous research mapping with independently identifiable dated prices and reproducible adjustment rules. Keep replay behavior stable and checkpoint the next completed slice.

## Do Not Repeat

- D01–D03, S01–S03, B01, Q01 and Q02 are verified. Reuse the existing data, evidence, strategy, audit and report paths.
- Keep synthetic data and survival scenarios separate from observed results. Keep live brokerage credentials and actual order execution outside AURUM.

## Continuation Instruction

Resume from the latest verified checkpoint. Inspect the referenced implementation, complete the next unblocked task, run relevant tests, and update the handoff.
