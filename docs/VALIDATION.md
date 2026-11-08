# AURUM validation

## Verified checkpoint — 2026-11-08

- Environment. macOS arm64, Python 3.12.7, SQLite 3.45.3 and the timezone package pinned in `pyproject.toml`.
- `.venv/bin/python -m pytest -q` passed 25 cases. Coverage includes tick math, timestamp precision, DST gap/fold and New York/Prague divergence, contract and price validation, calendar boundaries, gap detection, concurrent re-import, raw byte preservation, transaction rollback and a subprocess CLI round trip.
- `.venv/bin/ruff check .` passed.
- `.venv/bin/ruff format --check .` passed.
- `.venv/bin/mypy .` passed across eight source/test files.
- `.venv/bin/python -m pip check` reported no broken requirements.

All fixtures are explicitly synthetic. No authorized market dataset or production-reviewed schedule was used. The code trusts the stated provider, price basis, contract mapping and schedule coverage. Validation of real exchange-export semantics is D04. See [README](../README.md) for the format.

The reference 10 troy ounces and USD 0.10 per ounce tick imply USD 1 per contract tick. [COMEX Rulebook Chapter 120](https://www.cmegroup.com/rulebook/COMEX/1a/120.pdf)

## Protocol for upcoming research

- Test point-in-time decisions by truncating input at decision time and by changing future bars. Confirm replay/stream parity when equivalent information arrives. Require tests at boundaries for higher-timeframe candles, pivot confirmation, imbalance recognition and fills.
- Use chronological development, configuration validation and reserved holdout partitions with leakage controls for overlapping or correlated trades. Record input IDs, calendar, rule versions, costs, fill model, dates, sample counts, partition, parameter-selection history and uncertainty.
- The spec's illustrative $1.60 round-trip commission and $1 slippage are assumptions. There is no backtest or fee verification in this checkpoint. Before implementing provider-account scenarios, verify the applicable official version and separately test evaluation/funded behavior.
- Keep synthetic algorithm tests, historical results, hypothetical Monte Carlo and forward paper outcomes labeled and separate.
