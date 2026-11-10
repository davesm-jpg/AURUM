import asyncio
import json
import sqlite3
from collections import Counter, deque
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from secrets import compare_digest
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from aurum.api_config import (
    BacktestRequest,
    DeliveryProfileRequest,
    ForwardStartRequest,
    ProviderProfileRequest,
    backtest_configuration,
)
from aurum.api_reports import (
    ForwardReportRequest,
    IntrabarComparisonRequest,
    MatchedControlRequest,
    MonteCarloReportRequest,
    PaperAccountReportRequest,
    ReconciliationRequest,
    ValidationReportRequest,
    report_configuration,
)
from aurum.candles import CandleAggregator, Timeframe, completed_bar_start
from aurum.chart_evidence import CHART_EVENT_KINDS, ChartEvidence, chart_evidence, span_lookback
from aurum.exchange_calendar import SessionCalendar, TradingWindow
from aurum.forward import (
    ForwardPaper,
    describe_forward,
    forward_events,
    list_forward_runs,
    register_forward,
)
from aurum.instruments import MAX_SQLITE_INTEGER, MGC, MgcContract
from aurum.market_data import canonical_json
from aurum.monitoring import (
    HeartbeatStatus,
    applied_minute_batches,
    describe_monitor,
    market_chart_sequence_bounds,
    market_events_page,
    market_evidence_started_at,
    operational_monitor_status,
)
from aurum.providers import FeedStatus
from aurum.research_reports import backtest_analytics
from aurum.runtime_profiles import (
    describe_runtime_profile,
    list_runtime_profiles,
    save_runtime_profile,
)
from aurum.storage import DatasetStore
from aurum.timestamps import MINUTE, minute_timestamp, trade_timestamp

RecordId = Annotated[str, Path(pattern=r"^[a-f0-9]{64}$")]
PageLimit = Annotated[int, Query(ge=1, le=500)]
ChartTimeframe = Literal["1m", "5m", "15m", "1h", "4h", "1d"]
_CHART_KINDS = frozenset(CHART_EVENT_KINDS)


class CandleRecord(BaseModel):
    contract: str
    opened_at: str
    available_at: str
    open_ticks: int
    high_ticks: int
    low_ticks: int
    close_ticks: int
    volume: int
    trade_date: str | None = None
    shortened: bool = False


class MonitorCandleRecord(CandleRecord):
    source_minutes: dict[FeedStatus, int]


class MonitorChart(BaseModel):
    monitor_id: str
    contract: str
    source: str
    configured_status: FeedStatus
    state: str
    connected: bool
    checked_at: str | None
    heartbeat_status: HeartbeatStatus
    last_applied_close: str | None
    last_received: str | None
    pending_minutes: int
    fault: str | None
    alerts_suspended: bool
    stale_after_seconds: int
    market_status: Literal["open", "closed", "unavailable"]
    calendar_source: str
    calendar_version: str
    analysis_timeframe: Timeframe
    candles: list[MonitorCandleRecord]
    market_evidence_started_at: str | None
    market_evidence: ChartEvidence
    paper_evidence: ChartEvidence
    context_event: dict[str, object] | None


