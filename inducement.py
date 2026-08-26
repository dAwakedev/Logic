"""
Inducement detection.

Per the rules doc (§5, §6): an inducement is a minor liquidity sweep that
traps traders shortly before price makes its real move into/out of a POI.
For a bullish entry (buying), the inducement is a small LOW that gets swept
right before the rally. For a bearish entry (selling), it's a small HIGH
swept right before the drop.

Operationalized here as: the most recent WEAK swing point (a confirmed
liquidity sweep, not a BOS) of the correct type, found within `lookback`
candles before the zone/entry index. "Correct type" = a swept LOW for
bullish setups, a swept HIGH for bearish setups - the swing that traps
counter-trend traders right before the real move.

No numeric distance/pip threshold was given in the transcripts for how
"minor" or how close to the zone this needs to be - `lookback` is the
tunable stand-in for that. Start narrow (5-15 candles on your entry
timeframe) since inducement is described as happening right before entry,
not as a distant historical sweep.
"""
from typing import List, Optional
from models import SwingPoint, SwingType, Direction


def find_inducement(
    swings: List[SwingPoint],
    zone_index: int,
    direction: Direction,
    lookback: int = 15,
) -> Optional[SwingPoint]:
    """
    direction = Direction.BULLISH -> looking for a swept LOW (traps sellers/late shorts)
    direction = Direction.BEARISH -> looking for a swept HIGH (traps buyers/late longs)

    Returns the closest qualifying weak swing before `zone_index`, or None
    if no inducement has printed yet (per the rules: no inducement = don't
    enter yet).
    """
    wanted_type = SwingType.LOW if direction == Direction.BULLISH else SwingType.HIGH

    candidates = [
        s for s in swings
        if s.is_weak
        and s.type == wanted_type
        and zone_index - lookback <= s.index < zone_index
    ]
    if not candidates:
        return None

    # Closest one to the zone (most recent) is the relevant trap.
    return max(candidates, key=lambda s: s.index)