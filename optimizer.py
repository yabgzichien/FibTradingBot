"""
Walk-Forward Parameter Optimizer for the Fibonacci + Market Structure Trading Bot.
Prevents overfitting by optimizing on an In-Sample (Train) window and validating
blindly on an Out-of-Sample (Test) window, rolling chronologically through the dataset.
"""
from data_loader import initialize_mt5, get_data, get_symbol_specs
from strategy import generate_signals_refined
from backtest import run_backtest
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import itertools
import sys
import io

# ── Configurable Search Space ──────────────────────────────────────────────────────
SYMBOL = "XAUUSD"
HTF = "H4"
ETF = "M15"
INITIAL_BALANCE = 10000.0
RISK_PER_TRADE = 0.01  # This is now interpreted as $100 Fixed Risk (1% of Initial Balance)
LOOKBACK_DAYS = 365 * 2  # 2 years (MT5 M15 data limit)

# Walk-Forward Settings
TRAIN_MONTHS = 6   # In-Sample period
TEST_MONTHS = 3    # Out-Of-Sample period

# Parameter grid
FIB_LEVELS      = [0.618, 0.7, 0.786]
HTF_SWING_WINS  = [5, 7, 10]
ETF_SWING_WINS  = [1, 3, 5]
# ────────────────────────────────────────────────────────────────────────

def generate_signals_with_params(htf_df, etf_df, htf_window, etf_window, fib_level):
    """
    Wraps generate_signals_refined for dynamic parameter injection.
    Matches the latest 'live' logic.
    """
    return generate_signals_refined(
        htf_df,
        etf_df,
        anchor_swing_window=htf_window,
        execution_swing_window=etf_window,
        entry_retracement=fib_level,
        sweep_mode="prev_bar",
        internal_structure_lookback_bars=1,
        max_bos_wait_bars=8,
        max_pending_bars=96
    )


def silent_backtest(df, initial_balance, risk_per_trade, fib_level):
    """
    Runs run_backtest but suppresses all print output. Returns trades_df and key metrics.
    """
    old_stdout = sys.stdout
    sys.stdout = buffer = io.StringIO()

    specs = get_symbol_specs(SYMBOL) or {}
    point_value = float(specs.get("point") or 0.01)
    spread_points = float(specs.get("spread") or 0.0)

    trades_df = run_backtest(
        df, 
        initial_balance=initial_balance,
        risk_per_trade=risk_per_trade, 
        spread_points=spread_points,
        slippage_points=5,
        commission_per_unit=0.07,
        point_value=point_value,
        symbol=SYMBOL
    )

    output = buffer.getvalue()
    sys.stdout = old_stdout

    # Parse metrics from the captured output
    metrics = {}
    for line in output.splitlines():
        if "Total Trades:" in line:
            metrics['total_trades'] = int(line.split(":")[1].strip())
        elif "Win Rate:" in line:
            metrics['win_rate'] = line.split(":")[1].strip()
        elif "Total PnL:" in line:
            metrics['total_pnl'] = float(line.split("$")[1].strip())
        elif "Max Drawdown:" in line:
            metrics['max_drawdown'] = line.split(":")[1].strip()
        elif "Total Return:" in line:
            metrics['total_return'] = line.split(":")[1].strip()

    return trades_df, metrics


