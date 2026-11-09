# AURUM — DEVELOPMENT HANDOFF

## Project Status

- Current phase — L01 normalized monitoring and L03 optional notification delivery are complete. U01 local API/dashboard is next. L02 production verification, D04 historical reconciliation and B02 empirical backtesting are blocked on authorized access.
- Current milestone — L03 durable optional Telegram and Discord paper delivery.
- Current branch — `main`.
- Last verified commit — `5f73ae9a517db9e075fa0e0d13532719acc0ce73`.

## Current Objective

- Task ID — U01.
- Objective — Expose persisted research and monitor evidence through a local REST API and a React/Next.js dashboard.
- Acceptance criteria — Market, candle, setup, run and report data are traceable to source and assumptions. Test the local API and dashboard with labeled fixtures and preserve data access boundaries.

## Completed Work

- D01–D05 — Immutable authorized-format CSV/Parquet/HTTPS history, reviewed dated schedules, completed candle aggregation, point-in-time contract selection and explicit continuous research mapping.
- S01–S03 — Causal structure, liquidity, imbalances and mirrored paper strategy with contextual measurements, score and integer sizing. Versioned runs and evidence are persisted.
- B01/Q01/Q02 — Conservative paper backtest, chronological validation, conditional Monte Carlo and separate LucidFlex paper ledger. No empirical strategy performance has been measured.
- L01 journal — SQLite v4 holds monitor definitions, raw/normalized pending and applied minutes, cursor, research events and in-app alerts. Contiguous application and resulting events commit together. Restart restores the applied strategy prefix without replaying delivery.
- L01 transport — `streaming.py` accepts normalized WebSocket minute-close frames with explicit source status, reconnects with bounded backoff, can fill observed gaps using authorized HTTPS and gates stale or invalid data. The connector supports local synthetic transport. Known market closure and unavailable calendar coverage are separate states.
- L01 verification — `monitor-replay` matches strategy replay events. Tests cover gap/restart, SQLite rollback, duplicates/conflicts, scheduled closures, staleness, backfill, local sockets and in-app alert gating.

- L03 — `notifications.py` uses SQLite v5 durable claims, filtering, quiet hours, retries, rate limits and uncertain outcomes. `notification_transport.py` supports opt-in Telegram and Discord. Tests use fake responses.

## Work In Progress

- Current files — U01 is pending. Reuse `storage.py`, `monitoring.py`, `candles.py` and persisted run events. The store is at schema v5.
- Implemented foundations — `monitor_alerts` exposes eligible paper alerts with stable IDs, context, source age, New York and Prague timestamps and full setup evidence. Recovery and simulated data retain events without creating delivery alerts.
- Remaining portions — Build a local API and interactive dashboard. Forward paper reports and production checks remain separate.

## Important Decisions

- ADR-004–009 — Use reviewed dates, completed bars, actual evidence availability and conservative paper fills.
- ADR-010–012 — Keep holdout and assumptions separate, use an independent account ledger and preserve original dated prices.
- ADR-013 — Journal raw data, process only the known chronological prefix, hold gaps across restart and latch conflicting duplicates. Gate in-app alerts to fresh eligible real-time observations.
- Safety — Store only authorized market-data and channel credentials in environment variables. Never handle actual order execution or live brokerage accounts.

## Known Issues

- DATA-001 — High, blocked. Authorized historical MGC and a reviewed schedule are absent. No historical performance conclusions.
- CAL-002/EVENT-001 — Review effective exchange and economic snapshots. Production feeds remain unverified.
- DATA-002/DATA-003 — Sparse data triggers the integrity gate and history import buffers files and bars.
- ACCOUNT-001 — LucidFlex public rules leave unrealized treatment and optional DLL amount open.
- Monitor recovery currently rebuilds from the durable prefix. A conflicting duplicate requires investigation and a separate corrected run. Production connection and notification credentials are unavailable.

## Test Status

- Passed — `.venv/bin/python -m pytest -q` (134), Ruff lint/format, mypy (50 files), dependency checks and Git whitespace checks.
- Failed — None outstanding.
- Not run — Authorized real-data reconciliation, production feed verification and actual external delivery.
- Limitations — Local feed, backfill, signals and trading observations in tests are synthetic. A fake real-time classification exercises the outbox gate only.

## Environment

- Runtime — Python 3.12.7, macOS arm64, SQLite 3.45.3. SQLite 3.37+ required.
- Setup — `python3.12 -m venv .venv`, then `.venv/bin/python -m pip install -e '.[dev,parquet,stream]'`.
- Services — No external services for tests. Keep market-data files and local SQLite under ignored `data/`.
- Missing configuration — Authorized production source, matching reviewed calendar, external channel destinations and authorized credentials.

## Immediate Next Actions

1. Rerun the suite and review specification XVI and XVIII. Build paginated local API reads for datasets, candles, runs, reports and monitors with input validation.
2. Build the Next.js interface with source status, candle overlays, audit and metrics. Verify with a synthetic dataset and local preview.
3. Record precise UI coverage and update the handoff. Continue forward paper reporting or missing UI functionality while production access remains pending.

## Do Not Repeat

- D01–D05, S01–S03, B01, Q01–Q02 and L01 are verified. Reuse the same normalized strategy path and store.
- Never classify delayed/recovered data as a real-time signal. Do not infer production authorization from test transports.
- Keep real-money trading and live account credentials out of the project.

## Continuation Instruction

Resume from the latest verified checkpoint. Inspect the referenced implementation, complete the next unblocked task, run relevant tests, and update the handoff.
