from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from enum import StrEnum

from aurum.exchange_calendar import SessionCalendar
from aurum.timestamps import source_timezone

ACCOUNT_VERSION = 1
_BPS = 10_000


class AccountPhase(StrEnum):
    EVALUATION = "evaluation"
    FUNDED = "funded"


class AccountStatus(StrEnum):
    ACTIVE = "active"
    EVALUATION_PASSED = "evaluation_passed"
    BREACHED = "hard_loss_limit_breached"
    INACTIVE = "inactive"
    PAYOUT_LIMIT_REACHED = "payout_limit_reached"


class BreachMetric(StrEnum):
    EQUITY = "equity"
    BALANCE = "balance"


@dataclass(frozen=True, slots=True)
class ScaleTier:
    minimum_profit_cents: int
    max_micros: int


@dataclass(frozen=True, slots=True)
class AccountRules:
    profile: str = "lucidflex-25k-2026-11-09"
    starting_balance_cents: int = 2_500_000
    loss_allowance_cents: int = 100_000
    locked_floor_cents: int = 2_510_000
    breach_metric: BreachMetric = BreachMetric.EQUITY
    daily_loss_cents: int | None = None
    evaluation_target_cents: int = 125_000
    evaluation_consistency_bps: int = 4_000
    evaluation_max_micros: int = 20
    funded_tiers: tuple[ScaleTier, ...] = (ScaleTier(0, 10), ScaleTier(100_000, 20))
    payout_days: int = 5
    qualifying_day_profit_cents: int = 10_000
    minimum_payout_cents: int = 50_000
    maximum_payout_cents: int = 100_000
    profit_payout_cap_bps: int = 5_000
    trader_split_bps: int = 9_000
    max_payouts: int = 5
    allow_intraday_payouts: bool = False
    inactivity_days: int = 30
    activity_minimum_cents: int = 100

    def __post_init__(self) -> None:
        if (
            not self.profile.strip()
            or not 0 < self.loss_allowance_cents <= self.starting_balance_cents
        ):
            raise ValueError("invalid account profile or loss allowance")
        if self.locked_floor_cents < self.starting_balance_cents - self.loss_allowance_cents:
            raise ValueError("locked floor cannot be below the initial loss floor")
        if self.daily_loss_cents is not None and self.daily_loss_cents < 1:
            raise ValueError("daily loss limit must be positive")
        if self.allow_intraday_payouts and self.daily_loss_cents is None:
            raise ValueError("intraday payout scenarios require a daily loss limit")
        if (
            min(
                self.evaluation_target_cents,
                self.evaluation_max_micros,
                self.payout_days,
                self.qualifying_day_profit_cents,
                self.minimum_payout_cents,
                self.max_payouts,
                self.inactivity_days,
                self.activity_minimum_cents,
            )
            < 1
            or self.maximum_payout_cents < self.minimum_payout_cents
        ):
            raise ValueError("invalid evaluation, payout or inactivity thresholds")
        if any(
            not 0 < value <= _BPS
            for value in (
                self.evaluation_consistency_bps,
                self.profit_payout_cap_bps,
                self.trader_split_bps,
            )
        ):
            raise ValueError("account rule fractions must be positive basis points")
        thresholds = tuple(tier.minimum_profit_cents for tier in self.funded_tiers)
        if (
            not thresholds
            or thresholds[0] != 0
            or tuple(sorted(set(thresholds))) != thresholds
            or any(tier.max_micros < 1 for tier in self.funded_tiers)
        ):
            raise ValueError(
                "funded tiers require distinct, increasing profit thresholds from zero"
            )

    @property
    def trail_threshold_cents(self) -> int:
        return self.locked_floor_cents + self.loss_allowance_cents


LUCIDFLEX_25K = AccountRules()


