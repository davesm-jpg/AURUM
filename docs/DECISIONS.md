# Architecture decision records

## ADR-001 — Local vertical slice before service dependencies

- Date 2026-11-08.
- Context. The initial workspace was empty. Import and replay require deterministic offline verification.
- Alternatives. Start with a PostgreSQL/FastAPI deployment, use file-only persistence, or use a local transactional store.
- Decision. Use a Python 3.12+ package with SQLite v1 and a CLI for the first slice.
- Rationale. Transactions, indexes and versioned schema are available without external services.
- Consequences. This checkpoint has local deployment and SQLite's concurrency model. Multi-client deployment will require a service and storage decision.
- Files. `pyproject.toml`, `src/aurum/storage.py`, `src/aurum/cli.py`.

## ADR-002 — Caller-supplied reviewed schedule with bounded coverage

- Date 2026-11-08.
- Context. Weekly hours alone cannot explain holidays, early closes or halts and can mislabel feed gaps.
- Alternatives. Infer closures from observed candles, embed regular hours, or accept versioned explicit windows.
- Decision. Require ordered UTC-normalized open windows and explicit expected coverage. Omitted times inside coverage represent closures as attested by the snapshot. Pin timezone rules for naive CSV timestamps.
- Rationale. This makes the schedule assumption visible and reproducible, and detects missing first/last bars independently of observed bounds.
- Consequences. Snapshot quality must be reviewed externally. Future schedule work must distinguish exchange trading dates and preserve previously imported snapshots.
- Files. `src/aurum/exchange_calendar.py`, `src/aurum/timestamps.py`, `src/aurum/market_data.py`.

## ADR-003 — Immutable source identity and fail-closed replay

- Date 2026-11-08.
- Context. Dated and adjusted data can appear similar. Exports can be duplicated, incomplete or revised.
- Alternatives. Identify a series only by symbol/time, replace prior imports, or version each source/configuration combination.
- Decision. Require unadjusted dated MGC data for this path. Retain raw source by checksum, version provenance and store integer ticks. Reject malformed/duplicate/off-calendar input, sort and disclose disorder, and store gaps while blocking replay. Report zero volume without treating it alone as a gap.
- Rationale. The evidence needed for repeatable replay survives restarts and source revisions. Missing minutes cannot reach the research path.
- Consequences. Identical re-imports are idempotent. Correcting source or calendar inputs yields a new dataset. Sparse exports that omit no-trade minutes need a future explicit policy.
- Files. `src/aurum/instruments.py`, `src/aurum/market_data.py`, `src/aurum/storage.py`.

## ADR-004 — Date full sessions independently of import coverage

- Date 2026-11-09.
- Context. A single exchange trade date can begin on a previous civil day and contain several open intervals. Clipping the source windows at import boundaries would lose the information needed to anchor and complete aggregate bars.
- Alternatives. Infer the trade date from local time, store only clipped dated windows, or preserve full sessions with separately bounded coverage.
- Decision. Calendar schema v2 preserves full UTC-normalized open windows under an explicit exchange trade date. Use coverage to constrain expected source bars. Preserve the legacy format and JSON identity for existing datasets.
- Rationale. An authoritative schedule can assign holiday trade dates and exceptions. Full windows retain the information needed to distinguish partial coverage from a shortened exchange session.
- Consequences. The caller supplies and reviews the schedule. Dated snapshots add the trade date at replay from provenance without a database migration. A legacy dataset needs a dated snapshot before it can support exchange-session aggregation.
- Files. `src/aurum/exchange_calendar.py`, `src/aurum/cli.py`, `tests/test_exchange_calendar.py`, `tests/test_data_pipeline.py`.

## ADR-005 — Anchor aggregates to open windows and session close

