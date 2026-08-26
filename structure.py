"""
Structure engine: classifies each swing point as either a confirmed
Break of Structure (BOS) or a Liquidity Sweep / manipulation ("weak"
high/low, a.k.a. stop point), then tracks trend direction, ranges,
and structural shifts.

Rule source (rules doc §0):
  BOS   = price breaks a prior swing level AND continues in that direction.
  Sweep = price breaks a prior swing level AND reverses back immediately.

Operationalized here via close-confirmation:
  - A swing is "touched" the first time a later candle's high/low exceeds it.
  - If that candle's CLOSE is beyond the level, AND the next `confirm_candles`
    candles also close beyond it (price holds/continues), we call it BOS.
  - Otherwise (wicks through but closes back inside, or fails to hold for
    `confirm_candles`), we call it a Liquidity Sweep / weak point.

`confirm_candles` is one of the open questions from the rules doc - there was
no numeric threshold given in the transcripts. Default is 1 (next candle's
close must hold beyond the level). Increase if you're seeing false BOS
signals on choppy pairs during backtesting.
"""
from dataclasses import dataclass, field
from typing import List, Optional
from models import Candle, SwingPoint, SwingType, StructureEvent, Direction, Range


def classify_swings(candles: List[Candle], swings: List[SwingPoint], confirm_candles: int = 1) -> None:
    """Mutates each SwingPoint in place: sets .event, .is_weak, .broken_by_index."""
    n = len(candles)

    for sw in swings:
        for j in range(sw.index + 1, n):
            c = candles[j]
            touched = (c.high > sw.price) if sw.type == SwingType.HIGH else (c.low < sw.price)
            if not touched:
                continue

            # First touch found at candle j. Check close-confirmation.
            closed_beyond = (c.close > sw.price) if sw.type == SwingType.HIGH else (c.close < sw.price)

            if closed_beyond:
                holds = True
                for k in range(j + 1, min(j + 1 + confirm_candles, n)):
                    ck = candles[k]
                    still_beyond = (ck.close > sw.price) if sw.type == SwingType.HIGH else (ck.close < sw.price)
                    if not still_beyond:
                        holds = False
                        break
                if holds:
                    sw.event = StructureEvent.BREAK_OF_STRUCTURE
                    sw.is_weak = False
                else:
                    sw.event = StructureEvent.LIQUIDITY_SWEEP
                    sw.is_weak = True
            else:
                sw.event = StructureEvent.LIQUIDITY_SWEEP
                sw.is_weak = True

            sw.broken_by_index = j
            break  # only care about the first interaction with this level


@dataclass
class ShiftEvent:
    """A confirmed change of trend direction (genuine reversal, not just a stop point)."""
    index: int                 # candle index where the confirming BOS happened
    new_direction: Direction
    preceding_weak_points: int  # how many failed sweeps/stop points preceded it


@dataclass
class StructureState:
    trend: Direction = Direction.RANGING
    current_range: Optional[Range] = None
    ranges: List[Range] = field(default_factory=list)
    shifts: List[ShiftEvent] = field(default_factory=list)
    irl_points: List[SwingPoint] = field(default_factory=list)  # points inside current range, not yet a boundary


def build_structure(swings: List[SwingPoint]) -> StructureState:
    """
    Walk swings chronologically and maintain trend/range state.

    Core logic (rules doc §0, §2, §2.5):
    - A swing that confirms BOS in the direction of the current trend becomes
      the new range boundary (ERL) on that side. Previous boundary + all
      intervening non-boundary swings become IRL (internal range liquidity).
    - A swing that fails to confirm (is_weak) does NOT change the range -
      it's just a stop point / weak high-low, added to the IRL pool.
    - A confirmed BOS in the OPPOSITE direction of the current trend, after
      one or more weak points on that side, is a genuine shift. A BOS in
      the opposite direction with zero preceding weak points on record is
      still logged, since "genuine after multiple stop points" is a
      tendency observed in the transcripts, not an absolute rule (see
      rules doc §2.5 - open to tuning during backtesting).
    """
    state = StructureState()
    ordered = sorted(swings, key=lambda s: s.index)

    weak_streak = 0  # consecutive weak points in the direction opposite current trend

    last_high: Optional[SwingPoint] = None
    last_low: Optional[SwingPoint] = None

    for sw in ordered:
        if sw.type == SwingType.HIGH:
            last_high = sw
        else:
            last_low = sw

        if sw.event == StructureEvent.NONE:
            # Not yet resolved (e.g. near the end of the data) - skip.
            continue

        if sw.is_weak:
            state.irl_points.append(sw)
            # Track weak streak only if this weak point opposes the current trend
            opposes_trend = (
                (state.trend == Direction.BULLISH and sw.type == SwingType.LOW) or
                (state.trend == Direction.BEARISH and sw.type == SwingType.HIGH)
            )
            if opposes_trend:
                weak_streak += 1
            continue

        # sw.event == BREAK_OF_STRUCTURE from here on
        is_bullish_break = sw.type == SwingType.HIGH  # breaking a high = bullish BOS
        new_direction = Direction.BULLISH if is_bullish_break else Direction.BEARISH

        if state.trend == Direction.RANGING:
            state.trend = new_direction
            sw.is_range_boundary = True
            weak_streak = 0
            continue

        if new_direction == state.trend:
            # Continuation BOS - confirms current trend, becomes new boundary on that side.
            sw.is_range_boundary = True
            weak_streak = 0
        else:
            # Opposite-direction BOS = a genuine shift.
            state.shifts.append(ShiftEvent(index=sw.broken_by_index, new_direction=new_direction,
                                            preceding_weak_points=weak_streak))
            state.trend = new_direction
            sw.is_range_boundary = True
            weak_streak = 0

        # Update the current range with the two most recent opposing boundary swings.
        if last_high and last_low and (last_high.is_range_boundary or last_low.is_range_boundary):
            if last_high.price > last_low.price:
                new_range = Range(low=last_low, high=last_high)
                state.current_range = new_range
                state.ranges.append(new_range)

    return state