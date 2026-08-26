"""
poi.py - Point of Interest (POI) / Order Block (OB) detection module.
Rules doc §4 & §6:
  - Order block = the last opposite-direction candle before the impulsive move
    that broke structure (CHoCH or confirming BOS).
  - Mitigation is judged by candle body/extremes.
"""
from dataclasses import dataclass
from typing import List, Optional
from models import Candle, Direction


@dataclass
class OrderBlock:
    index: int
    direction: Direction  # Direction of the trade setup (BULLISH = demand zone, BEARISH = supply zone)
    high: float
    low: float
    open: float
    close: float

    @property
    def entry_price(self) -> float:
        # Limit entry at the near edge of the OB
        return self.high if self.direction == Direction.BEARISH else self.low

    @property
    def invalidation_price(self) -> float:
        # SL anchor at the far edge of the OB
        return self.high if self.direction == Direction.BULLISH else self.low


def find_order_block(candles: List[Candle], impulse_start_index: int, direction: Direction, lookback: int = 10) -> Optional[OrderBlock]:
    """
    Finds the last opposite-direction candle immediately before the impulse started.
    - For BEARISH setup: find the last bullish candle (close > open) prior to impulse.
    - For BULLISH setup: find the last bearish candle (close < open) prior to impulse.
    """
    start = max(0, impulse_start_index - lookback)
    
    for i in range(impulse_start_index - 1, start - 1, -1):
        c = candles[i]
        if direction == Direction.BEARISH:
            # Last bullish candle before bearish impulse
            if c.close >= c.open:
                return OrderBlock(
                    index=i,
                    direction=Direction.BEARISH,
                    high=c.high,
                    low=c.low,
                    open=c.open,
                    close=c.close
                )
        else:
            # Last bearish candle before bullish impulse
            if c.close <= c.open:
                return OrderBlock(
                    index=i,
                    direction=Direction.BULLISH,
                    high=c.high,
                    low=c.low,
                    open=c.open,
                    close=c.close
                )
    return None