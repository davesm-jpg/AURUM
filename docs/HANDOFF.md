# AURUM — DEVELOPMENT HANDOFF

## Project Status

- Current phase — L01 normalized monitoring and in-app alert journal complete. L03 optional delivery is next. L02 production verification, D04 historical reconciliation and B02 empirical backtesting are blocked on authorized access.
- Current milestone — L01 durable monitoring, gap recovery and replay parity.
- Current branch — `main`.
- Last verified commit — `f5cff1686c0b6fb5a66bee1b38233edae971f7c9`.

## Current Objective

- Task ID — L03.
- Objective — Add optional Telegram and Discord paper-alert delivery with durable status, configurable filters, retry and quiet hours.
- Acceptance criteria — Only authorized, configured channels consume eligible in-app alerts. Preserve freshness and quality gating, deduplication, retry/expiry, quiet hours and delivery audit. Use local fixtures for verification. No external message is sent during tests or development.

## Completed Work

- D01–D05 — Immutable authorized-format CSV/Parquet/HTTPS history, reviewed dated schedules, completed candle aggregation, point-in-time contract selection and explicit continuous research mapping.
- S01–S03 — Causal structure, liquidity, imbalances and mirrored paper strategy with contextual measurements, score and integer sizing. Versioned runs and evidence are persisted.
- B01/Q01/Q02 — Conservative paper backtest, chronological validation, conditional Monte Carlo and separate LucidFlex paper ledger. No empirical strategy performance has been measured.
- L01 journal — SQLite v4 holds monitor definitions, raw/normalized pending and applied minutes, cursor, research events and in-app alerts. Contiguous application and resulting events commit together. Restart restores the applied strategy prefix without replaying delivery.
- L01 transport — `streaming.py` accepts normalized WebSocket minute-close frames with explicit source status, reconnects with bounded backoff, can fill observed gaps using authorized HTTPS and gates stale or invalid data. The connector supports local synthetic transport. Known market closure and unavailable calendar coverage are separate states.
- L01 verification — `monitor-replay` matches strategy replay events. Tests cover gap/restart, SQLite rollback, duplicates/conflicts, scheduled closures, staleness, backfill, local sockets and in-app alert gating.

## Work In Progress

- Current files — L03 code is pending. Reuse `src/aurum/monitoring.py`, `streaming.py`, `storage.py` and alert payloads. A fresh schema v4 database is supported.
- Implemented foundations — `monitor_alerts` exposes eligible paper alerts with stable IDs, context, source age, New York and Prague timestamps and full setup evidence. Recovery and simulated data retain events without creating delivery alerts.
- Remaining portions — Implement a durable channel delivery ledger, preferences, rate limits and retries. Then add the API/UI and forward paper reports. Production live checks remain separate.

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

- Passed — `.venv/bin/python -m pytest -q` (129), Ruff lint/format, mypy (47 files), dependency checks and Git whitespace checks.
- Failed — None outstanding.
- Not run — Authorized real-data reconciliation, production feed verification and actual external delivery.
- Limitations — Local feed, backfill, signals and trading observations in tests are synthetic. A fake real-time classification exercises the outbox gate only.

## Environment

- Runtime — Python 3.12.7, macOS arm64, SQLite 3.45.3. SQLite 3.37+ required.
- Setup — `python3.12 -m venv .venv`, then `.venv/bin/python -m pip install -e '.[dev,parquet,stream]'`.
- Services — No external services for tests. Keep market-data files and local SQLite under ignored `data/`.
- Missing configuration — Authorized production source, matching reviewed calendar, external channel destinations and authorized credentials.

## Immediate Next Actions

1. Rerun the suite and review notification requirements in specification XV. Implement a separate, opt-in channel worker that reads only eligible paper alerts.
2. Persist channel/destination deduplication and attempts. Add quiet hours, rate limits, filters, expiry, cancellation and safe retries. Test with fake delivery and preserve uncertainty on ambiguous failures.
3. Update docs and handoff. Continue with a local REST/dashboard path and forward paper reporting while production access remains pending.

## Do Not Repeat

- D01–D05, S01–S03, B01, Q01–Q02 and L01 are verified. Reuse the same normalized strategy path and store.
- Never classify delayed/recovered data as a real-time signal. Do not infer production authorization from test transports.
- Keep real-money trading and live account credentials out of the project.

## Continuation Instruction

Resume from the latest verified checkpoint. Inspect the referenced implementation, complete the next unblocked task, run relevant tests, and update the handoff.
