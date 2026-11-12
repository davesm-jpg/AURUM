# AURUM — DEVELOPMENT HANDOFF

## Project Status

- Current phase — Offline research, monitoring, forward paper and dashboard implementation. Production data validation is blocked.
- Current milestone — CI-001 and setup preparation passed all hosted jobs. Native SDK CSV roundtrip passed the 331-test full suite and static checks.
- Current branch — codex/verified-research-platform, tracking origin at https://github.com/davesm-jpg/AURUM.git.
- Last verified commit — 1e5df19 passed all three hosted jobs in run 43173545400 and 330 local tests. The CI pin f816054 also passed all jobs in 43172077189 without the runtime annotation.
- Total completion — 50/54 roadmap tasks DONE (92.6 percent), three BLOCKED and O01 IN_PROGRESS. This is task completion, not empirical strategy validation or production readiness.

## Current Objective

- Task ID — O01 audit maintenance.
- Objective — Publish native SDK export proof, then add deterministic reconstruction of saved research journals.
- Acceptance criteria — Use original versions, source and shared detection/execution paths; compare persisted events without changing them or exposing holdout outcomes during selection.

## Completed Work

- Data — D01–D03/D05–D13 provide dated MGC imports, reviewed schedules, aggregation, reconciliation, print/quote data, continuous research plans and preserved-source audits.
- Detection/research — Shared causal pivots, BOS/MSS, liquidity, FVG/IFVG, context, setup states, scoring and integer paper sizing. Conservative backtesting, independent flat holdout, matched controls, sensitivity, bootstrap, Monte Carlo and account scenarios exist. No empirical MGC advantage has been measured.
- Monitoring/product — Durable normalized/native streaming, recovery, source validation, economic snapshots and reviewed activation, notifications, forward paper reports, API/SSE and Next.js terminal. Secrets are referenced by local variable names only.
- L17 — Schema v16 public calendar polling has durable claims, leases, retries, actual receipts, health and normalized audit. Adoption remains reviewed. Actual BEA receipt was captured on 2026-11-11; BLS returned HTTP 403. No scheduler remains running.
- D13 — verify-monitor reconstructs normalized/replay/native input with the pinned SDK decoder and no client/key. Restart verifies retained sources before detector recovery. It does not reproduce detector/alert journals or prove vendor entitlement.
- N06 — Optional developing/withdrawal notices collapse same-close stages, retain freshness/risk controls and cancel pending stale advisories. Disabled default IDs remain compatible. Browser verified profile saving with zero monitors started. All channel tests use fake transports.
- Reconnect — b05d4da retries SDK-wrapped socket/timeout errors by type/causal chain, keeps authorization failures permanent, redacts provider text and suspends prior-process readiness before connection. Hosted run 43088269838 passed.
- Readers — f6a1d63 releases public replay readers before yielding (hosted 43088753935). eee1a60 freezes monitor event sequence and alert ID membership with bounded payload pages (hosted 43090738305). Regressions reproduced writer locks before the fixes.
- Monte Carlo — 8f2be56 separates unrestricted strategy paths from accounts stopped after the first crossing loss, retaining overshoots and full RNG consumption. Version 2 gives new report IDs. Browser verified current/legacy labels; hosted 43089679113 passed.
- Clock guard — 8d3714f preserves new frames pending when processing time precedes the prior check. A versioned feed_clock_regressed event records actual time. A later frame at or beyond the bound recovers without fresh eligibility. Restart retains the hold. Heartbeats alone do not apply pending input.

## Work In Progress

- Monitor audit v2 rejects known application observations that decrease in source order, carrying the prior known bound across unknown legacy rows. Verified backups reject the same defect without publishing a partial file. Two regressions failed before the change and now pass.
- Final audit commit c84eacd is published; hosted run 43091664364 passed Python, frontend and containers. A separate 28-case synthetic differential check matched all pre-existing Monte Carlo fields against f6a1d63, confirming unchanged seeded unrestricted paths.
- No schema, dataset identity or monitor identity changed. Existing recorded observation times are not rewritten.
- O01 remains open: additional optimization needs workload evidence; optional ML requires authorized historical and out-of-sample evidence.
- On 2026-11-12 the user confirmed no provider account yet and chose Telegram. Do not ask these preferences again. docs/SETUP.md explains account/export steps, local credentials and separate real delivery authorization. telegram-inspect reads fixed bot metadata methods, skips polling when a webhook exists and returns only bounded private chat ID candidates. It sends and acknowledges nothing. Thirteen fake cases passed; no actual credentials or Telegram request were used.
- Native CSV compatibility is verified through an actual pinned SDK export from three constructed synthetic DBN bars and symbol metadata. Import and source audit pass, and both network client constructors are blocked in the test. Focused SDK/Telegram checks passed 26 tests.

