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
.venv/bin/aurum --db data/aurum.sqlite trade-prints TRADE_DATASET_ID --limit 20
.venv/bin/aurum --db data/aurum.sqlite reconcile-datasets PRIMARY_ID COMPARISON_ID
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
.venv/bin/aurum --db data/aurum.sqlite compare-intrabar STOP_FIRST_RUN_ID ORDERED_PRINT_RUN_ID
.venv/bin/aurum --db data/aurum.sqlite validate RUN_A RUN_B \
  --validation-start 2025-04-01T00:00Z --holdout-start 2025-07-01T00:00Z
.venv/bin/aurum --db data/aurum.sqlite parameter-sensitivity VALIDATION_ID BASELINE_RUN_ID \
  --factor minimum_gap_ticks
.venv/bin/aurum --db data/aurum.sqlite matched-control VALIDATION_ID MSS_RUN_ID \
  --replications 30 --strata session_direction --seed 42
.venv/bin/aurum --db data/aurum.sqlite monte-carlo --paths 1000 --trades 1000
.venv/bin/aurum --db data/aurum.sqlite account data/paper-ledger.json \
  --calendar data/account-schedule.json --phase funded
.venv/bin/aurum --db data/aurum.sqlite report REPORT_ID
.venv/bin/aurum --db data/aurum.sqlite contracts data/contract-catalog.json \
  --as-of 2025-03-10T13:00Z --current MGC-2025-04
