# AURUM — DEVELOPMENT HANDOFF

## Project Status

- Current phase — Offline research, forward paper simulation, monitoring and dashboard. Production data validation remains blocked.
- Current milestone — L17 public polling, D13 monitor input verification, N06 lifecycle alerts, reconnect safeguards and replay reader fix passed hosted CI. Monte Carlo account breach reporting passed local/browser checks.
- Current branch — codex/verified-research-platform, tracking origin at https://github.com/davesm-jpg/AURUM.git.
- Last verified commit — 8f2be56 passed all three hosted jobs in run 43089679113. Subsequent monitor journal export maintenance passed focused checks before this snapshot.

## Current Objective

- Task ID — O01 reliability maintenance (STORE-002).
- Objective — Publish verified nonblocking monitor exports and inspect hosted checks.
- Acceptance criteria — Captured event sequence and alert membership stay fixed while payload pages release readers before yielding; concurrent writes commit and remain outside the export.

## Completed Work

- Data — D01–D03 and D05–D12 implement exact dated MGC candles, reviewed exchange schedules, aggregation, CSV/Parquet/HTTPS, normalized prints and quotes, reconciliation, contract rolls and separate continuous research prices. Preserved bytes and normalized rows are auditable offline.
- Detection — S01–S03 share causal replay and monitoring for confirmed pivots, BOS/MSS, liquidity proxies/sweeps, FVG/IFVG, context, versioned scoring and integer paper sizing.
- Research — B01/B03–B06 and Q01–Q06 implement conservative paper execution, ordered-print ambiguity resolution, quote cost comparisons, chronological selection, flat final holdout, matched controls, bootstrap, Monte Carlo and LucidFlex scenarios. No empirical MGC strategy performance has been measured.
- Monitoring — L01/L03–L16 implement durable streaming, gap recovery, storage retries, archived public iCalendar capture/comparison/composition, reviewed runtime economic activations and native Databento SDK minute feeds. Replay bars remain historical until explicit completion. Native mapping and OHLCV DBN bytes are retained.
- Product — U01/U02/F01 provide loopback API, Next.js dashboard, traceable chart overlays, report/configuration workspaces and independent forward paper tracking. Native profiles store environment names only. Browser verified saving a synthetic profile with zero monitors started and the correct primary strategy default.
- Delivery — N04/N05 and L03 implement Telegram/Discord/email transport and durable report notifications. All channel tests use fake transports. No actual message has been sent.
- Operations — O02/O03 package unprivileged services and explicit workers, with pinned dependencies and hosted Python/frontend/container gates. CI verified synthetic data survives replacement, normalized worker audit, standalone assets and API authorization. Local macOS Colima is stopped after its VM download failed.

## Work In Progress

- L17 uses schema v16 tables economic_pollers and economic_poll_attempts. An explicit foreground worker claims before HTTP, preserves successful receipts separately, backs off after failure and marks expired leases uncertain. Read-only CLI/API and Settings show health. Reviewed monitor adoption remains separate.
- D13 adds monitor_audit.py and verify-monitor. It verifies input source equivalence in bounded pages, includes pending rows and preserves legacy clock limits. Native reconstruction reuses the SDK decoder without a client/key. Store and backup audits include monitors. Restart source/manifest mismatch latches an integrity fault before replaying detector state. Ten synthetic cases and 295 total tests passed. It does not reproduce detector/alert journals or prove vendor entitlements.
- N06 adds lifecycle_alerts on real-time monitors/profiles and direct CLI flags. False is omitted from serialization to preserve legacy IDs. Same-close transitions collapse per setup. Withdrawals cancel pending/retry developing messages, including invalidations learned from recovered history, and disclose source class/observation time. Developing ideas retain freshness, risk, score/session checks. Informational withdrawals retain severity/quiet/expiry/rate controls. Already accepted or in-flight messages cannot be recalled. Five synthetic lifecycle cases and 302 total tests passed. Browser saved an enabled synthetic profile with zero monitors/minutes. No external message sent.
- Nine synthetic polling cases passed, including concurrency, interruption after receipt, migration, failures and fail-closed verified backups. Full suite passed 285 tests. Frontend typecheck/build and browser health display passed.
- Actual public BEA receipt 54bfea8b12144d5b197f1e1d82c2573a245bd239f63a606c70f66536fb692b5a was captured at 2026-11-11 18:36:20.778515 UTC. BLS returned HTTP 403. Ignored local data/public-economics.sqlite holds one capture, two pollers and two attempts. Source and normalized store audits passed.
- No foreground preview or polling process remains running.
- Roadmap lists 50/54 DONE (92.6 percent), three BLOCKED tasks and O01 IN_PROGRESS.
- Reconnect maintenance recognizes OSError causes/context wrapped by the pinned SDK's BentoError, including suppressed timeout context. Unqualified authorization errors remain permanent. Callback registration is inside the same sanitized boundary. Shared run_stream suspends the saved connection before opening a replacement transport. Five added synthetic cases passed; no actual provider or delivery call was made.
- STORE-001 is resolved: DatasetStore.replay reuses immutable 1,000-row pages and closes the reader before yielding. Two regressions failed with database-is-locked before the fix and now commit another writer before/after the page boundary, preserving all full/bounded candles. No schema or rule version changed.
- MC-001 is resolved: Monte Carlo v2 separately retains unrestricted full-horizon strategy paths and stopped-account distributions, including crossing losses. Full return consumption preserves seeded paths. CLI/API persist the version and policy. Browser verified the all-loss assumed case and unavailable metrics on a legacy-shaped fixture; zero market observations. ADR-038 records the boundary. Temporary store: /var/folders/m4/rknhph5d7v15y87fjlx_4vgc0000gn/T/aurum-breach-preview-1yq0s6jn/assumed.sqlite. Preview servers are stopped.
- STORE-002 fixes demonstrated writer locks in both monitor exports. Events use a captured maximum sequence; alerts retain ordered starting IDs and read at most 256 payloads per page. Regressions verify writes, fixed membership and timestamp ties across pages. ID metadata memory remains proportional to alert count; no schema/identity changed. ADR-039 documents this choice.
- O01 optimization remains open. The 21,600-minute synthetic benchmark retained all chart digests after restart source validation. Ingestion measured 4.519 s and restart 1.168 s versus the prior approximately 0.8 s. See PERFORMANCE.md. Optional ML awaits historical evidence.

