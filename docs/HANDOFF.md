# AURUM — DEVELOPMENT HANDOFF

## Project Status

- Current phase — Local historical research, paper execution, monitoring and the dashboard are verified on labeled synthetic fixtures. Production data and delivery verification remain blocked.
- Current milestone — Native Databento core, email, report notifications and packaged services passed hosted CI. Native provider Settings and profile extensions passed local and browser verification. Authorized market-data validation remains blocked.
- Current branch — `codex/verified-research-platform`, tracking the same branch at `https://github.com/davesm-jpg/AURUM.git`.
- Last verified code commit — `1d6d92c` passed 276 local tests, static/frontend checks and browser verification, and is published. Its hosted run 43004980567 was queued at shutdown. Native core `6a0bcf0` passed all hosted jobs in run 43004011688.

## Current Objective

- Task ID — D04, B02 and L02 production validation.
- Objective — Verify the latest hosted profile extension checkpoint, then prepare authorized history and provider configuration for actual validation.
- Acceptance criteria — Keep paid requests and real channel sends disabled without approved inputs. Reuse the shared quality and monitoring paths, verify offline and retain exact continuation evidence.

## Completed Work

- D01–D08 — CSV, Parquet and normalized HTTPS OHLCV, reviewed dated exchange schedules, completed aggregation, dated contract selection and separate continuous research prices. Normalized trade prints and standalone bid/ask series preserve exact UTC nanoseconds, source sequence and raw bytes. Reconciliation compares distinct compatible imports without lifting replay gates. Quote lookups enforce strict precedence, freshness and reviewed open windows.
- D09–D11 — `verify-dataset`, `verify-quote-series` and `verify-store --verify-normalized` rebuild normalized records from preserved source bytes. `backup --verify-normalized` audits the private staged copy before publication. Synthetic altered-row tests verify fail-closed publication.
- S01–S03 — Deterministic causal structure, liquidity proxies and sweeps, FVG/IFVG evidence, mirrored setup state, higher-timeframe context, score and integer paper risk.
- B01 and B03–B06 — Shared conservative paper replay and explicit costs. The ordered-print variant resolves dual touches from stored prints and has a separate run identity. Intrabar sensitivity pairs saved runs. Quote reports assess observed spread and optionally reprice the paired subset at fixed fills.
- Q01–Q06 — Chronological validation, Monte Carlo, LucidFlex paper rules, development-fitted matched control, grouped execution diagnostics and pre-holdout one-factor sensitivity. Staged selection excludes all final outcomes and final evaluation starts paper execution flat after embargo. The browser exercised both reports. No historical strategy performance has been measured.
- L01 and L03–L13 — Durable normalized WebSocket monitoring, gap backfill, restart, health and optional Telegram/Discord delivery. Transient SQLite retries preserve transport receipt and record later processing while suppressing affected alert and forward eligibility. Economic snapshots can be imported from reviewed local iCalendar captures, can represent causal revisions and can be supplied through direct monitoring or provider profiles. Source-close and observation-time event risk remain separate. Standalone reviewed schedules can configure a worker before any dataset import. Ordered local iCalendar comparisons preserve capture digests, first observations and explicit cancellations. Composed partial snapshots retain isolated source IDs and per-source provenance. Indexed event assessment matched its prior output digest. Official BEA capture archived 43,459 bytes and normalized two selected releases using the saved clock. BLS returned HTTP 403. Normalized store and backup checks validate archived receipts and bytes. The offline API preview does not change the store.
- U01–U02 and F01 — Loopback API and dashboard, chart evidence with original availability, local report creation, provider settings, rollover and continuous preview. A forward consumer registers at an applied cursor, audits observed paper execution and can save completed daily, weekly and monthly periods. Browser checks exercised synthetic matched controls, reconciliation, provider settings, rollover and adjusted versus dated prices.
- O01 — The repeatable 21,600-minute synthetic workload measured 4.44 s batched monitor ingestion, approximately 0.81 s restart and 6, 10 and 12 ms for the three chart projections. All projection SHA-256 digests matched the recorded baseline. See `docs/PERFORMANCE.md`.

## Work In Progress

