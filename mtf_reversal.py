"""
mtf_reversal.py - Multi-Timeframe (HTF POI + LTF Entry) Alignment Engine

Features:
  1. HTF POI Zone Matching.
  2. Signal Deduplication (prevents redundant orders on the same candle).
  3. Minimum Risk-to-Reward Ratio Filter (e.g., min 1.5R).
"""
from dataclasses import dataclass
from typing import List, Optional
from models import Candle, SwingPoint, Direction
from structure import StructureState
from poi import find_order_block, OrderBlock
from reversal import find_reversal_entries, TradeSignal


@dataclass
class HTFZone:
    order_block: OrderBlock
    direction: Direction
    created_at_index: int
    is_mitigated: bool = False

    def contains_price(self, price: float) -> bool:
        return self.order_block.low <= price <= self.order_block.high


def extract_htf_zones(
    htf_candles: List[Candle],
    htf_swings: List[SwingPoint],
    htf_state: StructureState
) -> List[HTFZone]:
    zones: List[HTFZone] = []
    
    for shift in htf_state.shifts:
        bos_swings = [
            s for s in htf_swings 
            if s.broken_by_index is not None and s.broken_by_index >= shift.index
        ]
        if not bos_swings:
            continue
            
        confirming_bos = min(bos_swings, key=lambda s: s.broken_by_index)
        ob = find_order_block(htf_candles, impulse_start_index=confirming_bos.index, direction=shift.new_direction)
        
        if ob:
            zones.append(HTFZone(
                order_block=ob,
                direction=shift.new_direction,
                created_at_index=confirming_bos.broken_by_index
            ))
            
    return zones


def find_mtf_reversal_entries(
    ltf_candles: List[Candle],
    ltf_swings: List[SwingPoint],
    ltf_state: StructureState,
    htf_zones: List[HTFZone],
    min_preceding_weak_points: int = 1,
    min_r_multiple: float = 1.5
) -> List[TradeSignal]:
    
    raw_ltf_signals = find_reversal_entries(
        candles=ltf_candles,
        swings=ltf_swings,
        state=ltf_state,
        min_preceding_weak_points=min_preceding_weak_points
    )

    valid_mtf_signals: List[TradeSignal] = []
    seen_entry_keys = set()  # For Deduplication

    for sig in raw_ltf_signals:
        # 1. Deduplication Check (by entry_index and direction)
        dedup_key = (sig.entry_index, sig.direction, round(sig.entry_price, 2))
        if dedup_key in seen_entry_keys:
            continue

        # 2. Minimum R-Multiple Check
        risk = abs(sig.entry_price - sig.stop_loss)
        reward = abs(sig.take_profit - sig.entry_price) if sig.take_profit else 0.0
        
        if risk < 1e-4 or (reward / risk) < min_r_multiple:
            continue

        # 3. HTF POI Zone Alignment Check
        entry_candle = ltf_candles[sig.entry_index]
        is_inside_htf_poi = False
        
        for zone in htf_zones:
            if zone.direction == sig.direction:
                if zone.order_block.low <= entry_candle.close <= zone.order_block.high or \
                   zone.order_block.low <= sig.entry_price <= zone.order_block.high:
                    is_inside_htf_poi = True
                    break
                    
        if is_inside_htf_poi:
            seen_entry_keys.add(dedup_key)
            sig.rationale += f" | [MTF Confirmed | Proj R: {reward/risk:.2f}]"
            valid_mtf_signals.append(sig)

    return valid_mtf_signals