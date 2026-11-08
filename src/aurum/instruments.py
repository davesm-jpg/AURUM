from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from re import fullmatch

MAX_SQLITE_INTEGER = 2**63 - 1


@dataclass(frozen=True, slots=True)
class Instrument:
    symbol: str
    exchange: str
    contract_ounces: int
    tick_size: Decimal
    specification_url: str

    @property
    def tick_value(self) -> Decimal:
        return self.contract_ounces * self.tick_size

    def price_ticks(self, raw: str) -> int:
        try:
            price = Decimal(raw)
        except InvalidOperation as exc:
            raise ValueError("price must be a decimal number") from exc
        if (
            not price.is_finite()
            or price < self.tick_size
            or price > MAX_SQLITE_INTEGER * self.tick_size
        ):
            raise ValueError("price must be positive, finite, and within the supported range")
        numerator, denominator = price.as_integer_ratio()
        tick_numerator, tick_denominator = self.tick_size.as_integer_ratio()
        ticks, remainder = divmod(numerator * tick_denominator, denominator * tick_numerator)
        if remainder:
            raise ValueError(f"price must be a multiple of {self.tick_size}")
        return ticks


MGC = Instrument(
    symbol="MGC",
    exchange="COMEX",
    contract_ounces=10,
    tick_size=Decimal("0.10"),
    specification_url="https://www.cmegroup.com/rulebook/COMEX/1a/120.pdf",
)


@dataclass(frozen=True, slots=True)
class MgcContract:
    code: str

    def __post_init__(self) -> None:
        if fullmatch(r"MGC-[1-9][0-9]{3}-(?:0[1-9]|1[0-2])", self.code) is None:
            raise ValueError("contract must be a dated MGC-YYYY-MM code")