def print_wfo_report(wfo_results, combined_oos_trades):
    print("\n" + "=" * 110)
    print("                      WALK-FORWARD OPTIMIZATION (WFO) RESULTS")
    print("=" * 110)
    
    df = pd.DataFrame(wfo_results)
    
    for _, row in df.iterrows():
        print(f"Segment {row['segment']:02d} | Train: {row['train_start'].date()} -> {row['train_end'].date()} | Test: -> {row['test_end'].date()}")
        if row['best_fib'] is None:
            print("  [ERROR] No profitable in-sample params found. Skipping test.")
        else:
            print(f"  Best IS Params: Fib={row['best_fib']}, HTF={row['best_htfw']}, ETF={row['best_etfw']}  >>  IS PnL: ${row['is_pnl']:>7.2f} ({row['is_trades']} trades)")
            print(f"  Blind OOS Test:                                        >> OOS PnL: ${row['oos_pnl']:>7.2f} ({row['oos_trades']} trades)")
        print("-" * 110)
        
    print("\n" + "=" * 50)
    print("      BLIND OUT-OF-SAMPLE PORTFOLIO")
    print("=" * 50)
    
    if combined_oos_trades.empty:
        print("WFO FAILED: Strategy collapsed in out-of-sample testing.")
        return
        
    total_oos_pnl = combined_oos_trades['pnl'].sum()
    total_oos_trades = len(combined_oos_trades)
    oos_wins = len(combined_oos_trades[combined_oos_trades['result'] == 'Win'])
    oos_wr = oos_wins / total_oos_trades if total_oos_trades > 0 else 0
    
    print(f"Total True Out-Of-Sample Trades: {total_oos_trades}")
    print(f"True Out-Of-Sample Win Rate:     {oos_wr:.2%}")
    print(f"True Out-Of-Sample Gross PnL:    ${total_oos_pnl:.2f}")
    
    if total_oos_pnl > 0:
        print("\nSTATUS: ROBUST STRATEGY DETECTED. Parameters generalize well to unseen data.")
    else:
        print("\nSTATUS: OVERFIT STRATEGY. Backtest metrics are an illusion.")
    print("=" * 50)
    
    os.makedirs("backtest_results", exist_ok=True)
    combined_oos_trades.to_csv("backtest_results/wfo_out_of_sample_trades.csv", index=False)
    print("\nFull WFO Out-Of-Sample trades saved to backtest_results/wfo_out_of_sample_trades.csv")


