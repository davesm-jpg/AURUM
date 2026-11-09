# AURUM

AURUM is a paper-only research project for COMEX Micro Gold futures. This checkpoint supports local, calendar-validated import and chronological replay of historical 1-minute OHLCV for one dated contract per dataset.

## Install

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
mkdir -p data
```

## Import and replay

Provide an authorized UTF-8 CSV with this exact header.

```csv
timestamp,contract,open,high,low,close,volume
```

Use a dated identifier such as `MGC-2025-04` in every row. Export unadjusted prices for that contract. Prices must be multiples of USD 0.10 and volume must be an integer contract count. Set the IANA source timezone and indicate whether the timestamp labels the candle open or close. Explicit offsets must agree with the source timezone. Local timestamps during a repeated clock hour require an explicit offset.

Install `.[dev,parquet]` to enable `import-parquet`. Use the same columns and import flags. Parquet accepts native minute timestamps and numeric OHLCV, with the same validation as CSV. Use `import-https URL --format csv` or `--format parquet` for an authorized endpoint that serves this schema. Pass an environment variable name with `--token-env` if a bearer token is required. The raw response is preserved and only its origin enters endpoint provenance.

Supply a JSON snapshot of the applicable MGC trading windows. For real data, review the effective exchange schedule and record its source and revision. A session can open the previous evening and belong to the following exchange trade date. [CME Globex Reference Guide](https://www.cmegroup.com/content/dam/cmegroup/globex/files/GlobexRefGd.pdf) · [CME trading hours and holiday schedules](https://www.cmegroup.com/trading-hours.html)

Use `schema_version: 2` for new snapshots. Set `coverage` to the expected export period and include the full open windows for each intersecting session, even when they extend beyond coverage. Enter `trade_date` as assigned by the exchange. Use ISO timestamps with explicit offsets. All intervals include their start and exclude their end. Omit closed periods and use separate windows around a halt in the same session. For an early close, end the last window at that close. The importer expects one bar per open minute inside coverage and preserves the full session boundaries in provenance.

This **synthetic schema example** covers two minutes of a full session.

```json
{
  "schema_version": 2,
  "instrument": "MGC",
  "source": "synthetic schema example",
  "version": "example-revision-1",
  "coverage": {"start": "2025-03-10T13:00:00Z", "end": "2025-03-10T13:02:00Z"},
  "sessions": [{
    "trade_date": "2025-03-10",
    "windows": [{"start": "2025-03-09T17:00:00-05:00", "end": "2025-03-10T16:00:00-05:00"}]
  }]
}
```

```sh
.venv/bin/aurum --db data/aurum.sqlite import-csv data/mgc.csv \
  --calendar data/schedule.json --provider 'your authorized source' \
  --contract MGC-2025-04 --source-timezone UTC --timestamp-label open \
  --data-kind historical --price-basis unadjusted
.venv/bin/aurum --db data/aurum.sqlite inspect DATASET_ID
.venv/bin/aurum --db data/aurum.sqlite replay DATASET_ID
.venv/bin/aurum --db data/aurum.sqlite aggregate DATASET_ID --timeframe 5m --timeframe 1d
.venv/bin/aurum --db data/aurum.sqlite structure DATASET_ID --break-mode close
.venv/bin/aurum --db data/aurum.sqlite structure DATASET_ID --evidence --min-gap-ticks 2
.venv/bin/aurum --db data/aurum.sqlite structure-events RUN_ID
.venv/bin/aurum --db data/aurum.sqlite strategy DATASET_ID --variant sweep_mss_ifvg
.venv/bin/aurum --db data/aurum.sqlite strategy-events RUN_ID
.venv/bin/aurum --db data/aurum.sqlite backtest DATASET_ID --variant sweep_mss_ifvg
.venv/bin/aurum --db data/aurum.sqlite backtest-events RUN_ID
.venv/bin/aurum --db data/aurum.sqlite backtest-summary RUN_ID
.venv/bin/aurum --db data/aurum.sqlite validate RUN_A RUN_B \
  --validation-start 2025-04-01T00:00Z --holdout-start 2025-07-01T00:00Z
.venv/bin/aurum --db data/aurum.sqlite monte-carlo --paths 1000 --trades 1000
.venv/bin/aurum --db data/aurum.sqlite account data/paper-ledger.json \
  --calendar data/account-schedule.json --phase funded
.venv/bin/aurum --db data/aurum.sqlite report REPORT_ID
.venv/bin/aurum --db data/aurum.sqlite contracts data/contract-catalog.json \
  --as-of 2025-03-10T13:00Z --current MGC-2025-04