- Schema v15. Temporary preview services and the failed Colima runtime are stopped.
- L13 picker browser verification prepared and copied the expected synthetic capture without creating a provider profile. L15 adds reviewed CLI/API activations, observation/source-time gating and restart reconstruction through immutable activation history.
- Docker CLI and standalone `docker-compose` are installed locally, but no daemon is running. The user-provided GitHub destination was empty. The checkpoint was published without overwriting remote work. Hosted run 43001928370 on `03c3da4` passed Python, frontend and containers. Container verification included two synthetic minute imports, persisted data after replacement, normalized worker audit, standalone assets and direct API 401. Local macOS container verification remains unavailable.
- D12 imports pretty-formatted Databento `ohlcv-1m` CSV with exact raw provenance and audit reconstruction. Missing vendor bars remain gated. No authorized Databento data has been imported.
- N04 adds authenticated STARTTLS/implicit TLS email with durable retry and uncertain acknowledgement handling. Profiles persist environment names only. Eleven fake SMTP cases passed. External email was not sent.
- N05 queues saved daily/weekly/monthly report summaries atomically with publication. Manual `notify-report` deduplicates saved report IDs. Existing channel preferences and audit apply. Source classification, actual sample, net costs, coverage and uncertainty remain visible. No external message was sent.
- L16 implements native SDK minute monitoring with DBN evidence, dated mapping, bounded callbacks, integrity gates and historical intraday recovery. Settings saves native profiles with key variable names only; explicit workers bind the shared next-minute resume. Sixteen synthetic native cases and 276 total tests passed. Hosted native core passed. Browser saved one synthetic profile, and SQL confirmed zero monitors/minutes. A rebuilt reload verified the corrected primary strategy default. No authorized vendor connection opened.
- Completion is 92.2 percent, 47 of 51 roadmap tasks DONE. Three tasks are BLOCKED on production inputs. O01 remains IN_PROGRESS; optional ML awaits historical evidence.
- User asked which provider to obtain. Recommended Databento CME Standard using current official MGC coverage and pricing, USD 199/month for personal use. User has not yet confirmed a purchase or provided account access. Keep keys local.
- D04, B02 and L02 need authorized history, a matching reviewed schedule and approved production feed or delivery configuration.

## Important Decisions

- Source bytes and normalized evidence have immutable identities. Preserve dated contract identity and separate adjusted research prices from executable references.
- Read bounded pages from immutable data and stage replay computation outside long SQLite locks. Keep received, processing and source confirmation clocks distinct.
- Keep the final holdout out of configuration selection and sensitivity reports. Matched-control replications are conditional on shared structural opportunities.
- Use conservative paper fills by default. Opt-in ordered prints establish crossing order. Quote cost projections retain the original fill prices and use only the paired subset.
- Use environment variable references for authorized credentials. Live brokerage execution, funded accounts and real position management remain out of scope.

## Known Issues

- DATA-001 and CAL-002 — Authorized historical MGC and a matching reviewed production schedule are absent. Real outcomes and operating coverage remain unverified.
- DATA-002 and DATA-003 — Sparse expected minutes block replay, and normalization buffers source data. Use bounded exports for large sources.
- EVENT-001 — Scheduled economic polling remains open. Reviewed updates can be adopted through CLI/API. The official BLS iCalendar request returned HTTP 403 from this runtime. Local reviewed captures are supported.
- MONITOR-001 and STORE-001 — Legacy journals can lack observed timestamps. Long SQLite transactions still serialize. Conflicting duplicates require a corrected monitor run after investigation.
- ACCOUNT-001 — Public LucidFlex documentation leaves unrealized breach treatment and optional DLL amount open. Keep those simulation assumptions explicit.

## Test Status

- Passed at the latest checkpoint — `.venv/bin/python -m pytest -q` (276), native/API focused tests (24), `.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`, `.venv/bin/mypy .` (95 files), pip check, actionlint and Git whitespace checks. Four hosted CI runs passed all jobs through `6a0bcf0`.
- Frontend — Five tests, TypeScript and production build passed. Browser verified native profile saving and the corrected primary strategy default after a rebuilt reload. Earlier archive receipt selection was also verified.
- Failed — No outstanding automated failures.
- Not run — Authorized historical reconciliation, real provider or channel verification, and empirical out-of-sample analysis.
- Limitations — The performance workload, socket and delivery exercises, quotes and strategy outcomes are synthetic. Delivery tests use fake transports.

## Environment

- Python 3.12.7, macOS arm64, SQLite 3.45.3. SQLite 3.37+ required.
- Install `.venv/bin/python -m pip install -e '.[dev,parquet,stream,api,calendar]'`. Frontend commands run through `npm --prefix frontend`.
- No external services are needed for tests. Keep licensed inputs, SQLite files and credentials out of Git.

## Immediate Next Actions

1. Inspect `gh run view 43004980567 --repo davesm-jpg/AURUM` for published commit `1d6d92c`. Fix any actual failures before further features. Do not dispatch duplicate workflows. Temporary API/dashboard services were stopped. The final handoff-only commit is local; publish it after inspecting this run.
2. Obtain authorized bounded MGC history and a matching reviewed schedule. Preserve D04/B02/L02 as blocked until actual verification. Keep keys local, and do not query paid endpoints without confirmed authorization.
3. When approved inputs arrive, run historical reconciliation and integrity checks before any empirical backtest or production monitor trial. Recommended provider is Databento CME Standard; keys must stay in local environment variables.

Session stopped at the user's requested five-hour usage boundary, with 5 percent remaining. No worker or preview service was left running. GitHub CI is an actual hosted workflow, not ongoing agent work.

## Do Not Repeat

- Reuse verified ingestion, structure, replay, cost, report and monitoring paths. Keep test outcomes and historical evidence distinct.
- Preserve earlier monitor, profile, dataset and report identities where defaults are unchanged.
- Keep live order execution and brokerage credentials outside the repository.

## Continuation Instruction

Resume from the latest verified checkpoint. Inspect the referenced implementation, complete the next unblocked task, run relevant tests, and update the handoff.
