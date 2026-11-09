# AURUM architecture

## Running components

A Python 3.12+ modular monolith currently runs as a local CLI and requires no server. The editable package exposes the `aurum` executable.

| Module | Current responsibility |
| --- | --- |
| `instruments.py` | CME-sourced MGC tick economics, exact decimal-to-integer tick conversion and dated contract identity |
| `timestamps.py` | Aware UTC minute normalization, including rejection of ambiguous and nonexistent local times and source offset mismatches |
| `exchange_calendar.py` | Validation and lookup of covered open windows and exchange trading dates in a supplied, versioned MGC schedule |
| `market_data.py` | Import configuration, 1m candle model, CSV validation, provenance and gap detection |
| `providers.py`, `parquet_rows.py` | Interchangeable local and authorized HTTPS sources with shared normalized CSV/Parquet validation |
| `contracts.py`, `continuous.py` | Reviewed listings, point-in-time roll decisions and separately priced continuous research series |
| `storage.py` | Versioned transactional SQLite schema, immutable dataset records and chronological candle iterator |
| `candles.py` | Incremental aggregation of chronological minute closes into exchange-aligned intraday and daily bars |
| `market_structure.py` | Confirmed pivots, separate structure scopes, BOS and MSS with causal displacement evidence |
| `liquidity.py`, `liquidity_map.py`, `sessions.py` | Price-derived proxies, elapsed-window reclaims and timezone-aware source periods |
| `imbalances.py`, `evidence.py` | FVG and IFVG lifecycle and shared causal structure/evidence replay |
| `market_context.py`, `economic_events.py` | Completed higher-timeframe structure, regime, session and point-in-time event context |
| `strategy.py`, `paper_risk.py` | Setup state, rule variants, experimental scoring and exact paper sizing |
| `execution.py`, `backtesting.py`, `performance.py` | Minute paper fill model, strategy replay orchestration and trade-level performance summaries |
| `statistics.py`, `monte_carlo.py`, `research_reports.py` | Chronological selection and holdout, block-bootstrap diagnostics, conditional equity paths and saved analysis reports |
| `account_simulation.py`, `account_reports.py` | Independent versioned paper account ledger, loss trail, phase rules and persisted reports |
| `feed_frames.py`, `monitoring.py`, `streaming.py` | Normalized WebSocket, reviewed continuity, restart recovery, HTTPS backfill and in-app alert journal |
| `cli.py` | Import, inspection, replay, aggregation and persisted research runs |

## Flow and invariants