.venv/bin/aurum --db data/aurum.sqlite continuous data/series-plan.json
.venv/bin/aurum --db data/aurum.sqlite continuous-bars REPORT_ID
.venv/bin/aurum --db data/aurum.sqlite monitor-replay DATASET_ID
.venv/bin/aurum --db data/aurum.sqlite monitor-status MONITOR_ID
.venv/bin/aurum --db data/aurum.sqlite monitor-events MONITOR_ID
.venv/bin/aurum --db data/aurum.sqlite monitor-alerts MONITOR_ID
```

Import reports a stable dataset ID and any quality findings. Replay emits one JSON object per completed bar with UTC `opened_at` and `available_at` fields. Prices in replay are integer ticks. Dated snapshots add a `trade_date` to each replay event. Legacy snapshots with a top-level `windows` array continue to import and replay with their original IDs and event shape. A missing expected minute is preserved as a quality incident and prevents replay. Invalid prices, duplicate candles, mixed contracts, and rows outside open windows reject the import. Out-of-order rows are sorted and reported. Use `--data-kind synthetic` for generated fixtures.

For a normalized authorized trade export, use `import-trades FILE.csv` with a reviewed dated calendar, provider, dated contract, source timezone, data kind and unadjusted price basis. The header is `timestamp,contract,sequence,price,size`. Timestamps identify execution instants with up to nine fractional digits. The sequence must be a unique nonnegative integer for the export. AURUM retains the source bytes and normalized prints, then derives minute OHLC from chronological prices and sums size. Missing scheduled minutes continue to block research replay. The print reader supports an inclusive `--start`, exclusive `--end` and a paired `--after-timestamp` and `--after-sequence` cursor.

Use `import-quotes FILE.csv` with the same source flags for best bid and ask observations. Its header is `observed_at,contract,sequence,bid,ask,bid_size,ask_size`. Use the source observation time and leave unknown sizes blank. Quote series remain separate from candle datasets. Use `quote-series` to inspect provenance and `quote-at SERIES_ID --at 2025-03-10T13:00:01Z --maximum-age-ms 1000` to inspect the most recent observation strictly before the requested instant within the same open window. The response labels stale or unavailable data. Use `spread-observations BACKTEST_RUN_ID QUOTE_SERIES_ID` to save quote coverage and spread distributions at qualified setup decisions, modeled entry opens and ordered bracket touches. Add `--quote-cost-proxy` to replace the recorded spread charge with half the quoted width at each reference for the paired subset with fresh quotes. This sensitivity calculation uses the same recorded fill prices, size, commission and slippage.

Dataset reconciliation compares two distinct imports for the same dated contract and data kind. It audits normalized UTC minute opens, both reviewed schedules and exact integer OHLCV values. Use `--start` and `--end` together to scope the comparison inside shared calendar coverage. Bounded discrepancy examples and source checksums are saved in the report. Inspect an incomplete import through reconciliation while its ordinary replay gate remains enforced.

Aggregation requires a dated calendar. Omit `--timeframe` to emit all supported intervals (5m, 15m, 1h, 4h and 1d). Intraday buckets start at each open window and restart after closures. When an open window ends inside a bucket, a complete shorter bar is emitted with `shortened: true`. Daily bars combine all open windows on the trade date. Every published aggregate contains all expected source minutes and is emitted at its scheduled close. Export boundaries do not shorten a bucket.

Structure replay defaults to 5m candles, 3-left/3-right internal pivots, 5-left/5-right external pivots and close-based breaks. Strict local extrema reject ties inside the pivot window. Use `--internal-pivot LEFT RIGHT`, `--external-pivot LEFT RIGHT` and `--break-mode` to run separate configurations. Break modes are `wick`, `close`, `buffered_close` and `displacement_close`. Volatility filters use the full window of earlier true ranges. The manifest records the exact configuration, versions and event count. Each event identifies its source and confirmation candles.

Add `--evidence` to include price-derived liquidity levels, sweeps, FVGs and IFVGs. Use `--min-gap-ticks` and `--inversion-closes` to compare gap definitions. Each event contains its source prices and availability. Liquidity levels are observable price proxies. OHLCV supplies an observation window for a sweep, so the output reports elapsed bars and seconds between observed timestamps.

Strategy replay stores paper setup transitions, contextual measurements, sizing and reasons a setup matched or failed. Variants include `mss`, `sweep_mss`, `mss_fvg`, `mss_ifvg`, `sweep_mss_fvg`, `sweep_mss_ifvg` and `sweep_mss_ifvg_htf`. Use `--session`, `--regime`, volatility bounds and `--minimum-score` for filtered experiments. The default gross cap is USD 50, with optional `--net-risk-cents` for an all-in cap. Choose `--target-mode structural` to use the closest favorable confirmed pivot. The experimental score has no calibrated probability interpretation.

Supply `--economic-calendar` only when you have a reviewed point-in-time snapshot. Its JSON fields are `source`, `version`, `coverage` with `start` and `end`, `coverage_known_at` and `events`. Each event has `event_id`, `title`, `scheduled_at`, `published_at` and optional `actual_at` plus `actual_known_at`. Use offset-bearing ISO timestamps. Use optional `supersedes` to replace an earlier announcement ID at the new publication time. A cancellation also sets `cancelled: true`. Set `coverage_complete: false` for partial sources, which can indicate elevated risk but leave other periods unknown. Without effective coverage, the calendar status is unknown. `--require-clear-event-risk` blocks signals unless the snapshot establishes clear coverage around the decision.

Install `.[calendar]` to normalize a reviewed local iCalendar capture with
`normalize-economic-ics FILE.ics`. Supply `--source`, UTC coverage with
`--start` and `--end`, one or more `--title-prefix` values and an explicit
`--data-kind`. Use `observed_publication` for a reviewed release source and
`synthetic` for fixtures. By default the capture is known at normalization
time. Supply `--observed-at` only for a preserved earlier capture. The output
is compatible with `--economic-calendar` and provider settings. It is a partial
snapshot that can flag known releases. BLS publishes an iCalendar covering many
scheduled releases, including releases of interest to this workflow.
[BLS iCalendar documentation](https://www.bls.gov/help/hlpiCAL.htm)

Use `compare-economic-ics` with the same source, scope, filters and data kind
to process 2–64 preserved copies of one publication. Repeat
`--capture OBSERVED_AT FILE.ics` in increasing order with offset-bearing
timestamps that include seconds. The output is a compatible partial snapshot
and lists every input checksum, parser version and observation time. An
existing UID keeps its identity through a title or time change. A missing UID
leaves its last observed schedule in place. Only an explicit
`STATUS:CANCELLED` for a known UID records a cancellation. Duplicate UIDs,
recurrence and inconsistent supplied sequence numbers on schedule changes
require source review. Keep the original files with their capture records.
[RFC 5545](https://www.rfc-editor.org/rfc/rfc5545.html)


Backtest replay accepts the strategy options and simulates a single position at a time. It uses the next eligible minute open after a signal, applies the structural bracket and rechecks sizing at the fill price. Use `--latency-minutes`, `--entry-timeout-minutes`, `--round-trip-spread-ticks`, `--commission-cents` and `--slippage-ticks` to set execution assumptions. It uses stop-first for ambiguous same-minute touches, adverse opening prices for stop gaps and the target price for favorable opening gaps. On a trade-print dataset, `--resolve-with-trade-prints` creates a separate run that uses the first ordered trade print crossing either bracket in a dual-touch minute. The audit includes the print timestamp and sequence. Modeled bracket prices, observation at minute close and costs retain the minute paper assumptions. The execution sensitivity report pairs the saved runs and quantifies the net difference attributable to dual-touch ordering across the unpartitioned replay. It flattens at scheduled window close unless `--carry-across-closures` is set. Backtest audit distinguishes forced end-of-data exits. The summary reports actual sample size, net returns and closed-trade equity drawdown.

Validation compares runs with the same input dataset, timeframe, cost profile and execution assumptions. Set the two UTC boundaries inside the observed dataset range before evaluating results. Use `--embargo-minutes` and `--minimum-selection-trades` to define eligibility. Only the selected candidate's holdout is reported. Candidate differences, walk-forward windows and uncertainty are recorded in the saved report. Use `parameter-sensitivity` for a set of saved candidates varying just one setting. The report contains only development and validation outcomes. For a subset, supply `--candidate` once per included run, including the baseline.

For a matched random control, choose a broader opportunity variant before inspecting holdout results. Build its backtest with the same settings as the reference except for the variant, such as `mss` in place of `sweep_mss_ifvg`. The control fits admission rates on development decisions, then replays all arms from flat after the holdout embargo. The comparison describes sampled structural opportunities on one shared period. Record the opportunity rule, strata and seed in the research plan before evaluation.

Monte Carlo defaults to the reference assumed win rates of 30%, 35%, 40%, 45%, 50% and 60%. Set `--mode block_bootstrap --backtest RUN_ID` to resample net backtest outcomes, or use `shuffle` with a trade count at or below the observed sample size. Simulated paths use a generic fixed floor at trade closes. They are conditional on the source and assumptions.

The account command models a separate $25K LucidFlex paper ledger. Supply a dated schedule adjusted for the provider's applicable closing cutoff and holidays. The ledger has `data_kind` (`synthetic` or `paper_marks`), `source`, `started_at` and chronological `actions`. A `mark` action uses `at`, `trade_date`, optional `closed_trade_nets_cents`, `unrealized_cents`, `open_micros` and `end_of_day`. The EOD mark must be flat and match the scheduled final close. `payout` actions use `at` and `gross_cents`. `activate_funded` uses `at` after a simulated pass. All action objects include `kind`.

Use `--consistency-bps 5000` for the evaluation add-on profile and supply an explicit `--daily-loss-cents` for a daily-loss scenario. Default marked-equity checks are a conservative research assumption. Add `--lucidflex-trades-per-session` to Monte Carlo for an EOD-trailing drawdown scenario using trade closes and the specified session grouping. See the [rule sources and assumptions](docs/VALIDATION.md).

The contract catalog JSON contains `source`, `version`, `data_kind`, `listings` and optional `activity`. A listing uses `contract`, `first_trade_at`, `last_trade_at` and `known_at`. An activity row uses `contract`, `period_start`, `period_end`, `available_at` and volume or open interest. Roll decisions use only comparable periods available at `--as-of`. Supply independently reviewed listing and expiry timestamps. [CME expiration calendar](https://www.cmegroup.com/tools-information/calendars/expiration-calendar.html)

A continuous plan contains `basis` (`unadjusted` or `forward_adjusted`) and ordered `segments` with `dataset_id`, `start` and `end`. Forward adjustment requires `reference_open` for each successor, identifying a completed overlapping minute before the switch. Source calendars and provenance must be compatible. Output separates research OHLC from the dated unadjusted source candle. The Reports workspace can create rollover assessments and continuous plans from reviewed JSON. Its continuous preview keeps dated and research closes in separate columns. The REST route `/api/reports/REPORT_ID/continuous-bars` accepts a bounded limit and optional half-open UTC `start` and `end`.

Install `.[stream]` for `monitor WSS_URL --calendar SCHEDULE --contract MGC-YYYY-MM --source NAME --feed-status real_time`. Use the status supplied by the authorized provider. The normalized JSON frame has `event: candle_closed`, `contract`, `opened_at`, integer OHLC fields with the `_ticks` suffix and integer `volume`. An optional `source_status` must match the feed configuration. Pass a token variable name with `--token-env` when needed. The reviewed coverage determines the first expected open minute. The stream preserves transport receipt time and records processing time after the SQLite write transaction starts. It can retry a held frame twice after a transient store lock. Recovered frames remain ineligible for new alerts or forward execution. Direct monitoring and monitor replay also accept `--economic-calendar` and `--require-clear-event-risk`. The provider profile form supports the same reviewed JSON snapshot and filter. For the exchange schedule, choose an imported dataset or supply a reviewed dated calendar JSON and explicit MGC contract. A standalone schedule lets you save a profile before importing any historical data. Each supplied snapshot is frozen into the monitor definition. Expired or missing coverage yields unknown risk. The monitor retains the source-close assessment and separately checks the configured clear-risk policy at actual observation. A late publication can suppress the alert and a new forward entry while preserving the recorded setup evidence.

The monitor retains pending and applied minutes and reconstructs its research state from durable data on restart. A gap can be filled by ordered arrivals or by an optional `--backfill-url` returning the normalized CSV schema for the requested `contract`, `start` and `end`. Recovered, delayed and simulated setups remain in the event journal. The in-app alert queue requires a fresh real-time close outside a known gap. A conflicting duplicate latches an integrity fault for the monitor run.

An explicitly started `deliver MONITOR_ID --channel telegram --destination-env AURUM_TELEGRAM_CHAT_ID --token-env AURUM_TELEGRAM_TOKEN` worker consumes eligible alerts. For Discord use `--channel discord --destination-env AURUM_DISCORD_WEBHOOK`. Add `--watch` for polling and `--quiet 22:00 07:00 --timezone Europe/Prague` for local quiet hours. Filters include score, session and severity. `delivery-status` reads attempts, and `cancel-delivery MONITOR_ID ALERT_ID` cancels queued or retrying deliveries.

See [the roadmap](docs/ROADMAP.md), [the architecture](docs/ARCHITECTURE.md), and [the handoff](docs/HANDOFF.md) for implementation status.

## Local research dashboard

Install the API extra, then start the API and frontend in separate terminals.

```sh
.venv/bin/python -m pip install -e '.[dev,parquet,stream,api]'
.venv/bin/aurum --db data/aurum.sqlite serve
```

```sh
npm --prefix frontend ci
npm --prefix frontend run dev
```

Open [the local workspace](http://127.0.0.1:3000) or [the API schema](http://127.0.0.1:8765/docs). Import a dataset into the same database to populate the historical chart. Choose **Monitor journal** on the Market tab to view applied candles and their source classes from a configured monitor. Set the server-side `AURUM_API_BASE` if the API uses a different loopback port. For bearer authentication, pass `--token-env AURUM_API_TOKEN` to the API and set the same environment variable for both processes.

## Forward paper journal

Register a run against a configured monitor, then advance its applied journal. The registration captures the monitor cursor. Forward outcomes start with observations received after that checkpoint.

```sh
.venv/bin/aurum --db data/aurum.sqlite forward-start MONITOR_ID
.venv/bin/aurum --db data/aurum.sqlite forward-sync FORWARD_ID
.venv/bin/aurum --db data/aurum.sqlite forward-events FORWARD_ID
```

Use `forward-sync --watch` for a foreground paper consumer. A simulated monitor requires `forward-start --synthetic` and produces a labeled synthetic verification ledger.

Save a frozen period report from the processed journal. An optional
`--baseline-validation` must reference a compatible independent holdout saved
before registration.

```sh
.venv/bin/aurum --db data/aurum.sqlite forward-report FORWARD_ID --period weekly --trade-date 2025-03-10
```

Use `forward-sync FORWARD_ID --watch --auto-reports` to save completed reviewed
daily, weekly and monthly periods from the foreground worker. Weekly and
monthly publication requires reviewed schedule coverage into the next period.

## Runtime profiles

The **Settings** tab saves provider and notification profiles. Set their
`AURUM_` environment variable values in the corresponding worker environment.
Use the command shown with the saved profile, or list profiles with
`aurum runtime-profiles`. The existing CLI flags remain available for an
independently supplied reviewed calendar or direct transport configuration.

## Local backups and integrity

Use a new output path on a local filesystem that supports hard links. The
backup is checked before publication, has private file permissions and includes
a SHA-256 digest in the command output. The snapshot contains original source
data. Handle it under the same access and licensing requirements as the store.

```sh
.venv/bin/aurum --db data/aurum.sqlite backup data/aurum-backup.sqlite --verify-normalized
.venv/bin/aurum --db data/aurum-backup.sqlite verify-store --full --verify-sources --verify-normalized
.venv/bin/aurum --db data/aurum-backup.sqlite verify-dataset DATASET_ID
.venv/bin/aurum --db data/aurum-backup.sqlite verify-quote-series SERIES_ID
```

For a restore rehearsal, point `--db` at the verified snapshot and inspect or
replay a known dataset before switching workers. Keep the original store for
comparison. The command uses the [SQLite Online Backup API](https://sqlite.org/backup.html).

`verify-dataset` rebuilds normalized candles and stored trade prints from the
preserved source with the original supported import and timezone versions.
It compares metadata, quality findings and exact normalized rows offline. An
accurately stored incomplete dataset can verify while remaining blocked for replay.
`verify-quote-series` applies the same source-reconstruction check to normalized
bid and ask observations. The optional whole-store audit checks every recorded
dataset and quote series. With `backup --verify-normalized`, the staged copy
must pass before it is published. The backup timeout applies to the SQLite copy.
