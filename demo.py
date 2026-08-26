"""
End-to-end demo on synthetic OHLC data: generates a simple trending-with-
pullbacks price series, runs it through swing detection -> structure
classification -> range building, and prints out what the engine sees.

Swap `generate_synthetic_candles()` for a real OHLC loader (CSV / broker API)
once you're ready to backtest against actual price data.
"""
import random
from models import Candle
from swings import detect_swings
from structure import classify_swings, build_structure


def generate_synthetic_candles(n: int = 300, seed: int = 42, regimes: list = None) -> list:
    """
    Generates a synthetic price series with impulse legs + pullback legs.

    `regimes`: optional list of (length, continuation_probability) tuples,
    each defining a trend block. continuation_probability > 0.5 = bullish
    bias for that block, < 0.5 = bearish bias. If omitted, defaults to a
    single gentle bullish regime for the whole series (original behaviour).

    Use multiple regimes (e.g. a long bullish block followed by a long
    bearish block) to actually exercise the reversal/shift detection logic -
    a single-direction series will never produce a genuine CHoCH.
    """
    random.seed(seed)
    if regimes is None:
        regimes = [(n, 0.7)]

    candles = []
    price = 1.1000
    i = 0
    leg_len = 0
    leg_direction = 1

    for regime_len, continuation_prob in regimes:
        regime_end = i + regime_len
        while i < regime_end:
            if leg_len <= 0:
                leg_direction = 1 if random.random() < continuation_prob else -1
                leg_len = random.randint(5, 15)

            bias = 0.0006 if continuation_prob >= 0.5 else -0.0006
            drift = abs(bias) * leg_direction if continuation_prob >= 0.5 else -abs(bias) * leg_direction
            # simplify: drift follows leg_direction scaled by the regime's dominant bias magnitude
            drift = 0.0006 * leg_direction

            open_ = price
            close = open_ + drift + random.uniform(-0.0004, 0.0004)
            high = max(open_, close) + random.uniform(0, 0.0006)
            low = min(open_, close) - random.uniform(0, 0.0006)

            candles.append(Candle(index=i, timestamp=i, open=open_, high=high, low=low, close=close))
            price = close
            i += 1
            leg_len -= 1

    return candles


def main():
    candles = generate_synthetic_candles()

    swings = detect_swings(candles, window=2)
    classify_swings(candles, swings, confirm_candles=1)
    state = build_structure(swings)

    print(f"Total candles: {len(candles)}")
    print(f"Swings detected: {len(swings)}")
    print(f"  BOS (valid range boundaries): {sum(1 for s in swings if s.is_range_boundary)}")
    print(f"  Weak/swept points: {sum(1 for s in swings if s.is_weak)}")
    print(f"Final trend: {state.trend.value}")
    print(f"Structural shifts detected: {len(state.shifts)}")
    for sh in state.shifts:
        print(f"  -> shift to {sh.new_direction.value} at candle {sh.index} "
              f"(preceded by {sh.preceding_weak_points} weak point(s))")

    if state.current_range:
        r = state.current_range
        print(f"\nCurrent range: low={r.low.price:.5f} (idx {r.low.index}) "
              f"-> high={r.high.price:.5f} (idx {r.high.index})")
        print(f"Range midpoint: {r.midpoint:.5f}")
        last_price = candles[-1].close
        print(f"Last close: {last_price:.5f} -> currently in the "
              f"{r.zone_of(last_price)} of the range "
              f"({r.pct_into_range(last_price) * 100:.1f}% into range)")

    print(f"\nIRL pool size (internal points not used as boundaries): {len(state.irl_points)}")

    print("\nFirst 10 classified swings:")
    for sw in sorted(swings, key=lambda s: s.index)[:10]:
        print(f"  {sw}")


if __name__ == "__main__":
    main()