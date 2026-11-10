"""Evaluate the recorded selection once the final paper period is opened."""

from collections import defaultdict
from dataclasses import fields
from datetime import datetime
from typing import Any

from aurum.exchange_calendar import SessionCalendar
from aurum.execution import (
    EXECUTION_VERSION,
    TRADE_PRINT_ORDERING,
    ExecutionConfig,
    JournalSignal,
    PaperExecution,
)
from aurum.instruments import MgcContract
from aurum.paper_replay import PaperReplayPath, replay_scheduled_paths
from aurum.paper_risk import RiskConfig, TargetMode
from aurum.performance import PERFORMANCE_VERSION, summarize
from aurum.research_reports import VALIDATION_SELECTION_VERSION
from aurum.statistics import Observation, ValidationConfig, holdout_statistics
from aurum.storage import DatasetStore
from aurum.strategy import SetupState
from aurum.timestamps import MINUTE

HOLDOUT_EVALUATION_VERSION = 1


def _config(definition: dict[str, Any]) -> ValidationConfig:
    values = {field.name: definition[field.name] for field in fields(ValidationConfig)}
    for boundary in ("validation_start", "holdout_start"):
        values[boundary] = datetime.fromisoformat(values[boundary])
    values["extra_cost_per_contract_cents"] = tuple(values["extra_cost_per_contract_cents"])
    return ValidationConfig(**values)


def save_holdout_evaluation(store: DatasetStore, selection_id: str) -> str:
    selection = store.describe_analysis(selection_id)
    result, definition = selection["result"], selection["definition"]
    if (
        selection["kind"] != "chronological_selection"
        or not isinstance(result, dict)
        or not isinstance(definition, dict)
        or definition.get("validation_selection_version") != VALIDATION_SELECTION_VERSION
    ):
        raise ValueError("final evaluation requires a saved pre-holdout selection")
    chosen = result.get("selected_run_id")
    if not isinstance(chosen, str) or chosen not in definition["candidate_run_ids"]:
        raise ValueError("pre-holdout selection has no eligible candidate")
    config = _config(definition)
    manifest = store.backtest_manifest(chosen)
    if (
        manifest["execution_version"] != EXECUTION_VERSION
        or manifest["performance_version"] != PERFORMANCE_VERSION
    ):
        raise ValueError("final evaluation requires a current paper execution version")
    dataset_id = str(manifest["dataset_id"])
    dataset = store.describe(dataset_id)
    if dataset_id != definition["dataset"]["dataset_id"] or not dataset["replayable"]:
        raise ValueError("final evaluation requires the replayable selected dataset")
    start = config.holdout_start + MINUTE * config.embargo_minutes
    end = datetime.fromisoformat(dataset["last_close"])
    if start >= end:
        raise ValueError("the embargo consumes the entire holdout")

    provenance = dataset["provenance"]
    calendar = SessionCalendar.from_dict(provenance["calendar"])
    strategy = manifest["strategy_config"]
    execution_raw = manifest["execution_config"]
    if not isinstance(strategy, dict) or not isinstance(execution_raw, dict):
        raise ValueError("invalid persisted paper execution profile")
    risk_raw = strategy["risk"]
    risk = RiskConfig(**{**risk_raw, "target_mode": TargetMode(risk_raw["target_mode"])})
    execution = ExecutionConfig(**execution_raw)
    ordering = manifest.get("intrabar_ordering")
    if ordering not in (None, TRADE_PRINT_ORDERING):
        raise ValueError("unsupported intrabar ordering in saved backtest")
    lookup = store.trade_print_lookup(dataset_id) if ordering is not None else None
    path = PaperReplayPath(
        PaperExecution(
            calendar, MgcContract(str(provenance["contract"])), risk, execution, trade_prints=lookup
        ),
        audit=True,
    )
    scheduled: dict[datetime, dict[int, list[JournalSignal]]] = defaultdict(lambda: {0: []})
    decisions: dict[str, tuple[int, int | None]] = {}
    for sequence, payload in store.timed_structure_payloads(
        chosen, start, end, end_exclusive=True, kinds=("paper_setup_transition",)
    ):
        if payload["state"] != SetupState.PAPER_SIGNAL:
            continue
        signal = JournalSignal.from_setup_payload(payload)
        if signal.setup_id in decisions:
            raise ValueError("final evaluation encountered a duplicate qualified setup")
        score = payload.get("score")
        total = score.get("total") if isinstance(score, dict) else None
        if total is not None and (not isinstance(total, int) or isinstance(total, bool)):
            raise ValueError("invalid persisted setup score")
        decisions[signal.setup_id] = sequence, total
        scheduled[signal.confirmed_at][0].append(signal)

    replay_scheduled_paths(store, dataset_id, (path,), scheduled, end)
    observations = tuple(
        Observation(
            trade.signal.setup_id,
            trade.signal.confirmed_at,
            trade.entry_at,
            trade.recorded_at,
            trade.net_cents,
            trade.quantity,
            trade.signal.context.session,
            trade.signal.context.regime,
            trade.signal.direction,
            decisions[trade.signal.setup_id][1],
        )
        for trade in path.trades
    )
    performance = summarize(path.trades, execution.initial_equity_cents)
    if lookup is not None:
        performance["trade_print_resolved_exits"] = sum(
            trade.bracket_touch is not None for trade in path.trades
        )
    return store.save_analysis(
        "flat_holdout_evaluation",
        {
            "holdout_evaluation_version": HOLDOUT_EVALUATION_VERSION,
            "selection_id": selection_id,
            "selected_run_id": chosen,
            "dataset_id": dataset_id,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "execution_version": EXECUTION_VERSION,
            "performance_version": PERFORMANCE_VERSION,
        },
        {
            "holdout_evaluation_version": HOLDOUT_EVALUATION_VERSION,
            "sample_kind": "out_of_sample_flat_execution",
            "execution_mode": "paper_research",
            "data_kind": provenance["data_kind"],
            "selection_id": selection_id,
            "selected_run_id": chosen,
            "dataset": definition["dataset"],
            "validation_start": config.validation_start.isoformat(),
            "holdout_start": config.holdout_start.isoformat(),
            "first_eligible_decision": start.isoformat(),
            "last_close": end.isoformat(),
            "embargo_minutes": config.embargo_minutes,
            "initial_state": "flat_without_pending_order",
            "strategy_context": "causal_saved_decision_journal",
            "execution_config": execution_raw,
            "strategy_config": strategy,
            "intrabar_ordering": ordering or "stop_first",
            "holdout": holdout_statistics(chosen, observations, config),
            "performance": performance,
            "admitted_signals": path.admitted,
            "cancellations": dict(sorted(path.cancellations.items())),
            "audit": {
                "decisions": [
                    {
                        **item,
                        "source_event_id": f"{chosen}/{decisions[str(item['setup_id'])][0]}",
                    }
                    for item in path.selected
                ],
                "execution_events": path.events,
            },
        },
    )
