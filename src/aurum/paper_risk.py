from dataclasses import asdict, dataclass
from enum import StrEnum

from aurum.instruments import MGC
from aurum.market_structure import Direction

MGC_TICK_CENTS = int(MGC.tick_value * 100)


class TargetMode(StrEnum):
    FIXED = "fixed"
    STRUCTURAL = "structural"


class RiskRejection(StrEnum):
    INVALID_STOP = "invalid_structural_stop"
    INVALID_TARGET = "invalid_target_price"
    NO_STRUCTURAL_TARGET = "no_structural_target"
    SIZE_INCOMPATIBLE = "size_incompatible"


@dataclass(frozen=True, slots=True)
class RiskConfig:
    gross_allowance_cents: int = 5_000
    net_allowance_cents: int | None = None
    reward_numerator: int = 2
    reward_denominator: int = 1
    commission_round_trip_cents: int = 160
    slippage_round_trip_ticks: int = 1
    max_contracts: int | None = None
    target_mode: TargetMode = TargetMode.FIXED

    def __post_init__(self) -> None:
        if min(self.gross_allowance_cents, self.reward_numerator, self.reward_denominator) < 1:
            raise ValueError("risk allowance and reward multiple must be positive")
        if self.net_allowance_cents is not None and self.net_allowance_cents < 1:
            raise ValueError("net risk allowance must be positive")
        if min(self.commission_round_trip_cents, self.slippage_round_trip_ticks) < 0:
            raise ValueError("paper transaction costs cannot be negative")
        if self.max_contracts is not None and self.max_contracts < 1:
            raise ValueError("paper contract limit must be positive")


DEFAULT_RISK_CONFIG = RiskConfig()


@dataclass(frozen=True, slots=True)
class RiskPlan:
    direction: Direction
    entry_ticks: int
    stop_ticks: int
    target_ticks: int | None
    stop_distance_ticks: int
    quantity: int
    gross_risk_cents: int
    gross_reward_cents: int
    commission_cents: int
    slippage_cents: int
    intervening_level_ticks: int | None
    rejection: RiskRejection | None

    @property
    def net_loss_cents(self) -> int:
        return self.gross_risk_cents + self.commission_cents + self.slippage_cents

    @property
    def net_reward_cents(self) -> int:
        return self.gross_reward_cents - self.commission_cents - self.slippage_cents

    def as_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "net_loss_cents": self.net_loss_cents,
            "net_reward_cents": self.net_reward_cents,
        }


def size_paper_setup(
    direction: Direction,
    entry: int,
    stop: int,
    structural_prices: tuple[int, ...] = (),
    config: RiskConfig = DEFAULT_RISK_CONFIG,
) -> RiskPlan:
    sign = 1 if direction is Direction.BULLISH else -1
    risk_ticks = (entry - stop) * sign
    if min(entry, stop, risk_ticks) < 1:
        return _reject(direction, entry, stop, risk_ticks, RiskRejection.INVALID_STOP)

    distances = [
        (level - entry) * sign for level in structural_prices if (level - entry) * sign > 0
    ]
    first_level = min(distances, default=None)
    reward_ticks = (
        risk_ticks * config.reward_numerator + config.reward_denominator - 1
    ) // config.reward_denominator
    if config.target_mode is TargetMode.STRUCTURAL:
        if first_level is None:
            return _reject(direction, entry, stop, risk_ticks, RiskRejection.NO_STRUCTURAL_TARGET)
        reward_ticks = first_level

    target = entry + sign * reward_ticks
    if target < 1:
        return _reject(direction, entry, stop, risk_ticks, RiskRejection.INVALID_TARGET)
    barrier = (
        entry + sign * first_level
        if first_level is not None and first_level < reward_ticks
        else None
    )
    per_contract_risk = risk_ticks * MGC_TICK_CENTS
    slippage = config.slippage_round_trip_ticks * MGC_TICK_CENTS
    per_contract_cost = config.commission_round_trip_cents + slippage
    quantity = config.gross_allowance_cents // per_contract_risk
    if config.net_allowance_cents is not None:
        quantity = min(
            quantity, config.net_allowance_cents // (per_contract_risk + per_contract_cost)
        )
    if config.max_contracts is not None:
        quantity = min(quantity, config.max_contracts)
    return RiskPlan(
        direction,
        entry,
        stop,
        target,
        risk_ticks,
        quantity,
        per_contract_risk * quantity,
        reward_ticks * MGC_TICK_CENTS * quantity,
        config.commission_round_trip_cents * quantity,
        slippage * quantity,
        barrier,
        None if quantity else RiskRejection.SIZE_INCOMPATIBLE,
    )


def _reject(
    direction: Direction, entry: int, stop: int, risk_ticks: int, reason: RiskRejection
) -> RiskPlan:
    return RiskPlan(direction, entry, stop, None, risk_ticks, 0, 0, 0, 0, 0, None, reason)
