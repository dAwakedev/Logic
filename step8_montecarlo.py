"""
step8_montecarlo.py - Monte Carlo Risk of Ruin Engine
Shuffles out-of-sample trade outcomes across 10,000 randomized iterations.
"""
import random
import numpy as np

def run_monte_carlo(r_multiples, iterations=10000, starting_buffer_r=10.0):
    ruin_count = 0
    max_drawdowns = []

    for _ in range(iterations):
        shuffled = random.sample(r_multiples, len(r_multiples))
        equity_peak = starting_buffer_r
        current_equity = starting_buffer_r
        max_dd = 0.0

        for r in shuffled:
            current_equity += r
            if current_equity > equity_peak:
                equity_peak = current_equity
            
            dd = equity_peak - current_equity
            if dd > max_dd:
                max_dd = dd

            # Ruin condition: Depleting the 10.0 R real capital cushion
            if current_equity <= 0:
                ruin_count += 1
                break

        max_drawdowns.append(max_dd)

    print("\n" + "=" * 60)
    print("         STEP 8: MONTE CARLO STRESS TEST RESULTS")
    print("=" * 60)
    print(f"Total Iterations    : {iterations:,}")
    print(f"Risk of Ruin        : {(ruin_count / iterations) * 100:.2f}%")
    print(f"95th Pct Drawdown   : {np.percentile(max_drawdowns, 95):.2f} R")
    print(f"99th Pct Drawdown   : {np.percentile(max_drawdowns, 99):.2f} R")
    print(f"Worst Max Drawdown  : {max(max_drawdowns):.2f} R")
    print("=" * 60)

if __name__ == "__main__":
    # Combined out-of-sample filled trade outcomes from XAUUSD + US30
    xau_oos = [3.0]*47 + [-1.0]*22
    us30_oos = [3.0]*5 + [-1.0]*3
    combined_oos = xau_oos + us30_oos
    
    run_monte_carlo(combined_oos)