## Important Decisions

- UTC source availability, callback receipt and processing clocks stay distinct. Never backdate confirmations or use stale prices for new alerts.
- Exact source bytes and dated contract identity stay separate from adjusted research prices.
- Keep final holdout outcomes outside configuration selection. Keep synthetic, assumed, historical and forward results distinct.
- Runtime profiles persist variable names, not secret values. No live execution or brokerage connections.
- ADR-035 defines durable public capture and reviewed adoption. See DECISIONS.md and ARCHITECTURE.md.

## Known Issues

- D04/B02/L02 — Authorized bounded MGC history, its matching reviewed schedule, production feed and approved channel configuration are absent.
- DATA-002/003 — Sparse omitted minutes block replay; large imports are buffered. Do not fabricate bars.
- EVENT-001 — BLS public iCalendar returns 403 here. FOMC ingestion remains open. Public receipts are partial and require review; successful polling does not establish clear risk.
- MONITOR-001 — Legacy rows can lack observation clocks. SQLite bulk writers serialize; paused public replay no longer holds a read lock.
- ACCOUNT-001 — Provider unrealized-breach/DLL ambiguities remain explicit simulation assumptions.
- Local containers lack a running macOS daemon. Hosted Linux verification passed.

## Test Status

- Last full command — .venv/bin/pytest -q — 313 passed, including both paused monitor exports. Ruff check/format and strict mypy (100 files) passed.
- Static — Ruff check/format, strict mypy (100 files), Git whitespace, pip check and actionlint passed. Frontend typecheck/build and five tests passed for lifecycle Settings. Browser verified unchecked default, enabled profile saving and zero monitor creation.
- Integration — Real public BEA/BLS one-shot attempts and browser capture health verified. Source/normalized audit passed on the ignored public-data store.
- Hosted — All three jobs passed for Monte Carlo v2 8f2be56 in run 43089679113. Monitor export maintenance awaits publication.
- Failed — No outstanding automated failure.
- Not run — Authorized MGC reconciliation, actual feed/channel trials and empirical out-of-sample evaluation. Do not interpret fixture outcomes as evidence.

## Environment

- Python 3.12.7, macOS arm64, SQLite 3.45.3; SQLite 3.37+ required.
- Local .venv includes pinned runtime extras and Databento SDK 0.90.0. Native SDK tests use constructed records and fake clients.
- npm --prefix frontend runs frontend commands. No external service is needed for automated tests.
- Market keys, destinations, licensed data and SQLite files stay outside Git.

## Immediate Next Actions

1. Commit/push verified monitor export maintenance and inspect its hosted run.
2. Inspect the next evidence-backed reliability gap without reimplementing completed features. Optional ML remains dependent on authorized historical evidence.
3. For D04/B02/L02, wait for authorized data/configuration. Recommended provider was Databento; no purchase or access has been confirmed. Keep keys local and do not issue paid queries without authorization.

## Do Not Repeat

- Reuse existing detection, execution, audit, profile and delivery paths.
- Preserve existing dataset, report, monitor and legacy profile identities.
- Do not restart Colima or duplicate hosted runs without a new reason.
- Do not leave a foreground worker running or promise ongoing agent work.

## Continuation Instruction

Resume from the latest verified checkpoint. Inspect the referenced implementation, complete the next unblocked task, run relevant tests, and update the handoff.
