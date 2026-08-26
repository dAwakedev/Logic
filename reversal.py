"""
reversal.py - Complete Production Engine

Rules Architecture (§4 & Step 6):
  1. HTF Zone Identification (H4/H1 POI active & unmitigated)
  2. Lower Timeframe Structure Shift (IRL taken -> CHoCH)
  3. Retest/Manipulation of CHoCH Origin
  4. Confirming BOS + Inducement Generation
  5. 50% OB Equilibrium Entry Refinement
  6. Dynamic SL Protection Buffer + Capped Risk-to-Reward TP
"""

from dataclasses import dataclass, field
from typing import List, Optional
from enum import Enum
from models import Candle, SwingPoint, SwingType, Direction, StructureEvent
from structure import StructureState, ShiftEvent
from inducement import find_inducement
from poi import find_order_block, OrderBlock


# =====================================================================
# DATA MODELS & HTF STATE TRACKING
# =====================================================================

class ZoneStatus(Enum):
    ACTIVE = "ACTIVE"
    PARTIALLY_MITIGATED = "PARTIALLY_MITIGATED"
    MITIGATED = "MITIGATED"
    EXPIRED = "EXPIRED"


@dataclass
class HTFZone:
    tf: str
    direction: Direction
    top: float
    bottom: float
    created_index: int
    order_block: Optional[OrderBlock] = None
    status: ZoneStatus = ZoneStatus.ACTIVE
    mitigation_index: Optional[int] = None

    @property
    def midpoint(self) -> float:
        return self.bottom + ((self.top - self.bottom) * 0.5)

    def check_mitigation(self, candle: Candle, current_index: int) -> bool:
        """Updates zone status when HTF price sweeps into the POI."""
        if self.status == ZoneStatus.MITIGATED:
            return True

        if self.direction == Direction.BULLISH:
            if candle.low <= self.top:
                if candle.low <= self.bottom:
                    self.status = ZoneStatus.MITIGATED
                    self.mitigation_index = current_index
                else:
                    self.status = ZoneStatus.PARTIALLY_MITIGATED
                return True
        else:  # Direction.BEARISH
            if candle.high >= self.bottom:
                if candle.high >= self.top:
                    self.status = ZoneStatus.MITIGATED
                    self.mitigation_index = current_index
                else:
                    self.status = ZoneStatus.PARTIALLY_MITIGATED
                return True
        return False


@dataclass
class TradeSignal:
    direction: Direction
    entry_index: int
    entry_price: float
    stop_loss: float
    take_profit: Optional[float]
    inducement: SwingPoint
    origin_shift: ShiftEvent
    order_block: Optional[OrderBlock]
    htf_zone: Optional[HTFZone]
    rationale: str
    expiration_index: int

    @property
    def risk(self) -> float:
        return abs(self.entry_price - self.stop_loss)

    @property
    def reward(self) -> float:
        if self.take_profit is None:
            return 0.0
        return abs(self.take_profit - self.entry_price)

    @property
    def projected_r(self) -> float:
        return self.reward / self.risk if self.risk > 0 else 0.0


# =====================================================================
# HELPER STRUCTURE FUNCTIONS
# =====================================================================

def _find_retest(
    candles: List[Candle],
    origin_price: float,
    direction: Direction,
    after_index: int,
    lookahead: int = 40,
    tolerance_pct: float = 0.001,
) -> Optional[int]:
    """Locates the candle that retests/manipulates the CHoCH origin level."""
    tolerance = origin_price * tolerance_pct
    end = min(after_index + lookahead, len(candles))

    for j in range(after_index, end):
        c = candles[j]
        if abs(c.high - origin_price) <= tolerance or abs(c.low - origin_price) <= tolerance:
            return j
        if direction == Direction.BULLISH and c.low <= origin_price <= c.high:
            return j
        if direction == Direction.BEARISH and c.low <= origin_price <= c.high:
            return j
    return None


def _find_confirming_bos(
    swings: List[SwingPoint],
    direction: Direction,
    after_index: int,
    lookahead: int = 60,
) -> Optional[SwingPoint]:
    """Finds the break of structure confirming structural realignments."""
    wanted_type = SwingType.HIGH if direction == Direction.BULLISH else SwingType.LOW
    candidates = [
        s for s in swings
        if s.type == wanted_type
        and s.event == StructureEvent.BREAK_OF_STRUCTURE
        and s.broken_by_index is not None
        and after_index < s.broken_by_index <= after_index + lookahead
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda s: s.broken_by_index)


def _is_valid_premium_discount(
    entry_price: float,
    direction: Direction,
    swings: List[SwingPoint],
    entry_index: int,
) -> bool:
    """Enforces Rule §9: Buying in Discount, Selling in Premium."""
    past_highs = [s.price for s in swings if s.type == SwingType.HIGH and s.index <= entry_index]
    past_lows = [s.price for s in swings if s.type == SwingType.LOW and s.index <= entry_index]

    if not past_highs or not past_lows:
        return True

    range_high = max(past_highs)
    range_low = min(past_lows)

    if range_high == range_low:
        return True

    equilibrium = range_low + (range_high - range_low) * 0.5

    if direction == Direction.BULLISH and entry_price > equilibrium:
        return False
    if direction == Direction.BEARISH and entry_price < equilibrium:
        return False

    return True


def _verify_htf_mitigation(
    candle: Candle,
    htf_zones: List[HTFZone],
    direction: Direction,
) -> Optional[HTFZone]:
    """Validates if entry candle is executing within an active HTF Zone."""
    if not htf_zones:
        return None

    for zone in htf_zones:
        if zone.direction != direction or zone.status == ZoneStatus.MITIGATED:
            continue
        # Check active overlap
        if candle.low <= zone.top and candle.high >= zone.bottom:
            return zone
    return None


