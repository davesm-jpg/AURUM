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
```

Import reports a stable dataset ID and any quality findings. Replay emits one JSON object per completed bar with UTC `opened_at` and `available_at` fields. Prices in replay are integer ticks. Dated snapshots add a `trade_date` to each replay event. Legacy snapshots with a top-level `windows` array continue to import and replay with their original IDs and event shape. A missing expected minute is preserved as a quality incident and prevents replay. Invalid prices, duplicate candles, mixed contracts, and rows outside open windows reject the import. Out-of-order rows are sorted and reported. Use `--data-kind synthetic` for generated fixtures.

Aggregation requires a dated calendar. Omit `--timeframe` to emit all supported intervals (5m, 15m, 1h, 4h and 1d). Intraday buckets start at each open window and restart after closures. When an open window ends inside a bucket, a complete shorter bar is emitted with `shortened: true`. Daily bars combine all open windows on the trade date. Every published aggregate contains all expected source minutes and is emitted at its scheduled close. Export boundaries do not shorten a bucket.

Structure replay defaults to 5m candles, 3-left/3-right internal pivots, 5-left/5-right external pivots and close-based breaks. Strict local extrema reject ties inside the pivot window. Use `--internal-pivot LEFT RIGHT`, `--external-pivot LEFT RIGHT` and `--break-mode` to run separate configurations. Break modes are `wick`, `close`, `buffered_close` and `displacement_close`. Volatility filters use the full window of earlier true ranges. The manifest records the exact configuration, versions and event count. Each event identifies its source and confirmation candles.

Add `--evidence` to include price-derived liquidity levels, sweeps, FVGs and IFVGs. Use `--min-gap-ticks` and `--inversion-closes` to compare gap definitions. Each event contains its source prices and availability. Liquidity levels are observable price proxies. OHLCV supplies an observation window for a sweep, so the output reports elapsed bars and seconds between observed timestamps.

Strategy replay stores paper setup transitions, contextual measurements, sizing and reasons a setup matched or failed. Variants include `mss`, `sweep_mss`, `mss_fvg`, `mss_ifvg`, `sweep_mss_fvg`, `sweep_mss_ifvg` and `sweep_mss_ifvg_htf`. Use `--session`, `--regime`, volatility bounds and `--minimum-score` for filtered experiments. The default gross cap is USD 50, with optional `--net-risk-cents` for an all-in cap. Choose `--target-mode structural` to use the closest favorable confirmed pivot. The experimental score has no calibrated probability interpretation.

Supply `--economic-calendar` only when you have a reviewed point-in-time snapshot. Its JSON fields are `source`, `version`, `coverage` with `start` and `end`, `coverage_known_at` and `events`. Each event has `event_id`, `title`, `scheduled_at`, `published_at` and optional `actual_at` plus `actual_known_at`. Use offset-bearing ISO timestamps. Without effective coverage, the calendar status is unknown. `--require-clear-event-risk` blocks signals unless the snapshot establishes clear coverage around the decision.

See [the roadmap](docs/ROADMAP.md), [the architecture](docs/ARCHITECTURE.md), and [the handoff](docs/HANDOFF.md) for implementation status.
