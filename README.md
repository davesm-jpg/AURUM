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

Supply a JSON snapshot of the applicable MGC trading windows from an exchange-reviewed schedule. Its coverage is the expected import period. Every open minute inside that period is expected to have one bar. Clip trading windows to the requested coverage and omit closed intervals, including maintenance and any applicable halts. Use ISO timestamps with explicit offsets. The following schedule is a **synthetic schema example**. For real data use the applicable [CME schedule](https://www.cmegroup.com/trading-hours.html) and record its source and revision.

```json
{
  "instrument": "MGC",
  "source": "synthetic schema example",
  "version": "example-v1",
  "coverage": {"start": "2025-03-10T13:00:00Z", "end": "2025-03-10T13:02:00Z"},
  "windows": [{"start": "2025-03-10T13:00:00Z", "end": "2025-03-10T13:02:00Z"}]
}
```

```sh
.venv/bin/aurum --db data/aurum.sqlite import-csv data/mgc.csv \
  --calendar data/schedule.json --provider 'your authorized source' \
  --contract MGC-2025-04 --source-timezone UTC --timestamp-label open \
  --data-kind historical --price-basis unadjusted
.venv/bin/aurum --db data/aurum.sqlite inspect DATASET_ID
.venv/bin/aurum --db data/aurum.sqlite replay DATASET_ID
```

Import reports a stable dataset ID and any quality findings. Replay emits one JSON object per completed bar with UTC `opened_at` and `available_at` fields. Prices in replay are integer ticks. A missing expected minute is preserved as a quality incident and prevents replay. Invalid prices, duplicate candles, mixed contracts, and rows outside open windows reject the import. Out-of-order rows are sorted and reported. Use `--data-kind synthetic` for generated fixtures.

See [the roadmap](docs/ROADMAP.md), [the architecture](docs/ARCHITECTURE.md), and [the handoff](docs/HANDOFF.md) for implementation status.