# =====================================================================
# MAIN ENTRY GENERATION ENGINE
# =====================================================================

def find_reversal_entries(
    candles: List[Candle],
    swings: List[SwingPoint],
    state: StructureState,
    htf_zones: Optional[List[HTFZone]] = None,
    retest_lookahead: int = 40,
    confirm_lookahead: int = 60,
    inducement_lookback: int = 15,
    min_preceding_weak_points: int = 1,
    min_sl_dist: float = 1.50,         # Suggestion 1: Noise/Spread Floor
    max_target_r: float = 3.0,         # Suggestion 3: R-Ratio Ceiling
    order_validity_bars: int = 48,     # Expiration limit for unfilled orders
    require_htf_alignment: bool = True,
) -> List[TradeSignal]:
    """Executes structural reversal logic with HTF filtering and safety rules."""
    signals: List[TradeSignal] = []
    htf_zones = htf_zones or []

    for shift in state.shifts:
        if shift.preceding_weak_points < min_preceding_weak_points:
            continue

        # 1. Locate CHoCH Origin
        origin_candidates = [
            s for s in swings
            if s.broken_by_index == shift.index and s.event == StructureEvent.BREAK_OF_STRUCTURE
        ]
        if not origin_candidates:
            continue
        origin = origin_candidates[0]

        # 2. Retest Check
        retest_index = _find_retest(
            candles, origin.price, shift.new_direction, shift.index + 1, retest_lookahead
        )
        if retest_index is None:
            continue

        # 3. Confirming BOS Check
        confirming_bos = _find_confirming_bos(
            swings, shift.new_direction, retest_index, confirm_lookahead
        )
        if confirming_bos is None:
            continue

        # 4. Inducement Check
        inducement = find_inducement(
            swings, confirming_bos.broken_by_index, shift.new_direction, inducement_lookback
        )
        if inducement is None:
            continue

        entry_index = confirming_bos.broken_by_index
        entry_candle = candles[entry_index]

        # 5. Step 6: HTF Zone Mitigation Verification
        matched_htf_zone = _verify_htf_mitigation(entry_candle, htf_zones, shift.new_direction)
        if require_htf_alignment and htf_zones and matched_htf_zone is None:
            continue  # Block entry if not inside HTF POI

        # 6. Order Block Detection & Suggestion 2 (50% OB Equilibrium Entry)
        ob = find_order_block(candles, impulse_start_index=confirming_bos.index, direction=shift.new_direction)
        if ob:
            entry_price = ob.low + ((ob.high - ob.low) * 0.5)
        else:
            entry_price = entry_candle.close

        # 7. Structural Stop Loss Calculation + Suggestion 1 (Noise Cushion)
        intermediate_candles = candles[shift.index:entry_index + 1]

        if shift.new_direction == Direction.BULLISH:
            structural_extreme = min(c.low for c in intermediate_candles) if intermediate_candles else origin.price
            ob_sl = ob.low if ob else origin.price
            stop_loss = min(ob_sl, structural_extreme, origin.price)

            # Apply Minimum Stop Distance Buffer
            if (entry_price - stop_loss) < min_sl_dist:
                stop_loss = entry_price - min_sl_dist

        else:  # Direction.BEARISH
            structural_extreme = max(c.high for c in intermediate_candles) if intermediate_candles else origin.price
            ob_sl = ob.high if ob else origin.price
            stop_loss = max(ob_sl, structural_extreme, origin.price)

            # Apply Minimum Stop Distance Buffer
            if (stop_loss - entry_price) < min_sl_dist:
                stop_loss = entry_price + min_sl_dist

        # 8. Rule §9 Premium/Discount Check
        if not _is_valid_premium_discount(entry_price, shift.new_direction, swings, entry_index):
            continue

        # 9. Risk & Take Profit Target Capping (Suggestion 3)
        risk = abs(entry_price - stop_loss)
        if risk < 1e-4:
            continue

        if shift.new_direction == Direction.BULLISH:
            bos_highs = [s.price for s in swings if s.type == SwingType.HIGH and shift.index <= s.index <= entry_index]
            raw_tp = max(bos_highs) if bos_highs else confirming_bos.price
            max_tp = entry_price + (risk * max_target_r)
            take_profit = min(raw_tp, max_tp)
        else:  # Direction.BEARISH
            bos_lows = [s.price for s in swings if s.type == SwingType.LOW and shift.index <= s.index <= entry_index]
            raw_tp = min(bos_lows) if bos_lows else confirming_bos.price
            max_tp = entry_price - (risk * max_target_r)
            take_profit = max(raw_tp, max_tp)

        # Build Signal
        htf_str = f" | [HTF Matched: {matched_htf_zone.tf}]" if matched_htf_zone else ""
        signals.append(TradeSignal(
            direction=shift.new_direction,
            entry_index=entry_index,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            inducement=inducement,
            origin_shift=shift,
            order_block=ob,
            htf_zone=matched_htf_zone,
            expiration_index=entry_index + order_validity_bars,
            rationale=(
                f"Shift to {shift.new_direction.value} at idx {shift.index} "
                f"({shift.preceding_weak_points} preceding weak pts) -> retest at {retest_index} "
                f"-> confirming BOS at {confirming_bos.broken_by_index} -> "
                f"inducement at idx {inducement.index}"
                f"{' -> 50% OB EQ limit entry @ ' + str(round(entry_price, 2)) if ob else ''}"
                f"{htf_str}"
            ),
        ))

    return signals