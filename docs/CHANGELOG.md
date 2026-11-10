# AURUM changelog

## 2026-11-10 — Economic schedule revisions

- Added published reschedules, cancellations and partial-source coverage to economic snapshots. Older assessments remain causal and legacy snapshot representations are preserved.


## 2026-11-10 — Operational heartbeat status

- Shared the notification freshness deadline with CLI, API and dashboard monitor status. Overdue or clock-ahead heartbeats suspend operational eligibility while preserving the durable state.


## 2026-11-10 — Economic context in monitoring

- Added reviewed point-in-time economic snapshots and a clear-risk filter to monitoring, replay and provider settings. Existing definitions retain their IDs.


## 2026-11-10 — Historical data reconciliation

- Added normalized minute and schedule comparison with bounded discrepancy examples, preserved replay gates and CLI, API and dashboard entry points.


## 2026-11-10 — Matched structural control

- Added seeded control replays with development-only admission, flat-start holdout fills, CLI/API parity and a dashboard report.


## 2026-11-10 — Verified local backups

- Added private, non-overwriting SQLite snapshots and structural, foreign-key and source-checksum validation.

## 2026-11-10 — Bounded recovery transactions

- Batched historical backfill and simulated replay while preserving individual frame audit and live eligibility rules.

## 2026-11-10 — Indexed history and concurrent restart

- Indexed historical chart availability and verified exact aggregate lookbacks across clipped schedules.
- Released SQLite locks before replaying a monitor restart and applied only the committed tail when catching up.

## 2026-11-10 — Bounded monitor chart projection

- Added a reproducible synthetic benchmark and indexed monitor evidence reads bounded by recorded primary closes.

## 2026-11-09 — Monitor stream recovery

- Added native event-stream resume and checkpoint-triggered chart refresh with polling fallback.

## 2026-11-09 — Runtime profiles and loopback protections

- Added immutable provider and paper-delivery profiles backed by environment variable names.
- Restricted browser mutations and bearer forwarding to the local workspace.

## 2026-11-09 — Automatic forward research periods

- Added opt-in completed-period publication and frozen report checkpoints with durable deduplication.

## 2026-11-09 — Forward research periods

- Added exchange-date period reports, prior-period drift diagnostics and compatible independent historical comparison.
- Added a Forward dashboard workspace for registration, journal inspection and saved reports.

## 2026-11-09 — Forward paper ledger

- Added registration, atomic replay of observed monitor minutes and separate forward audit with conservative interruption handling.

## 2026-11-09 — Report creation

- Added local creation forms and API endpoints for chronological validation, Monte Carlo and paper account ledgers.

## 2026-11-09 — Versioned experiment profiles

- Added server-provided defaults, structured settings, full profile editing and reload of immutable run configurations.

## 2026-11-09 — Research analytics and reports

- Added net backtest curves, rolling diagnostics, grouped outcomes, score bands, setup frequency and fixed-fill cost sensitivity.
- Added dedicated chronological validation, Monte Carlo and paper account report views.

## 2026-11-09 — Observed monitor evidence

- Added atomic market evidence and context journaling with distinct source and observation times.
- Added monitor overlays and higher-timeframe context based on recorded availability.

## 2026-11-09 — Shorter SQLite transactions

- Released chart readers between bounded immutable pages and staged replay evidence before atomic publication.

## 2026-11-09 — Applied monitor chart

- Added an independent monitor source selector, source-minute attribution, gap warnings and current schedule and recorded health status.

## 2026-11-09 — Causal chart overlays

- Bound historical overlays to persisted level, imbalance and paper-order lifecycles.
- Added independent overlay controls and cross-timeframe alignment without backdating a marker.

## 2026-11-09 — Local research dashboard

- Added loopback FastAPI endpoints and a Next.js workspace for provenance, candle charts, evidence, paper backtests and saved reports.
- Verified against a synthetic SQLite fixture. See [validation](VALIDATION.md).

