# AURUM — DEVELOPMENT HANDOFF

## Project Status

- Current phase — Local historical research, paper execution, monitoring and the dashboard are verified on labeled synthetic fixtures. Production data and delivery verification remain blocked.
- Current milestone — Email delivery is verified on synthetic SMTP transports. Local deployment packaging passed native smoke and configuration. The CI workflow passed local actionlint. Container and hosted execution remain open.
- Current branch — `main`.
- Last verified code commit — `3b99888`.

## Current Objective

- Task ID — O03 and O02.
- Objective — Validate the workflow and attempt the isolated container smoke test when a runtime is available.
- Acceptance criteria — CI passes local linting and runs the documented gates. Confirm API authorization, standalone assets, persistent data and worker integrity inside the packaged services. Disclose unavailable execution environments.

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

- Schema v15. Recent changes are committed. Temporary preview services are stopped.
- L13 picker browser verification prepared and copied the expected synthetic capture without creating a provider profile. L15 adds reviewed CLI/API activations, observation/source-time gating and restart reconstruction through immutable activation history.
- Docker CLI is installed, but its daemon and Compose/buildx plugins are unavailable. Inspect installed runtime options before treating containers as verified.
- Standalone `docker-compose` exists and validated the service configuration. Pinned image manifests and runtime lock are committed. Native standalone dashboard served assets and proxied the authenticated API. Starting Colima failed on a GitHub VM image download, and it was stopped. Container execution remains O02.
- D12 imports pretty-formatted Databento `ohlcv-1m` CSV with exact raw provenance and audit reconstruction. Missing vendor bars remain gated. No authorized Databento data has been imported.
- N04 adds authenticated STARTTLS/implicit TLS email with durable retry and uncertain acknowledgement handling. Profiles persist environment names only. Eleven fake SMTP cases passed. External email was not sent.
- Completion is 87.8 percent, 43 of 49 roadmap tasks DONE. Three tasks are BLOCKED on production inputs. O02/O03 execution evidence is still incomplete, and optional ML awaits historical evidence.
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

- Passed at the latest checkpoint — `.venv/bin/python -m pytest -q` (257), `.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`, `.venv/bin/mypy` (91 files), actionlint and Git whitespace checks. Deployment checkpoint pip check passed.
- Frontend — Five tests, TypeScript typecheck and production build passed at the prior code checkpoint. Browser verified archive receipt selection, preview and copied JSON with zero profiles saved.
- Failed — No outstanding automated failures.
- Not run — Authorized historical reconciliation, real provider or channel verification, and empirical out-of-sample analysis.
- Limitations — The performance workload, socket and delivery exercises, quotes and strategy outcomes are synthetic. Delivery tests use fake transports.

## Environment

- Python 3.12.7, macOS arm64, SQLite 3.45.3. SQLite 3.37+ required.
- Install `.venv/bin/python -m pip install -e '.[dev,parquet,stream,api,calendar]'`. Frontend commands run through `npm --prefix frontend`.
- No external services are needed for tests. Keep licensed inputs, SQLite files and credentials out of Git.

## Immediate Next Actions

1. Inspect the current Colima start attempt. Stop it when container validation is finished or its image download fails. All unrelated existing Colima profiles were stopped before this session.
2. Commit the locally validated CI workflow and record whether container execution succeeded. Hosted workflow execution requires the repository's normal remote workflow.
3. When approved inputs arrive, run the historical reconciliation and integrity checks before any empirical backtest or production monitor trial.

## Do Not Repeat

- Reuse verified ingestion, structure, replay, cost, report and monitoring paths. Keep test outcomes and historical evidence distinct.
- Preserve earlier monitor, profile, dataset and report identities where defaults are unchanged.
- Keep live order execution and brokerage credentials outside the repository.

## Continuation Instruction

Resume from the latest verified checkpoint. Inspect the referenced implementation, complete the next unblocked task, run relevant tests, and update the handoff.
