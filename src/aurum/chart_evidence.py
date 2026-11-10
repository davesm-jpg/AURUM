"""Project persisted evidence into point-in-time chart markers and price ranges."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from aurum.execution import OrderKind
from aurum.imbalances import GapEventKind
from aurum.liquidity import LiquidityEventKind
from aurum.strategy import SetupState

_GAP_START = {
    GapEventKind.FVG_CONFIRMED: "fvg",
    GapEventKind.IFVG_CONFIRMED: "ifvg",
}
_GAP_END = {
    GapEventKind.FVG_INVALIDATED,
    GapEventKind.IFVG_INVALIDATED,
    GapEventKind.EXPIRED,
}
_LEVEL_END = {
    LiquidityEventKind.SUPERSEDED,
    LiquidityEventKind.LEVEL_EXPIRED,
    LiquidityEventKind.SWEPT,
    LiquidityEventKind.SUSTAINED_BREAK,
    LiquidityEventKind.RECLAIM_UNCONFIRMED,
}
_LEVEL_CATEGORY = {
    "equal": "equal",
    "previous_day": "prior_day",
    "previous_week": "prior_week",
    "asia": "session_levels",
    "london": "session_levels",
}
_POINTS = {
    "swing_confirmed": ("swings", "Swing"),
    "bos_confirmed": ("bos", "BOS"),
    "mss_confirmed": ("mss", "MSS"),
    LiquidityEventKind.SWEPT: ("sweeps", "Sweep"),
    GapEventKind.FVG_FILLED: ("fvg", "FVG filled"),
    GapEventKind.IFVG_REACTED: ("ifvg", "IFVG reaction"),
    OrderKind.FILLED: ("entry", "Paper fill"),
}
_SETUP_END = {SetupState.COMPLETED, SetupState.INVALIDATED, SetupState.EXPIRED}
_PAPER_FIELDS = {"entry": "entry_ticks", "stop": "stop_ticks", "target": "target_ticks"}
CHART_EVENT_KINDS = (
    *_GAP_START,
    *_GAP_END,
    *_LEVEL_END,
    *_POINTS,
    LiquidityEventKind.MAPPED,
    "paper_setup_transition",
    "paper_trade_completed",
    OrderKind.CANCELED,
)


def span_lookback(evidence_config: object, evidence_version: object) -> int | None:
    """A span retires by the first eligible bar plus its configured lifetime."""
    if evidence_version != 1 or not isinstance(evidence_config, dict):
        return None
    liquidity, imbalance = evidence_config.get("liquidity"), evidence_config.get("imbalances")
    if not isinstance(liquidity, dict) or not isinstance(imbalance, dict):
        return None
    level_age, gap_age = liquidity.get("max_level_age_bars"), imbalance.get("max_age_bars")
    if (
        not isinstance(level_age, int)
        or not isinstance(gap_age, int)
        or min(level_age, gap_age) < 1
    ):
        return None
    return max(level_age, gap_age) + 2


@dataclass(frozen=True, slots=True)
class ChartMarker:
    category: str
    label: str
    available_at: datetime
    below_bar: bool
    sequence: int


@dataclass(frozen=True, slots=True)
class ChartSpan:
    category: str
    lower_ticks: int
    upper_ticks: int
    available_at: datetime
    until: datetime
    source_sequence: int
    end_sequence: int | None


@dataclass(frozen=True, slots=True)
class ChartEvidence:
    markers: list[ChartMarker]
    spans: list[ChartSpan]


@dataclass(frozen=True, slots=True)
class _OpenSpan:
    category: str
    lower_ticks: int
    upper_ticks: int
    available_at: datetime
    sequence: int

    def close(self, until: datetime, end_sequence: int | None = None) -> ChartSpan:
        return ChartSpan(
            self.category,
            self.lower_ticks,
            self.upper_ticks,
            self.available_at,
            until,
            self.sequence,
            end_sequence,
        )


def chart_evidence(
    events: Iterable[tuple[int, dict[str, Any]]],
    start: datetime,
    through: datetime,
    *,
    tracks_execution: bool,
    use_observed_time: bool = False,
) -> ChartEvidence:
    """Fold the prefix, retaining only points and ranges intersecting the requested window."""
    markers: list[ChartMarker] = []
    spans: list[ChartSpan] = []
    active: dict[tuple[str, str], tuple[_OpenSpan, ...]] = {}
    for sequence, event in events:
        at = datetime.fromisoformat(event.get("recorded_at") or event["confirmed_at"])
        if use_observed_time:
            at = max(at, datetime.fromisoformat(event["observed_at"]))
        if at > through:
            continue
        kind = event["event"]
        category = _POINTS.get(kind)
        if category and at >= start:
            name, label = category
            side = event.get("side") or (event.get("level") or {}).get("side")
            direction = event.get("direction") or (event.get("zone") or {}).get("direction")
            below = side == "low" or (side is None and direction == "bullish")
            markers.append(ChartMarker(name, label, at, below, sequence))

        closed: tuple[_OpenSpan, ...] = ()
        if kind in _GAP_START or kind in _GAP_END:
            zone = event["zone"]
            key = ("gap", zone["zone_id"])
            if kind in _GAP_START:
                active[key] = (
                    _OpenSpan(
                        _GAP_START[kind], zone["lower_ticks"], zone["upper_ticks"], at, sequence
                    ),
                )
            else:
                closed = active.pop(key, ())
        elif kind == LiquidityEventKind.MAPPED or kind in _LEVEL_END:
            level = event["level"]
            key = ("level", level["level_id"])
            if kind == LiquidityEventKind.MAPPED:
                name = _LEVEL_CATEGORY.get(level["kind"], "other_levels")
                ticks = level["price_ticks"]
                active[key] = (_OpenSpan(name, ticks, ticks, at, sequence),)
            else:
                closed = active.pop(key, ())
        elif "setup_id" in event:
            key = ("setup", event["setup_id"])
            if kind == "paper_setup_transition" and event["state"] == SetupState.PAPER_SIGNAL:
                paper = tuple(
                    _OpenSpan(name, ticks, ticks, at, sequence)
                    for name, field in _PAPER_FIELDS.items()
                    if (ticks := event["plan"].get(field)) is not None
                )
                if tracks_execution:
                    active[key] = paper
                elif at >= start:
                    spans.extend(item.close(at) for item in paper)
                if at >= start:
                    markers.append(
                        ChartMarker(
                            "entry", "Paper signal", at, event["direction"] == "bullish", sequence
                        )
                    )
            elif kind in (OrderKind.CANCELED, "paper_trade_completed") or (
                kind == "paper_setup_transition" and event["state"] in _SETUP_END
            ):
                closed = active.pop(key, ())
        if at >= start:
            spans.extend(item.close(at, sequence) for item in closed)

    spans.extend(item.close(through) for group in active.values() for item in group)
    markers.sort(key=lambda item: (item.available_at, item.sequence))
    spans.sort(key=lambda item: (item.available_at, item.source_sequence))
    return ChartEvidence(markers, spans)
