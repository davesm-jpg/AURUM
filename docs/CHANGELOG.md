# AURUM changelog

## 2026-11-09 — Causal structure replay

- Added independent internal and external confirmed pivots, structural classifications and versioned BOS/MSS rules.
- Added versioned structure runs and replayable evidence. SQLite v2 preserves existing datasets and migrates on write.
- Verified mirrored breaks, future-data isolation, rule thresholds, persistence and rollback on synthetic inputs.

## 2026-11-09 — Completed candle aggregation

- Added incremental 5m, 15m, 1h, 4h and daily OHLCV from dated one-minute data.
- Added the `aggregate` command with versioned event IDs, exchange trade dates, source-minute counts and shortened-bucket flags.
- Verified coverage, closures, gaps and replay parity using synthetic inputs.

## 2026-11-09 — Exchange trading dates

- Added versioned MGC schedules with explicit exchange trade dates, full session boundaries and split windows for halts.
- Preserved legacy dataset IDs and replay output. Dated snapshots now add the trade date to candle-close replay.
- Verified coverage clipping, regular session boundaries, DST, synthetic exceptions and compatibility against a database created by the previous checkpoint.

## 2026-11-08 — Historical data foundation

- Initialized Git, the Python package and persistent project operating documents.
- Added CME-sourced MGC metadata, explicit contract identity and UTC timestamp validation.
- Added versioned exchange window snapshots, CSV import and quality gates, content-addressed source retention, atomic SQLite persistence and deterministic candle-close replay.
- Verified the CLI and data path with labeled synthetic fixtures, including concurrency and rollback.
