"""
Core data models for the Aart structure/liquidity system.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Direction(Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    RANGING = "ranging"


class SwingType(Enum):
    HIGH = "high"
    LOW = "low"


class StructureEvent(Enum):
    """What happened when price interacted with a prior swing level."""
    BREAK_OF_STRUCTURE = "break_of_structure"   # closed beyond, and continued
    LIQUIDITY_SWEEP = "liquidity_sweep"          # wicked beyond, reversed back (a.k.a. manipulation / stop point)
    NONE = "none"                                # level not yet interacted with


@dataclass
class Candle:
    index: int
    timestamp: object  # datetime or raw timestamp, kept generic
    open: float
    high: float
    low: float
    close: float

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open

    @property
    def body_high(self) -> float:
        return max(self.open, self.close)

    @property
    def body_low(self) -> float:
        return min(self.open, self.close)


@dataclass
class SwingPoint:
    """A local high or low identified via fractal detection."""
    index: int          # index into the candle list
    price: float
    type: SwingType
    candle: Candle

    # Structural classification - filled in by the structure engine
    event: StructureEvent = StructureEvent.NONE
    is_weak: bool = False          # True if this swing failed to break structure (a "stop point")
    is_range_boundary: bool = False  # True if this swing became a valid range extreme (ERL)
    broken_by_index: Optional[int] = None  # index of the candle that broke/swept this swing

    def __repr__(self):
        tag = "WEAK" if self.is_weak else self.event.value
        return f"<{self.type.value.upper()} @ {self.price:.5f} idx={self.index} [{tag}]>"


@dataclass
class Range:
    """
    A trading range whose boundaries are swing points that BOTH
    swept liquidity AND broke structure (per the rules doc, §2).
    """
    low: SwingPoint
    high: SwingPoint

    @property
    def size(self) -> float:
        return self.high.price - self.low.price

    @property
    def midpoint(self) -> float:
        return self.low.price + self.size / 2

    def zone_of(self, price: float) -> str:
        """Returns 'premium' or 'discount' relative to this range."""
        return "premium" if price >= self.midpoint else "discount"

    def pct_into_range(self, price: float) -> float:
        """0.0 = at range low, 1.0 = at range high."""
        if self.size == 0:
            return 0.5
        return (price - self.low.price) / self.size