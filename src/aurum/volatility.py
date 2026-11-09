from collections import deque


class TrueRangeWindow:
    """Trailing true ranges of already processed candles."""

    def __init__(self, period: int) -> None:
        if period < 1:
            raise ValueError("true-range period must be positive")
        self.period = period
        self._ranges: deque[int] = deque(maxlen=period)
        self._sum = 0
        self._last_close: int | None = None

    @property
    def ready_sum(self) -> int | None:
        return self._sum if len(self._ranges) == self.period else None

    def push(self, high: int, low: int, close: int) -> None:
        value = high - low
        if self._last_close is not None:
            value = max(value, abs(high - self._last_close), abs(low - self._last_close))
        if len(self._ranges) == self.period:
            self._sum -= self._ranges[0]
        self._ranges.append(value)
        self._sum += value
        self._last_close = close
