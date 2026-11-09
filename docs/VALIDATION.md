# AURUM validation

## Verified checkpoint — 2026-11-09

- Environment. macOS arm64, Python 3.12.7, SQLite 3.45.3 and the timezone package pinned in `pyproject.toml`.
- `.venv/bin/python -m pytest -q` passed 151 cases with PyArrow 26.0.1 and websockets 15.0.1 installed. New tests cover notification concurrency, retry timing, uncertain results, health, quiet hours, cancellation and fake Telegram/Discord payloads. Monitor tests cover local WebSocket, gaps, restart, source status, rollback and synthetic parity.
- A synthetic SQLite database created by the v1 implementation was opened and migrated. `cmp` verified identical provenance and replay before and after the write migration. SQLite's integrity check passed. The original dataset ID is asserted in the CLI test.
- `.venv/bin/ruff check .` passed.
- `.venv/bin/ruff format --check .` passed.
- `.venv/bin/mypy .` passed across 60 source/test files.
- `.venv/bin/python -m pip check` reported no broken requirements.

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
