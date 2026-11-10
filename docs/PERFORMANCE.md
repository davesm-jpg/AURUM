# Local performance measurements

## Synthetic workload

`benchmarks/synthetic_pipeline.py` generates invented MGC prices and weekday windows in an isolated temporary directory. It imports the source, builds an evidence run and a paper replay, ingests the minute stream into a simulated monitor, reopens the monitor and calls the local chart routes. The supplied windows are benchmark fixtures. Read and restart times are medians of five measurements. Import, replay and ingestion are single measurements. Each chart output has a stable SHA-256 digest for parity comparisons.

```sh
.venv/bin/python benchmarks/synthetic_pipeline.py --sessions 20
.venv/bin/python benchmarks/synthetic_pipeline.py --sessions 60
```

## Baseline on 2026-11-10

macOS arm64, Python 3.12.7 and SQLite 3.45.3. Each session has 360 synthetic minutes.

| Operation | 7,200 minutes | 21,600 minutes |
| --- | ---: | ---: |
| Import and persist | 117 ms | 317 ms |
| Evidence replay and persist | 274 ms | 845 ms |
| Paper replay and persist | 279 ms | 852 ms |
| Monitor ingest | 10.20 s | 32.87 s |
| Monitor restart | 249 ms | 774 ms |
| Historical chart, first window | 49 ms | 145 ms |
| Historical chart, recent window | 45 ms | 143 ms |
| Monitor chart, recent window | 116 ms | 335 ms |

The larger run stored 15,904 historical evidence events and 20,224 monitor market events. Its baseline chart digests are:

- Historical first window `a2de1d4349eeb5ffadc49f02b95e23cf81eb32ef2b8bfac1f0a03b2f10ce581f`
- Historical recent window `6aa28e7e083562e04cb583abd69947f92490bf8cdc7cea66d460bc0cb52f37af`
- Monitor recent window `594c7214cab60b4adf18c84ca43357a80a40093e6495f5ba86d090bcdf078e88`

Chart time grew with journal length for fixed-width views, even when the requested historical window was at the beginning. Monitor restart replayed its whole applied prefix while holding a write transaction. Those are the first optimization targets. Live provider latency and strategy performance require authorized data.

## Bounded monitor projection

At 21,600 synthetic minutes the recent monitor chart median decreased from 335.1 ms to 11.7 ms. The output retained the baseline digest. The monitor chart counts actual analyzed primary closes before the earliest observation in view, then starts no later than the maximum configured span lifetime plus two bars. Recovery can make a much earlier source bar available within the view. The lower bound follows that observation and the upper bound follows the pinned applied cursor. The v9 fallback still scans the relevant full journal.

The 60-session simulated ingestion measurement moved from 32.87 s to 33.47 s after adding the indexes. These are single measurements and include SQLite commits per minute.

## Indexed historical projection and restart lock scope

On the same 21,600-minute workload, the first historical chart took 6.3 ms and the recent historical chart took 10.0 ms, compared with 144.9 and 143.1 ms in the baseline. The recent monitor chart took 11.8 ms. All three projection digests remained unchanged. Evidence replay and persistence measured 883.6 ms versus 845.3 ms before indexing. A historical backtest with carried positions still reads its earlier order prefix when the view might intersect an open bracket.

Restart reconstruction still processes the applied prefix when a process has no in-memory state. The concurrency test verified that another writer commits while reconstruction is paused. Benchmark restart times above precede that lock-scope change.
