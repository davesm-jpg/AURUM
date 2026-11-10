# AURUM — DEVELOPMENT HANDOFF

## Project Status

- Current phase — L01 normalized monitoring and L03 optional notification delivery are complete. The U01 local API and dashboard slice is verified. L02 production verification, D04 historical reconciliation and B02 empirical backtesting are blocked on authorized access.
- Current milestone — Q03–Q05, D06–D07, B03–B04 and L04–L05 are complete. O01 has measured optimizations and local backups. D04 still needs authorized inputs.
- Current branch — `main`.
- Last verified commit — `09186960d7041947b201c7425b50ecf0f3174d27`.

## Current Objective

- Task ID — D08.
- Objective — Import and inspect independently labeled historical best bid and ask observations.
- Acceptance criteria — Preserve exact observation times, sequences, raw bytes and contract provenance. A point-in-time lookup must reject stale, future and prior-window quotes.

## Completed Work

- D01–D05 — Immutable authorized-format CSV/Parquet/HTTPS history, reviewed dated schedules, completed candle aggregation, point-in-time contract selection and explicit continuous research mapping.
- D07 — Schema v12 preserves normalized trade prints and raw bytes with derived minute OHLCV. Exact nanosecond/sequence ordering and normal replay gates are verified.
- D06 — Data reconciliation compares two immutable imports with bounded samples of missing minutes, calendar differences and exact OHLCV mismatches. It preserves replay eligibility and distinguishes synthetic inputs.
- S01–S03 — Causal structure, liquidity, imbalances and mirrored paper strategy with contextual measurements, score and integer sizing. Versioned runs and evidence are persisted.
- B01/Q01/Q02 — Conservative paper backtest, chronological validation, conditional Monte Carlo and separate LucidFlex paper ledger. No empirical strategy performance has been measured.
- B04 — The saved paired execution sensitivity report validates manifests and unchanged trade identity before reporting net differences and precise touch evidence.
- B03 — Separate opt-in paper backtests use stored source prints to order dual-touch bars while retaining bracket-price and cost assumptions.
- Q05 — Saved one-factor reports compare immutable pre-holdout candidates and withhold holdout outcomes. The candidate lookup exposes only IDs.
- Q04 — Backtest analytics groups duration, modeled excursions, costs, ambiguous exits and return dispersion by exchange date and decision context. Saved run IDs are unchanged.
- Q03 — Matched controls fit admission on development decisions and replay identical paper costs from flat after embargo. CLI/API report parity and the dashboard build are verified on labeled synthetic evidence.
- L01 journal — SQLite v4 holds monitor definitions, raw/normalized pending and applied minutes, cursor, research events and in-app alerts. Contiguous application and resulting events commit together. Restart restores the applied strategy prefix without replaying delivery.
- L01 transport — `streaming.py` accepts normalized WebSocket minute-close frames with explicit source status, reconnects with bounded backoff, can fill observed gaps using authorized HTTPS and gates stale or invalid data. The connector supports local synthetic transport. Known market closure and unavailable calendar coverage are separate states.
- L01 verification — `monitor-replay` matches strategy replay events. Tests cover gap/restart, SQLite rollback, duplicates/conflicts, scheduled closures, staleness, backfill, local sockets and in-app alert gating.

- L05 — Provider profiles can pin a standalone dated schedule and contract before historical import. Synthetic calendars stay restricted to simulated workers.
- L04 — Monitor and replay share versioned economic snapshots. Linked revisions take effect at publication, cancellations retire earlier versions and partial coverage cannot yield clear risk. API/CLI health uses the same heartbeat cutoff as notification delivery.

- L03 — `notifications.py` uses SQLite v5 durable claims, filtering, quiet hours, retries, rate limits and uncertain outcomes. `notification_transport.py` supports opt-in Telegram and Discord. Tests use fake responses.

- U01 — Loopback API and server-side proxy, interactive candle chart and evidence overlays, paper backtest controls and saved report inspection. A dedicated chart projection respects original availability and recorded level, zone and paper-plan retirement. The monitor view reads the applied prefix with source-minute provenance, independent selection and overlays based on observed evidence. Schema v6 journals structure, liquidity, imbalances and contextual measurements. Research analytics show closed-trade equity, drawdown, rolling diagnostics, groups, costs and experimental score bands. Saved validation, Monte Carlo and account results have dedicated views. The paper form uses typed server defaults and can reload an immutable run profile. The dashboard creates validation, Monte Carlo and explicit paper ledger reports. Verified using labeled synthetic fixtures.