- Date 2026-11-09.
- Context. Import coverage, early closes and halts can cut through nominal candle intervals.
- Alternatives. Use UTC clock buckets, use one clock anchored to the full session across a halt, or anchor intraday buckets to the open windows.
- Decision. Start 5m, 15m, 1h and 4h buckets at the full open-window start and reset at the next window. Publish the shortened final bucket at the window close when all of its expected minutes exist and flag it as shortened. Combine all open windows for daily bars. Require the scheduled closing minute and every expected open minute, independent of export coverage.
- Rationale. Bucket boundaries are reproducible across imports and intraday bars do not bridge a closure. The shortened flag lets downstream research select which bars qualify.
- Consequences. Chart boundaries may differ from vendor clock buckets, especially after a halt. Historical replay rejects a dataset with gaps. The incremental consumer rejects disordered input and suppresses any bucket missing an expected minute.
- Files. `src/aurum/candles.py`, `src/aurum/cli.py`, `tests/test_candles.py`.

## ADR-006 — Point-in-time pivots and structure breaks

- Date 2026-11-09.
- Context. Plausible pivot, break and MSS definitions can produce different histories. Research needs to reproduce each rule.
- Alternatives. Mark pivots retrospectively, use unconfirmed levels, or evaluate only completed candles against previously confirmed levels.
- Decision. Use strict local pivots with 3/3 internal and 5/5 external defaults. Equality inside a pivot window rejects a candidate. Separately confirmed pivots at equal prices receive equal labels. Compare the latest pair of highs and lows per scope. Before confirming new pivots, evaluate and consume previously confirmed levels once per rule. Buffered and displacement rules use a simple mean of the full prior true-range window. Label a qualifying single-direction break as MSS only with opposite prior structure and a close on the valid side of the latest opposing pivot. After MSS, keep the transition state until a high and low are confirmed strictly later.
- Rationale. Event timestamps, strict ordering and exact integer thresholds make outputs reconstructable and avoid future data and same-candle eligibility. A candle crossing both directions can report two BOS events without guessing their order.
- Consequences. This is rule version 1. Latest confirmed levels supersede older levels, and completed shortened bars count in the pivot and ATR windows. SQLite v2 adds atomic, idempotent structure-run persistence. The CLI defaults to close-based breaks and exposes each rule's thresholds.
- Files. `src/aurum/market_structure.py`, `src/aurum/storage.py`, `src/aurum/cli.py`, `tests/test_market_structure.py`.

## ADR-007 — Price-derived levels and explicit imbalance lifecycles

- Date 2026-11-09.
- Context. Sweeps and gaps are defined from observable bars. OHLCV cannot resolve intrabar trade order or the precise time a price spent beyond a level.
- Alternatives. Infer event order or liquidity from wicks, publish on arbitrary price proximity, or record distinct causal lifecycle facts.
- Decision. Register confirmed swings, equal and repeated pivots, rolling range edges and completed day/week/Asia/London extremes after the selected close. Use Chicago Sunday-to-Sunday coverage for complete weeks, Tokyo 09:00–15:00 for Asia and London 08:00–12:00 local for the London proxy. Require an actual penetration and configured closing reclaim. Separate timed-out breaks and unconfirmed reclaims. Keep strict adjacent three-candle FVGs, track first touch and maximum mitigation, and invert on the configured consecutive closes beyond the far edge. Only subsequent candles can retest the inverted zone.
- Rationale. All decisions can be replayed from known completed candles. The rules keep observation duration distinct from actual intrabar duration. The latest valid earlier sweep can be linked to MSS without assuming same-bar order.
- Consequences. Parameters and regional hours are research assumptions captured in the run. Pending session proxies publish at the next analysis close. A sweep can be linked to more than one structure scope. Range edges refresh on price changes. Level lifetimes are configurable in analysis bars. Under a coarse analysis timeframe only the latest rolling proxy per kind since the prior analysis close is published.
- Files. `src/aurum/liquidity.py`, `src/aurum/liquidity_map.py`, `src/aurum/sessions.py`, `src/aurum/imbalances.py`, `src/aurum/evidence.py`.

## ADR-008 — Versioned paper qualification with explicit unknown context

