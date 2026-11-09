# AURUM validation

## Verified checkpoint — 2026-11-09

- Environment. macOS arm64, Python 3.12.7, SQLite 3.45.3 and the timezone package pinned in `pyproject.toml`.
- `.venv/bin/python -m pytest -q` passed 99 cases. Execution tests cover next-minute eligibility, latency, spread and risk rechecks, both-way ambiguity, gap stops and targets, terminal exits, sample-aware metrics and repeatable CLI backtest audit. Earlier cases cover mirrored strategy, point-in-time economic context, evidence, storage, calendar and aggregation.
- A synthetic SQLite database created by the v1 implementation was opened and migrated. `cmp` verified identical provenance and replay before and after the write migration. SQLite's integrity check passed. The original dataset ID is asserted in the CLI test.
- `.venv/bin/ruff check .` passed.
- `.venv/bin/ruff format --check .` passed.
- `.venv/bin/mypy .` passed across thirty source/test files.
- `.venv/bin/python -m pip check` reported no broken requirements.

All fixtures are explicitly synthetic. No authorized market dataset or production-reviewed schedule was used. The code trusts the stated provider, price basis, contract mapping and schedule coverage. Validation of real exchange-export semantics is D04. See [README](../README.md) for the format.

The reference 10 troy ounces and USD 0.10 per ounce tick imply USD 1 per contract tick. [COMEX Rulebook Chapter 120](https://www.cmegroup.com/rulebook/COMEX/1a/120.pdf)

CME lists MGC's regular Globex hours as Sunday evening through Friday afternoon, with a daily maintenance break. The Globex guide assigns the Sunday evening opening to Monday's trade date. Fixtures use these regular-hour conventions where applicable. The exception times in tests are invented. [CME Micro Metals](https://www.cmegroup.com/markets/microsuite/metals.html) · [CME Globex Reference Guide](https://www.cmegroup.com/content/dam/cmegroup/globex/files/GlobexRefGd.pdf) · [CME holiday schedules](https://www.cmegroup.com/trading-hours.html)

## Protocol for upcoming research

- Test point-in-time decisions by truncating input at decision time and by changing future bars. Confirm replay/stream parity when equivalent information arrives. Require tests at boundaries for higher-timeframe candles, pivot confirmation, imbalance recognition and fills.
- Use chronological development, configuration validation and reserved holdout partitions with leakage controls for overlapping or correlated trades. Record input IDs, calendar, rule versions, costs, fill model, dates, sample counts, partition, parameter-selection history and uncertainty.
- The spec's illustrative $1.60 round-trip commission and $1 slippage are assumptions. Simulated backtests have been run on synthetic fixtures only. Provider fee verification is pending. Before implementing provider-account scenarios, verify the applicable official version and separately test evaluation/funded behavior.
- Backtest drawdown uses closed-trade equity. The Wilson interval is descriptive and assumes independent win outcomes. Dependence-aware validation remains Q01. No synthetic result measures strategy performance.
- Keep synthetic algorithm tests, historical results, hypothetical Monte Carlo and forward paper outcomes labeled and separate.
