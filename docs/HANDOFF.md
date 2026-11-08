# AURUM — DEVELOPMENT HANDOFF

## Project Status

- Current phase — Phase 1, repository and data foundation.
- Current milestone — D01 is verified on synthetic fixtures and complete. Phase 1 continues with D02 and D03. External historical-data verification D04 is blocked.
- Current branch — `main`.
- Last verified commit — `4aff6206a8cb5f51f8f988f93558d1c25fd2676b`. Later handoff-only changes may be ahead of this code checkpoint.

## Current Objective

- Task ID — D02.
- Objective — Add versioned exchange trading-date semantics and normalize reviewed MGC session snapshots before aggregation.
- Acceptance criteria — A new snapshot explicitly assigns trade dates to windows, represents separate windows within one session and models calendar exceptions. Existing v1 datasets retain their IDs, provenance and replay behavior. Tests cover evening opens, maintenance, weekends, early close, holiday closure, exceptional halt and US/Europe DST divergence. Identify which fixture information is synthetic and which rules were sourced from the exchange.

## Completed Work

- Initialized an empty Git repository and Python package. Saved the canonical numbered requirements, operating instructions, architecture, roadmap, ADRs, validation, known issues and changelog.
- Implemented the CLI flow `import-csv` → `inspect` → `replay`. It handles a single dated, unadjusted MGC contract with 1-minute OHLCV per dataset. CSV bytes and the normalized calendar, source metadata, import settings, version, checksum and quality findings are retained.
- Implemented deterministic tick conversion, timestamp handling with pinned timezone rules, contract and OHLCV checks, duplicate rejection, out-of-order sorting, calendar checks and coverage-based gap detection. Gaps block replay. Each emitted event carries the data kind, contract, stable ID and UTC close availability.
- Verified persistence across connections and subprocesses, concurrent idempotent re-import, source blob deduplication and rollback after a simulated candle write failure. See `tests/test_data_pipeline.py` and `docs/VALIDATION.md`.

## Work In Progress

- Current files for D02 — `src/aurum/exchange_calendar.py`, `src/aurum/market_data.py`, `src/aurum/storage.py`, `tests/test_data_pipeline.py` and README.
- Implemented portions — V1 accepts an ordered schedule of half-open UTC windows and explicit coverage, without grouping windows by exchange trading date. The normalized calendar is part of the immutable dataset identity.
- Remaining portions — D02 design and code are pending. D03 will aggregate completed 5m, 15m, 1h, 4h and daily bars through a shared incremental input path. Research strategy and service phases follow the roadmap.

## Important Decisions

- ADR-001 — Use a local SQLite v1 store with no external services for the initial data path.
- ADR-002 — Require independently reviewed, caller-supplied windows covering the expected export range. Use pinned IANA timezone rules for CSV timestamp conversion.
- ADR-003 — Preserve raw bytes and versioned identity, store integer ticks and block incomplete replay. Zero volume is a warning. Corrected inputs produce new dataset IDs.
- Source of reference tick economics — COMEX Rulebook Chapter 120, linked in `docs/VALIDATION.md`. For effective calendar rules consult CME's product and trading-hours publications, linked in the architecture and README.

## Known Issues

- DATA-001 — High, blocked. No authorized MGC sample or production-reviewed schedule was supplied. D04 requires both.
- CAL-001 — Medium, open. Calendar snapshots currently depend on caller review and have no trading-date grouping.
- DATA-002 — Medium, open. Exports omitting no-trade minutes are treated as incomplete under the current coverage rule.
- DATA-003 — Medium, open. Imports buffer the CSV and normalized bars in memory. Use bounded export windows.

## Test Status

- Passed — `.venv/bin/python -m pytest -q` (25 cases), `.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`, `.venv/bin/mypy .` (8 files), `.venv/bin/python -m pip check` and staged diff whitespace checks.
- Failed — No outstanding failures.
- Not run — Real market data reconciliation, production schedule checks, replay/stream comparison and all later strategy, backtest and live checks.
- Limitations — All test market observations are synthetic. No trades, research performance or current Lucid account rules were validated.

## Environment

- Runtime — Python 3.12.7 on macOS arm64 and SQLite 3.45.3. SQLite 3.37+ is required.
- Setup — `python3.12 -m venv .venv`, then `.venv/bin/python -m pip install -e '.[dev]'`.
- Required services — None for the current CLI. Keep local data in the ignored `data/` directory.
- Missing configuration — Authorized historical CSV and the reviewed schedule for its exact period. No provider or alert credentials are needed for D02.

## Immediate Next Actions

1. Inspect the v1 calendar format and provenance hashing in `exchange_calendar.py` and `market_data.py`, plus the source identity and close-label tests. Verify the checkpoint with pytest.
2. Check authoritative CME product/calendar references, define a versioned snapshot with explicit exchange trading dates and keep the v1 calendar representation stable for existing imports. Document what a snapshot can assert and how coverage clipping interacts with trade dates.
3. Implement D02 and test weekend/maintenance exclusions, normal evening sessions, DST divergence, exceptions and old dataset replay. Update documentation and save the verified checkpoint before starting D03.

## Do Not Repeat

- Repository initialization, instrument tick verification and the D01 pipeline are complete.
- Avoid inferring closures from absent bars or trusting a generic weekly schedule for historical holiday and halt coverage.
- Keep synthetic fixtures and unverified account assumptions out of empirical findings.

## Continuation Instruction

Resume from the latest verified checkpoint. Inspect the referenced implementation, complete the next unblocked task, run relevant tests, and update the handoff.
