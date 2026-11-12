# AURUM — DEVELOPMENT HANDOFF

## Project Status

- Current phase: Offline research, monitoring, dashboard and forward paper implementation; production validation is blocked.
- Current milestone: Official Fed monthly calendar source extension passed local checks and browser verification; ready to publish and verify hosted CI.
- Current branch: codex/verified-research-platform, tracking https://github.com/davesm-jpg/AURUM.git.
- Last verified commit: 88cfc38, all three hosted jobs passed in run 43176440170 and 362 local tests. Current Fed changes are uncommitted, locally verified with 403 tests.
- Total completion: 50/54 roadmap tasks DONE (92.6%), three BLOCKED and O01 IN_PROGRESS. This measures tasks, not empirical strategy validation or production readiness.

## Current Objective

- Task ID: L17 official-source extension, then next evidence-supported maintenance slice.
- Objective: Publish the verified Fed calendar integration, observe hosted checks and continue until the user's five-hour allowance reaches 5% remaining.
- Acceptance criteria: Preserve public bytes and actual receipt, use explicit release columns with a versioned timezone convention, keep partial coverage/reviewed adoption, pass tests and save this checkpoint.

## Completed Work

- Core: Dated unadjusted imports, reviewed schedules, aggregation, contract research mappings, print/quote data and source audits. Shared causal pivots/BOS/MSS, liquidity, FVG/IFVG, context, deterministic setup states, scoring and integer paper sizing.
- Research: Conservative execution, trade/performance audits, staged chronological selection, independent final holdout, matched controls, bootstrap, sensitivity, Monte Carlo and separate account simulation. No empirical MGC advantage has been measured.
- Product: Durable streaming/replay, recovery, source validation, economic snapshots and reviewed activation, notifications, forward paper reports, API/SSE and Next.js terminal. No live order capability.
- Setup: User has no provider yet and chose Telegram. docs/SETUP.md gives historical-first Databento account/export guidance and local Telegram preparation. telegram-inspect reads bounded bot metadata/private chat candidates, sends nothing, acknowledges no updates and preserves webhooks. Thirteen fake cases passed; no actual Telegram request or credentials used.
- SDK proof: 8b8ae96 exports three constructed synthetic DBN bars through the pinned SDK, then imports/audits them. Client constructors are blocked. Hosted run 43174313758 passed.
- Research audit: 88cfc38 extracts the shared replay factory without changing IDs or events. verify-run and opt-in verify-store/backup --verify-research reconstruct finalized journals. Thirty-one cases cover 646 frozen baseline events, versions, nested rules, corruption, invented profits, duplicate keys, ordered prints, concurrent writes and paging. It returns integrity/count/digest and stays outside holdout selection. Separate analysis, monitor and forward journals are excluded.
- Reliability: Published prior checkpoints fix SDK-wrapped retries, startup readiness, paused export readers, account metrics after breach, processing-clock rollback and audit chronology. CI-001 resolved with the verified setup-node v7.1.0/Node24 runtime pin. Reuse these paths.

## Work In Progress

- Uncommitted Fed slice: fed_calendar.py parses only explicit timed FOMC rows from official monthly HTML. Current New York month policy, bounded non-executing parser, source/month checks, DST ambiguity rejection and no description-date inference.
- Archive/poll/CLI reuse existing schema16 and receipt identity. BEA/BLS definitions and old capture-economic-ics import/CLI aliases stay compatible. Generic capture-economic-calendar supports fed.
- Normalization binds original bytes, source, actual receipt, parser, timezone and filters. Each Fed capture requires independent review because HTML has no stable UID. Coverage always partial; no inferred actual release, historical publication, cancellation or automatic monitor adoption.
- Settings explains one-Fed-capture/month scope and disables multiple Fed captures. Browser verified a partial one-release preview and the multi-selection gate. No profile or monitor saved.
- Actual public Fed capture succeeded after one recorded poll failure. Direct receipt cf8bdbdd7a8d4b76845afe69e16db74cbe31aa6cb9a432fa42f6b7611cf6830a at 2026-11-12T08:07:46.999971Z preserved 106,307 bytes. Due polling recovered with receipt 90bf896716916d64c76ad7bd255a30ebdcf4f3f7f150a6f961595c89eda053fc at 08:15:36.036587Z. One scheduled FOMC Minutes event normalizes to November18 19:00 UTC. Actual release unknown.
- O01 remains IN_PROGRESS: further optimization needs workload evidence; optional ML needs authorized history and out-of-sample benefit.