- F01 — Registered forward runs consume only newly applied observed minutes. The ledger records signal, order, outcome and unresolved events with an atomic cursor. The CLI can synchronize or watch. Synthetic verification is a separate source class. Daily, weekly and monthly reports freeze the applied prefix, separate unresolved outcomes, report prior-period differences and optionally compare an earlier independent compatible holdout. The foreground worker can save completed reviewed periods once. Later periods preserve earlier IDs.

## Work In Progress

- Current files — `src/aurum/api.py`, `src/aurum/chart_evidence.py`, `frontend/app/price-chart.tsx` and their tests. The store is at schema v12. The benchmark entry point is `benchmarks/synthetic_pipeline.py`. F01 code is in `src/aurum/forward.py` and `src/aurum/execution.py`.
- Implemented foundations — `monitor_alerts` exposes eligible paper alerts with stable IDs, context, source age, New York and Prague timestamps and full setup evidence. Recovery and simulated data retain events without creating delivery alerts.
- Remaining portions — Monitor streams support native reconnect cursors and checkpoint-triggered chart refresh. Production feed and delivery remain unverified. The Forward worker can already publish completed periods with `--auto-reports`. Provider and channel runtime configuration is available through the CLI. Forward paper reporting and production checks remain separate.

## Important Decisions

- ADR-004–009 — Use reviewed dates, completed bars, actual evidence availability and conservative paper fills.
- ADR-010–012 — Keep holdout and assumptions separate, use an independent account ledger and preserve original dated prices.
- ADR-017 — Market evidence records source close and observed time separately. Legacy applied minutes retain unknown observation times.
- ADR-016 — Dashboard reads use bounded immutable pages. Replay computes before atomic publication so monitor writes can proceed.
- ADR-013 — Journal raw data, process only the known chronological prefix, hold gaps across restart and latch conflicting duplicates. Gate in-app alerts to fresh eligible real-time observations.
- Safety — Store only authorized market-data and channel credentials in environment variables. Never handle actual order execution or live brokerage accounts.

## Known Issues

- DATA-001 — High, blocked. Authorized historical MGC and a reviewed schedule are absent. No historical performance conclusions.
- CAL-002/EVENT-001 — Review effective exchange and economic snapshots. Production feeds remain unverified.
- DATA-002/DATA-003 — Sparse data triggers the integrity gate and history import buffers files and bars.
- ACCOUNT-001 — LucidFlex public rules leave unrealized treatment and optional DLL amount open.
- Monitor recovery currently rebuilds from the durable prefix. A conflicting duplicate requires investigation and a separate corrected run. Production connection and notification credentials are unavailable.

## Test Status

- Latest checkpoint — 188 Python tests, Ruff lint/format and mypy across 69 files passed. Five frontend tests, typecheck and production build passed. Three backup/source-integrity tests are included. The latest 21,600-minute benchmark took 4.44 s for batched ingestion, with all projection hashes unchanged.
- Passed — `.venv/bin/python -m pytest -q` (188), Ruff lint/format, mypy (69 files), dependency and Git whitespace checks. Five frontend timeline, histogram and loopback proxy tests, typecheck and production build passed. Desktop overlay toggles, source switching, analytics controls, forward journal/report and three other saved report views passed. The protected runtime profile form saved through the browser and fit at 390px. The source switch and no horizontal overflow were verified at 390px in an earlier checkpoint. The current built workspace saved a synthetic matched control, reconciled 350 fixture minutes and saved a provider profile with a partial economic snapshot.
- Failed — None outstanding.
- Not run — Authorized real-data reconciliation, production feed verification, actual external delivery and the full mobile UI.
- Limitations — Local feed, backfill, signals and trading observations in tests are synthetic. A fake real-time classification exercises the outbox gate only.

## Environment

- Runtime — Python 3.12.7, macOS arm64, SQLite 3.45.3. SQLite 3.37+ required.
- Setup — `python3.12 -m venv .venv`, then `.venv/bin/python -m pip install -e '.[dev,parquet,stream,api]'`.
- Services — No external services for tests. Keep market-data files and local SQLite under ignored `data/`.
- Missing configuration — Authorized production source, matching reviewed calendar, external channel destinations and authorized credentials.

## Immediate Next Actions

1. Add a normalized quote observation schema and historical import alongside immutable trade datasets.
2. Implement exact as-of lookups with freshness and exchange-window bounds. Keep spread analysis separate from modeled execution until the observation path is verified.
3. Continue authorized source and delivery reconciliation when configuration is available.

## Do Not Repeat

- D01–D05, S01–S03, B01, Q01–Q02 and L01 are verified. Reuse the same normalized strategy path and store.
- Never classify delayed/recovered data as a real-time signal. Do not infer production authorization from test transports.
- Keep real-money trading and live account credentials out of the project.

## Continuation Instruction

Resume from the latest verified checkpoint. Inspect the referenced implementation, complete the next unblocked task, run relevant tests, and update the handoff.
