# AURUM architecture

## Running components

A Python 3.12+ modular monolith currently runs as a local CLI and requires no server. The editable package exposes the `aurum` executable.

| Module | Current responsibility |
| --- | --- |
| `instruments.py` | CME-sourced MGC tick economics, exact decimal-to-integer tick conversion and dated contract identity |
| `timestamps.py` | Aware UTC minute normalization, including rejection of ambiguous and nonexistent local times and source offset mismatches |
| `exchange_calendar.py` | Validation and lookup of open windows in a supplied, versioned MGC schedule |
| `market_data.py` | Import configuration, 1m candle model, CSV validation, provenance and gap detection |
| `storage.py` | Versioned transactional SQLite schema, immutable dataset records and chronological candle iterator |
| `cli.py` | `import-csv`, `inspect` and JSONL `replay` entry points |

## Flow and invariants

1. The caller supplies an authorized unadjusted single-contract CSV, source timezone, timestamp labeling, provider, data kind and reviewed schedule. Calendar coverage is the full expected export interval. Each disjoint window is half-open and clipped to coverage. Closed intervals inside coverage are represented by the absence of open windows.
2. Ingestion reads the exact source bytes, validates the fixed CSV schema and every bar, converts UTC or source-local timestamps to UTC, and maps close labels back one minute. Timezone conversion uses the pinned package, and the package version enters dataset provenance.
3. Normalization sorts uniquely timestamped bars and checks each expected open minute, including boundary and entirely absent windows. Invalid rows, duplicates or contract mismatches reject the import. Disorder and zero volume are reported. Gaps are retained as diagnostics and gate replay.
4. A SHA-256 ID covers the raw source checksum, source settings, import version, price basis and normalized schedule. The store saves raw bytes, metadata and candle rows atomically. An identical request resolves to the same dataset and retains its initial import time.
5. The repository reads a dataset in open-time order via an iterator. The CLI emits one stable ID per candle-close event. `available_at` is the historical candle close and models logical availability at close. Actual vendor delivery latency is a future input. Events keep their dated contract and historical/synthetic label.

## Current database

SQLite 3.37+ is required for STRICT tables. Schema v1 is identified by the AURUM application ID and SQLite user version. Writes use an immediate transaction, foreign keys and rollback on error. Read commands open in read-only mode. Migrations must explicitly handle each supported version.

- `source_blobs` keys raw CSV bytes by SHA-256 and deduplicates repeated source content.
- `datasets` keys records by dataset ID and references the source blob. It stores versioned provenance with the normalized schedule, actual range, import time, count, quality issues and replay eligibility.
- `candles` uses `(dataset_id, opened_at)` as its primary time-series index. OHLC values are integer ticks. Contract identity is inherited from the dataset.

The import parser holds a CSV and its normalized rows in memory. SQLite is a local, single-machine deployment. Schedule, provider and contract assertions are caller supplied. This checkpoint does not independently authenticate them. [CME trading hours and holiday schedules](https://www.cmegroup.com/trading-hours.html)

## Next boundaries

Introduce explicit exchange trading-date metadata before aggregation so split windows can belong to the same session and closures cannot bridge candle buckets incorrectly. Keep raw adapter events separate from normalized candles. Store future structure events with source candle IDs and confirmation time. Plan a shared incremental path for replay and monitoring.

Service/database, API, dashboard, strategy, simulation and notifications remain roadmap work. Evaluate PostgreSQL and migrations before a multi-client deployment. Keep credentials outside the data and strategy models.
