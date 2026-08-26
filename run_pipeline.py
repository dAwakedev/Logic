"""
run_pipeline.py - Production Institutional Execution Pipeline
Runs end-to-end multi-timeframe SMC strategy on any compatible asset CSV.
"""
import sys
from data_loader import load_candles_from_csv
from resample import resample_candles
from swings import detect_swings
from structure import classify_swings, build_structure
from reversal import find_reversal_entries
from trade_sim import simulate_trade, summarize
from backtest_demo import extract_htf_zones_from_state, run_demo_account_simulation

def execute_strategy_pipeline(csv_path: str, htf_tf: int = 240, ltf_tf: int = 15):
    print("=" * 70)
    print(f"       EXECUTING SMC STRATEGY PIPELINE | SOURCE: {csv_path}")
    print("=" * 70)

    # 1. Ingest Data
    raw_candles = load_candles_from_csv(csv_path)
    print(f"[1/4] Loaded {len(raw_candles):,} base candles.")

    # 2. Resample Timeframes
    htf_candles = resample_candles(raw_candles, minutes=htf_tf)
    ltf_candles = resample_candles(raw_candles, minutes=ltf_tf)
    print(f"[2/4] Resampled to HTF ({htf_tf}m: {len(htf_candles):,} bars) & LTF ({ltf_tf}m: {len(ltf_candles):,} bars).")

    # 3. Process Higher Timeframe Context & Lower Timeframe Entries
    htf_swings = detect_swings(htf_candles, window=2)
    classify_swings(htf_candles, htf_swings, confirm_candles=1)
    htf_state = build_structure(htf_swings)
    htf_zones = extract_htf_zones_from_state(htf_candles, htf_swings, htf_state)

    ltf_swings = detect_swings(ltf_candles, window=2)
    classify_swings(ltf_candles, ltf_swings, confirm_candles=1)
    ltf_state = build_structure(ltf_swings)

    # 4. Generate Signals & Simulate Execution
    signals = find_reversal_entries(
        candles=ltf_candles,
        swings=ltf_swings,
        state=ltf_state,
        htf_zones=htf_zones,
        min_preceding_weak_points=1,
        min_sl_dist=1.50,
        max_target_r=3.0,
        require_htf_alignment=True
    )
    print(f"[3/4] Strategy generated {len(signals):,} qualified limit signals.")

    outcomes = [simulate_trade(sig, ltf_candles) for sig in signals]

    print("\n[4/4] PERFORMANCE REPORT:")
    print("-" * 70)
    print(summarize(outcomes))
    print()
    run_demo_account_simulation(outcomes, demo_size=10000.0, risk_pct=0.01, actual_capital=1000.0)
    print("=" * 70)

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "XAUUSD_M1_2021_2024.csv"
    execute_strategy_pipeline(path)