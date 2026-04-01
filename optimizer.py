"""
Parameter Optimizer for the Fibonacci + Market Structure Trading Bot.
Sweeps across key parameter combinations and outputs a ranked comparison table.
"""
from data_loader import initialize_mt5, get_data, get_symbol_specs
from strategy import find_swings, determine_trend
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

# Parameter grid
FIB_LEVELS      = [0.618, 0.65, 0.7, 0.75, 0.786]
HTF_SWING_WINS  = [5, 7, 10, 12, 15]
ETF_SWING_WINS  = [3, 5, 7, 10, 15]
# ────────────────────────────────────────────────────────────────────────

def generate_signals_with_params(htf_df, etf_df, htf_window, etf_window, fib_level):
    """
    Same as strategy.generate_signals but accepts variable swing windows.
    """
    htf = find_swings(htf_df, window=htf_window)
    htf = determine_trend(htf)
    etf = find_swings(etf_df, window=etf_window)

    htf_trend = htf[['trend']].rename(columns={'trend': 'htf_trend'})
    etf = etf.sort_index()
    htf_trend = htf_trend.sort_index().shift(1)

    combined = pd.merge_asof(etf, htf_trend, left_index=True, right_index=True, direction='backward')
    combined['signal'] = 0
    combined['entry_price'] = np.nan
    combined['sl'] = np.nan
    combined['tp'] = np.nan
    return combined


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
        fib_level=fib_level,
        spread_points=spread_points,
        slippage_points=5,
        commission_per_unit=0.07,
        point_value=point_value
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
        elif "Sharpe Ratio:" in line:
            metrics['sharpe_ratio'] = float(line.split(":")[1].strip())
        elif "Ann. Std Dev:" in line:
            metrics['ann_std'] = line.split(":")[1].strip()
        elif "Final Balance:" in line:
            metrics['final_balance'] = float(line.split("$")[1].strip())
        elif "Drawdown >10% Episodes:" in line:
            metrics['dd_episodes'] = int(line.split(":")[1].strip())
        elif "Num Trades / DD" in line:
            metrics['trades_per_dd'] = float(line.split(":")[1].strip())
        elif "Total Return:" in line:
            metrics['total_return'] = line.split(":")[1].strip()
        elif "Prop Firm Challenge" in line:
            # Example: Prop Firm Challenge (+15% before -8%): 33 Passes / 1 Fails
            parts = line.split(":")[1].strip()
            passes_part, fails_part = parts.split("/")
            metrics['prop_firm_passes'] = int(passes_part.strip().split(" ")[0])
            metrics['prop_firm_fails'] = int(fails_part.strip().split(" ")[0])
        elif "Max Consecutive Take Profit Wins:" in line:
            metrics['max_consecutive_tp_wins'] = int(line.split(":")[1].strip())

    return trades_df, metrics


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
    total = len(combos)
    print(f"\nRunning {total} parameter combinations...\n")

    results = []

    for i, (fib, htf_w, etf_w) in enumerate(combos, 1):
        label = f"Fib={fib} | HTF_W={htf_w} | ETF_W={etf_w}"
        print(f"[{i}/{total}] {label}", end=" ... ")

        try:
            strategy_df = generate_signals_with_params(htf_data, etf_data,
                                                        htf_window=htf_w,
                                                        etf_window=etf_w,
                                                        fib_level=fib)
            _, metrics = silent_backtest(strategy_df, INITIAL_BALANCE, RISK_PER_TRADE, fib)

            if metrics.get('total_trades', 0) == 0:
                print("No trades.")
                continue

            results.append({
                'fib_level': fib,
                'htf_swing_window': htf_w,
                'etf_swing_window': etf_w,
                **metrics
            })
            print(f"Trades={metrics.get('total_trades',0)} | "
                  f"WR={metrics.get('win_rate','N/A')} | "
                  f"Ret={metrics.get('total_return','N/A')} | "
                  f"MaxDD={metrics.get('max_drawdown','N/A')} | "
                  f"PF Passes={metrics.get('prop_firm_passes',0)} | "
                  f"PF Fails={metrics.get('prop_firm_fails',0)}")
        except Exception as e:
            print(f"Error: {e}")

    if not results:
        print("No valid results.")
        return

    results_df = pd.DataFrame(results)

    # Calculate Trades / DD>10% Episodes ratio
    # Lower = better (minimize). If dd_episodes=0, set to 0 (best possible - no drawdowns)
    results_df['trades_per_dd'] = results_df.apply(
        lambda r: r['total_trades'] / r['dd_episodes'] if r['dd_episodes'] > 0 else 0, axis=1
    )

    # Sort primarily by Prop Firm Passes (descending), then Trades/DD (ascending) (0 is best)
    results_df.sort_values(['prop_firm_passes', 'trades_per_dd'], ascending=[False, True], inplace=True)
    results_df.reset_index(drop=True, inplace=True)
    results_df.index += 1  # 1-indexed rank
    results_df.index.name = 'Rank'

    print("\n" + "=" * 130)
    print("            PARAMETER OPTIMIZATION RESULTS (Ranked by Prop Firm Passes)")
    print("=" * 130)
    
    # Select columns to display for clarity
    display_cols = ['fib_level', 'htf_swing_window', 'etf_swing_window', 'prop_firm_passes', 'prop_firm_fails', 'max_consecutive_tp_wins', 'total_trades', 'win_rate', 'total_return', 'max_drawdown', 'sharpe_ratio', 'trades_per_dd']
    print(results_df[display_cols].to_string())

    # Save to CSV
    results_df.to_csv("optimization_results_v4.csv")
    print("\nFull results saved to optimization_results_v4.csv")

    # Highlight the best combo
    best = results_df.iloc[0]
    print("\n" + "=" * 100)
    print("  BEST PARAMETER SET (Max Prop Firm Passes)")
    print("=" * 100)
    print(f"  Fib Level:           {best['fib_level']}")
    print(f"  HTF Swing Window:    {best['htf_swing_window']}")
    print(f"  ETF Swing Window:    {best['etf_swing_window']}")
    print(f"  Prop Firm Passes:    {best['prop_firm_passes']}")
    print(f"  Prop Firm Fails:     {best['prop_firm_fails']}")
    print(f"  Total Trades:        {best['total_trades']}")
    print(f"  Win Rate:            {best['win_rate']}")
    print(f"  Total Return:        {best['total_return']}")
    print(f"  Total PnL:           ${best['total_pnl']:.2f}")
    print(f"  Max Drawdown:        {best['max_drawdown']}")
    print(f"  Sharpe Ratio:        {best['sharpe_ratio']:.2f}")
    print(f"  Ann. Std Dev:        {best['ann_std']}")
    print(f"  DD >10% Episodes:    {best['dd_episodes']}")
    print(f"  Trades/DD>10%:       {best['trades_per_dd']:.2f}")
    print(f"  Max Consecutive TP Wins: {best.get('max_consecutive_tp_wins', 0)}")
    print(f"  Final Balance:       ${best['final_balance']:.2f}")
    print("=" * 100)


if __name__ == "__main__":
    main()