- Date 2026-11-09.
- Context. Setup variants need shared evidence, traceable sizing and stable replay. Higher-timeframe bars and economic releases may be unavailable.
- Alternatives. Use implicit defaults and generate a trading instruction, or emit paper state transitions with explicit evidence and rejection reasons.
- Decision. Keep at most one developing candidate per direction and use the first eligible candidate. If several same-close sweeps qualify, select by penetration then stable ID. Require a strictly earlier sweep for sweep variants. MSS and zone may confirm at the same close, and the reaction must be later. Use the outermost relevant structural pivot or sweep extreme plus a configurable buffer. Size in integer ticks, cents and whole contracts. Evaluate the 2:1 target and nearest structural target as separate rules.
- Rationale. The ordering avoids assumed intrabar chronology. Separating qualification from simulated fills keeps historical entry timing auditable. Economic coverage and higher-timeframe availability are explicit, and scores are experimental.
- Consequences. The reference USD 50 cap is gross and an optional net cap includes modeled costs. Context periods and filters are persisted. Regional New York hours are 08:30–16:00 local for this research version. Calendar snapshots use publication and actual-known timestamps. Completed run persistence is atomic, and recovery of an unfinished historical run recomputes from its source. Live checkpointing is future work.
- Files. `src/aurum/strategy.py`, `src/aurum/paper_risk.py`, `src/aurum/market_context.py`, `src/aurum/economic_events.py`.

## ADR-009 — Paper fills from eligible minute opens and conservative brackets

- Date 2026-11-09.
- Context. Candle-close setup information and opening, intrabar and closing execution have distinct availability.
- Alternatives. Fill at the confirming close, assume favorable same-minute ordering, or simulate eligible future opens with conservative bracket rules.
- Decision. Process existing orders and positions before observing each minute's setup results. At entry, preserve the signal stop and target and recheck size against the new open and costs. Hold at most one position. Treat opening stop gaps at the adverse open and target gaps at the target. Use stop-first for ambiguous range touches. Flatten at scheduled window close by default and separately flag forced terminal closes. Record costs as explicit cents and delay intrabar outcomes to candle close.
- Rationale. This exposes eligibility, costs, realized slippage from gaps and intrabar uncertainty in the audit.
- Consequences. Entry uses the next minute's open, which can share a UTC boundary with signal confirmation. Excursions on an intrabar exit omit the remainder of the exit minute. Gap losses can exceed the planned risk. Drawdown in B01 is closed-trade equity. Account simulations need separate intraday equity modeling.
- Files. `src/aurum/execution.py`, `src/aurum/backtesting.py`, `src/aurum/performance.py`.

## ADR-010 — Chronological selection and conditional simulations

- Date 2026-11-09.
- Context. Trade outcomes can cross sample boundaries and exhibit dependence. A scenario's assumed win rate is different from an observed strategy result.
- Alternatives. Randomly split trades, compare all candidates on the final holdout, or separate chronological selection from final evaluation.
- Decision. Purge observations whose outcome becomes available at or after the next split and embargo early decisions in the later split. Select by net validation expectancy with a configured minimum count and deterministic ties. Evaluate only the selected holdout. Limit walk-forward windows to pre-holdout data. Use circular moving-block bootstrap and Holm adjustment on candidate validation p-values. Expose fixed-fill cost sensitivity and separate seeded shuffle, block and assumed Monte Carlo modes.
- Rationale. Split rules prevent automatic selection on the final holdout. Dependence diagnostics and explicit source labels make conditional outputs interpretable.
- Consequences. The caller controls preregistration and can still reuse a holdout outside the tool. Cost sensitivity adjusts returns without rerunning fills. Bootstrap uncertainty depends on the sample and chosen block. Monte Carlo survival models a generic fixed floor at trade closes. SQLite v3 persists report definitions and results.
- Files. `src/aurum/statistics.py`, `src/aurum/monte_carlo.py`, `src/aurum/research_reports.py`.

## ADR-011 — Explicit paper account marks and separate drawdown scenarios

- Date 2026-11-09.
- Context. Account loss-limit updates, intraday violations, evaluation eligibility and payouts are distinct from a trading strategy and from closed-trade equity metrics.
- Alternatives. Reuse closed-trade equity as proof of intraday compliance, or drive an independent ledger with reviewed session boundaries and observed marks.
- Decision. Use the independent ledger. Implement a versioned LucidFlex $25K profile with EOD floor updates, inclusive floor breach, 40% or 50% evaluation consistency, funded scaling, inactivity and hypothetical payout effects. Parameterize balance versus equity and DLL amounts. Support conditional trade-close-only Monte Carlo drawdown through the same loss trail.
- Rationale. The model can distinguish what observed equity proves from conditional estimates. Payouts and activation remain simulated ledger operations.
- Consequences. The paper default uses marked equity conservatively. The public drawdown page does not define unrealized treatment. Users supply flat provider-adjusted EOD marks and any DLL amount. Optional intraday payout handling is a research interpretation of the published window with extra conservative eligibility checks. See the provider links in `docs/VALIDATION.md`.
- Files. `src/aurum/account_simulation.py`, `src/aurum/account_reports.py`, `src/aurum/monte_carlo.py`.

