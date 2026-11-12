# AURUM changelog

## 2026-11-12 — Databento and Telegram setup preparation

- Added an actionable historical-first setup guide and the selected Telegram preference without configuring a vendor account or buying data.
- Added read-only Telegram bot/private chat inspection with bounded responses, redacted errors and no sends, update acknowledgement or webhook changes.
- Full suite passed 330 tests. No real credentials or external Telegram calls were used.

## 2026-11-12 — Hosted action runtime

- Updated the pinned setup-node action to verified v7.1.0, whose internal runtime is Node.js 24. Frontend Node version and explicit cache settings are unchanged.
- Hosted compatibility and annotation verification are pending.

## 2026-11-11 — Audit known observation chronology

- Monitor audit v2 and verified backups reject decreasing known application times, including across unknown legacy rows.
- Preserve original observations and disclose that unknown clocks cannot establish complete chronology.

## 2026-11-11 — Processing-clock rollback guard

- Preserve newly received candles as pending when processing time moves behind the last known check, recording the actual clock without backdating discovery.
- Recovery after restart uses a later actual frame clock and suppresses fresh eligibility for the recovered prefix. Existing observations remain unchanged.

## 2026-11-11 — Nonblocking monitor exports

- Monitor event exports freeze their starting sequence. Alert exports capture ordered IDs and read bounded payload pages. Both close readers before yielding.
- Reproduced writer timeouts are covered across page boundaries and timestamp ties. Concurrent new entries remain outside that export and appear on a fresh read.

## 2026-11-11 — Monte Carlo account breach boundary

- Added version 2 account distributions stopped at the first loss-limit breach while retaining seeded unrestricted strategy paths.
- Dashboard labels full-horizon continuation, trade-close-only measurement and legacy missing metrics. Existing saved reports remain unchanged.
- Full suite passed 311 tests; frontend build and browser result/legacy checks passed.

## 2026-11-11 — Nonblocking public replay

- Closed public replay readers before yielding candles, fixing a reproduced same-store writer timeout without buffering the entire dataset.
- Full and bounded replay regressions preserve exact output across page boundaries. Full suite passed 309 tests and static checks.

## 2026-11-11 — Reconnect safeguards

- Fixed transient native subscription failures wrapped by the pinned SDK while keeping authorization failures permanent and provider text private.
- Suspend persisted prior-process readiness before opening a replacement stream, holding queued research alerts until a fresh connection is established.
- Full suite passed 307 tests. N06's preceding hosted checks passed all three jobs.

## 2026-11-11 — Optional setup lifecycle notifications

- Added opt-in developing notices and withdrawals with same-close collapse and durable cancellation of queued advisories.
- Recovered invalidations remain informational and disclose their source class and observation time. Existing disabled-default IDs and confirmed paper behavior remain compatible.
- Full suite passed 302 tests; browser verified saved configuration without starting a monitor or sending a message.

## 2026-11-11 — Preserved monitor input verification

- Added bounded offline reconstruction of monitor candles from normalized, replay and native DBN evidence.
- Verified backups reject inconsistent monitor input. Restart source verification faults the monitor before analysis resumes.
- Full suite passed 295 tests. The synthetic benchmark retained all chart digests with the disclosed restart validation cost.

## 2026-11-11 — Durable public calendar polling

- Added explicit foreground public capture with persisted cadence, exclusive leases, bounded retry and interruption recovery.
- Added read-only health, attempt audit and Settings status. Monitor adoption remains reviewed.
- Extended normalized backup verification to reconstruct polling journals. Full suite passed 285 tests; actual BEA capture and BLS failure were recorded and browser-verified.

## 2026-11-11 — Native Databento adapter

- Added explicit dated MGC minute subscriptions using a pinned SDK, raw mapping/record evidence and the existing stream monitor.
- Bounded buffering, strict record validation, historical recovery classification and durable next-minute resume preserve alert eligibility boundaries.
- Fifteen native synthetic cases and the full 275-test suite passed locally. No vendor connection or paid history request was opened.
- Added native provider profiles and Settings controls with local key-variable references and shared resume. Legacy profile identities are preserved. API and browser checks passed, and the full suite reached 276 tests.
- Fixed asynchronous strategy choices selecting MSS-only instead of the primary hypothesis. Browser reload verified the intended default.

## 2026-11-11 — Forward report notifications and hosted verification

- Added opt-in durable daily, weekly and monthly report notifications with source labels, coverage, actual counts and saved report references.
- Publication and alert queuing commit together. Existing channel delivery controls and destination deduplication remain shared.
- Published the verified checkpoint to the user-provided GitHub repository. All three hosted Python, frontend and container smoke jobs passed on `10573ba`.

