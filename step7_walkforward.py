"""
step7_walkforward.py - Multi-Asset & Out-of-Sample Walkforward Engine
Splits datasets 70/30 (In-Sample / Out-of-Sample) to test model generalization.
"""
import sys
from data_loader import load_candles_from_csv
from resample import resample_candles
from swings import detect_swings
from structure import classify_swings, build_structure
from reversal import find_reversal_entries
from trade_sim import simulate_trade, summarize
from backtest_demo import extract_htf_zones_from_state, run_demo_account_simulation

def run_walkforward_asset(csv_path: str, asset_name: str, train_ratio: float = 0.7):
    print("\n" + "=" * 75)
    print(f"       RUNNING STEP 7 WALKFORWARD VALIDATION: {asset_name.upper()}")
    print("=" * 75)
    
    # 1. Load Universal Data
    raw_candles = load_candles_from_csv(csv_path)
    print(f"Loaded {len(raw_candles):,} raw candles from {csv_path}")

    # 2. Split 70% In-Sample (Train) / 30% Out-of-Sample (Test)
    split_idx = int(len(raw_candles) * train_ratio)
    splits = {
        "IN-SAMPLE (Optimization Block)": raw_candles[:split_idx],
        "OUT-OF-SAMPLE (Validation Block)": raw_candles[split_idx:]
    }

    # 3. Process Each Phase Independent of Each Other
    for phase_name, candles in splits.items():
        print(f"\n--- {phase_name} | {len(candles):,} Candles ---")
        
        # Resample H4 and LTF (Auto-detects 1m or 5m base)
        htf_candles = resample_candles(candles, minutes=240)
        ltf_candles = resample_candles(candles, minutes=15)

        # H4 POI State
        htf_swings = detect_swings(htf_candles, window=2)
        classify_swings(htf_candles, htf_swings, confirm_candles=1)
        htf_state = build_structure(htf_swings)
        htf_zones = extract_htf_zones_from_state(htf_candles, htf_swings, htf_state)

        # M15 Entry State
        ltf_swings = detect_swings(ltf_candles, window=2)
        classify_swings(ltf_candles, ltf_swings, confirm_candles=1)
        ltf_state = build_structure(ltf_swings)

        # Step 6 Entry Rules & Safety Enforcers
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

        # Execution Simulation
        outcomes = [simulate_trade(sig, ltf_candles) for sig in signals]
        
        # Summary & Demo Capital Tracking
        print(summarize(outcomes))
        run_demo_account_simulation(outcomes, demo_size=10000.0, risk_pct=0.01, actual_capital=1000.0)

if __name__ == "__main__":
    filepath = sys.argv[1] if len(sys.argv) > 1 else "EURUSD_5m.csv"
    asset = sys.argv[2] if len(sys.argv) > 2 else "EURUSD"
    run_walkforward_asset(filepath, asset)