## ADR-012 — Shared source validation and causal forward adjustment

- Date 2026-11-09.
- Context. Vendor formats and activity publication times vary. A continuous series can obscure which dated price was observable.
- Alternatives. Normalize by vendor without a shared integrity gate, back-adjust old prices after each roll, or use one validation and explicitly mapped successor adjustment.
- Decision. Reuse identical row validation for local CSV, Parquet and authorized HTTPS exports. Preserve the original bytes and legacy CSV identity. Use reviewed listing dates and compare available volume or open interest in matching intervals. Store explicit continuous switches and use the last specified overlapping close available by the switch for forward adjustment. Keep source OHLC separate from research prices.
- Rationale. Market assumptions and roll inputs remain inspectable without rewriting older research values. Contract selection does not hardcode expiry or vendor rules.
- Consequences. Actual source formats may need a normalization gateway. Compatibility checks require matching source provider, basis, data kind, tick settings and overlapping session dates/windows. Continuous series are separate research reports. Current backtests use a single dated contract per run.
- Files. `src/aurum/providers.py`, `src/aurum/parquet_rows.py`, `src/aurum/contracts.py`, `src/aurum/continuous.py`.

## ADR-013 — Journal minute continuity before publishing alerts

- Date 2026-11-09.
- Context. A live transport can drop, duplicate or reorder minute frames. Strategy state must match replay after recovery.
- Alternatives. Continue processing through gaps, drop pending frames on restart, or journal validated input and apply only the known prefix.
- Decision. Store raw and normalized frames, enforce the reviewed minute schedule and append research events atomically with cursor updates. Hold future frames during a gap. Resume from ordered frames or authorized HTTPS backfill. Rebuild strategy state by replaying the applied prefix. Gate alerts to fresh real-time closes and suppress them during catch-up.
- Rationale. Durable chronology preserves the same evidence path through restart, duplicate and late delivery. A conflicting duplicate is recorded and latches the run for investigation.
- Consequences. Calendar coverage and source status are explicit caller inputs. Startup expects the first covered minute. Restart rebuild cost grows with applied history. Existing SQLite readers are released between replay batches before monitor writes.
- Files. `src/aurum/monitoring.py`, `src/aurum/streaming.py`, `src/aurum/feed_frames.py`, `src/aurum/storage.py`.

## ADR-014 — Delivery leases and explicit uncertain state

- Date 2026-11-09.
- Context. Channel calls can fail after a provider may have accepted a message.
- Alternatives. Retry all failures, send without durable claims, or distinguish explicit failure from uncertain delivery.
- Decision. Persist a unique channel/destination/alert claim and its attempts. Retry explicit throttling and temporary HTTP failures. Mark ambiguous transport or expired claims uncertain and require separate investigation. Respect local quiet hours, limits and freshness. Permit health warnings independently of confirmed paper alerts.
- Rationale. The worker is safe under concurrent claims and avoids automatic retransmission when receipt is unknown.
- Consequences. Queued cancellation applies before sending. External provider responses, production limits and real channel behavior remain unverified. Tests use fake transport.
- Files. `src/aurum/notifications.py`, `src/aurum/notification_transport.py`, `src/aurum/storage.py`.

## ADR-015 — Loopback research workspace

- Date — 2026-11-09.
- Context — Chart evidence and paper reports need an interactive interface over the existing store.
- Alternatives — Local frontend proxy or a separately deployed public API.
- Decision — Use local FastAPI and a Next.js server-side proxy with optional bearer forwarding. Reuse persisted candles and research runs.
- Consequences — The workspace operates without a market feed. Public deployment and applied monitor chart data require separate work.
- Files — `src/aurum/api.py`, `src/aurum/cli.py`, `frontend/`.

