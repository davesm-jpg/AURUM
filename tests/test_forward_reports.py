from datetime import date

from aurum.forward_reports import ForwardPeriod, period_bounds


def test_synthetic_forward_period_boundaries_follow_exchange_trade_dates() -> None:
    assert period_bounds(ForwardPeriod.DAILY, date(2024, 2, 29)) == (
        date(2024, 2, 29),
        date(2024, 3, 1),
    )
    assert period_bounds(ForwardPeriod.WEEKLY, date(2025, 1, 1)) == (
        date(2024, 12, 30),
        date(2025, 1, 6),
    )
    assert period_bounds(ForwardPeriod.MONTHLY, date(2025, 12, 31)) == (
        date(2025, 12, 1),
        date(2026, 1, 1),
    )
