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
```

Import reports a stable dataset ID and any quality findings. Replay emits one JSON object per completed bar with UTC `opened_at` and `available_at` fields. Prices in replay are integer ticks. Dated snapshots add a `trade_date` to each replay event. Legacy snapshots with a top-level `windows` array continue to import and replay with their original IDs and event shape. A missing expected minute is preserved as a quality incident and prevents replay. Invalid prices, duplicate candles, mixed contracts, and rows outside open windows reject the import. Out-of-order rows are sorted and reported. Use `--data-kind synthetic` for generated fixtures.

Aggregation requires a dated calendar. Omit `--timeframe` to emit all supported intervals (5m, 15m, 1h, 4h and 1d). Intraday buckets start at each open window and restart after closures. When an open window ends inside a bucket, a complete shorter bar is emitted with `shortened: true`. Daily bars combine all open windows on the trade date. Every published aggregate contains all expected source minutes and is emitted at its scheduled close. Export boundaries do not shorten a bucket.

See [the roadmap](docs/ROADMAP.md), [the architecture](docs/ARCHITECTURE.md), and [the handoff](docs/HANDOFF.md) for implementation status.
