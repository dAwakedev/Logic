"""
backtest_demo.py - Complete Step 6 Engine + $10k Demo Account Risk Engine
"""
import sys
from data_loader import load_candles_from_csv
from resample import resample_candles
from swings import detect_swings
from structure import classify_swings, build_structure
from reversal import find_reversal_entries, HTFZone, ZoneStatus
from trade_sim import simulate_trade, summarize


def extract_htf_zones_from_state(htf_candles, htf_swings, htf_state) -> list:
    """Extracts H4 Order Blocks / POIs into HTFZone objects for Step 6 tracking."""
    zones = []
    for shift in htf_state.shifts:
        # Map preceding structural leg candles to build HTF POI bounds
        start_idx = max(0, shift.index - 5)
        leg_candles = htf_candles[start_idx:shift.index + 1]
        if not leg_candles:
            continue
        
        top = max(c.high for c in leg_candles)
        bottom = min(c.low for c in leg_candles)
        
        zones.append(HTFZone(
            tf="H4",
            direction=shift.new_direction,
            top=top,
            bottom=bottom,
            created_index=shift.index,
            status=ZoneStatus.ACTIVE
        ))
    return zones


def run_demo_account_simulation(outcomes, demo_size: float = 10000.0, risk_pct: float = 0.01, actual_capital: float = 1000.0):
    """Calculates dollar PnL, equity curve, and real drawdown cushion for demo funds."""
    dollar_risk_per_trade = demo_size * risk_pct  # 1% risk = $100 per 1.0R
    current_demo_balance = demo_size
    current_real_capital = actual_capital
    peak_demo_balance = demo_size
    max_real_drawdown = 0.0

    filled_trades = [o for o in outcomes if o.result in ("win", "loss")]

    print("\n" + "=" * 60)
    print("        DEMO ACCOUNT & CAPITAL RISK PERFORMANCE")
    print("=" * 60)
    print(f"Starting Demo Balance : ${demo_size:,.2f}")
    print(f"Risk Per Trade (1.0R)  : ${dollar_risk_per_trade:,.2f} ({risk_pct * 100}%)")
    print(f"Actual Capital Cushion : ${actual_capital:,.2f} (Max Drawdown Buffer: 10.0R)")
    print("-" * 60)

    for i, o in enumerate(filled_trades, 1):
        r_mult = o.r_multiple if o.result == "win" else -1.0
        dollar_pnl = r_mult * dollar_risk_per_trade
        
        current_demo_balance += dollar_pnl
        current_real_capital += dollar_pnl

        # Track Max Capital Loss against the $1,000 real buffer
        real_drawdown = actual_capital - current_real_capital
        if real_drawdown > max_real_drawdown:
            max_real_drawdown = real_drawdown

        if current_demo_balance > peak_demo_balance:
            peak_demo_balance = current_demo_balance

    print(f"Final Demo Balance     : ${current_demo_balance:,.2f} (Net Gain: ${current_demo_balance - demo_size:+,.2f})")
    print(f"Remaining Real Capital : ${current_real_capital:,.2f}")
    print(f"Max Real Buffer Loss   : ${max_real_drawdown:,.2f} ({max_real_drawdown / dollar_risk_per_trade:.2f} R)")
    
    if current_real_capital <= 0:
        print("\n[ALERT]: Actual capital buffer wiped out! Reduce risk to 0.5% ($50/trade).")
    else:
        print("\n[PASS]: Capital cushion survived all drawdown sequences successfully.")
    print("=" * 60)


def run(csv_path: str, htf_min: int = 240, ltf_min: int = 15, min_weak_pts: int = 1):
    raw_candles = load_candles_from_csv(csv_path)
    print(f"Loaded {len(raw_candles)} raw 1m candles from {csv_path}")

    # 1. Higher Timeframe (H4) Processing & Zone Extraction
    htf_candles = resample_candles(raw_candles, minutes=htf_min)
    htf_swings = detect_swings(htf_candles, window=2)
    classify_swings(htf_candles, htf_swings, confirm_candles=1)
    htf_state = build_structure(htf_swings)
    htf_zones = extract_htf_zones_from_state(htf_candles, htf_swings, htf_state)
    print(f"HTF ({htf_min}m): {len(htf_candles)} bars | Shifts: {len(htf_state.shifts)} | Active HTF POIs: {len(htf_zones)}")

    # 2. Lower Timeframe (M15) Processing
    ltf_candles = resample_candles(raw_candles, minutes=ltf_min)
    ltf_swings = detect_swings(ltf_candles, window=2)
    classify_swings(ltf_candles, ltf_swings, confirm_candles=1)
    ltf_state = build_structure(ltf_swings)
    print(f"LTF ({ltf_min}m): {len(ltf_candles)} bars | Shifts: {len(ltf_state.shifts)}")

    # 3. Filter LTF Entries with Step 6 HTF State Engine
    signals = find_reversal_entries(
        candles=ltf_candles,
        swings=ltf_swings,
        state=ltf_state,
        htf_zones=htf_zones,
        min_preceding_weak_points=min_weak_pts,
        min_sl_dist=1.50,
        max_target_r=3.0,
        require_htf_alignment=True
    )
    print(f"\nStep 6 MTF Trade Signals Generated: {len(signals)}")

    # 4. Simulate Trades on LTF
    outcomes = []
    for sig in signals:
        outcome = simulate_trade(sig, ltf_candles)
        outcomes.append(outcome)
        print(f"\n[{sig.direction.value.upper()}] M15 entry idx={sig.entry_index} @ {sig.entry_price:.2f}")
        print(f"  SL={sig.stop_loss:.2f} | TP={sig.take_profit:.2f}")
        print(f"  {sig.rationale}")
        print(f"  -> Result: {outcome.result}" +
              (f" | R={outcome.r_multiple:.2f}" if outcome.r_multiple is not None else ""))

    # 5. Summary & Capital Risk Breakdown
    print("\n--- Step 6 MTF Simulation Summary ---")
    print(summarize(outcomes))

    # 6. Run Demo Account & Real Risk Tracking
    run_demo_account_simulation(outcomes, demo_size=10000.0, risk_pct=0.01, actual_capital=1000.0)


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "sample_data.csv"
    run(path, htf_min=240, ltf_min=15, min_weak_pts=1)