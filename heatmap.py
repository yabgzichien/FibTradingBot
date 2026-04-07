import pandas as pd
import numpy as np
import seaborn as sns
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
import os
import sys

from data_loader import initialize_mt5, get_data
from optimizer import generate_signals_with_params, silent_backtest

# --- Heatmap Configuration ---
SYMBOL = "XAUUSD"
HTF = "H4"
ETF = "M15"
LOOKBACK_DAYS = 180  # 6 Months to keep backtest fast
INITIAL_BALANCE = 10000.0
RISK_PER_TRADE = 0.01  # 1% risk

# Lock the Fibonacci retracement level for the 2D grid
FIXED_FIB_LEVEL = 0.618

# Define the Grid Search Space
# Try HTF Swings from 3 to 15 (step 2)
HTF_GRID = list(range(3, 16, 2))
# Try ETF Swings from 1 to 7 (step 1)
ETF_GRID = list(range(1, 8, 1))

def generate_heatmap():
    if not initialize_mt5():
        return

    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=LOOKBACK_DAYS)
    
    # Needs warmup buffer for indicator
    htf_warmup_days = 60
    etf_warmup_days = 4
    
    htf_fetch_start = start_date - timedelta(days=htf_warmup_days)
    etf_fetch_start = start_date - timedelta(days=etf_warmup_days)

    print(f"Fetching {HTF} data for {SYMBOL}...")
    htf_data = get_data(SYMBOL, HTF, htf_fetch_start, end_date)
    print(f"Fetching {ETF} data for {SYMBOL}...")
    etf_data = get_data(SYMBOL, ETF, etf_fetch_start, end_date)

    if htf_data.empty or etf_data.empty:
        print("Error fetching data. Exiting.")
        return

    total_iters = len(HTF_GRID) * len(ETF_GRID)
    print(f"\nStarting Parameter Sensitivity Heatmap Generation ({total_iters} iterations)")
    print(f"Fixed Fib Level: {FIXED_FIB_LEVEL}")
    
    # Initialize a clean results matrix
    pnl_matrix = np.zeros((len(HTF_GRID), len(ETF_GRID)))
    
    iter_count = 0
    for i, htf_w in enumerate(HTF_GRID):
        for j, etf_w in enumerate(ETF_GRID):
            iter_count += 1
            sys.stdout.write(f"\r  Running Grid: {iter_count}/{total_iters} | HTF:{htf_w:02d} ETF:{etf_w:02d}")
            sys.stdout.flush()
            
            try:
                # 1. Generate Signals
                strategy_df = generate_signals_with_params(htf_data, etf_data, htf_w, etf_w, FIXED_FIB_LEVEL)
                # Trim warmup
                strategy_df = strategy_df[strategy_df.index >= pd.Timestamp(start_date)]
                
                # 2. Backtest Silently
                _, metrics = silent_backtest(strategy_df, INITIAL_BALANCE, RISK_PER_TRADE, FIXED_FIB_LEVEL)
                
                # 3. Record PnL
                pnl = metrics.get('total_pnl', 0)
                pnl_matrix[i, j] = pnl
                
            except Exception as e:
                pnl_matrix[i, j] = 0

    print("\n\nGrid search complete! Rendering Heatmap...")
    
    # Convert numpy matrix to DataFrame for seaborn
    heatmap_df = pd.DataFrame(pnl_matrix, index=HTF_GRID, columns=ETF_GRID)
    
    # Plotting using Seaborn
    plt.figure(figsize=(10, 8))
    
    # Create the heatmap
    ax = sns.heatmap(
        heatmap_df, 
        annot=True,          # Show values in cells
        fmt=".0f",           # No decimal places for PnL
        cmap="RdYlGn",       # Red (Negative) to Yellow (Neutral) to Green (Positive)
        center=0,            # Force 0 PnL to be the middle color
        cbar_kws={'label': 'Total Net Profit ($)'},
        linewidths=.5
    )
    
    # Axis labels
    plt.title(f"Parameter Sensitivity Heatmap\n{SYMBOL} (Last {LOOKBACK_DAYS} days) | Fixed Fib: {FIXED_FIB_LEVEL}", pad=15)
    plt.xlabel('ETF Swing Window (Local Structure)')
    plt.ylabel('HTF Swing Window (Macro Trend)')
    
    # Invert Y axis so lower values are at bottom (standard charting style)
    ax.invert_yaxis()
    
    os.makedirs("backtest_results", exist_ok=True)
    out_path = "backtest_results/heatmap_sensitivity.png"
    plt.tight_layout()
    plt.savefig(out_path, dpi=120)
    plt.close()
    
    print(f"\n[SUCCESS] Heatmap successfully saved to: {out_path}")
    print("\nHow to interpret:")
    print(" - Look for clusters of Solid Green (profitability).")
    print(" - If your chosen parameter is green, but surrounded by deep red, your strategy is overfitted.")
    print(" - If your chosen parameter is surrounded by green/yellow, your strategy is robust.")

if __name__ == "__main__":
    generate_heatmap()
