"""
Swing point (fractal) detection.

Open question from the rules doc: exact swing detection method / window size
was never specified in the transcripts. This uses a configurable N-candle
fractal (default N=2, i.e. classic 5-candle fractal: 2 left + pivot + 2 right).
Tune `window` once you're backtesting against real data - tighter windows
(1) find more, noisier swings; wider windows (3-5) find fewer, more
significant ones. Aart's examples generally look like higher-timeframe,
fairly significant swings, so start wide and tighten if you're missing moves.
"""
from typing import List
from models import Candle, SwingPoint, SwingType


def detect_swings(candles: List[Candle], window: int = 2) -> List[SwingPoint]:
    """
    A candle at index i is a swing HIGH if its high is >= the high of every
    candle within `window` positions on both sides.
    A candle at index i is a swing LOW if its low is <= the low of every
    candle within `window` positions on both sides.

    Uses wicks (high/low), not candle bodies, for swing identification itself -
    this is standard for fractal/pivot detection, distinct from the
    "mitigation is judged by body not wick" rule which applies to zone
    interaction, not swing detection.
    """
    swings: List[SwingPoint] = []
    n = len(candles)

    for i in range(window, n - window):
        c = candles[i]
        left = candles[i - window:i]
        right = candles[i + 1:i + 1 + window]
        neighborhood = left + right

        if all(c.high >= o.high for o in neighborhood):
            swings.append(SwingPoint(index=i, price=c.high, type=SwingType.HIGH, candle=c))
        elif all(c.low <= o.low for o in neighborhood):
            swings.append(SwingPoint(index=i, price=c.low, type=SwingType.LOW, candle=c))

    return swings