## 2026-11-09 — Optional paper-alert delivery

- Added health warnings, optional Telegram/Discord workers, filters and quiet hours.
- Added durable claims, retry/expiry, queued cancellation and explicit uncertain outcomes.
- Verified delivery with synthetic provider responses and concurrent worker tests.

## 2026-11-09 — Persistent paper monitoring

- Added normalized WebSocket ingestion, durable minute continuity and restart recovery.
- Added explicit feed health, optional HTTPS gap backfill and a deduplicated in-app research alert journal.
- Verified local synthetic transport and matching replay strategy evidence.

## 2026-11-09 — Historical adapters and contract mapping

- Added Parquet and normalized HTTPS historical adapters with the same checks as CSV.
- Added reviewed contract listings, activity-aware roll assessment and versioned continuous research plans.
- Preserved immutable dated source prices and kept forward-adjusted values separate.

## 2026-11-09 — Paper account simulation

- Added a versioned independent LucidFlex paper ledger with phase, loss, scaling, inactivity and payout behavior.
- Added observed mark replay and a conditional trade-close EOD trailing-floor Monte Carlo scenario.
- Recorded provider sources and explicit assumptions for unsettled rule details.

## 2026-11-09 — Chronological validation and simulations

- Added purged chronological validation, embargo, pre-holdout walk-forward and independent selected holdout reporting.
- Added fixed-seed block-bootstrap uncertainty, clustering and cost diagnostics, and conditional Monte Carlo paths.
- Added SQLite v3 report persistence and repeatable analysis commands.

## 2026-11-09 — Auditable paper backtesting

- Added next-eligible-minute fills, structural bracket and size rechecks, conservative ambiguity handling and explicit modeled costs.
- Added closed-trade equity, expectancy and sample-aware performance summaries.
- Added reproducible backtest, summary and audit commands with synthetic integration tests.

## 2026-11-09 — Paper setup research

- Added versioned strategy variants, causal setup transitions and experimental scoring.
- Added higher-timeframe, regional-session, structural range, volatility and scheduled-event context.
- Added exact tick-based risk planning and persistent paper setup replay through the CLI.

## 2026-11-09 — Liquidity and imbalance evidence

- Added confirmed price proxies, mirrored penetration and reclaim events, and causal sweep-to-MSS links.
- Added FVG and IFVG formation, mitigation, inversion, retest, reaction and expiration.
- Added versioned evidence runs to the existing structure replay CLI and store.

## 2026-11-09 — Causal structure replay

- Added independent internal and external confirmed pivots, structural classifications and versioned BOS/MSS rules.
- Added versioned structure runs and replayable evidence. SQLite v2 preserves existing datasets and migrates on write.
- Verified mirrored breaks, future-data isolation, rule thresholds, persistence and rollback on synthetic inputs.

## 2026-11-09 — Completed candle aggregation

- Added incremental 5m, 15m, 1h, 4h and daily OHLCV from dated one-minute data.
- Added the `aggregate` command with versioned event IDs, exchange trade dates, source-minute counts and shortened-bucket flags.
- Verified coverage, closures, gaps and replay parity using synthetic inputs.

## 2026-11-09 — Exchange trading dates

- Added versioned MGC schedules with explicit exchange trade dates, full session boundaries and split windows for halts.
- Preserved legacy dataset IDs and replay output. Dated snapshots now add the trade date to candle-close replay.
- Verified coverage clipping, regular session boundaries, DST, synthetic exceptions and compatibility against a database created by the previous checkpoint.

## 2026-11-08 — Historical data foundation

- Initialized Git, the Python package and persistent project operating documents.
- Added CME-sourced MGC metadata, explicit contract identity and UTC timestamp validation.
- Added versioned exchange window snapshots, CSV import and quality gates, content-addressed source retention, atomic SQLite persistence and deterministic candle-close replay.
- Verified the CLI and data path with labeled synthetic fixtures, including concurrency and rollback.
