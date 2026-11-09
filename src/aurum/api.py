import asyncio
import json
import sqlite3
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import datetime, timedelta
from secrets import compare_digest
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from aurum.candles import CandleAggregator, Timeframe
from aurum.exchange_calendar import SessionCalendar, TradingWindow
from aurum.execution import ExecutionConfig
from aurum.instruments import MGC, MgcContract
from aurum.market_data import canonical_json
from aurum.market_structure import DEFAULT_STRUCTURE_CONFIG, BreakMode, PivotRule
from aurum.monitoring import describe_monitor
from aurum.paper_risk import DEFAULT_RISK_CONFIG, TargetMode
from aurum.storage import DatasetStore
from aurum.strategy import DEFAULT_STRATEGY_CONFIG, Variant
from aurum.timestamps import MINUTE, minute_timestamp

RecordId = Annotated[str, Path(pattern=r"^[a-f0-9]{64}$")]
PageLimit = Annotated[int, Query(ge=1, le=500)]
ChartTimeframe = Literal["1m", "5m", "15m", "1h", "4h", "1d"]


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


class BacktestRequest(BaseModel):
    dataset_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    timeframe: Timeframe = Timeframe.FIVE_MINUTES
    variant: Variant = Variant.SWEEP_MSS_IFVG
    break_mode: BreakMode = BreakMode.CLOSE
    pivot_left: int = Field(default=3, ge=1, le=50)
    pivot_right: int = Field(default=3, ge=1, le=50)
    gross_risk_cents: int = Field(default=5_000, ge=1, le=1_000_000)
    commission_cents: int = Field(default=160, ge=0, le=100_000)
    slippage_ticks: int = Field(default=1, ge=0, le=1_000)
    latency_minutes: int = Field(default=0, ge=0, le=60)
    target_mode: TargetMode = TargetMode.FIXED


def create_app(store: DatasetStore, access_token: str | None = None) -> FastAPI:
    """Create the local research API without starting a server or changing data."""
    app = FastAPI(
        title="AURUM research API",
        version="0.1.0",
        description="Provenance, market evidence, simulated research and feed health.",
    )

    def authorize(authorization: Annotated[str | None, Header()] = None) -> None:
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

    @router.get("/backtests/{run_id}")
    def backtest(run_id: RecordId) -> dict[str, object]:
        return store.describe_backtest(run_id)

    @router.post("/backtests", status_code=201)
    def build_backtest(request: BacktestRequest) -> dict[str, object]:
        structure = replace(
            DEFAULT_STRUCTURE_CONFIG,
            internal=PivotRule(request.pivot_left, request.pivot_right),
            breaks=replace(DEFAULT_STRUCTURE_CONFIG.breaks, mode=request.break_mode),
        )
        risk = replace(
            DEFAULT_RISK_CONFIG,
            gross_allowance_cents=request.gross_risk_cents,
            commission_round_trip_cents=request.commission_cents,
            slippage_round_trip_ticks=request.slippage_ticks,
            target_mode=request.target_mode,
        )
        strategy = replace(DEFAULT_STRATEGY_CONFIG, variant=request.variant, risk=risk)
        execution = ExecutionConfig(
            latency_minutes=request.latency_minutes,
            entry_timeout_minutes=max(5, request.latency_minutes),
        )
        run_id = store.build_strategy_run(
            request.dataset_id, request.timeframe, structure, strategy, execution=execution
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
        return describe_monitor(store, monitor_id)

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
    ) -> list[dict[str, object]]:
        return _events(store, monitor_id, after, limit, monitor=True)

    @router.get("/monitors/{monitor_id}/stream")
    async def stream(
        request: Request,
        monitor_id: RecordId,
        after: Annotated[int, Query(ge=0)] = 0,
    ) -> StreamingResponse:
        await asyncio.to_thread(describe_monitor, store, monitor_id)

        async def chunks() -> AsyncIterator[str]:
            sequence = after
            while not await request.is_disconnected():
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
    bars = store.replay(dataset_id, span=TradingWindow(start, end))
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