class LossTrail:
    def __init__(self, rules: AccountRules) -> None:
        self._rules = rules
        self.peak_eod_cents = rules.starting_balance_cents
        self.floor_cents = rules.starting_balance_cents - rules.loss_allowance_cents
        self.locked = False

    def settle(self, balance_cents: int) -> None:
        self.peak_eod_cents = max(self.peak_eod_cents, balance_cents)
        if self.locked:
            return
        if self.peak_eod_cents >= self._rules.trail_threshold_cents:
            self.lock()
        else:
            self.floor_cents = self.peak_eod_cents - self._rules.loss_allowance_cents

    def lock(self) -> None:
        self.floor_cents = self._rules.locked_floor_cents
        self.locked = True


@dataclass(frozen=True, slots=True)
class AccountMark:
    at: datetime
    trade_date: date
    closed_trade_nets_cents: tuple[int, ...] = ()
    unrealized_cents: int = 0
    open_micros: int = 0
    end_of_day: bool = False

    def __post_init__(self) -> None:
        if self.at.tzinfo is not UTC or self.open_micros < 0:
            raise ValueError("account marks require UTC timestamps and nonnegative exposure")
        if (self.end_of_day and self.open_micros) or (
            not self.open_micros and self.unrealized_cents
        ):
            raise ValueError("account marks must be flat at EOD with realized PnL supplied")


@dataclass(frozen=True, slots=True)
class PayoutDecision:
    accepted: bool
    gross_cents: int
    trader_cents: int
    reason: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "accepted": self.accepted,
            "gross_cents": self.gross_cents,
            "trader_cents": self.trader_cents,
            "reason": self.reason,
        }