## Important Decisions

- Keep source availability, actual receipt and actual application distinct; never invent an application clock to hide rollback.
- Keep final holdout outcomes outside configuration selection. Synthetic, assumed, historical and forward results remain distinct.
- ADR-035/036 cover durable public capture and monitor input reconstruction; ADR-037 covers lifecycle notices; ADR-038 separates strategy/account paths; ADR-039 defines nonblocking immutable export membership; ADR-040 defines rollback handling and audit v2.
- Keep research/paper execution only. No brokerage connection, live orders, purchases or actual notification sending was authorized by these tests.

## Known Issues

- D04/B02/L02 — User has no provider account yet. No authorized historical export, reviewed matching schedule, production feed or approved delivery destination has been supplied. Telegram is selected; credentials remain local. First needed input is an original small export in data/incoming plus its path, raw dated symbol and requested UTC period. We prepare the schedule after dates are known.
- DATA-002/003 — Sparse omitted minutes block replay; large imports are buffered. Never fabricate candles.
- EVENT-001 — BLS returns HTTP 403 in this environment; FOMC ingestion remains open. Partial publication evidence cannot establish clear economic risk.
- MONITOR-001 — Unknown legacy observation clocks cannot be recovered. Audit v2 does not establish chronology through unknown timestamps.
- ACCOUNT-001 — Provider unrealized-breach/DLL ambiguities remain disclosed paper assumptions.
- SQLite bulk writes serialize. Alert export retains O(alert count) ID metadata, with at most 256 payloads per page.
- Local macOS container daemon is unavailable after a prior Colima VM download timeout. Hosted Linux container verification passed.
- CI-001 is resolved. GitHub API/CLI status requests timed out from this host; public browser run summaries worked without authentication. Prefer that fallback over repeated API retries if the same connectivity fault recurs.

## Test Status

- Last full command — .venv/bin/pytest -q: 331 passed, including actual pinned SDK export from constructed synthetic DBN records.
- Static — Ruff check/format, strict mypy (102 files) and Git whitespace checks passed. actionlint passed for the v7.1.0 pin.
- Frontend — Latest UI change (Monte Carlo) passed typecheck, five tests, production build and browser current/legacy report verification. Later changes are backend-only.
- Hosted — Browser verified Python, frontend and containers passed for 1e5df19 in 43173545400 and f816054 in 43172077189. No code verification is pending for those commits.
- Failed — No outstanding automated failure. Regression failures before fixes are recorded in VALIDATION.md.
- Not run — Authorized historical reconciliation/backtesting, production feed/channel trials and empirical out-of-sample evaluation.

## Environment

- Python 3.12.7, macOS arm64, SQLite 3.45.3; SQLite 3.37+ required.
- Local .venv contains pinned runtime extras and Databento SDK 0.90.0. Automated SDK tests construct records/use fake clients.
- No external services are needed for tests. Frontend commands use npm --prefix frontend.
- No preview, API, polling or notification worker remains running.
- Ignored data/public-economics.sqlite holds the actual BEA receipt and failed BLS polling evidence. Market keys, destinations, licensed data and SQLite files remain outside Git.

## Immediate Next Actions

1. Read AGENTS.md, this handoff, Git status and relevant roadmap before resuming. c84eacd is the verified code checkpoint; do not repeat its completed fixes.
2. Continue saved research-journal reconstruction: factor the existing replay factory/manifest into a pure domain module, strictly decode pinned configs/versions, replay original verified source through the same engine and compare exact event sequences. Add explicit verify-run and opt-in verify-research backup checks; do not expose holdout outcomes through selection paths. Preserve all existing run IDs. This work is planned, not implemented yet. Do not repeat completed CI verification.
3. For D04/B02/L02, obtain authorized data/configuration. Databento was recommended; access or purchase is not confirmed. Keep keys local and do not issue paid queries without authorization.

## Do Not Repeat

- Reuse shared detection, execution, audits, profiles and delivery; preserve existing immutable IDs and results.
- Do not restart Colima's failed VM download or duplicate hosted runs without a new reason.
- Do not treat synthetic fixtures/assumed scenarios/public economic receipts as empirical MGC strategy results.
- Do not leave foreground services running or promise ongoing agent work.
- Include completion percentage and exact DONE/total count in user feedback, with blockers separate.

## Continuation Instruction

Resume from the latest verified checkpoint. Inspect the referenced implementation, complete the next unblocked task, run relevant tests, and update the handoff.