def create_app(store: DatasetStore, access_token: str | None = None) -> FastAPI:
    """Create the local research API without starting a server or changing data."""
    app = FastAPI(
        title="AURUM research API",
        version="0.1.0",
        description="Provenance, market evidence, simulated research and feed health.",
    )

    def authorize(request: Request, authorization: Annotated[str | None, Header()] = None) -> None:
        if request.method == "POST":
            origin = request.headers.get("origin")
            site = request.headers.get("sec-fetch-site")
            expected = f"{request.url.scheme}://{request.url.netloc}"
            if (origin is not None and origin != expected) or site not in (
                None,
                "same-origin",
                "none",
            ):
                raise HTTPException(status_code=403, detail="local API origin required")
        if access_token is not None and (
            authorization is None
            or not compare_digest(authorization.encode(), f"Bearer {access_token}".encode())
        ):
            raise HTTPException(status_code=401, detail="research access token required")

    router = APIRouter(prefix="/api", dependencies=[Depends(authorize)])

    @app.exception_handler(ValueError)
    async def invalid_request(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(
            {"detail": str(exc)},
            status_code=404 if str(exc).startswith("unknown ") else 422,
        )

    @app.exception_handler(sqlite3.Error)
    async def unavailable_store(request: Request, exc: sqlite3.Error) -> JSONResponse:
        return JSONResponse({"detail": "research store unavailable"}, status_code=503)

    @app.exception_handler(RequestValidationError)
    async def invalid_body(request: Request, exc: RequestValidationError) -> JSONResponse:
        issues = [
            {field: issue[field] for field in ("loc", "msg", "type")} for issue in exc.errors()
        ]
        return JSONResponse({"detail": issues}, status_code=422)

    @router.get("/health")
    def health() -> dict[str, object]:
        with store.connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
        return {"status": "ok", "schema_version": version, "execution_mode": "paper_research"}

    @router.get("/instrument")
    def instrument() -> dict[str, object]:
        return {
            "symbol": MGC.symbol,
            "exchange": MGC.exchange,
            "contract_ounces": MGC.contract_ounces,
            "tick_size": str(MGC.tick_size),
            "tick_value_usd": str(MGC.tick_value),
            "specification": MGC.specification_url,
        }

    @router.get("/datasets")
    def datasets(limit: PageLimit = 100) -> list[dict[str, object]]:
        with store.connection() as db:
            return [
                _dataset_item(row)
                for row in db.execute(
                    """SELECT id, provenance, candle_count, first_open, last_close, replayable
                   FROM datasets ORDER BY imported_at DESC, id LIMIT ?""",
                    (limit,),
                )
            ]

    @router.get("/datasets/{dataset_id}")
    def dataset(dataset_id: RecordId) -> dict[str, object]:
        return dict(store.describe(dataset_id))

    @router.get("/datasets/{dataset_id}/candles", response_model=list[CandleRecord])
    def candles(
        dataset_id: RecordId,
        timeframe: ChartTimeframe = "5m",
        limit: PageLimit = 240,
        through: Annotated[
            str | None, Query(description="Inclusive UTC candle close bound")
        ] = None,
    ) -> list[dict[str, object]]:
        return _candles(store, dataset_id, timeframe, limit, through)

    @router.get("/datasets/{dataset_id}/trade-prints")
    def trades(
        dataset_id: RecordId,
        start: str | None = None,
        end: str | None = None,
        after_timestamp: str | None = None,
        after_sequence: Annotated[int | None, Query(ge=0, le=MAX_SQLITE_INTEGER)] = None,
        limit: PageLimit = 100,
    ) -> list[dict[str, object]]:
        if (after_timestamp is None) != (after_sequence is None):
            raise ValueError("trade cursor requires both timestamp and sequence")
        cursor = (
            (trade_timestamp(after_timestamp), after_sequence)
            if after_timestamp is not None and after_sequence is not None
            else None
        )
        provenance = store.describe(dataset_id)["provenance"]
        return [
            {
                **trade.as_dict(),
                "dataset_id": dataset_id,
                "data_kind": provenance["data_kind"],
                "event_id": f"{dataset_id}/{trade.sequence}",
            }
            for trade in store.trade_prints(
                dataset_id,
                start=trade_timestamp(start) if start is not None else None,
                end=trade_timestamp(end) if end is not None else None,
                after=cursor,
                limit=limit,
            )
        ]

    @router.get("/runs")
    def runs(limit: PageLimit = 100) -> list[dict[str, object]]:
        with store.connection() as db:
            if _schema(db) < 2:
                return []
            return [
                _run_item(row)
                for row in db.execute(
                    """SELECT id, definition, created_at FROM structure_runs
                   ORDER BY created_at DESC, id LIMIT ?""",
                    (limit,),
                )
            ]

    @router.get("/runs/{run_id}")
    def run(run_id: RecordId) -> dict[str, object]:
        return store.describe_structure_run(run_id)

    @router.get("/runs/{run_id}/events")
    def events(
        run_id: RecordId,
        after: Annotated[int, Query(ge=0)] = 0,
        limit: PageLimit = 200,
        latest: bool = False,
    ) -> list[dict[str, object]]:
        return _events(store, run_id, after, limit, latest=latest)

    @router.get("/runs/{run_id}/chart-evidence")
    def chart(
        run_id: RecordId,
        start: Annotated[str, Query(description="Inclusive UTC chart window start")],
        through: Annotated[str, Query(description="Inclusive UTC chart window end")],
    ) -> ChartEvidence:
        left, right = minute_timestamp(start), minute_timestamp(through)
        if left >= right:
            raise ValueError("chart window must have positive duration")
        run = store.describe_structure_run(run_id)
        return chart_evidence(
            _chart_events(store, run_id, _history_chart_start(store, run, left), right),
            left,
            right,
            tracks_execution="backtest_version" in run,
        )

    @router.get("/backtests/{run_id}")
    def backtest(run_id: RecordId) -> dict[str, object]:
        return store.describe_backtest(run_id)

    @router.get("/backtests/{run_id}/analytics")
    def analytics(
        run_id: RecordId, window: Annotated[int, Query(ge=2, le=500)] = 20
    ) -> dict[str, object]:
        return backtest_analytics(store, run_id, window)

    @router.get("/configuration/backtest")
    def default_backtest_configuration() -> dict[str, object]:
        return backtest_configuration()

    @router.get("/configuration/runtime")
    def runtime_profiles(limit: PageLimit = 100) -> list[dict[str, object]]:
        return list_runtime_profiles(store, limit)

    @router.post("/configuration/providers", status_code=201)
    def configure_provider(request: ProviderProfileRequest) -> dict[str, object]:
        return describe_runtime_profile(store, save_runtime_profile(store, request.profile))

    @router.post("/configuration/deliveries", status_code=201)
    def configure_delivery(request: DeliveryProfileRequest) -> dict[str, object]:
        return describe_runtime_profile(store, save_runtime_profile(store, request.profile))

    @router.post("/backtests", status_code=201)
    def build_backtest(request: BacktestRequest) -> dict[str, object]:
        profile = request.resolved()
        run_id = store.build_strategy_run(
            request.dataset_id,
            request.timeframe,
            profile.config,
            profile.strategy_config,
            evidence=profile.evidence_config,
            context=profile.context_config,
            economic=profile.economic_events(),
            execution=profile.execution_config,
            resolve_with_trade_prints=profile.resolve_with_trade_prints,
        )
        return store.describe_backtest(run_id)

    @router.get("/reports")
    def reports(limit: PageLimit = 100) -> list[dict[str, object]]:
        with store.connection() as db:
            if _schema(db) < 3:
                return []
            return [
                dict(row)
                for row in db.execute(
                    """SELECT id AS report_id, kind, created_at FROM analysis_reports
                   ORDER BY created_at DESC, id LIMIT ?""",
                    (limit,),
                )
            ]

    @router.get("/reports/{report_id}")
    def report(report_id: RecordId) -> dict[str, object]:
        return store.describe_analysis(report_id)

    @router.get("/configuration/reports")
    def default_report_configuration() -> dict[str, object]:
        return report_configuration()

    @router.post("/reports/validation", status_code=201)
    def create_validation(request: ValidationReportRequest) -> dict[str, object]:
        return store.describe_analysis(request.run(store))

    @router.post("/reports/matched-control", status_code=201)
    def create_matched_control(request: MatchedControlRequest) -> dict[str, object]:
        return store.describe_analysis(request.run(store))

    @router.post("/reports/intrabar-comparison", status_code=201)
    def create_intrabar_comparison(request: IntrabarComparisonRequest) -> dict[str, object]:
        return store.describe_analysis(request.run(store))

    @router.post("/reports/reconciliation", status_code=201)
    def create_reconciliation(request: ReconciliationRequest) -> dict[str, object]:
        return store.describe_analysis(request.run(store))

    @router.post("/reports/monte-carlo", status_code=201)
    def create_monte_carlo(request: MonteCarloReportRequest) -> dict[str, object]:
        return store.describe_analysis(request.run(store))

    @router.post("/reports/paper-account", status_code=201)
    def create_account(request: PaperAccountReportRequest) -> dict[str, object]:
        return store.describe_analysis(request.run(store))

    @router.post("/reports/forward", status_code=201)
    def create_forward_report(request: ForwardReportRequest) -> dict[str, object]:
        return store.describe_analysis(request.run(store))

    @router.get("/monitors")
    def monitors(limit: PageLimit = 100) -> list[dict[str, object]]:
        with store.connection() as db:
            if _schema(db) < 4:
                return []
            return [
                _monitor_item(row)
                for row in db.execute(
                    """SELECT id, definition, state, connected, checked_at FROM monitor_runs
                   ORDER BY checked_at DESC, id LIMIT ?""",
                    (limit,),
                )
            ]

    @router.get("/monitors/{monitor_id}")
    def monitor(monitor_id: RecordId) -> dict[str, object]:
        status = describe_monitor(store, monitor_id)
        return operational_monitor_status(status, datetime.now(UTC))

    @router.get("/monitors/{monitor_id}/chart")
    def applied_chart(
        monitor_id: RecordId,
        timeframe: ChartTimeframe = "5m",
        limit: PageLimit = 240,
        through: Annotated[
            str | None, Query(description="Inclusive UTC candle close bound")
        ] = None,
    ) -> MonitorChart:
        return _monitor_chart(store, monitor_id, timeframe, limit, through)

    @router.get("/monitors/{monitor_id}/alerts")
    def alerts(monitor_id: RecordId, limit: PageLimit = 100) -> list[dict[str, object]]:
        describe_monitor(store, monitor_id)
        with store.connection() as db:
            return [
                {**json.loads(row["payload"]), "created_at": row["created_at"]}
                for row in db.execute(
                    """SELECT payload, created_at FROM monitor_alerts WHERE run_id = ?
                       ORDER BY created_at DESC, alert_id LIMIT ?""",
                    (monitor_id, limit),
                )
            ]

    @router.get("/monitors/{monitor_id}/events")
    def monitored_events(
        monitor_id: RecordId,
        after: Annotated[int, Query(ge=0)] = 0,
        limit: PageLimit = 200,
        latest: bool = False,
    ) -> list[dict[str, object]]:
        return _events(store, monitor_id, after, limit, monitor=True, latest=latest)

    @router.get("/monitors/{monitor_id}/market-events")
    def monitored_market_events(
        monitor_id: RecordId,
        after: Annotated[int, Query(ge=0)] = 0,
        limit: PageLimit = 200,
        latest: bool = False,
    ) -> list[dict[str, object]]:
        describe_monitor(store, monitor_id)
        return market_events_page(store, monitor_id, after=after, limit=limit, latest=latest)

    @router.get("/monitors/{monitor_id}/stream")
    async def stream(
        request: Request,
        monitor_id: RecordId,
        after: Annotated[int, Query(ge=0, le=MAX_SQLITE_INTEGER)] = 0,
        last_event_id: Annotated[
            int | None, Header(alias="Last-Event-ID", ge=0, le=MAX_SQLITE_INTEGER)
        ] = None,
        mode: Literal["events", "checkpoint"] = "events",
    ) -> StreamingResponse:
        await asyncio.to_thread(describe_monitor, store, monitor_id)

        async def chunks() -> AsyncIterator[str]:
            sequence = max(after, last_event_id or 0)
            previous = None
            while not await request.is_disconnected():
                if mode == "checkpoint":
                    status = await asyncio.to_thread(describe_monitor, store, monitor_id)
                    status = operational_monitor_status(status, datetime.now(UTC))
                    state = {
                        field: status[field]
                        for field in (
                            "last_applied_open",
                            "state",
                            "pending_minutes",
                            "connected",
                            "fault",
                            "heartbeat_status",
                        )
                    }
                    if state != previous:
                        yield f"event: checkpoint\ndata: {canonical_json(state)}\n\n"
                        previous = state
                    else:
                        yield ": aurum\n\n"
                    await asyncio.sleep(1)
                    continue
                batch = await asyncio.to_thread(
                    _events, store, monitor_id, sequence, 100, monitor=True
                )
                for event in batch:
                    sequence = int(str(event["sequence"]))
                    yield f"id: {sequence}\ndata: {canonical_json(event)}\n\n"
                if not batch:
                    yield ": aurum\n\n"
                    await asyncio.sleep(1)

        return StreamingResponse(
            chunks(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/forward-runs")
    def forward_runs(limit: PageLimit = 100) -> list[dict[str, object]]:
        return list_forward_runs(store, limit)

    @router.post("/forward-runs", status_code=201)
    def start_forward(request: ForwardStartRequest) -> dict[str, object]:
        reference = register_forward(
            store,
            request.monitor_id,
            datetime.now(UTC),
            request.execution_config,
            synthetic=request.synthetic,
        )
        return describe_forward(store, reference)

    @router.get("/forward-runs/{forward_id}")
    def forward_state(forward_id: RecordId) -> dict[str, object]:
        return describe_forward(store, forward_id)

    @router.get("/forward-runs/{forward_id}/events")
    def forward_journal(
        forward_id: RecordId,
        after: Annotated[int, Query(ge=0)] = 0,
        limit: PageLimit = 200,
        latest: bool = False,
    ) -> list[dict[str, object]]:
        return forward_events(store, forward_id, after=after, limit=limit, latest=latest)

    @router.post("/forward-runs/{forward_id}/sync")
    def forward_sync(
        forward_id: RecordId,
        limit: PageLimit = 500,
    ) -> dict[str, object]:
        return ForwardPaper(store, forward_id).sync(limit)

    app.include_router(router)
    return app


def _schema(db: sqlite3.Connection) -> int:
    return int(db.execute("PRAGMA user_version").fetchone()[0])


def _dataset_item(row: sqlite3.Row) -> dict[str, object]:
    provenance = json.loads(row["provenance"])
    return {
        "dataset_id": row["id"],
        "contract": provenance["contract"],
        "data_kind": provenance["data_kind"],
        "provider": provenance["provider"],
        "candle_count": row["candle_count"],
        "first_open": row["first_open"],
        "last_close": row["last_close"],
        "replayable": bool(row["replayable"]),
    }


def _run_item(row: sqlite3.Row) -> dict[str, object]:
    definition = json.loads(row["definition"])
    kind = "structure"
    if "backtest_version" in definition:
        kind = "backtest"
    elif "strategy_version" in definition:
        kind = "strategy"
    elif "evidence_version" in definition:
        kind = "evidence"
    return {
        "run_id": row["id"],
        "dataset_id": definition["dataset_id"],
        "kind": kind,
        "timeframe": definition["timeframe"],
        "created_at": row["created_at"],
        "variant": definition.get("strategy_config", {}).get("variant"),
        "intrabar_ordering": definition.get("intrabar_ordering"),
    }


def _monitor_item(row: sqlite3.Row) -> dict[str, object]:
    definition = json.loads(row["definition"])
    return {
        "monitor_id": row["id"],
        "contract": definition["contract"],
        "state": row["state"],
        "checked_at": row["checked_at"],
        "connected": bool(row["connected"]),
        "source": definition["monitor_config"]["source"],
        "source_status": definition["monitor_config"]["source_status"],
    }


def _events(
    store: DatasetStore,
    reference: str,
    after: int,
    limit: int,
    *,
    monitor: bool = False,
    latest: bool = False,
) -> list[dict[str, object]]:
    with store.connection() as db:
        if monitor:
            if (
                _schema(db) < 4
                or db.execute("SELECT 1 FROM monitor_runs WHERE id = ?", (reference,)).fetchone()
                is None
            ):
                raise ValueError(f"unknown monitor: {reference}")
            query = """SELECT sequence, payload FROM monitor_events
                       WHERE run_id = ? AND sequence > ?"""
        else:
            if (
                _schema(db) < 2
                or db.execute("SELECT 1 FROM structure_runs WHERE id = ?", (reference,)).fetchone()
                is None
            ):
                raise ValueError(f"unknown research run: {reference}")
            query = """SELECT sequence, payload FROM structure_events
                       WHERE run_id = ? AND sequence > ?"""
        query += " ORDER BY sequence DESC LIMIT ?" if latest else " ORDER BY sequence LIMIT ?"
        rows = db.execute(query, (reference, after, limit)).fetchall()
        if latest:
            rows.reverse()
        return [
            {
                **json.loads(row["payload"]),
                "sequence": row["sequence"],
                "event_id": f"{reference}/{row['sequence']}",
            }
            for row in rows
        ]


def _history_chart_start(
    store: DatasetStore, run: dict[str, object], left: datetime
) -> datetime | None:
    if "backtest_version" in run:
        # An open bracket can span many sessions under a carry policy.
        return None
    if "evidence_version" not in run or "strategy_version" in run:
        return left
    lookback = span_lookback(run.get("evidence_config"), run["evidence_version"])
    if lookback is None or run.get("aggregation_version") != 1:
        return None
    dataset = store.describe(str(run["dataset_id"]))
    calendar = SessionCalendar.from_dict(dataset["provenance"]["calendar"])
    if calendar.sessions is None:
        return None
    return (
        completed_bar_start(calendar, Timeframe(str(run["timeframe"])), left - MINUTE, lookback)
        or left
    )


def _chart_events(
    store: DatasetStore, run_id: str, start: datetime | None, through: datetime
) -> Iterator[tuple[int, dict[str, object]]]:
    for sequence, event in store.timed_structure_payloads(run_id, start, through):
        if event["event"] in _CHART_KINDS:
            yield sequence, event


def _candles(
    store: DatasetStore,
    dataset_id: str,
    timeframe: ChartTimeframe,
    limit: int,
    through: str | None,
) -> list[dict[str, object]]:
    dataset = store.describe(dataset_id)
    provenance = dataset["provenance"]
    calendar = SessionCalendar.from_dict(provenance["calendar"])
    end = datetime.fromisoformat(dataset["last_close"])
    if through is not None:
        end = min(end, minute_timestamp(through))
    frame = Timeframe(timeframe) if timeframe != "1m" else None
    minutes = frame.minutes if frame else 1
    start = _chart_start(calendar, end, minutes, limit)
    if start is None or start >= end:
        return []
    batches = store.replay_batches(dataset_id, TradingWindow(start, end))
    bars = (candle for batch in batches for candle in batch)
    if frame is None:
        return [
            {
                **candle.as_dict(),
                "trade_date": session.trade_date.isoformat()
                if (session := calendar.session_at(candle.opened_at))
                else None,
            }
            for candle in deque(bars, maxlen=limit)
        ]
    aggregator = CandleAggregator(calendar, MgcContract(str(provenance["contract"])), (frame,))
    return [bar.as_dict() for bar in deque(aggregator.consume(bars), maxlen=limit)]


def _chart_start(
    calendar: SessionCalendar,
    end: datetime,
    minutes: int | None,
    limit: int,
) -> datetime | None:
    if minutes is None:
        sessions = [session for session in calendar.sessions or () if session.end <= end]
        if not sessions:
            return None
        return max(calendar.coverage.start, sessions[-min(limit + 1, len(sessions))].start)
    needed = (limit + 1) * minutes
    earliest = None
    for window in reversed(calendar.windows):
        close = min(window.end, end)
        if close <= window.start:
            continue
        duration = (close - window.start) // MINUTE
        earliest = window.start
        if duration >= needed:
            return close - timedelta(minutes=needed)
        needed -= duration
    return earliest


def _monitor_chart(
    store: DatasetStore, monitor_id: str, timeframe: ChartTimeframe, limit: int, through: str | None
) -> MonitorChart:
    bound = minute_timestamp(through) if through is not None else None
    status = describe_monitor(store, monitor_id)
    definition = status["definition"]
    assert isinstance(definition, dict)
    config = definition["monitor_config"]
    calendar = SessionCalendar.from_dict(definition["calendar"])
    contract = MgcContract(definition["contract"])
    last_open = status["last_applied_open"]
    last_close = datetime.fromisoformat(last_open) + MINUTE if isinstance(last_open, str) else None
    bars: list[MonitorCandleRecord] = []
    if last_close is not None:
        end = min(last_close, bound) if bound is not None else last_close
        frame = Timeframe(timeframe) if timeframe != "1m" else None
        start = _chart_start(calendar, end, frame.minutes if frame else 1, limit)
        if start is not None and start < end:
            bars = _applied_bars(store, monitor_id, calendar, contract, frame, start, end, limit)

    market_evidence = paper_evidence = ChartEvidence([], [])
    capture_started = None
    context = None
    if isinstance(last_open, str):
        capture_started = market_evidence_started_at(store, monitor_id, last_open)
        recent_context = market_events_page(
            store,
            monitor_id,
            limit=1,
            latest=True,
            through_open=last_open,
            observed_through=bound,
            kind="market_context_observed",
        )
        context = recent_context[0] if recent_context else None
        if bars:
            left = datetime.fromisoformat(bars[0].opened_at)
            right = datetime.fromisoformat(bars[-1].available_at)
            if capture_started is not None:
                market_evidence = chart_evidence(
                    _market_chart_events(
                        store,
                        monitor_id,
                        last_open,
                        left,
                        right,
                        span_lookback(
                            definition.get("evidence_config"), definition.get("evidence_version")
                        ),
                    ),
                    left,
                    right,
                    tracks_execution=False,
                    use_observed_time=True,
                )
            paper_evidence = chart_evidence(
                _monitor_setup_chart_events(store, monitor_id, last_open, left, right),
                left,
                right,
                tracks_execution=False,
                use_observed_time=True,
            )

    now = datetime.now(UTC)
    market = "unavailable"
    if calendar.coverage.start <= now < calendar.coverage.end:
        market = "open" if calendar.permits(now.replace(second=0, microsecond=0)) else "closed"
    return MonitorChart.model_validate(
        {
            **operational_monitor_status(status, now),
            "contract": contract.code,
            "source": config["source"],
            "configured_status": config["source_status"],
            "stale_after_seconds": config["stale_seconds"],
            "analysis_timeframe": config["timeframe"],
            "calendar_source": calendar.source,
            "calendar_version": calendar.version,
            "last_applied_close": last_close.isoformat() if last_close is not None else None,
            "market_status": market,
            "candles": bars,
            "market_evidence_started_at": capture_started,
            "market_evidence": market_evidence,
            "paper_evidence": paper_evidence,
            "context_event": context,
        }
    )


def _market_chart_events(
    store: DatasetStore,
    monitor_id: str,
    last_open: str,
    left: datetime,
    right: datetime,
    lookback: int | None,
) -> Iterator[tuple[int, dict[str, object]]]:
    after, ceiling = (
        (0, None)
        if lookback is None
        else market_chart_sequence_bounds(store, monitor_id, last_open, left, right, lookback)
    )
    while ceiling is None or after < ceiling:
        batch = market_events_page(
            store,
            monitor_id,
            after=after,
            limit=500,
            through_open=last_open,
            observed_through=right,
            kinds=_CHART_KINDS,
            through_sequence=ceiling,
        )
        yield from ((int(str(event["sequence"])), event) for event in batch)
        if len(batch) < 500:
            return
        after = int(str(batch[-1]["sequence"]))


def _monitor_setup_chart_events(
    store: DatasetStore,
    monitor_id: str,
    last_open: str,
    left: datetime,
    right: datetime,
) -> Iterator[tuple[int, dict[str, object]]]:
    applied_close = datetime.fromisoformat(last_open) + MINUTE
    with store.connection() as db:
        indexed = _schema(db) >= 10
    if indexed:
        lower = left.isoformat()
        after = 0
        while True:
            with store.connection() as db:
                rows = db.execute(
                    """SELECT sequence, payload, json_extract(payload, '$.observed_at') AS observed
                       FROM monitor_events
                       WHERE run_id = ?
                         AND json_extract(payload, '$.event') = 'paper_setup_transition'
                         AND json_extract(payload, '$.state') = 'paper_signal'
                         AND json_extract(payload, '$.observed_at') >= ?
                         AND (json_extract(payload, '$.observed_at'), sequence) > (?, ?)
                         AND json_extract(payload, '$.observed_at') <= ?
                       ORDER BY json_extract(payload, '$.observed_at'), sequence LIMIT 500""",
                    (monitor_id, lower, lower, after, right.isoformat()),
                ).fetchall()
            for row in rows:
                event = json.loads(row["payload"])
                if datetime.fromisoformat(event["confirmed_at"]) <= applied_close:
                    yield row["sequence"], event
            if len(rows) < 500:
                return
            after = rows[-1]["sequence"]
            lower = rows[-1]["observed"]
    after = 0
    while True:
        batch = _events(store, monitor_id, after, 500, monitor=True)
        for event in batch:
            if event["event"] == "paper_setup_transition":
                if datetime.fromisoformat(str(event["confirmed_at"])) > applied_close:
                    return
                yield int(str(event["sequence"])), event
        if len(batch) < 500:
            return
        after = int(str(batch[-1]["sequence"]))


def _applied_bars(
    store: DatasetStore,
    monitor_id: str,
    calendar: SessionCalendar,
    contract: MgcContract,
    frame: Timeframe | None,
    start: datetime,
    end: datetime,
    limit: int,
) -> list[MonitorCandleRecord]:
    batches = applied_minute_batches(store, monitor_id, TradingWindow(start, end))
    records = (row for batch in batches for row in batch)
    bars: deque[MonitorCandleRecord] = deque(maxlen=limit)
    aggregator = CandleAggregator(calendar, contract, (frame,)) if frame else None
    source: deque[tuple[datetime, FeedStatus]] = deque()
    for candle, classification in records:
        if aggregator is None:
            session = calendar.session_at(candle.opened_at)
            bars.append(
                MonitorCandleRecord.model_validate(
                    {
                        **candle.as_dict(),
                        "trade_date": session.trade_date.isoformat() if session else None,
                        "source_minutes": {classification: 1},
                    }
                )
            )
            continue
        source.append((candle.opened_at, classification))
        for bar in aggregator.push(candle):
            while source[0][0] < bar.opened_at:
                source.popleft()
            bars.append(
                MonitorCandleRecord.model_validate(
                    {**bar.as_dict(), "source_minutes": Counter(status for _, status in source)}
                )
            )
            source.clear()
    return list(bars)
