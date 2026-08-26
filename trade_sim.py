"""
trade_sim.py - Real Limit Order & Trade Outcome Simulator

Rules doc §4 & §6:
  1. Signal generates a PENDING Limit Order at POI / Order Block boundary.
  2. Walk forward bar-by-bar:
     - Check if price touches entry_price -> ORDER FILLED.
     - If price reaches take_profit BEFORE filling entry (on subsequent bars) -> CANCELED.
  3. Once filled, track SL / TP hit (evaluating subsequent bars to avoid same-bar TP spoofing).
"""
from dataclasses import dataclass
from typing import List, Optional
from models import Candle, Direction


@dataclass
class TradeOutcome:
    signal: object
    result: str  # "win", "loss", "canceled", "unresolved"
    fill_index: Optional[int]
    exit_index: Optional[int]
    exit_price: Optional[float]
    r_multiple: Optional[float]


def simulate_trade(signal, candles: List[Candle], max_pending_bars: int = 100) -> TradeOutcome:
    entry_idx = getattr(signal, 'entry_index', getattr(signal, 'candle_index', 0))
    direction = signal.direction
    entry_p = signal.entry_price
    sl = signal.stop_loss
    tp = signal.take_profit

    fill_idx = None
    is_filled = False

    # Phase 1: Wait for Limit Order Fill
    end_pending = min(entry_idx + max_pending_bars, len(candles))
    for i in range(entry_idx, end_pending):
        c = candles[i]

        # 1. Check for Limit Order Fill FIRST
        if direction == Direction.BULLISH and c.low <= entry_p:
            is_filled = True
            fill_idx = i
            break
        elif direction == Direction.BEARISH and c.high >= entry_p:
            is_filled = True
            fill_idx = i
            break

        # 2. Target Invalidation check (only on subsequent bars AFTER setup bar)
        if i > entry_idx:
            if direction == Direction.BULLISH and c.high >= tp:
                return TradeOutcome(signal=signal, result="canceled", fill_index=None, exit_index=i, exit_price=tp, r_multiple=0.0)
            if direction == Direction.BEARISH and c.low <= tp:
                return TradeOutcome(signal=signal, result="canceled", fill_index=None, exit_index=i, exit_price=tp, r_multiple=0.0)

    if not is_filled:
        return TradeOutcome(signal=signal, result="unresolved", fill_index=None, exit_index=None, exit_price=None, r_multiple=0.0)

    # Phase 2: Manage Active Trade (Post-Fill)
    risk_dist = abs(entry_p - sl)
    if risk_dist < 1e-5:
        return TradeOutcome(signal=signal, result="invalid", fill_index=fill_idx, exit_index=fill_idx, exit_price=entry_p, r_multiple=0.0)

    target_r = abs(tp - entry_p) / risk_dist

    # CRITICAL FIX: Start evaluation strictly on bars AFTER the fill index to prevent lookahead/same-bar TP spoofing
    for i in range(fill_idx + 1, len(candles)):
        c = candles[i]

        if direction == Direction.BULLISH:
            # Conservative check: Stop Loss prioritized over Take Profit if both hit on same candle
            if c.low <= sl:
                return TradeOutcome(signal=signal, result="loss", fill_index=fill_idx, exit_index=i, exit_price=sl, r_multiple=-1.0)
            if c.high >= tp:
                return TradeOutcome(signal=signal, result="win", fill_index=fill_idx, exit_index=i, exit_price=tp, r_multiple=target_r)

        else:  # BEARISH
            if c.high >= sl:
                return TradeOutcome(signal=signal, result="loss", fill_index=fill_idx, exit_index=i, exit_price=sl, r_multiple=-1.0)
            if c.low <= tp:
                return TradeOutcome(signal=signal, result="win", fill_index=fill_idx, exit_index=i, exit_price=tp, r_multiple=target_r)

    return TradeOutcome(signal=signal, result="unresolved", fill_index=fill_idx, exit_index=None, exit_price=None, r_multiple=0.0)


def summarize(outcomes: List[TradeOutcome]) -> str:
    total = len(outcomes)
    if total == 0:
        return "No trades to summarize."

    filled_trades = [o for o in outcomes if o.result in ("win", "loss")]
    canceled = sum(1 for o in outcomes if o.result == "canceled")
    unresolved = sum(1 for o in outcomes if o.result == "unresolved")

    wins = sum(1 for o in filled_trades if o.result == "win")
    losses = sum(1 for o in filled_trades if o.result == "loss")

    total_r = sum(o.r_multiple for o in filled_trades if o.r_multiple is not None)
    win_rate = (wins / len(filled_trades) * 100) if filled_trades else 0.0

    lines = [
        f"Total signals: {total}",
        f"Filled: {len(filled_trades)} (Wins: {wins}, Losses: {losses})",
        f"Canceled (TP before fill): {canceled} | Unresolved: {unresolved}",
        f"Win rate (of filled): {win_rate:.1f}%",
        f"Total R: {total_r:.2f}"
    ]
    return "\n".join(lines)