## 2026-11-11 — Repository quality workflow

- Added isolated Python, frontend and container verification jobs with pinned action revisions and read-only repository access.

## 2026-11-11 — Email research delivery

- Added authenticated TLS email transport through the existing durable delivery system, CLI, profiles and Settings.
- Added recipient validation, stable message identity and acknowledgement-aware failure handling.

## 2026-11-11 — Native Databento minute export import

- Added strict Databento pretty-formatted CSV normalization with original byte provenance and existing missing-minute research gates.

## 2026-11-11 — Local service packaging

- Added pinned container base images and runtime dependencies, persistent local Compose services and standalone dashboard output.
- Added an explicit API bind option that requires a token for all-interface binding. Verified native production startup and proxy health.

## 2026-11-11 — Reviewed monitoring calendar updates

- Verified the archived snapshot picker in the browser.
- Added immutable calendar activation through CLI and API, with distinct source and observation clocks, restart recovery and recorded unavailable context.

## 2026-11-10 — Archived economic snapshot review

- Added local capture previews in provider Settings and included archive receipt and byte validation in verified backups.

## 2026-11-10 — Official economic capture archive

- Added explicit BEA and BLS iCalendar retrieval, immutable SQLite receipts, raw export and point-in-time normalization using archived receipt clocks.

## 2026-11-10 — Indexed economic risk assessment

- Added in-memory release-time indexing. A 20,000-query synthetic run retained all decisions and reduced assessment time.

## 2026-11-10 — Multiple reviewed economic publications

- Added composition of reviewed calendar snapshots with per-publication evidence and isolated revision chains.

## 2026-11-10 — Staged chronological validation

- Added a pre-holdout selection report and a separately opened final paper replay with a flat initial position, embargo and source-linked execution audit.
- The dashboard defaults to selection before evaluation. Existing combined validation remains available, and final flat-start results can feed matched controls and forward baseline comparisons.

## 2026-11-10 — Economic release capture comparison

- Added causal schedule revisions and explicit cancellations from ordered local iCalendar publications, with checksums and observation times retained in the snapshot.

## 2026-11-10 — Whole-store normalization audit

- Added optional normalized archive verification and a pre-publication check on staged backups.


## 2026-11-10 — Observation-time economic gate

- Rechecked event risk at monitor observation for new alert and forward eligibility while preserving source-close research evidence.


## 2026-11-10 — Contract research workspace

- Added rollover assessment and continuous research plan creation in the dashboard, with bounded continuous previews showing dated and research prices separately.


## 2026-11-10 — Offline quote-series integrity

- Added read-only reconstruction of normalized best bid and ask observations from preserved bytes.


## 2026-11-10 — Offline dataset integrity reproduction

- Added a read-only command that reconstructs candle and trade datasets from stored raw bytes and compares their exact normalized rows.


## 2026-11-10 — Reviewed iCalendar captures

- Added local timed-release normalization with source class, capture time, DST verification and partial coverage.


## 2026-11-10 — Streaming store contention recovery

- Added bounded retry for SQLite locks and preserved transport receipt separately from processing time. Retried frames are audited and ineligible for alert and forward fill processing.


## 2026-11-10 — Paired quote cost sensitivity

- Added an opt-in fixed-fill projection using fresh quoted widths at eligible entry and exit references, with explicit subset and exclusions.


## 2026-11-10 — Historical spread observations

- Added saved spread and quote coverage diagnostics for paper decision, entry and ordered bracket references.


## 2026-11-10 — Historical best bid and ask

- Added separately versioned quote series with exact observation-time lookups and explicit freshness, calendar and closure states.


## 2026-11-10 — Parameter sensitivity

- Added one-factor reports over saved development and validation candidates, with profile equality checks and a candidate metadata route that omits holdout outcomes.


## 2026-11-10 — Intrabar ordering sensitivity

- Added paired stop-first and ordered-print outcome comparisons with exact source-touch examples and immutable manifest checks.


## 2026-11-10 — Ordered print bracket resolution

- Added an opt-in paper execution variant for dual-touch bars on trade-derived data, with exact source touch audit and separate comparison compatibility.


## 2026-11-10 — Historical trade prints

- Added normalized trade CSV import, exact nanosecond and sequence ordering, derived unadjusted minute bars and paged CLI/API print inspection.


## 2026-11-10 — Grouped paper execution diagnostics

- Added holding time, favorable and adverse excursion, execution cost, ambiguous exit and sample net P&L dispersion to the backtest breakdown.


## 2026-11-10 — Standalone provider schedules

- Added immutable reviewed exchange schedule and dated contract settings for provider profiles without a historical dataset.


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