.venv/bin/aurum --db data/aurum.sqlite continuous data/series-plan.json
.venv/bin/aurum --db data/aurum.sqlite continuous-bars REPORT_ID
```

Import reports a stable dataset ID and any quality findings. Replay emits one JSON object per completed bar with UTC `opened_at` and `available_at` fields. Prices in replay are integer ticks. Dated snapshots add a `trade_date` to each replay event. Legacy snapshots with a top-level `windows` array continue to import and replay with their original IDs and event shape. A missing expected minute is preserved as a quality incident and prevents replay. Invalid prices, duplicate candles, mixed contracts, and rows outside open windows reject the import. Out-of-order rows are sorted and reported. Use `--data-kind synthetic` for generated fixtures.

Aggregation requires a dated calendar. Omit `--timeframe` to emit all supported intervals (5m, 15m, 1h, 4h and 1d). Intraday buckets start at each open window and restart after closures. When an open window ends inside a bucket, a complete shorter bar is emitted with `shortened: true`. Daily bars combine all open windows on the trade date. Every published aggregate contains all expected source minutes and is emitted at its scheduled close. Export boundaries do not shorten a bucket.

Structure replay defaults to 5m candles, 3-left/3-right internal pivots, 5-left/5-right external pivots and close-based breaks. Strict local extrema reject ties inside the pivot window. Use `--internal-pivot LEFT RIGHT`, `--external-pivot LEFT RIGHT` and `--break-mode` to run separate configurations. Break modes are `wick`, `close`, `buffered_close` and `displacement_close`. Volatility filters use the full window of earlier true ranges. The manifest records the exact configuration, versions and event count. Each event identifies its source and confirmation candles.

Add `--evidence` to include price-derived liquidity levels, sweeps, FVGs and IFVGs. Use `--min-gap-ticks` and `--inversion-closes` to compare gap definitions. Each event contains its source prices and availability. Liquidity levels are observable price proxies. OHLCV supplies an observation window for a sweep, so the output reports elapsed bars and seconds between observed timestamps.

Strategy replay stores paper setup transitions, contextual measurements, sizing and reasons a setup matched or failed. Variants include `mss`, `sweep_mss`, `mss_fvg`, `mss_ifvg`, `sweep_mss_fvg`, `sweep_mss_ifvg` and `sweep_mss_ifvg_htf`. Use `--session`, `--regime`, volatility bounds and `--minimum-score` for filtered experiments. The default gross cap is USD 50, with optional `--net-risk-cents` for an all-in cap. Choose `--target-mode structural` to use the closest favorable confirmed pivot. The experimental score has no calibrated probability interpretation.

Supply `--economic-calendar` only when you have a reviewed point-in-time snapshot. Its JSON fields are `source`, `version`, `coverage` with `start` and `end`, `coverage_known_at` and `events`. Each event has `event_id`, `title`, `scheduled_at`, `published_at` and optional `actual_at` plus `actual_known_at`. Use offset-bearing ISO timestamps. Without effective coverage, the calendar status is unknown. `--require-clear-event-risk` blocks signals unless the snapshot establishes clear coverage around the decision.

Backtest replay accepts the strategy options and simulates a single position at a time. It uses the next eligible minute open after a signal, applies the structural bracket and rechecks sizing at the fill price. Use `--latency-minutes`, `--entry-timeout-minutes`, `--round-trip-spread-ticks`, `--commission-cents` and `--slippage-ticks` to set execution assumptions. It uses stop-first for ambiguous same-minute touches, adverse opening prices for stop gaps and the target price for favorable opening gaps. It flattens at scheduled window close unless `--carry-across-closures` is set. Backtest audit distinguishes forced end-of-data exits. The summary reports actual sample size, net returns and closed-trade equity drawdown.

Validation compares runs with the same input dataset, timeframe, cost profile and execution assumptions. Set the two UTC boundaries inside the observed dataset range before evaluating results. Use `--embargo-minutes` and `--minimum-selection-trades` to define eligibility. Only the selected candidate's holdout is reported. Candidate differences, walk-forward windows and uncertainty are recorded in the saved report.

Monte Carlo defaults to the reference assumed win rates of 30%, 35%, 40%, 45%, 50% and 60%. Set `--mode block_bootstrap --backtest RUN_ID` to resample net backtest outcomes, or use `shuffle` with a trade count at or below the observed sample size. Simulated paths use a generic fixed floor at trade closes. They are conditional on the source and assumptions.

The account command models a separate $25K LucidFlex paper ledger. Supply a dated schedule adjusted for the provider's applicable closing cutoff and holidays. The ledger has `data_kind` (`synthetic` or `paper_marks`), `source`, `started_at` and chronological `actions`. A `mark` action uses `at`, `trade_date`, optional `closed_trade_nets_cents`, `unrealized_cents`, `open_micros` and `end_of_day`. The EOD mark must be flat and match the scheduled final close. `payout` actions use `at` and `gross_cents`. `activate_funded` uses `at` after a simulated pass. All action objects include `kind`.

Use `--consistency-bps 5000` for the evaluation add-on profile and supply an explicit `--daily-loss-cents` for a daily-loss scenario. Default marked-equity checks are a conservative research assumption. Add `--lucidflex-trades-per-session` to Monte Carlo for an EOD-trailing drawdown scenario using trade closes and the specified session grouping. See the [rule sources and assumptions](docs/VALIDATION.md).

The contract catalog JSON contains `source`, `version`, `data_kind`, `listings` and optional `activity`. A listing uses `contract`, `first_trade_at`, `last_trade_at` and `known_at`. An activity row uses `contract`, `period_start`, `period_end`, `available_at` and volume or open interest. Roll decisions use only comparable periods available at `--as-of`. Supply independently reviewed listing and expiry timestamps. [CME expiration calendar](https://www.cmegroup.com/tools-information/calendars/expiration-calendar.html)

A continuous plan contains `basis` (`unadjusted` or `forward_adjusted`) and ordered `segments` with `dataset_id`, `start` and `end`. Forward adjustment requires `reference_open` for each successor, identifying a completed overlapping minute before the switch. Source calendars and provenance must be compatible. Output separates research OHLC from the dated unadjusted source candle.

See [the roadmap](docs/ROADMAP.md), [the architecture](docs/ARCHITECTURE.md), and [the handoff](docs/HANDOFF.md) for implementation status.