## Important Decisions

- ADR041: read-only Telegram onboarding; no implicit send authorization.
- ADR042: strict original-version shared replay reconstruction; exact producer text rejects duplicate-key ambiguity.
- ADR043: monthly Fed HTML has no timezone label. America/New_York is an explicit versioned inference checked against official winter EST/summer EDT release headers. Each partial receipt is reviewed separately.
- Preserve source-close, receipt and application clocks. Hold input through clock rollback rather than inventing a timestamp.
- Final held-out outcomes stay outside model selection. Keep synthetic, assumed, historical and forward results distinct.

## Known Issues

- D04/B02/L02 BLOCKED: no authorized historical MGC, matching reviewed production schedule, active subscription or approved production delivery destination. First needed input is a small original authorized unadjusted dated MGC export in ignored data/incoming, its local path/raw symbol/requested UTC period. Prepare the calendar once dates are known.
- EVENT001: BLS HTTP403 persists; broader event coverage and production adoption remain open. Partial Fed/BEA evidence never establishes clear risk.
- DATA002/003: omitted minutes block replay; imports buffer in memory. Do not fabricate candles.
- MONITOR001: legacy unknown application clocks cannot be recovered. ACCOUNT001: provider unrealized-breach/DLL ambiguity remains disclosed.
- SQLite bulk writes serialize. macOS container daemon unavailable after prior Colima download timeout; hosted Linux containers passed.
- GitHub API status calls timed out; public browser workflow summaries work. Prefer that fallback to repeated long retries.

## Test Status

- Passed: .venv/bin/pytest -q — 403 tests; Ruff format/check; mypy107 files; Git whitespace checks. Fed-focused module has41 cases.
- Frontend: TypeScript, five tests and production build passed; browser verified public polling states and partial preview.
- Public archive: verify-store --verify-sources --verify-normalized passed with3 receipts,3 pollers and4 attempts.
- Hosted: 88cfc38/43176440170, 8b8ae96/43174313758, setup1e5df19/43173545400 and runtime pin f816054/43172077189 all passed. Fed extension not pushed yet.
- Failed: no outstanding automated failure. Initial new tests exposed single-capture provenance misuse; fixed by retaining receipt identity in the snapshot version, consistent with the existing single-ICS path.
- Not run: authorized MGC reconciliation/backtests, empirical holdout evaluation, production market feed/Telegram delivery.

## Environment

- Python3.12.7, SQLite3.45.3, macOS arm64; .venv has pinned extras/Databento0.90.0. No external services needed for tests.
- Ignored data/public-economics.sqlite contains actual public Fed/BEA receipts and BLS failure. data/fed-reviewed-preview.json is a local partial preview, not an adopted monitor configuration.
- Newly started API8785/frontend3000 previews were stopped. A pre-existing Python listener PID89183 on8765 was preserved. No poller/watch or delivery worker started this session remains running.
- User has not authorized paid data requests, purchases or actual Telegram sends. Secrets stay local and outside chat/Git.

## Immediate Next Actions

1. Review current Git diff, run focused final checks for any further edits, commit the Fed source slice and push the existing branch.
2. Verify Python/frontend/container hosted jobs through the public GitHub Actions page, then update this handoff with the commit/run.
3. Continue evidence-supported fixes or measured profiling while usage remains above the requested5% threshold. Recheck actual allowance; do not infer remaining quota from context size.
4. For D04/B02/L02, follow docs/SETUP.md once the user supplies authorized data/local configuration. Do not ask provider/channel preferences again.

## Do Not Repeat

- Do not recreate shared replay factories, completed audits, setup guide or original SDK proof.
- Do not restart Colima's failed VM download, overwrite the existing8765 listener or issue repeated API status calls when the browser works.
- Do not treat synthetic fixtures, Monte Carlo assumptions or public economic receipts as actual MGC strategy results.
- Do not promise background agent work; no recurring automation was created.
- Every feedback includes92.6%,50/54 DONE and3 blocked unless roadmap statuses actually change.

## Continuation Instruction

Resume from the latest verified checkpoint. Inspect the referenced implementation, complete the next unblocked task, run relevant tests, and update the handoff.
