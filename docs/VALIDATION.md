# AURUM validation

## Verified checkpoint — 2026-11-10

- Environment. macOS arm64, Python 3.12.7, SQLite 3.45.3 and the timezone package pinned in `pyproject.toml`.
- `.venv/bin/python -m pytest -q` passed 159 cases with PyArrow 26.0.1 and websockets 15.0.1 installed. New tests cover notification concurrency, retry timing, uncertain results, health, quiet hours, cancellation and fake Telegram/Discord payloads. Monitor tests cover local WebSocket, gaps, restart, source status, rollback and synthetic parity.
- A synthetic SQLite database created by the v1 implementation was opened and migrated. `cmp` verified identical provenance and replay before and after the write migration. SQLite's integrity check passed. The original dataset ID is asserted in the CLI test.
- `.venv/bin/ruff check .` passed.
- `.venv/bin/ruff format --check .` passed.
- `.venv/bin/mypy .` passed across 61 source/test and benchmark files.
- `.venv/bin/python -m pip check` reported no broken requirements.

Three storage operations tests verified a private snapshot with identical source and replay bytes, rejection of an existing output, cleanup on timeout and detection of modified preserved source data. [SQLite Online Backup API](https://sqlite.org/backup.html)

The bounded-replay test matched single-frame journal, context and minute rows through synthetic out-of-order recovery, verified batch rollback, and re-ran the larger benchmark with all projection digests unchanged.

A concurrent restart test held strategy reconstruction open while the original writer committed, then verified the resumed instance replayed only the new durable tail. Historical lookup matched the full journal across an indexed migration, and all five timeframe lookbacks matched aggregation across shortened and clipped windows.

The November 10 performance checkpoint used 7,200 and 21,600 synthetic minutes. The monitor projection matched the full-journal fallback in recovery tests and retained the benchmark digest. See [performance measurements](PERFORMANCE.md).

The local synthetic monitor stream resumed at sequence 2 with Last-Event-ID 1 through the frontend proxy. Checkpoint mode emitted a named SSE event. The final suite passed 151 Python and five TypeScript tests, static checks and the production build.

Runtime profile tests verified stable IDs, worker resolution by environment name and redaction of rejected values. The browser saved a synthetic provider profile through the bearer proxy at local width and fit at 390px. Local HTTP tests returned 403 for cross-origin mutations and an untrusted Host. Five TypeScript tests passed.

The completed-period CLI test verified no early publication, a daily and weekly report at the reviewed close, deduplication after restart and stable Friday IDs after Monday ingestion.

Forward reports passed leap-day and weekly/monthly boundary checks, stable snapshots, coverage counts, unresolved outcome exclusion and independent baseline compatibility. The browser displayed a labeled synthetic trade and saved a weekly report, and the Forward workspace fit at 390px.

Forward tests verified a second-level receipt skips the already-open minute, restart restores queued and open states, a data gap leaves the position unresolved, event write rollback is retryable and post-registration cursor rules prevent earlier observations from becoming forward outcomes. CLI and API audit paths passed on synthetic fixtures.

Report API tests verified authorized creation, deterministic assumed scenarios, explicit synthetic account marks and validation with zero eligible candidates. The browser reopened a saved profile and created a distinct assumed Monte Carlo report with zero market observations.

The API profile integration test reproduced the existing flat-request run ID, created a separate run for a changed gap rule, preserved the original response and rejected mixed or inconsistent settings. Frontend tests, typecheck and build passed.

Research diagnostics tests verified full rolling windows ordered by observed outcome, source and sample labels, qualified setup counts and fixed-fill cost changes on a known synthetic trade. Three frontend cases include histogram count preservation. Browser checks verified analytics controls and the saved validation, Monte Carlo and account views. At 390px, wide tables scroll within the report.

Monitor v6 tests verified prior identity, null legacy observation times, atomic market-evidence rollback and parity with historical detection. A recovered synthetic FVG confirmed at 13:15 appeared on the chart at its 13:17 observation, and the 13:15 context view remained unchanged.

Synthetic contention tests paused chart aggregation during three monitor writes and verified the older snapshot remained fixed. Two concurrent replays published a single run, with a separate report write succeeding while they computed. Existing mid-write rollback tests passed.

The monitor chart API test held pending minutes outside the chart, released a completed bar after a gap was filled and preserved mixed historical and configured real-time source counts in a synthetic fixture. Desktop preview verified separate overlay controls and source switching. At 390px the chart fit without page-level horizontal overflow.

Overlay tests checked causal prefix equality, bounded zone and level lifetimes and recorded paper event times. Two TypeScript timeline cases verified cross-timeframe mapping and clipping across missing intervals. The chart endpoint was exercised with saved synthetic evidence and backtest runs.

The API integration test covered token enforcement, dataset inventory, 5-minute candles, invalid inputs and paper backtest creation. `npm --prefix frontend run typecheck` and `npm --prefix frontend run build` passed. Both local HTTP endpoints returned 200. In the desktop browser, the Market view rendered chart canvases and labeled synthetic provenance, Research showed the backtest summary and paper form, and Reports listed a saved Monte Carlo run. Mobile and production browser workflows remain unverified.

All fixtures are explicitly synthetic. No authorized market dataset or production-reviewed schedule was used. The code trusts the stated provider, price basis, contract mapping and schedule coverage. Validation of real exchange-export semantics is D04. See [README](../README.md) for the format.

Telegram and Discord transports use the official documented payload shapes and require a provider confirmation before marking a delivery sent. All development calls used fake transport. [Telegram Bot API](https://core.telegram.org/bots/api#sendmessage) · [Discord Webhook Resource](https://docs.discord.com/developers/resources/webhook#execute-webhook)

The reference 10 troy ounces and USD 0.10 per ounce tick imply USD 1 per contract tick. [COMEX Rulebook Chapter 120](https://www.cmegroup.com/rulebook/COMEX/1a/120.pdf)

CME lists MGC's regular Globex hours as Sunday evening through Friday afternoon, with a daily maintenance break. The Globex guide assigns the Sunday evening opening to Monday's trade date. Fixtures use these regular-hour conventions where applicable. The exception times in tests are invented. [CME Micro Metals](https://www.cmegroup.com/markets/microsuite/metals.html) · [CME Globex Reference Guide](https://www.cmegroup.com/content/dam/cmegroup/globex/files/GlobexRefGd.pdf) · [CME holiday schedules](https://www.cmegroup.com/trading-hours.html)

## Protocol for upcoming research

- Test point-in-time decisions by truncating input at decision time and by changing future bars. Confirm replay/stream parity when equivalent information arrives. Require tests at boundaries for higher-timeframe candles, pivot confirmation, imbalance recognition and fills.
- Use chronological development, configuration validation and reserved holdout partitions with leakage controls for overlapping or correlated trades. Record input IDs, calendar, rule versions, costs, fill model, dates, sample counts, partition, parameter-selection history and uncertainty.
- The spec's illustrative $1.60 round-trip commission and $1 slippage are assumptions. Simulated backtests have been run on synthetic fixtures only. Provider fee verification is pending.
- Backtest drawdown uses closed-trade equity. The Wilson interval is descriptive and assumes independent win outcomes. Q01 adds block-bootstrap expectancy intervals, validation multiple-comparison adjustment and clustering diagnostics. Check effective sample and dependence before interpreting an interval. No synthetic result measures strategy performance.
- Keep synthetic algorithm tests, historical results, hypothetical Monte Carlo and forward paper outcomes labeled and separate.

## LucidFlex paper profile verified 2026-11-09

- The $25K reference starts with a $1,000 maximum loss allowance. EOD balance advances the floor, capped at $25,100 when the EOD high reaches $26,100. A payout locks the floor. Reaching the active floor is a hard breach. The public page uses the term balance without defining unrealized PnL treatment. The paper ledger defaults to equity conservatively and offers a balance-only comparison. [Drawdown](https://support.lucidtrading.com/en/articles/12945815-lucidflex-drawdown)
- Evaluation uses a $1,250 target and 40% consistency, or 50% with the add-on. Funded has no consistency test. Evaluation uses up to 20 micros. Funded starts at 10 and reaches 20 at $1,000 of simulated profit, with changes applied at EOD or after an approved settled payout. The simulator starts a new funded ledger when an eligible evaluation is explicitly activated. [Evaluation](https://support.lucidtrading.com/en/articles/12945790-lucidflex-evaluation-account) · [Funded](https://support.lucidtrading.com/en/articles/12945795-lucidflex-funded-account) · [Scaling](https://support.lucidtrading.com/en/articles/12945808-lucidflex-scaling-plan)
- DLL is optional and locks trading for the session without itself causing hard breach. The amount is an explicit scenario input. The regular profile uses a provider-adjusted cutoff of 16:45 New York local time and reviewed earlier holiday closes. Inactivity uses 30 New York calendar days and an absolute net closed-trade change of at least $1 as qualifying activity in this model. [Customization](https://support.lucidtrading.com/en/articles/16226050-lucidflex-customization) · [Allowed trading times](https://support.lucidtrading.com/en/articles/11404729-allowed-trading-times) · [Inactivity](https://support.lucidtrading.com/en/articles/11404632-inactivity-policy)
- A funded cycle requires five settled days at $100 or more and positive cycle PnL. Use the payout table's 50% of profit cap, with $500 minimum, $1,000 maximum and 90% trader split. Approval is immediate in the ledger, and the fifth payout ends the simulated profile. The optional intraday scenario also requires a flat ledger and five prior settled qualifying days, limits the payout to the session's realized profit, and locks the session after the debit. Discretionary provider reviews and eligibility are outside the model. [Payouts](https://support.lucidtrading.com/en/articles/12945796-lucidflex-payouts) · [Customization](https://support.lucidtrading.com/en/articles/16226050-lucidflex-customization)
- Explicit ledger observations can show a breach at a supplied intraday mark. Trade-close Monte Carlo simulates only drawdown using a fixed number of trades per session. Neither establishes what happened between observations.

## Matched random control

Q03 compares the validation-selected reference against sampled qualified signals from a distinct structural opportunity variant. Both backtest manifests must match apart from the variant. Admission uses qualified development decisions before validation and ignores outcomes. For each configured group, the rate is the reference count divided by the opportunity count, capped at one, with no admission if the denominator is zero. A versioned SHA-256 draw keys each replication to the seed and available decision identity.

All arms start flat at the first eligible decision after the embargo. Each uses the original bracket, minute bars, position constraints and paper costs. The report persists arm counts, uncertainty on reference expectancy, descriptive control quantiles and the reference and first control audit. Empty paths count in trade counts and total P&L. Expectancy quantiles include only paths with trades. Declare the opportunity variant and controls before opening the holdout. The software cannot prove this research procedure happened. Structural opportunities and repeated paths on one period provide a conditional comparison. A baseline over arbitrary market times would be a separate experiment.

A five-session invented MGC fixture verified half-rate admission, development fit stability when moving the holdout or adding embargo, conservative cost parity, empty controls and identical saved CLI/API output. These results are synthetic verification.

## Historical export reconciliation

For D04, independently check the reviewed exchange schedule and document both source timestamp conventions. Import the authorized exports separately and run `aurum reconcile-datasets PRIMARY_ID COMPARISON_ID`. The report checks distinct normalized imports over overlapping calendar coverage. It records source checksums, both schedule versions, expected and present minute counts, exchange-date differences and exact integer OHLCV discrepancies. Review same-checksum inputs for independence and explicitly investigate gaps or differences. A clean scoped comparison cannot lift the full dataset replay gate. One or both calendars lacking dates prevents an exact schedule finding. This diagnostic does not establish source authorization or certify the exchange schedule.

Three synthetic tests verified UTC/open versus Prague/close normalization, raw checksum equality, scoped results on a blocked import, discrepancy sampling and saved CLI/API identity. Authorized D04 reconciliation is pending.

## Monitoring economic risk

Two synthetic restart cases replayed a partially applied monitor through a paper decision with a fictional release published before or after the decision. Historical setup transitions matched the monitor payloads apart from journal-specific identity. The clear-risk filter admitted the setup only when no release was known near the decision under the supplied coverage. Provider settings reject a malformed snapshot and retain the prior manifest shape without a snapshot. The direct monitor uses the same constructor. Automated retrieval from an authorized calendar source remains pending.

The operational monitor read distinguishes current, overdue, clock-ahead and unobserved heartbeats for real-time and delayed configurations. Historical and synthetic replay have no wall-clock heartbeat. A synthetic regression verified the inclusive deadline, suspension immediately after expiry, clock skew, API and chart parity and an unchanged journal. Delivery uses the same heartbeat function.

Economic snapshot revisions reference a distinct earlier announcement ID and take effect at their own publication time. Synthetic verification covered earlier-assessment equality, rescheduling, cancellation, ambiguous or malformed chains, and partial coverage. Coverage from a partial source can add elevated-risk evidence while leaving other times unknown. The BLS describes its public feed as covering most national office releases. A direct request for its iCalendar returned HTTP 403 from the local runtime on 2026-11-10. Automated external ingestion has not been verified. [BLS calendar documentation](https://www.bls.gov/help/hlpiCAL.htm)

Browser verification on a temporary synthetic store saved a matched-control report with 50% development admission, reconciled 350 exact fixture minutes with identical raw checksums disclosed, and persisted a simulated provider profile with a partial economic snapshot and clear-risk requirement. No external worker or delivery was started.

A fresh initialized store with zero datasets accepted an explicit synthetic dated exchange schedule in a simulated provider profile and constructed its local worker configuration. The profile kept the reviewed schedule and contract, and repeated saves resolved to the same ID. Tests rejected missing or simultaneous schedule sources, malformed contracts, legacy undated calendars, missing synthetic classification and synthetic schedules on a delayed feed.

Backtest analytics v2 computes group execution diagnostics from immutable completed-trade events and keeps existing run and report IDs. The synthetic stop-first fixture independently checks a 60-second hold, 33-tick modeled adverse excursion, zero favorable excursion and 260 cents of modeled costs. Sample standard deviation is absent for fewer than two observations. Monthly consistency only covers exchange months with trades. Net trade dispersion is a return measure and does not estimate market volatility.

## Trade print import

Nine synthetic cases verified nanosecond order, exact same-timestamp sequence ties, UTC normalization and ambiguous local-time rejection, OHLCV derivation, source checksum retention, duplicate/off-session/invalid input rejection, missing-minute replay gates, a v11 migration and keyset pagination across 1,002 prints. CLI and REST returned identical labeled print records. The exporter must provide a unique stable sequence. This slice handles normalized historical trade prints. Vendor-native payloads, quotes, depth and live trade streaming remain outside this import path.

The ordered-print variant is opt-in for a saved trade-print dataset. It reconstructs the minute and validates order before resolving the first bracket touch. The matched-control replay uses the same resolver when both arms opt in. The synthetic cases exercised both directions and stop-first and target-first sequences at an identical timestamp. The source print and logical observation clock are audited separately. Minute-only backtests preserve their original rule and run identity. Modeled fills retain the bracket price and costs. Test outcomes are synthetic and supply no empirical evidence of a trading advantage.

The intrabar comparison requires otherwise identical saved configurations and pairs completed trade journals. Unaffected rows must agree exactly. Resolved rows may differ in bracket price, exit reason, excursion and net outcome while entry, observation, quantity and costs remain paired. The synthetic integration test verified a 9,900-cent modeled difference on one invented dual touch, matching CLI/API reports, a zero-example cap, an empty sample and rejection of another pivot setting. The comparison covers unpartitioned replay and reports execution sensitivity.

The one-factor sensitivity report reads the immutable validation report's candidate configurations and outcomes. The synthetic test supplied a validation with actual fixture trades in the holdout and verified that the sensitivity result and candidate endpoint omit holdout outcomes. It checked embargo boundaries, one-factor subset selection, incompatible settings and identical saved CLI/API output. Adjusted p values retain the original validation family and test individual expectancy against zero. The reported difference between candidates is descriptive.

Six synthetic quote cases verified schema v12-to-v13 migration, source-byte checksum verification, equal-time sequence selection, one-nanosecond expiry, closed-window reset, unknown sizes and lock disclosure, invalid contract/crossing/sequence rejection, and matching CLI/REST results. Quote lookup applies to the supplied historical observation timestamps and needs a reviewed dated calendar. Quotes are separate from modeled fills and from the strategy engine.

The spread observation report samples strictly preceding quotes at qualified setup decisions, modeled entry minute opens and exact stored bracket touches where available. Contract, tick size and data class must match. If both calendars cover an instant and disagree on its exchange date or window, the report labels a calendar mismatch and excludes the quote. Stale, unavailable and mismatched references stay in coverage counts. The synthetic test confirmed a 50 ms quote was stale under a 20 ms cutoff, a 10 ms quote qualified at the ordered bracket touch, a same-timestamp quote was excluded and the saved backtest remained byte-equivalent.

The opt-in spread cost projection replaces the recorded spread cost with half the preceding quoted width at the modeled entry open and the available exit reference. An exit reference can be an exact stored bracket print, a modeled opening exit, or a deterministic close exit. An ordinary OHLC touch has unknown intrabar timing and is excluded. Both quotes must be fresh and calendar-aligned. The report shows paired sample size and first exclusion reason, and keeps fill prices, sizes, commissions and slippage fixed. A synthetic trade verified that replacing a 100-cent spread charge with a 250-cent proxy reduced modeled net by 150 cents. Stale entry and unknown exit cases were excluded. These are quote-based cost assumptions for the paired subset.

Live stream storage actions retry SQLite busy/locked errors up to twice, after SQLite's configured connection wait. A successful retried frame preserves its original receipt and records the later in-transaction processing time. It remains ineligible for a new research alert or a forward paper fill. Synthetic faults injected after transactional application verified full rollback, one committed retry event, normal eligibility for the following frame, bounded exhaustion, immediate propagation of I/O errors, and explicit disconnection on malformed frames. An ordinary in-transaction processing timestamp also controls staleness. Processing time is captured inside the write transaction and does not represent SQLite commit completion.

The optional local iCalendar converter uses the recorded capture time as the earliest availability for selected timed releases. It pins the parser and date utility versions, verifies IANA zone offsets against pinned tzdata, and records the exact input checksum. Five labeled synthetic tests covered before/after the US DST transition, absence of pre-capture knowledge, cancellation filtering, partial coverage, recurrence and unresolved local dates. Explicitly synthetic snapshots are gated to simulated research. The official BLS live iCalendar URL remains unverified from this runtime after HTTP 403. [BLS iCalendar documentation](https://www.bls.gov/help/hlpiCAL.htm) [Pinned parser release](https://pypi.org/project/icalendar/7.3.0/)

The read-only `verify-dataset` check reconstructs CSV, Parquet and normalized trade imports from stored source bytes, original normalization settings and reviewed calendar. It checks the current supported original importer and tzdata versions, identity, checksum, metadata, warning list, actual row counts and exact row values. Archived HTTPS-origin data is verified without credentials or a network request. An accurate incomplete import verifies with replay still blocked. Synthetic tests detected an altered candle and an altered trade even when the source checksum remained valid.

`verify-quote-series` uses the original quote normalization and pinned timezone data to compare the full stored quote series with its source. The synthetic test reconstructed 1,002 observations from a restored backup after removing the external fixture file, then detected a changed ask on the second read page. Raw source verification still passed because the original bytes were intact. The original store verified separately.

Contract and continuous reports use the same immutable manifest and result through the CLI and API. Synthetic tests checked that a listing published after the chosen as-of time is excluded, a preview can span a roll and a continuous report ID cannot serve as a dated backtest dataset. In the built browser workspace, a synthetic rollover report saved with its source and as-of time, and the continuous preview displayed the successor's dated close of 29,100 ticks alongside its adjusted research close of 29,000 ticks.

Two synthetic timing cases published a fictional release after a qualifying candle close but either before or after the monitor's later observation. Both preserved the same clear-risk source-close decision. With the clear-risk filter enabled, the release known by observation blocked the new forward setup. The underlying minute retained price processing eligibility. The monitor records both clocks and assessments, and the notification renderer includes observed risk when supplied.

The optional `verify-store --verify-normalized` audit captures dataset and quote-series IDs from the checked snapshot, closes the structural reader, and rebuilds each source without network access. It reports counts including accurately stored incomplete datasets. `backup --verify-normalized` runs the audit on the private staged copy before publication. Synthetic tests verified normalized candle and quote counts, reported a blocked incomplete import, and confirmed a failed normalization audit leaves no destination or staged file.

## Compared release captures

`compare-economic-ics` uses the operator's recorded observation clock for each
preserved file. A stable UID follows a release through changes without changing
earlier assessments. Missing or replaced UIDs never cancel earlier evidence.
The output remains partial, includes raw SHA-256 and parser version for every
capture and can be loaded through the existing calendar and provider paths.
Keep the raw files for review. The command cannot establish that the capture
clock is authentic or that the selected publication covers other releases.

Eleven synthetic iCalendar tests passed, including six comparison cases covering
a changed release time, unchanged repeat, omission, explicit cancellation,
duplicate UID, stale or conflicting sequence and out-of-order captures.

## Staged final holdout

The optional staged workflow saves only outcomes recorded strictly before the
final boundary. Candidate selection, pre-holdout walk-forward windows and
validation multiplicity adjustment reuse the established policy. The final
report references the immutable selection and starts without an open order or
position. Qualified decisions at the embargo boundary are eligible. A decision
at the dataset's last close is excluded because no next minute exists.
Strategy detection carries causal context from the source replay. The final
paper executor uses the selected run's fill rules and optional trade-print
ordering, including the ordinary end-of-data position mark.

The synthetic tests compare normal CLI and API results, exact embargo
inclusion, empty final samples, a prior position blocking a later order in a
full replay, and matching reference and forward baselines. The dashboard was
tested on a temporary synthetic loopback store through both saved reports.
There were 218 Python and five frontend tests. Ruff, mypy, TypeScript and the
production frontend build passed. Historical strategy performance is pending
authorized data. Existing full backtests remain accessible, so predeclare
rules and avoid viewing the final period before the selection is fixed.

## Combined economic publications

Local composition preserves source-specific original event IDs, versions,
normalized SHA-256 hashes and any raw capture digests. Independent records
with equal original IDs remain distinct. Explicit same-source revisions keep
their publication clocks. Complete coverage claimed by an individual source
does not certify that the composed bundle covers all relevant announcements.
Two synthetic tests verified causal cancellation, separately known actual
times, repeated deterministic CLI output, mixed-class rejection and provider
profile use. The inputs remain local and independently reviewable.

The indexed economic assessor matched an independent scan for 1,048
synthetic complete and partial queries, including actual releases known at the
same instant as cancellation, restored events and shuffled event order.
The 20,000-query benchmark preserved its output digest. The complete Python
suite passed 222 tests.

## Official economic archive smoke test

On 2026-11-10, the explicit BEA capture stored 43,459 public response bytes
and normalized two releases selected by the Personal Income and Outlays
prefix in a temporary store. The snapshot was partial and used the saved
receipt time. The store checksum audit verified the raw blob. The direct BLS
capture returned HTTP 403 and did not publish a receipt. This smoke test
establishes current public release access for BEA. Historical publication
availability and authorized MGC research remain separate.
[BEA calendar subscription](https://www.bea.gov/news/schedule/icalendar) ·
[BLS iCalendar documentation](https://www.bls.gov/help/hlpiCAL.htm)

The archived preview API matched offline normalization and left the SQLite
file checksum unchanged. Tests altered archive bytes and receipt metadata
and verified audit rejection and no backup publication. The Settings picker
compiled in the production frontend build. On 2026-11-11, browser verification
prepared and copied a synthetic capture snapshot, confirmed the observation
clock and partial coverage, and left the store with zero provider profiles.

## Reviewed monitoring updates

Five synthetic tests verified immutable activation, concurrent idempotency,
rejection of changes to processed history, publication knowledge checks,
independent source and observation clocks, unavailable risk, restart and
corrupted-record rejection. CLI audit matched the API journal, and the API
rejected client-supplied activation times. The full suite passed 233 tests.
The reviewed update workflow is manual. Delivery of previously queued alerts
continues to use their recorded evidence and the existing freshness policy.

## Local service packaging

Compose configuration passed `docker-compose config --quiet` with a synthetic
test token. The standalone dashboard build served its JavaScript asset and
proxied the API health route with HTTP 200. Direct API access without a token
returned 401. A regression test verifies that all-interface binding requires a
token before creating the database. The full suite passed 234 tests, and five
frontend tests, TypeScript and the standalone production build passed.
Container builds remain unverified because the available Colima VM image
download timed out. The attempted runtime was stopped.

## Databento minute CSV adapter

Twelve synthetic cases verified the strict pretty export format, dated MGC
month/year checks, one instrument and publisher, exact prices and raw checksum
reproduction. Duplicate minutes and conflicting identities are rejected. A
missing interval remains absent and blocks ordinary replay. The complete
Python suite passed 246 tests. No paid historical query was issued, and no
authorized Databento file has been validated yet.

## Email delivery

Eleven synthetic tests verified explicit and implicit TLS, certificate checks,
authentication order, stable message ID, redacted errors, temporary/permanent
rejection, uncertain acknowledgement, accepted messages followed by disconnect,
profile credential isolation and recipient injection rejection. The full Python
suite passed 257 tests. TypeScript and the production frontend build passed.
Tests used fake SMTP transports and did not send external email.

## Repository verification workflow

The GitHub Actions workflow defines independent Python and frontend checks plus
an isolated container smoke job. It uses verified immutable action revisions,
read-only repository permission, explicit interpreter versions and the runtime
dependency lock. `actionlint .github/workflows/verify.yml` passed locally.
Python, frontend and native smoke commands passed in this workspace. On
2026-11-11 the authorized empty GitHub repository received the checkpoint on
`codex/verified-research-platform`. Hosted run
[43001173432](https://github.com/davesm-jpg/AURUM/actions/runs/43001173432)
passed all three jobs on `10573ba`. Container images built and started healthy.
The dashboard proxy returned schema v15 health, the direct unauthenticated API
returned 401 and the worker audited the empty store. Run
[43001928370](https://github.com/davesm-jpg/AURUM/actions/runs/43001928370)
passed all three jobs on `03c3da4`, including two persisted synthetic minute
imports, API/dashboard container replacement, normalized audit and standalone
JavaScript delivery. Local macOS container execution remains unverified.

## Forward period notification publication

The full local Python suite passed 260 tests. Synthetic fixtures covered daily,
weekly and monthly summaries, opt-in behavior, concurrent manual deduplication,
publication/outbox rollback, restart delivery suppression, severity filtering,
source disclosure and informational email subjects. Ruff and strict mypy passed.
No external notification was sent. Summaries retain actual outcome counts and
saved model versions; they do not assert historical or live profitability.

## Native Databento adapter

The full local suite passed 275 tests. Fifteen native cases use constructed
SDK 0.90.0 records and fake clients, never a market-data connection. They cover
mapping identity and interval, fixed-point tick conversion, byte preservation,
premature bars, replay completion, publisher changes, queue overflow, stale
connection handling, reconnect retry, resume cursor and missing credentials.
Ruff, strict mypy (95 files), pip check, actionlint and whitespace checks passed.
The adapter uses the existing monitor and paper eligibility gates. Native timing,
entitlements, calendar reconciliation and sparse data remain production tasks.

Hosted run [43004011688](https://github.com/davesm-jpg/AURUM/actions/runs/43004011688)
passed all three jobs on native core commit `6a0bcf0`. Native profile extensions
then passed 276 total local tests, Ruff, mypy, frontend tests (5), TypeScript and
the production build. The browser saved one explicitly labeled synthetic native
profile in an isolated temporary store; SQL confirmed zero monitors and minutes.
Reloading the rebuilt dashboard confirmed the primary sweep/MSS/IFVG default
survives asynchronous option loading. No subscription was started.

## Durable public calendar polling

The complete Python suite passed 285 tests. Nine polling cases cover migration,
retained cadence, concurrent claims, HTTP 403, invalid bodies, bounded backoff,
receipt-before-acknowledgement interruption, redacted failures, API/CLI parity,
state reconstruction and rejection of corrupted backups. Ruff, mypy (97 files),
TypeScript and the frontend production build passed. Browser verification showed
BEA captured and BLS failed/HTTP 403 beside the preserved BEA receipt.

Actual public BEA receipt `54bfea8b12144d5b197f1e1d82c2573a245bd239f63a606c70f66536fb692b5a`
was observed at 2026-11-11 18:36:20.778515 UTC in ignored local
`data/public-economics.sqlite`. BLS returned HTTP 403 at its one explicit attempt.
The store passed source checksums and normalized audit with one capture, two
pollers and two attempts. No market data, monitor adoption or external message
was involved. Native preview processes were stopped after browser verification.

## Preserved monitor input audit

The complete suite passed 295 tests. Ten synthetic audit cases cover a
1,002-minute paged source, unchanged read-only CLI output, pending frames,
legacy unknown clocks, normalized WebSocket and serialized native DBN input,
changed source/normalized rows, manifest identity, invalid application/eligibility
and rejection of corrupted verified backups. Native audit constructs no client
and requires no key. Restart source failure latched an integrity fault before
research resumed. Ruff and strict mypy passed for 99 files.

The 21,600-minute synthetic benchmark retained every chart projection digest.
Batched ingestion measured 4.519 seconds and restart 1.168 seconds versus the
prior approximately 0.8-second restart. This is disclosed source-validation cost,
not a production feed latency measurement. See PERFORMANCE.md.
