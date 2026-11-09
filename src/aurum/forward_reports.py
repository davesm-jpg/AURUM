import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any

from aurum.exchange_calendar import SessionCalendar
from aurum.forward import describe_forward, forward_events
from aurum.market_context import DEFAULT_CONTEXT_CONFIG
from aurum.market_data import canonical_json
from aurum.statistics import Observation, bootstrap_expectancy, outcome_summary
from aurum.storage import DatasetStore
from aurum.timestamps import MINUTE

FORWARD_REPORT_VERSION = 2


class ForwardPeriod(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


def period_bounds(period: ForwardPeriod, trade_date: date) -> tuple[date, date]:
    if not 2 <= trade_date.year <= 9998:
        raise ValueError("forward report year must be between 2 and 9998")
    if period is ForwardPeriod.DAILY:
        return trade_date, trade_date + timedelta(days=1)
    if period is ForwardPeriod.WEEKLY:
        monday = trade_date - timedelta(days=trade_date.weekday())
        return monday, monday + timedelta(days=7)
    start = trade_date.replace(day=1)
    following = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start, following


@dataclass(slots=True)
class _Sample:
    starts: date
    ends: date
    observations: list[Observation] = field(default_factory=list)
    events: Counter[str] = field(default_factory=Counter)
    signals_by_session: Counter[str] = field(default_factory=Counter)
    signal_ids: set[str] = field(default_factory=set)
    close_equity: int = 0
    peak: int = 0
    max_drawdown: int = 0

    def record(self, event: dict[str, Any], signal: dict[str, Any] | None) -> None:
        kind = event["event"]
        self.events[kind] += 1
        if kind == "paper_forward_signal":
            self.signals_by_session[event["session"]] += 1
            self.signal_ids.add(event["setup_id"])
        elif kind == "paper_trade_completed":
            if signal is None:
                raise ValueError("forward trade has no earlier recorded signal")
            score = signal["score"]
            observation = Observation(
                event["setup_id"],
                datetime.fromisoformat(signal["recorded_at"]),
                datetime.fromisoformat(event["entry_at"]),
                datetime.fromisoformat(event["recorded_at"]),
                event["net_cents"],
                event["quantity"],
                event["session"],
                event["regime"],
                event["direction"],
                score["total"] if isinstance(score, dict) else None,
            )
            self.observations.append(observation)
            self.close_equity += observation.net_cents
            self.peak = max(self.close_equity, self.peak)
            self.max_drawdown = max(self.max_drawdown, self.peak - self.close_equity)

    def summary(self) -> dict[str, object]:
        return {
            "first_trade_date": self.starts.isoformat(),
            "end_trade_date_exclusive": self.ends.isoformat(),
            "qualified_signals": len(self.signal_ids),
            "entries_filled": self.events["paper_entry_filled"],
            "entries_canceled": self.events["paper_entry_canceled"],
            "unresolved_outcomes": self.events["paper_trade_unresolved"],
            "signals_by_session": dict(sorted(self.signals_by_session.items())),
            "period_max_closed_trade_drawdown_cents": self.max_drawdown,
            "scored_completed_trades": sum(item.score is not None for item in self.observations),
            "outcomes": outcome_summary(self.observations),
        }


def save_forward_report(
    store: DatasetStore,
    forward_id: str,
    period: ForwardPeriod,
    trade_date: date,
    *,
    baseline_validation_id: str | None = None,
    bootstrap_draws: int = 1_000,
    block_length: int = 5,
    seed: int = 42,
) -> str:
    if not 2 <= bootstrap_draws <= 10_000 or not 1 <= block_length <= 1_000:
        raise ValueError("invalid forward bootstrap configuration")
    start, end = period_bounds(period, trade_date)
    prior_start, _ = period_bounds(period, start - timedelta(days=1))
    run, monitor, calendar = _source(store, forward_id)
    run = _freeze_at_period_end(store, run, calendar, end)
    prior, current = _Sample(prior_start, start), _Sample(start, end)
    _record_samples(store, run, calendar, (prior, current))
    current_result, prior_result = current.summary(), prior.summary()
    current_result["coverage"] = _coverage(store, run, calendar, current)
    prior_result["coverage"] = _coverage(store, run, calendar, prior)
    for sample, result in ((current, current_result), (prior, prior_result)):
        uncertainty = bootstrap_expectancy(
            [item.net_cents for item in sample.observations], bootstrap_draws, block_length, seed
        )
        result["uncertainty"] = uncertainty.as_dict() if uncertainty else None
    historical = (
        _historical_baseline(store, baseline_validation_id, run, monitor)
        if baseline_validation_id is not None
        else None
    )
    definition = {
        "forward_report_version": FORWARD_REPORT_VERSION,
        "forward_id": forward_id,
        "monitor_id": run["monitor_id"],
        "period": period,
        "first_trade_date": start.isoformat(),
        "end_trade_date_exclusive": end.isoformat(),
        "cursor": run["cursor"],
        "last_event_sequence": run["event_count"],
        "data_kind": run["data_kind"],
        "baseline_validation_id": baseline_validation_id,
        "bootstrap_draws": bootstrap_draws,
        "block_length": block_length,
        "seed": seed,
    }
    return store.save_analysis(
        "forward_paper_research",
        definition,
        {
            "data_kind": run["data_kind"],
            "contract": run["contract"],
            "source": run["source"],
            "execution_mode": "paper_research",
            "period_attribution": "source_minute_exchange_trade_date",
            "period": current_result,
            "previous_period": prior_result,
            "prior_period_change": _change(current_result["outcomes"], prior_result["outcomes"]),
            "historical_holdout": historical,
            "historical_holdout_change": (
                _change(current_result["outcomes"], historical["outcomes"]) if historical else None
            ),
            "strategy_version": monitor["strategy_version"],
            "strategy_config": monitor["strategy_config"],
            "execution_config": run["execution_config"],
        },
    )


def _source(
    store: DatasetStore, forward_id: str
) -> tuple[dict[str, object], dict[str, Any], SessionCalendar]:
    run = describe_forward(store, forward_id)
    with store.connection() as db:
        row = db.execute(
            "SELECT definition FROM monitor_runs WHERE id = ?", (run["monitor_id"],)
        ).fetchone()
    monitor: dict[str, Any] = json.loads(row["definition"])
    return run, monitor, SessionCalendar.from_dict(monitor["calendar"])


def _freeze_at_period_end(
    store: DatasetStore, run: dict[str, object], calendar: SessionCalendar, end: date
) -> dict[str, object]:
    through = run["cursor"]
    latest_scheduled = max(
        (
            min(session.end, calendar.coverage.end)
            for session in calendar.sessions or ()
            if session.trade_date < end
        ),
        default=None,
    )
    cursor = None
    sequence = 0
    if isinstance(through, str) and latest_scheduled is not None:
        bound = min(through, (latest_scheduled - MINUTE).isoformat())
        with store.connection() as db:
            cursor = db.execute(
                """SELECT max(opened_at) FROM monitor_minutes
                   WHERE run_id = ? AND applied = 1 AND opened_at <= ?
                     AND opened_at > ? AND applied_at >= ?""",
                (run["monitor_id"], bound, run["start_after_open"] or "", run["started_at"]),
            ).fetchone()[0]
            if cursor is not None:
                sequence = db.execute(
                    """SELECT coalesce(max(sequence), 0) FROM forward_events
                       WHERE run_id = ? AND minute_open <= ?""",
                    (run["forward_id"], cursor),
                ).fetchone()[0]
    return {**run, "cursor": cursor, "event_count": sequence}


def publish_completed_periods(
    store: DatasetStore,
    forward_id: str,
    periods: tuple[ForwardPeriod, ...] = tuple(ForwardPeriod),
    *,
    baseline_validation_id: str | None = None,
) -> list[str]:
    """Save each completed reviewed period once, including after a worker restart."""
    run, monitor, calendar = _source(store, forward_id)
    if baseline_validation_id is not None:
        _historical_baseline(store, baseline_validation_id, run, monitor)
    if not isinstance(run["cursor"], str) or not calendar.sessions:
        return []
    closed_through = datetime.fromisoformat(run["cursor"]) + MINUTE
    registered = datetime.fromisoformat(str(run["started_at"]))
    baseline = baseline_validation_id or ""
    with store.connection(write=True) as db:
        saved = {
            (row["period"], row["first_trade_date"])
            for row in db.execute(
                """SELECT period, first_trade_date FROM forward_report_publications
                   WHERE forward_id = ? AND baseline_key = ?""",
                (forward_id, baseline),
            )
        }
    reports = []
    for period in periods:
        candidates: dict[tuple[date, date], datetime] = {}
        for session in calendar.sessions:
            start, end = period_bounds(period, session.trade_date)
            candidates[(start, end)] = session.end
        for (start, end), last_close in candidates.items():
            if (
                (period, start.isoformat()) in saved
                or last_close <= registered
                or last_close > min(closed_through, calendar.coverage.end)
                or (period is not ForwardPeriod.DAILY and calendar.sessions[-1].trade_date < end)
            ):
                continue
            if not _coverage(store, run, calendar, _Sample(start, end))["applied_forward_minutes"]:
                continue
            report_id = save_forward_report(
                store, forward_id, period, start, baseline_validation_id=baseline_validation_id
            )
            with store.connection(write=True) as db:
                created = db.execute(
                    """INSERT INTO forward_report_publications VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT DO NOTHING""",
                    (forward_id, period, start.isoformat(), baseline, report_id),
                )
                if created.rowcount:
                    reports.append(report_id)
                    saved.add((period, start.isoformat()))
    return reports


def _record_samples(
    store: DatasetStore,
    run: dict[str, object],
    calendar: SessionCalendar,
    samples: tuple[_Sample, _Sample],
) -> None:
    remaining = int(str(run["event_count"]))
    after = 0
    signals: dict[str, dict[str, object]] = {}
    while remaining:
        batch = forward_events(
            store, str(run["forward_id"]), after=after, limit=min(remaining, 500)
        )
        if not batch:
            raise ValueError("forward journal changed during a frozen report")
        for item in batch:
            source = datetime.fromisoformat(str(item["source_minute_open"]))
            session = calendar.session_at(source)
            if session is None:
                raise ValueError("forward journal contains an event outside reviewed sessions")
            setup = str(item["setup_id"])
            if item["event"] == "paper_forward_signal":
                signals[setup] = item
            for sample in samples:
                if sample.starts <= session.trade_date < sample.ends:
                    sample.record(item, signals.get(setup))
            if item["event"] in ("paper_trade_completed", "paper_trade_unresolved") or (
                item["event"] == "paper_entry_canceled" and item["reason"] != "duplicate_setup"
            ):
                signals.pop(setup, None)
        after = int(str(batch[-1]["sequence"]))
        remaining -= len(batch)


def _coverage(
    store: DatasetStore, run: dict[str, object], calendar: SessionCalendar, sample: _Sample
) -> dict[str, object]:
    windows = [
        window
        for window in calendar.windows
        if (session := calendar.session_at(window.start)) is not None
        and sample.starts <= session.trade_date < sample.ends
    ]
    expected = sum((window.end - window.start) // MINUTE for window in windows)
    count = trusted = 0
    cursor = run["cursor"]
    if windows and isinstance(cursor, str):
        with store.connection() as db:
            row = db.execute(
                """SELECT count(*) AS observed, coalesce(sum(execution_eligible), 0) AS trusted
                   FROM monitor_minutes
                   WHERE run_id = ? AND applied = 1 AND opened_at >= ? AND opened_at < ?
                     AND opened_at <= ? AND opened_at > ? AND applied_at >= ?""",
                (
                    run["monitor_id"],
                    windows[0].start.isoformat(),
                    windows[-1].end.isoformat(),
                    cursor,
                    run["start_after_open"] or "",
                    run["started_at"],
                ),
            ).fetchone()
        count, trusted = row["observed"], row["trusted"]
    return {
        "calendar_source": calendar.source,
        "calendar_version": calendar.version,
        "reviewed_minutes": expected,
        "applied_forward_minutes": count,
        "execution_eligible_minutes": trusted,
        "all_reviewed_minutes_applied": expected > 0 and count == expected,
        "scheduled_first_open": windows[0].start.isoformat() if windows else None,
        "scheduled_last_close": windows[-1].end.isoformat() if windows else None,
    }


def _historical_baseline(
    store: DatasetStore, report_id: str, run: dict[str, object], monitor: dict[str, Any]
) -> dict[str, Any]:
    report = store.describe_analysis(report_id)
    result = report["result"]
    if report["kind"] != "chronological_validation" or not isinstance(result, dict):
        raise ValueError("forward comparison requires a chronological validation report")
    holdout = result.get("holdout")
    if not isinstance(holdout, dict) or holdout.get("used_for_selection") is not False:
        raise ValueError("forward comparison requires an independent selected holdout")
    registered = datetime.fromisoformat(str(run["started_at"]))
    if datetime.fromisoformat(result["last_close"]) > registered:
        raise ValueError("forward comparison requires history ending before registration")
    if datetime.fromisoformat(str(report["created_at"])) > registered:
        raise ValueError("forward comparison requires validation saved before registration")
    expected_source = (
        "synthetic" if run["data_kind"] == "synthetic_forward_verification" else "historical"
    )
    if result["data_kind"] != expected_source:
        raise ValueError("forward and historical comparison source classes are incompatible")
    history = store.describe_backtest(holdout["run_id"])
    matched = {
        "timeframe": monitor["monitor_config"]["timeframe"],
        "aggregation_version": monitor["aggregation_version"],
        "structure_version": monitor["structure_version"],
        "evidence_version": monitor["evidence_version"],
        "context_version": monitor["context_version"],
        "strategy_version": monitor["strategy_version"],
        "execution_version": run["execution_version"],
        "config": monitor["structure_config"],
        "evidence_config": monitor["evidence_config"],
        "strategy_config": monitor["strategy_config"],
        "context_config": asdict(DEFAULT_CONTEXT_CONFIG),
        "execution_config": run["execution_config"],
        "economic_calendar": None,
    }
    if any(
        canonical_json(history.get(key)) != canonical_json(value) for key, value in matched.items()
    ):
        raise ValueError("forward comparison requires matching rules and execution assumptions")
    return {
        "report_id": report_id,
        "backtest_run_id": holdout["run_id"],
        "data_kind": result["data_kind"],
        "dataset": result["dataset"],
        "contract": history["contract"],
        "holdout_start": result["holdout_start"],
        "last_close": result["last_close"],
        "used_for_selection": False,
        "outcomes": holdout["summary"],
        "uncertainty": holdout["uncertainty"],
    }


def _change(current: object, prior: object) -> dict[str, object]:
    assert isinstance(current, dict) and isinstance(prior, dict)
    result = {}
    for key in ("expectancy_cents", "win_rate"):
        left, right = current.get(key), prior.get(key)
        result[key] = (
            left - right
            if isinstance(left, int | float) and isinstance(right, int | float)
            else None
        )
    return {
        "interpretation": "descriptive_difference",
        "current_completed_trades": current["trades"],
        "comparison_completed_trades": prior["trades"],
        "difference": result,
    }