class AccountSimulator:
    """Independent paper ledger driven by explicit observations and reviewed session bounds."""

    def __init__(
        self,
        calendar: SessionCalendar,
        started_at: datetime,
        phase: AccountPhase = AccountPhase.EVALUATION,
        rules: AccountRules = LUCIDFLEX_25K,
    ) -> None:
        if calendar.sessions is None or started_at.tzinfo is not UTC:
            raise ValueError("account simulation requires dated sessions and a UTC start")
        self._calendar = calendar
        self._sessions = {session.trade_date: session for session in calendar.sessions}
        self.rules = rules
        self._last_at = started_at
        self._new_phase(phase, started_at)

    def _new_phase(self, phase: AccountPhase, started_at: datetime) -> None:
        self.phase = phase
        self.status = AccountStatus.ACTIVE
        self.balance_cents = self.rules.starting_balance_cents
        self.equity_cents = self.balance_cents
        self.trail = LossTrail(self.rules)
        self._day: date | None = None
        self._day_start = self.balance_cents
        self._day_profit = self._best_day = self._cycle_profit = 0
        self._qualifying_days = self._payouts = self._open_micros = 0
        self._daily_locked = self._settled = False
        self._rule_violation = False
        self._previous_activity_day = _civil_date(started_at)
        self._max_micros = (
            self.rules.evaluation_max_micros
            if phase is AccountPhase.EVALUATION
            else self.rules.funded_tiers[0].max_micros
        )
        self._breached_at: datetime | None = None

    def observe(self, mark: AccountMark) -> dict[str, object]:
        if self.status is not AccountStatus.ACTIVE:
            raise ValueError("paper account is not active")
        if mark.at < self._last_at or (self._day is not None and mark.at == self._last_at):
            raise ValueError("account marks must advance chronologically")
        session = self._sessions.get(mark.trade_date)
        if (
            session is None
            or not session.start <= mark.at <= session.end
            or not self._calendar.coverage.start <= mark.at <= self._calendar.coverage.end
            or mark.end_of_day != (mark.at == session.end)
        ):
            raise ValueError("account mark does not match the reviewed session bounds")
        if self._day is not None:
            if mark.trade_date < self._day or (mark.trade_date == self._day and self._settled):
                raise ValueError("account trade date must be open and chronological")
            if mark.trade_date > self._day and not self._settled:
                raise ValueError("settle the previous account session before advancing")
        if (mark.closed_trade_nets_cents or mark.open_micros != self._open_micros) and not any(
            window.start <= mark.at <= window.end for window in session.windows
        ):
            raise ValueError("account fills must be observed during eligible trading windows")
        self._last_at = mark.at
        if self._is_inactive(mark.at):
            self.status = AccountStatus.INACTIVE
            return self.snapshot(mark.at, ("inactivity_threshold",))

        events = []
        if self._day != mark.trade_date:
            self._day = mark.trade_date
            self._day_start = self.balance_cents
            self._day_profit = 0
            self._daily_locked = self._settled = False
            events.append("session_started")
        if mark.open_micros > self._max_micros:
            events.append("contract_limit_exceeded")
            self._rule_violation = True
        if self._daily_locked and mark.open_micros > self._open_micros:
            events.append("exposure_increased_during_lockout")
            self._rule_violation = True
        self._open_micros = mark.open_micros

        delta = sum(mark.closed_trade_nets_cents)
        self.balance_cents += delta
        self.equity_cents = self.balance_cents + mark.unrealized_cents
        self._day_profit += delta
        self._cycle_profit += delta
        if any(
            abs(value) >= self.rules.activity_minimum_cents
            for value in mark.closed_trade_nets_cents
        ):
            self._previous_activity_day = _civil_date(mark.at)

        if self._metric_value() <= self.trail.floor_cents:
            self.status = AccountStatus.BREACHED
            self._breached_at = mark.at
            events.append("hard_loss_limit_breached")
        else:
            daily = self.rules.daily_loss_cents
            if (
                daily is not None
                and not self._daily_locked
                and self._metric_value() <= self._day_start - daily
            ):
                self._daily_locked = True
                events.append("daily_loss_lockout")
            if mark.end_of_day:
                self._settle(events)
        return self.snapshot(mark.at, tuple(events))

    def _settle(self, events: list[str]) -> None:
        self._settled = True
        before = self.trail.floor_cents
        self.trail.settle(self.balance_cents)
        events.append("session_settled")
        if self.trail.floor_cents > before:
            events.append("trailing_floor_raised")
        if self.phase is AccountPhase.FUNDED:
            if self._day_profit >= self.rules.qualifying_day_profit_cents:
                self._qualifying_days += 1
            self._scale()
            return

        self._best_day = max(self._best_day, self._day_profit)
        profit = self.balance_cents - self.rules.starting_balance_cents
        if (
            profit >= self.rules.evaluation_target_cents
            and self._best_day * _BPS <= profit * self.rules.evaluation_consistency_bps
        ):
            self.status = AccountStatus.EVALUATION_PASSED
            events.append("evaluation_passed")

    def activate_funded(self, at: datetime) -> dict[str, object]:
        self._check_action_time(at)
        if self.status is not AccountStatus.EVALUATION_PASSED:
            raise ValueError("paper funding requires a passed evaluation")
        self._last_at = at
        self._new_phase(AccountPhase.FUNDED, at)
        return self.snapshot(at, ("paper_funded_activated",))

    def request_payout(self, at: datetime, gross_cents: int) -> PayoutDecision:
        self._check_action_time(at)
        self._last_at = at
        if self.status is not AccountStatus.ACTIVE or self.phase is not AccountPhase.FUNDED:
            return PayoutDecision(False, gross_cents, 0, "funded_account_inactive")
        if self._is_inactive(at):
            self.status = AccountStatus.INACTIVE
            return PayoutDecision(False, gross_cents, 0, "inactivity_threshold")
        if self._rule_violation:
            return PayoutDecision(False, gross_cents, 0, "observed_rule_violation")
        maximum = self.payout_cap_cents()
        if not self._payout_window(at):
            return PayoutDecision(False, gross_cents, 0, "payout_window_unavailable")
        if not self._settled:
            maximum = min(maximum, self._day_profit)
        if self._open_micros:
            return PayoutDecision(False, gross_cents, 0, "open_exposure")
        if not self.rules.minimum_payout_cents <= gross_cents <= maximum:
            return PayoutDecision(False, gross_cents, 0, "payout_requirements_or_amount")

        trader = gross_cents * self.rules.trader_split_bps // _BPS
        self.trail.lock()
        self.balance_cents -= gross_cents
        self.equity_cents = self.balance_cents
        self._qualifying_days = self._cycle_profit = 0
        self._payouts += 1
        if self._settled:
            self._scale()
        else:
            self._daily_locked = True
            self._day_profit = 0
            self._day_start -= gross_cents
        if self._metric_value() <= self.trail.floor_cents:
            self.status = AccountStatus.BREACHED
            self._breached_at = at
        elif self._payouts >= self.rules.max_payouts:
            self.status = AccountStatus.PAYOUT_LIMIT_REACHED
        return PayoutDecision(True, gross_cents, trader, None)

    def payout_cap_cents(self) -> int:
        if (
            self.phase is not AccountPhase.FUNDED
            or self.status is not AccountStatus.ACTIVE
            or self._rule_violation
            or self._qualifying_days < self.rules.payout_days
            or self._cycle_profit <= 0
        ):
            return 0
        profit = max(0, self.balance_cents - self.rules.starting_balance_cents)
        return min(
            self.rules.maximum_payout_cents, profit * self.rules.profit_payout_cap_bps // _BPS
        )

    def snapshot(self, at: datetime, events: tuple[str, ...] = ()) -> dict[str, object]:
        return {
            "at": at.isoformat(),
            "phase": self.phase,
            "status": self.status,
            "balance_cents": self.balance_cents,
            "equity_cents": self.equity_cents,
            "peak_eod_cents": self.trail.peak_eod_cents,
            "loss_floor_cents": self.trail.floor_cents,
            "floor_locked": self.trail.locked,
            "remaining_to_floor_cents": max(0, self._metric_value() - self.trail.floor_cents),
            "breach_metric": self.rules.breach_metric,
            "breached_at": self._breached_at.isoformat() if self._breached_at else None,
            "max_micros": self._max_micros,
            "open_micros": self._open_micros,
            "trade_date": self._day.isoformat() if self._day else None,
            "session_settled": self._settled,
            "daily_locked": self._daily_locked,
            "day_profit_cents": self._day_profit,
            "best_evaluation_day_cents": self._best_day,
            "qualifying_payout_days": self._qualifying_days,
            "payout_cycle_profit_cents": self._cycle_profit,
            "payout_cap_cents": self.payout_cap_cents(),
            "payout_window_open": self._payout_window(at),
            "observed_rule_violation": self._rule_violation,
            "payout_count": self._payouts,
            "events": events,
            "execution_mode": "paper_research",
        }

    def _scale(self) -> None:
        profit = max(0, self.balance_cents - self.rules.starting_balance_cents)
        self._max_micros = next(
            tier.max_micros
            for tier in reversed(self.rules.funded_tiers)
            if profit >= tier.minimum_profit_cents
        )

    def _metric_value(self) -> int:
        return (
            self.equity_cents
            if self.rules.breach_metric is BreachMetric.EQUITY
            else self.balance_cents
        )

    def _is_inactive(self, at: datetime) -> bool:
        return (_civil_date(at) - self._previous_activity_day).days >= self.rules.inactivity_days

    def _check_action_time(self, at: datetime) -> None:
        if (
            at.tzinfo is not UTC
            or at < self._last_at
            or not self._calendar.coverage.start <= at <= self._calendar.coverage.end
        ):
            raise ValueError("account actions must be chronological and UTC")

    def _payout_window(self, at: datetime) -> bool:
        if self.phase is not AccountPhase.FUNDED or self.status is not AccountStatus.ACTIVE:
            return False
        session = self._calendar.session_at(at.replace(second=0, microsecond=0))
        if self._settled:
            return session is None or session.trade_date == self._day
        return (
            self.rules.allow_intraday_payouts
            and not self._daily_locked
            and _intraday_time(at)
            and session is not None
            and session.trade_date == self._day
        )


def _civil_date(at: datetime) -> date:
    return at.astimezone(source_timezone("America/New_York")).date()


def _intraday_time(at: datetime) -> bool:
    local = at.astimezone(source_timezone("America/New_York")).time()
    return local >= time(18) or local <= time(15, 45)