## ADR-016 — Keep computation outside SQLite locks

- Date — 2026-11-09.
- Context — Holding a rollback-journal reader during ingestion blocked the synthetic monitor writer.
- Alternatives — Use bounded immutable reads or switch the store to WAL.
- Decision — Capture the applied cursor and read immutable pages. Stage deterministic replay evidence in a bounded private spool before publishing the run and events atomically.
- Consequences — Calculation can overlap writes. Final writes remain serialized. The installed SQLite 3.45.3 falls in the documented WAL-reset bug range, so this change preserves the existing journal mode. [SQLite WAL documentation](https://sqlite.org/wal.html#the_wal_reset_bug)
- Files — `src/aurum/api.py`, `src/aurum/monitoring.py`, `src/aurum/storage.py`.

## ADR-017 — Journal observed market evidence separately from setup alerts

- Date — 2026-11-09.
- Context — The monitor calculated market evidence but persisted setup transitions only.
- Alternatives — Reconstruct historical market structure at chart time or record evidence while applying each minute.
- Decision — Persist the strategy step's evidence and context atomically with its applied minute. Keep existing monitor IDs and setup journal sequences.
- Consequences — Charts can separate source confirmation from observation during gap recovery. Schema v6 leaves legacy applied observation times unset.
- Files — `src/aurum/storage.py`, `src/aurum/strategy.py`, `src/aurum/monitoring.py`, `src/aurum/api.py`.

## ADR-018 — Forward execution uses the observation clock

- Date — 2026-11-09.
- Context — Closed-minute market evidence can arrive after the next minute has already opened.
- Alternatives — Reuse source close as the entry clock or require an open at or after actual observation.
- Decision — Register against the current applied cursor, ceil actual observation to a minute open, then add latency. Use the shared paper fill engine and persist the decision and observation clocks.
- Consequences — A timely direct observation can qualify. Recovered, stale or otherwise unverified data cancels pending paper entries and leaves open paper outcomes unresolved. Synthetic verification is a separate source class.
- Files — `forward.py`, `execution.py`, `monitoring.py`, `storage.py`.

## ADR-019 — Runtime profiles reference worker environment names

- Date — 2026-11-09.
- Context — The dashboard needs reproducible runtime settings without receiving feed or channel credentials.
- Alternatives — Store credentials, keep settings exclusively in CLI arguments or persist nonsecret profiles.
- Decision — Save content-addressed profiles with AURUM environment names. Resolve values only in CLI workers. Guard the bearer proxy with loopback host and same-origin mutation checks.
- Consequences — Profile creation does not launch workers. Production authorization and integration verification remain separate.
- Files — `runtime_profiles.py`, `api.py`, `cli.py`, `frontend/app/runtime-settings.tsx`.

## ADR-020 — Match random admissions on development signals

- Date — 2026-11-10.
- Context — Strategy evaluation needs a comparison that uses the same execution rules and controls for opportunity frequency without fitting on the final holdout.
- Alternatives — Reshuffle realized returns, match counts after observing the holdout or fit admission on development decisions.
- Decision — Sample qualified decisions from an analyst-selected structural opportunity variant. Estimate admission separately by session and direction, with optional regime, using pre-validation decisions only. Run the reference and seeded control replications from flat after the holdout embargo.
- Consequences — The same cost model and position constraints apply across arms. Trade counts can differ, zero-rate strata are reported and only paths with trades contribute to expectancy quantiles. All paths share one holdout, so quantiles describe conditional variation. The analyst must predeclare the opportunity rule and settings outside the software.
- Files — `src/aurum/control.py`, `src/aurum/execution.py`, `src/aurum/api_reports.py`, `tests/test_market_structure.py`.

## ADR-021 — Bind economic context to monitor identity

- Date — 2026-11-10.
- Context — Streaming and historical analysis must assess event risk with the same release-time evidence.
- Alternatives — Mutate a calendar in place or freeze it in each monitor definition.
- Decision — Store normalized point-in-time calendar content and use the shared strategy evaluator at each candle close. Omit new default provider fields and omit an absent calendar from monitor manifests.
- Consequences — Existing profiles and monitor IDs remain usable. Updating the snapshot creates a separate monitor. A forward baseline requires matching economic assumptions. Automatic source updates and schedule revision history remain open.
- Files — `economic_events.py`, `monitoring.py`, `runtime_profiles.py`, `forward_reports.py`.

## ADR-022 — Use stored prints to order dual touches

- Date — 2026-11-10.
- Context — Minute OHLC can show both brackets touched without exposing the sequence.
- Alternatives — Keep only stop-first, infer synthetic intrabar paths or use an ordered immutable print stream.
- Decision — Preserve stop-first as the default. The optional rule reconstructs the ambiguous minute from its source prints and applies the first bracket crossing, retaining the reference price, costs and logical observation clock.
- Consequences — The rule uses a separate saved run identity. A chronological candidate comparison requires the same ordering policy, and a minute-only forward baseline excludes this policy. Observed prints establish touch order. Actual fills and queue priority remain outside the model.
- Files — `execution.py`, `backtesting.py`, `storage.py`, `control.py`, `tests/test_execution.py`.

## ADR-023 — Restrict sensitivity comparisons to one pre-holdout factor

- Date — 2026-11-10.
- Context — Research needs transparent comparison of deliberate parameter changes while preserving the final holdout.
- Alternatives — Generate a new parameter search from the whole dataset or project selected candidates from saved chronological validation.
- Decision — Compare candidates only when all saved profiles agree after removing one declared setting. Reuse development and validation metrics, including original confidence and Holm adjustment.
- Consequences — Users can isolate a subset without changing the original comparison family. Differences are descriptive against a chosen baseline. The candidate lookup returns IDs only and the sensitivity report has no holdout outcomes.
- Files — `sensitivity_reports.py`, `api_reports.py`, `frontend/app/report-builder.tsx`, `tests/test_market_structure.py`.

## ADR-024 — Separate asynchronous quote observations from candles

- Date — 2026-11-10.
- Context — Top-of-book observations have distinct timing, availability and provenance from executed prints or candle OHLCV.
- Alternatives — Embed quotes in minute bars or persist a separate observed series.
- Decision — Keep a separate immutable normalized quote series with exact timestamp and sequence. A point-in-time read uses a strictly earlier quote within the same reviewed open window and explicitly classifies freshness.
- Consequences — Quotes stay out of candle construction and fill simulation. No observation is carried over a closure. A future spread experiment must report its source and sampling assumptions separately.
- Files — `quotes.py`, `timestamps.py`, `storage.py`, `tests/test_quotes.py`.

## ADR-025 — Treat iCalendar captures as partial point-in-time evidence

- Date — 2026-11-10.
- Context — Public release calendars can supplement economic context but a current file cannot establish historical publication availability or complete risk coverage.
- Alternatives — Infer availability from calendar edit metadata or use a known observation timestamp.
- Decision — Normalize selected timed events from a reviewed local capture and use its observed time for availability. Mark the snapshot partial and record the source checksum and parser versions.
- Consequences — A partial source can produce elevated risk. Absence leaves risk unknown. Recurring or unresolved timed releases are rejected. Automatic retrieval remains separate work. ADR-026 covers comparisons of preserved captures.
- Files — `economic_ics.py`, `economic_events.py`, `timestamps.py`, `pyproject.toml`.

## ADR-026 — Compare reviewed captures by UID and observation time

- Date — 2026-11-10.
- Context — A later publication can move or cancel a release. A missing item alone cannot establish either change.
- Alternatives — Compare titles and nearby times, trust edit metadata or track publication UIDs using preserved capture clocks.
- Decision — Follow a selected UID within one reviewed publication. Append changed titles and times at first observation. Require explicit cancellation of a known UID, and reject ambiguous UID or sequence evidence. Preserve all input checksums and observation times in the partial snapshot.
- Consequences — The prior schedule still informs earlier assessments. The last observed schedule remains possible after omission. Publishers with unstable UIDs may produce independent schedule entries. Comparison is local and does not attest capture times or refresh a running monitor.
- Files — `economic_ics.py`, `economic_events.py`, `cli.py`, `tests/test_economic_ics.py`.

## ADR-027 — Separate final holdout execution from selection

- Date — 2026-11-10.
- Context — Cutting trades from a full replay can leave holdout opportunities suppressed by orders or positions from earlier periods.
- Alternatives — Keep filtering completed full-path trades or save selection before running final execution independently.
- Decision — Bound selection reads to outcomes recorded strictly before the final boundary. Save that selection before separately opening a fresh paper executor for the chosen candidate after the embargo. Reuse the recorded causal strategy decisions and their original rule versions.
- Consequences — Full-replay validation remains reproducible. The new report measures a flat execution start with warmed strategy context. Existing raw data and other reports remain accessible to the operator, so analyst discipline still determines whether the period was unseen.
- Files — `statistics.py`, `research_reports.py`, `holdout.py`, `paper_replay.py`, CLI, API and Reports.

## ADR-028 — Isolate economic publication evidence when composing

- Date — 2026-11-10.
- Context — Relevant US releases are spread across publishers. Their event IDs, source clocks and coverage claims are independent.
- Alternatives — Infer matches by title and time or preserve separate publication namespaces.
- Decision — Compose a partial envelope with matching source classes. Attach original event IDs and normalized snapshot hashes. Retain individual coverage and capture provenance. Require revisions to stay within one publication.
- Consequences — A cancellation cannot suppress another source. Duplicate observations of the same release may remain separate. Bundle coverage establishes known event risk and leaves other windows unknown.
- Files — `economic_composition.py`, `economic_events.py`, `cli.py`, `tests/test_economic_composition.py`.

## ADR-029 — Archive public calendar responses before applying scope

- Date — 2026-11-10.
- Context — The operator needs a reproducible first-observed clock and unaltered evidence for official public calendar releases.
- Alternatives — Use calendar metadata as the observation clock, write paired local files or persist a receipt and shared raw blob transactionally.
- Decision — Accept an explicit fetch from a fixed official publisher. Bound the response, refuse redirects and capture the time after receiving the body. Store exact bytes once per checksum, with a receipt per observation. Normalize independently using the receipt.
- Consequences — Failed requests produce no receipt. Current captures cannot be used as earlier historical evidence. The worker continues to use a frozen reviewed snapshot. Polling and runtime refresh remain open.
- Files — `economic_archive.py`, `economic_ics.py`, `storage.py`, CLI and archive tests.

## ADR-030 — Activate reviewed economic updates on a durable clock

- Date — 2026-11-11.
- Context — A running monitor needs reviewed publication updates while retaining the evidence available for earlier decisions and restart.
- Alternatives — Replace the initial monitor definition, restart with a new identity or append timestamped updates to the monitor journal.
- Decision — Append immutable reviewed snapshots with an activation time later than all processed source and observation clocks. Source context resolves the snapshot at candle close. Alert and new paper-entry checks resolve it at actual observation. Restart replays the same activation timeline. A recorded unavailable update produces unknown risk.
- Consequences — Each affected decision identifies the applied update. Publication knowledge cannot predate activation. Existing alerts and outcomes retain their evidence. Historical baseline comparison requires compatible economic context, including adopted updates.
- Files — Planned `economic_updates.py`, `monitoring.py`, `strategy.py`, CLI and update tests.

## ADR-031 — Keep local service traffic in one network namespace

- Date — 2026-11-11.
- Context — Container services need to communicate while preserving the dashboard's loopback proxy and token boundary.
- Alternatives — Allow arbitrary Docker hostnames in the proxy, package both processes into one container or share an API network namespace.
- Decision — Separate API and dashboard containers share the API network namespace. Host port mappings bind to localhost. The API accepts an explicit container bind only with an access token. Base manifests and runtime dependencies are pinned.
- Consequences — Services share a network lifecycle, and Compose restarts the dashboard with its API dependency. Workers launch explicitly and share persistent data. Native launch and configuration passed. Container runtime verification is pending.
- Files — Dockerfiles, `compose.yaml`, `requirements-runtime.lock`, `frontend/next.config.mjs`, `docs/OPERATIONS.md`.

## ADR-032 — Preserve native vendor exports through shared validation

- Date — 2026-11-11.
- Context — Databento offers MGC history and live data, and its native exports contain vendor-specific identity and timestamp conventions.
- Alternatives — Rewrite source files externally or normalize the preserved source through an explicit adapter.
- Decision — Add a versioned Databento minute CSV format, requiring explicit pretty export options, one dated MGC symbol and one publisher/instrument pair. Reuse exact tick, calendar, duplicate, gap and provenance logic.
- Consequences — Normalized rows can be reproduced from their original bytes. Epoch timestamps and fixed-point price encodings require an explicitly supported format. A missing vendor bar cannot establish a no-trade interval by itself, so affected research stays gated.
- Files — `databento_csv.py`, `market_data.py`, `providers.py`, CLI, dataset audit and adapter tests.

## ADR-033 — Publish report notifications through the existing outbox

- Date — 2026-11-11.
- Context — Saved daily, weekly and monthly forward reports need optional delivery without repeated summaries after worker restart.
- Alternatives — Direct sends from the report worker or a shared durable alert queue.
- Decision — Queue a source-labeled immutable report summary atomically with the period publication marker. Use the saved report ID for deduplication. Manual queuing supports older saved reports. Existing delivery workers retain all channel, expiry, quiet-hour and attempt rules.
- Consequences — Report creation remains usable offline. No channel is contacted by the publishing command. Enabling notifications later does not automatically resend earlier publications. Past-period summaries remain eligible when a feed is stale, while confirmed setup delivery still requires freshness.
- Files — `report_notifications.py`, `forward_reports.py`, notification transports, CLI and forward report tests.

## ADR-034 — Keep native data recovery in the shared monitor

- Date — 2026-11-11.
- Context — The recommended vendor's native stream should feed existing causal detection without a separate strategy implementation.
- Alternatives — Require an external normalized WebSocket bridge or implement an SDK frame source.
- Decision — Add a pinned Databento source with bounded callback buffering, exact fixed-point conversion, prior dated symbol mapping and preserved DBN bytes. Native replay remains historical until explicit completion. Resolve restart from the next reviewed minute, within a 23-hour recovery horizon, and use existing monitor retries and integrity faults.
- Consequences — No credentials enter persisted configuration. No paid history query or live connection is required for synthetic SDK tests. Longer gaps and missing no-trade intervals remain blocked. Native subscription behavior still needs authorized verification under L02.
- Sources — [Client](https://databento.com/docs/api-live/client?lang=python), [symbology](https://databento.com/docs/conventions/symbology), [OHLCV](https://databento.com/docs/schemas/ohlcv).
- Files — `databento_live.py`, shared streaming, CLI, dependency pins and native tests.

## ADR-035 — Poll public calendars through durable receipt claims

- Date — 2026-11-11.
- Context — Official economic publications need periodic capture and explicit outage status without rewriting reviewed monitor context.
- Alternatives — An external cron with no shared state, automatic monitor replacement or a durable foreground capture worker.
- Decision — Store versioned cadence, exclusive attempt leases, actual receipt references and bounded exponential backoff. Fetch outside write transactions. Treat an expired claim as uncertain and retain any separately archived receipt. Adoption stays explicit and reviewed.
- Consequences — Concurrent workers avoid repeated requests, restart preserves due time, and read-only health remains available. Source failure does not mean no event exists. Normalized backup audit reconstructs attempt cadence. Scheduled service hosting is an operator action.
- Sources — [BEA release schedule](https://www.bea.gov/news/schedule), [BLS calendar](https://www.bls.gov/help/hlpiCAL.htm).
- Files — `economic_polling.py`, schema v16, CLI/API, source health UI and polling tests.

## ADR-036 — Reconstruct monitor inputs before restart analysis

- Date — 2026-11-11.
- Context — Dataset audits covered imports, but monitor candle rows could differ from their retained frame evidence after storage corruption.
- Alternatives — Trust normalized monitor rows or reconstruct them through shared frame/native decoding.
- Decision — Add a read-only bounded input audit and include it in verified backups. During restart, validate each retained source against its candle before advancing the detector; persist an integrity fault on failure. Native reconstruction uses the production decoder without a client or credential read.
- Consequences — Source proof and unknown legacy clock limits remain explicit. Existing monitor IDs are preserved. Restart costs slightly more; chart reads retain their existing projection path. Full detector/alert-journal reproduction is a separate validation scope.
- Files — `monitor_audit.py`, native decoder, monitoring restart, storage verification, CLI and tests.
