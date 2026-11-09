from datetime import datetime
from typing import Any

from aurum.chart_evidence import ChartSpan, chart_evidence
from aurum.timestamps import minute_timestamp


def at(minute: int) -> datetime:
    return minute_timestamp(f"2025-03-10T13:{minute:02d}Z")


def event(sequence: int, minute: int, kind: str, **data: Any) -> tuple[int, dict[str, Any]]:
    return sequence, {"event": kind, "confirmed_at": at(minute).isoformat(), **data}


def test_synthetic_chart_keeps_prefix_and_binds_zones_and_levels_to_retirement() -> None:
    zone = {"zone_id": "synthetic-fvg", "lower_ticks": 29000, "upper_ticks": 29002}
    inverse = {**zone, "zone_id": "synthetic-ifvg"}
    day = {"level_id": "synthetic-day", "kind": "previous_day", "price_ticks": 28997}
    equal = {"level_id": "synthetic-equal", "kind": "equal", "side": "low", "price_ticks": 29000}
    evidence = [
        event(1, 1, "fvg_confirmed", zone=zone),
        event(2, 1, "liquidity_level_mapped", level=day),
        event(3, 2, "liquidity_level_mapped", level=equal),
        event(4, 3, "liquidity_level_superseded", level=day),
        event(5, 5, "fvg_filled", zone=zone),
        event(6, 6, "liquidity_sweep_confirmed", level=equal),
        event(7, 7, "fvg_invalidated", zone=zone),
        event(8, 7, "ifvg_confirmed", zone=inverse),
        event(9, 8, "gap_expired", zone=inverse),
    ]

    prefix = chart_evidence(evidence, at(5), at(6), tracks_execution=False)
    assert prefix == chart_evidence(evidence[:6], at(5), at(6), tracks_execution=False)
    assert prefix.spans == [
        ChartSpan("fvg", 29000, 29002, at(1), at(6), 1, None),
        ChartSpan("equal", 29000, 29000, at(2), at(6), 3, 6),
    ]
    assert [item.label for item in prefix.markers] == ["FVG filled", "Sweep"]
    assert prefix.markers[-1].below_bar

    full = chart_evidence(evidence, at(5), at(9), tracks_execution=False)
    assert full.spans == [
        ChartSpan("fvg", 29000, 29002, at(1), at(7), 1, 7),
        ChartSpan("equal", 29000, 29000, at(2), at(6), 3, 6),
        ChartSpan("ifvg", 29000, 29002, at(7), at(8), 8, 9),
    ]


def test_synthetic_paper_reference_and_audited_order_timestamps() -> None:
    signal = event(
        1,
        4,
        "paper_setup_transition",
        state="paper_signal",
        direction="bullish",
        setup_id="synthetic-setup",
        plan={"entry_ticks": 29010, "stop_ticks": 29000, "target_ticks": 29030},
    )
    fill = (
        2,
        {
            "event": "paper_entry_filled",
            "recorded_at": at(6).isoformat(),
            "entry_at": at(5).isoformat(),
            "setup_id": "synthetic-setup",
        },
    )
    exit_event = (
        3,
        {
            "event": "paper_trade_completed",
            "recorded_at": at(8).isoformat(),
            "exit_at": at(7).isoformat(),
            "setup_id": "synthetic-setup",
        },
    )
    report = chart_evidence([signal, fill, exit_event], at(0), at(9), tracks_execution=True)
    assert {item.category for item in report.spans} == {"entry", "stop", "target"}
    assert all(
        item.available_at == at(4) and item.until == at(8) and item.end_sequence == 3
        for item in report.spans
    )
    assert [item.available_at for item in report.markers] == [at(4), at(6)]

    standalone = chart_evidence([signal], at(0), at(9), tracks_execution=False)
    assert all(item.until == at(4) and item.end_sequence is None for item in standalone.spans)
    canceled = (
        2,
        {
            "event": "paper_entry_canceled",
            "recorded_at": at(4).isoformat(),
            "setup_id": "synthetic-setup",
        },
    )
    rejected = chart_evidence([signal, canceled], at(0), at(9), tracks_execution=True)
    assert all(item.until == at(4) and item.end_sequence == 2 for item in rejected.spans)
