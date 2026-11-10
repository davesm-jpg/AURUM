"""Pair otherwise identical paper replays to measure dual-touch ordering sensitivity."""

from collections import Counter
from collections.abc import Iterator

from aurum.execution import TRADE_PRINT_ORDERING, ExitReason
from aurum.storage import DatasetStore

INTRABAR_COMPARISON_VERSION = 1
_RUN_METADATA = {"run_id", "created_at", "event_count", "summary", "intrabar_ordering"}
_ORDERING_EFFECTS = {
    "exit_ticks",
    "reason",
    "ambiguous_stop_target",
    "mfe_ticks",
    "mae_ticks",
    "gross_cents",
    "net_cents",
    "intrabar_ordering",
    "bracket_touch_print",
}


def _completed(store: DatasetStore, run_id: str) -> Iterator[dict[str, object]]:
    for batch in store.structure_payload_batches(run_id):
        for _, item in batch:
            if item["event"] == "paper_trade_completed":
                yield item


def _other_fields(item: dict[str, object], excluded: set[str]) -> dict[str, object]:
    return {key: value for key, value in item.items() if key not in excluded}


def _net(item: dict[str, object]) -> int:
    value = item.get("net_cents")
    if type(value) is not int:
        raise ValueError("intrabar comparison requires integer net trade evidence")
    return value


def save_intrabar_comparison(
    store: DatasetStore,
    stop_first_run_id: str,
    trade_print_run_id: str,
    *,
    example_limit: int = 20,
) -> str:
    if not 0 <= example_limit <= 100:
        raise ValueError("comparison example limit must be between 0 and 100")
    baseline = store.describe_backtest(stop_first_run_id)
    ordered = store.describe_backtest(trade_print_run_id)
    if (
        baseline.get("intrabar_ordering") is not None
        or ordered.get("intrabar_ordering") != TRADE_PRINT_ORDERING
    ):
        raise ValueError("select one stop-first run and one ordered trade-print run")
    if _other_fields(baseline, _RUN_METADATA) != _other_fields(ordered, _RUN_METADATA):
        raise ValueError("backtests must differ only in their intrabar ordering policy")

    base_summary, ordered_summary = baseline["summary"], ordered["summary"]
    assert isinstance(base_summary, dict) and isinstance(ordered_summary, dict)
    if _other_fields(base_summary, {"performance"}) != _other_fields(
        ordered_summary, {"performance"}
    ):
        raise ValueError("backtest journals disagree on shared setup and order totals")
    dataset_id = str(baseline["dataset_id"])
    provenance = store.describe(dataset_id)["provenance"]
    count = before_net = after_net = 0
    resolved: Counter[str] = Counter()
    examples: list[dict[str, object]] = []
    pairs = zip(
        _completed(store, stop_first_run_id),
        _completed(store, trade_print_run_id),
        strict=True,
    )
    for before, after in pairs:
        count += 1
        first, second = _net(before), _net(after)
        before_net += first
        after_net += second
        if before["ambiguous_stop_target"] is not True:
            if before != after:
                raise ValueError("backtest journals differ outside a dual-touch exit")
            continue
        if _other_fields(before, _ORDERING_EFFECTS) != _other_fields(after, _ORDERING_EFFECTS):
            raise ValueError("backtest journals disagree on the paired trade identity")
        touch = after.get("bracket_touch_print")
        reason = after.get("reason")
        if (
            before["reason"] != ExitReason.STOP
            or not isinstance(touch, dict)
            or after.get("intrabar_ordering") != TRADE_PRINT_ORDERING
            or after["ambiguous_stop_target"] is not False
            or reason not in (ExitReason.STOP, ExitReason.TARGET)
        ):
            raise ValueError("dual-touch exit is missing a valid ordered resolution")
        resolved[str(reason)] += 1
        if len(examples) < example_limit:
            examples.append(
                {
                    "setup_id": before["setup_id"],
                    "direction": before["direction"],
                    "session": before["session"],
                    "entry_at": before["entry_at"],
                    "recorded_at": before["recorded_at"],
                    "stop_first_net_cents": first,
                    "trade_print_net_cents": second,
                    "net_difference_cents": second - first,
                    "trade_print_exit_reason": reason,
                    "bracket_touch_print": touch,
                }
            )
    for summary, net in ((base_summary, before_net), (ordered_summary, after_net)):
        performance = summary["performance"]
        if (
            not isinstance(performance, dict)
            or performance["trades"] != count
            or performance["net_profit_cents"] != net
        ):
            raise ValueError("paired trade totals do not reconcile with the saved summary")
    before_stats, after_stats = base_summary["performance"], ordered_summary["performance"]
    if (
        before_stats["ambiguous_exits"] != resolved.total()
        or after_stats["trade_print_resolved_exits"] != resolved.total()
        or after_stats["ambiguous_exits"] != 0
    ):
        raise ValueError("dual-touch totals do not reconcile with the saved summaries")
    return store.save_analysis(
        "intrabar_ordering_comparison",
        {
            "intrabar_comparison_version": INTRABAR_COMPARISON_VERSION,
            "dataset_id": dataset_id,
            "stop_first_run_id": stop_first_run_id,
            "trade_print_run_id": trade_print_run_id,
            "example_limit": example_limit,
        },
        {
            "sample_kind": "unpartitioned_replay",
            "data_kind": provenance["data_kind"],
            "provider": provenance["provider"],
            "contract": provenance["contract"],
            "source_sha256": provenance["source_sha256"],
            "first_open": base_summary["first_open"],
            "last_close": base_summary["last_close"],
            "strategy_version": baseline["strategy_version"],
            "execution_config": baseline["execution_config"],
            "strategy_config": baseline["strategy_config"],
            "paired_trades": count,
            "resolved_dual_touch_exits": resolved.total(),
            "stop_before_target": resolved[ExitReason.STOP],
            "target_before_stop": resolved[ExitReason.TARGET],
            "stop_first_net_cents": before_net,
            "trade_print_net_cents": after_net,
            "net_difference_cents": after_net - before_net,
            "expectancy_difference_cents": (after_net - before_net) / count if count else None,
            "examples": examples,
        },
    )