1. The caller supplies an authorized unadjusted single-contract CSV, source timezone, timestamp labeling, provider, data kind and reviewed schedule. Calendar coverage is the expected export interval. The dated snapshot groups full half-open open windows by exchange trade date. The calendar intersects those windows with coverage for candle validation. Closed intervals are represented by the absence of open windows, and split windows can share a trade date. Legacy snapshots keep their original clipped window representation.
2. Ingestion reads the exact source bytes, validates the fixed CSV schema and every bar, converts UTC or source-local timestamps to UTC, and maps close labels back one minute. Timezone conversion uses the pinned package, and the package version enters dataset provenance.
3. Normalization sorts uniquely timestamped bars and checks each expected open minute, including boundary and entirely absent windows. Invalid rows, duplicates or contract mismatches reject the import. Disorder and zero volume are reported. Gaps are retained as diagnostics and gate replay.
4. A SHA-256 ID covers the raw source checksum, source settings, import version, price basis and normalized schedule. The store saves raw bytes, metadata and candle rows atomically. An identical request resolves to the same dataset and retains its initial import time.
5. The repository reads a dataset in open-time order via an iterator. The CLI emits one stable ID per candle-close event. `available_at` is the historical candle close and models logical availability at close. Actual vendor delivery latency is a future input. Events keep their dated contract and historical/synthetic label. For dated schedules, the CLI resolves the exchange date at the candle open using the stored snapshot.
6. The same incremental aggregator accepts each completed minute from replay or another chronological caller. It holds one running OHLCV bucket per selected timeframe. Intraday buckets restart at each full open window and daily bars combine all expected open windows for a trade date. A bucket publishes at its scheduled close only when every expected minute was observed. The CLI derives aggregate IDs from the dataset, aggregation version, timeframe and bucket open.
7. Structure analysis consumes completed aggregates for one timeframe and contract. It processes breaks against previously confirmed levels before confirming new swings, then updates the classifications. Each event's UTC source opens can be joined to the input dataset's aggregate events for the run timeframe and version. The engine holds bounded pivot and ATR windows and the latest levels per scope.
8. An evidence run also observes completed named-session minutes and daily bars. On an analysis close it evaluates previously mapped levels, structure and imbalances, links strictly earlier eligible sweeps to MSS, and then releases newly known price proxies. Full daily, weekly and named-period coverage is checked before publication. A gap or proxy is never retested by its confirming bar.
9. Strategy replay advances the same evidence stream and completed 15m/1h/4h context before each setup decision. It writes state transitions with source evidence, explicit filters, costs and contextual uncertainty. Setup IDs are stable inside the versioned run. Historical restart recovery can reconstruct active state by replaying the input prefix. Rebuilding a completed definition returns its existing events.
10. Backtest replay processes queued orders and existing positions against each minute before asking the strategy about that minute's close. Entry orders become eligible only after the signal, at the next eligible open under configured latency. The run stores setup transitions, order audit, completed trades and a terminal summary. The manifest records execution, cost and performance versions.
11. Research reports join completed trades to their setup timestamps and scores. Split eligibility uses both decision and observed outcome times. Candidate ranking and walk-forward use pre-holdout data. Scenario simulations have their own assumed label, and resampling retains the original backtest provenance.
12. The account ledger consumes explicit paper observations, not trade orders. Session close updates the loss trail and funded sizing tier. Intraday checks compare the selected measurement against the current floor. Payout and phase actions are explicit and separately audited. The same loss trail powers an optional trade-close drawdown Monte Carlo model.
13. Contract analysis filters reviewed listings by effective availability and compares activity over a common measurement period. Continuous series plans reference immutable datasets and explicit switches. Forward adjustment uses a shared close known by the switch and applies only to the successor segment. Streaming a series uses indexed time bounds and keeps dated source candles separate.
14. The monitor journals normalized and raw frames. It applies only the contiguous prefix of reviewed open minutes, passing those minutes to the existing strategy engine. Recovered setup events retain their actual observed time and source status. A fresh, connected, real-time close can enqueue a paper alert. Missing data, clock staleness and latched integrity conflicts suspend delivery eligibility.

## Current database

SQLite 3.37+ is required for STRICT tables. The AURUM application ID and SQLite user version identify the database. Read commands open in read-only mode, and earlier input schemas remain readable. The first write applies pending migrations through v4 transactionally. Writes use an immediate transaction, foreign keys and rollback on error.

- `source_blobs` keys raw CSV bytes by SHA-256 and deduplicates repeated source content.
- `datasets` keys records by dataset ID and references the source blob. It stores versioned provenance with the normalized schedule, actual range, import time, count, quality issues and replay eligibility. The calendar format has its own independent version in provenance JSON.
- `candles` uses `(dataset_id, opened_at)` as its primary time-series index. OHLC values are integer ticks. Contract identity is inherited from the dataset.
- `structure_runs` records a content-addressed run definition covering dataset, timeframe, exact rule configuration and implementation versions. `structure_events` stores ordered evidence keyed by run and sequence. Strategy transitions reuse the same tables with a versioned strategy manifest. Run and event writes commit together.
- `analysis_reports` stores content-addressed report definitions and results. Validation and resampled reports reference immutable backtest IDs. Assumed scenarios are reproducible without a dataset.
- `monitor_runs`, `monitor_minutes`, `monitor_events` and `monitor_alerts` keep source definition, durable cursor, pending frames, transition audit and deduplicated in-app alerts. Advance of the cursor, applied minute and resulting events commit together.

The import parser holds a CSV and its normalized rows in memory. SQLite is a local, single-machine deployment. Schedule, provider and contract assertions are caller supplied. This checkpoint does not independently authenticate them. [CME trading hours and holiday schedules](https://www.cmegroup.com/trading-hours.html)

## Next boundaries

Add optional external delivery and a read-only API for research and monitor state. Keep alert dispatch separate from the source connection. Verify the normalized connector against an authorized production provider when credentials and a reviewed calendar are available.

Service/database, API, dashboard, strategy, simulation and notifications remain roadmap work. Evaluate PostgreSQL and migrations before a multi-client deployment. Keep credentials outside the data and strategy models.