def main():
    if not initialize_mt5():
        return

    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=LOOKBACK_DAYS)

    print(f"Fetching {HTF} data for {SYMBOL}...")
    htf_data = get_data(SYMBOL, HTF, start_date, end_date)
    print(f"Fetching {ETF} data for {SYMBOL}...")
    etf_data = get_data(SYMBOL, ETF, start_date, end_date)

    if htf_data.empty or etf_data.empty:
        print("Error fetching data. Exiting.")
        return

    combos = list(itertools.product(FIB_LEVELS, HTF_SWING_WINS, ETF_SWING_WINS))
    total_combos = len(combos)
    print(f"\nStarting Walk-Forward Optimization (WFO)")
    print(f"Train Window: {TRAIN_MONTHS} Months | Test Window: {TEST_MONTHS} Months")
    print(f"Search Space: {total_combos} combinations per rolling segment...\n")

    current_train_start = etf_data.index.min()
    end_date_limit = etf_data.index.max()
    
    wfo_results = []
    oos_trades_list = []
    
    segment = 1
    htf_warmup_days = 60
    etf_warmup_days = 4
    
    while True:
        train_end = current_train_start + pd.DateOffset(months=TRAIN_MONTHS)
        test_end = train_end + pd.DateOffset(months=TEST_MONTHS)
        
        if train_end >= end_date_limit:
            break
            
        print(f"\n--- Processing Segment {segment} ---")
        print(f"In-Sample Train:   {current_train_start.date()} to {train_end.date()}")
        
        htf_train_start = current_train_start - pd.Timedelta(days=htf_warmup_days)
        etf_train_start = current_train_start - pd.Timedelta(days=etf_warmup_days)
        
        htf_train = htf_data[(htf_data.index >= htf_train_start) & (htf_data.index < train_end)]
        etf_train = etf_data[(etf_data.index >= etf_train_start) & (etf_data.index < train_end)]
        
        best_combo = None
        best_objective_score = -999999
        best_metrics = {}
        
        # 1. Grid Search on In-Sample Data
        for idx, (fib, htf_w, etf_w) in enumerate(combos, 1):
            sys.stdout.write(f"\r  Optimizing IS: Combo {idx}/{total_combos}")
            sys.stdout.flush()
            
            try:
                os.makedirs("backtest_results", exist_ok=True)
                strategy_df = generate_signals_with_params(htf_train, etf_train, htf_w, etf_w, fib)
                strategy_df.tail(100).to_csv(f"backtest_results/strategy_data_tail_{SYMBOL}.csv")
                strategy_df = strategy_df[strategy_df.index >= current_train_start] # Trim warmup
                
                _, metrics = silent_backtest(strategy_df, INITIAL_BALANCE, RISK_PER_TRADE, fib)
                
                # Objective Function: Maximize Total PnL (only if trades > 0)
                pnl = metrics.get('total_pnl', 0)
                trades = metrics.get('total_trades', 0)
                
                if pnl > best_objective_score and trades > 0:
                    best_objective_score = pnl
                    best_combo = (fib, htf_w, etf_w)
                    best_metrics = metrics
            except Exception as e:
                pass
                
        print() # Newline after progress
        
        if best_combo is None:
            print(f"  [!] No profitable combos found In-Sample.")
            current_train_start += pd.DateOffset(months=TEST_MONTHS)
            segment += 1
            wfo_results.append({
                'segment': segment-1,
                'train_start': current_train_start,
                'train_end': train_end,
                'test_end': min(test_end, end_date_limit),
                'best_fib': None
            })
            continue
            
        print(f"  Found IS Best: Fib={best_combo[0]}, HTF={best_combo[1]}, ETF={best_combo[2]} -> PnL: ${best_metrics.get('total_pnl',0):.2f}")
        
        # 2. Out-of-Sample Blind Test
        max_test_end = min(test_end, end_date_limit)
        print(f"  Running Out-of-Sample Blind Test: {train_end.date()} to {max_test_end.date()}")
        
        htf_test_start = train_end - pd.Timedelta(days=htf_warmup_days)
        etf_test_start = train_end - pd.Timedelta(days=etf_warmup_days)
        
        htf_test = htf_data[(htf_data.index >= htf_test_start) & (htf_data.index <= max_test_end)]
        etf_test = etf_data[(etf_data.index >= etf_test_start) & (etf_data.index <= max_test_end)]
        
        strategy_df_test = generate_signals_with_params(htf_test, etf_test, best_combo[1], best_combo[2], best_combo[0])
        strategy_df_test = strategy_df_test[strategy_df_test.index >= train_end]
        
        oos_trades_df, oos_metrics = silent_backtest(strategy_df_test, INITIAL_BALANCE, RISK_PER_TRADE, best_combo[0])
        
        print(f"  OOS Result -> PnL: ${oos_metrics.get('total_pnl', 0):.2f} ({oos_metrics.get('total_trades', 0)} trades)")
        
        if not oos_trades_df.empty:
            oos_trades_list.append(oos_trades_df)
            
        wfo_results.append({
            'segment': segment,
            'train_start': current_train_start,
            'train_end': train_end,
            'test_end': max_test_end,
            'best_fib': best_combo[0],
            'best_htfw': best_combo[1],
            'best_etfw': best_combo[2],
            'is_trades': best_metrics.get('total_trades', 0),
            'is_pnl': best_metrics.get('total_pnl', 0),
            'oos_trades': oos_metrics.get('total_trades', 0),
            'oos_pnl': oos_metrics.get('total_pnl', 0)
        })
        
        # Roll forward chronologically by 'TEST_MONTHS'
        current_train_start += pd.DateOffset(months=TEST_MONTHS)
        segment += 1

    combined_oos_trades = pd.concat(oos_trades_list, ignore_index=True) if oos_trades_list else pd.DataFrame()
    print_wfo_report(wfo_results, combined_oos_trades)


if __name__ == "__main__":
    main()
