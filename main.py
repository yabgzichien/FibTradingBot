from data_loader import initialize_mt5, get_data, get_symbol_specs
from strategy import generate_signals_refined
from backtest import run_backtest, plot_results, monte_carlo_simulation, plot_return_distribution
from datetime import datetime, timedelta
import pandas as pd
import pytz

def main():
    if not initialize_mt5():
        return

    symbol = input("Enter trading symbol (e.g. XAUUSD, BTCUSD): ").upper() or "XAUUSD"
    try:
        days = int(input("Enter number of days for backtesting (default 3): ") or 3)
    except ValueError:
        print("Invalid input for days, using default 3.")
        days = 3
    
    anchor_tf = "H1"
    etf = "M15"
    entry_retracement = 0.618
    htf_warmup_days = 60  # Extra lookback so H4 swings/trend are fully initialised
    etf_warmup_candles = 360  # 15M warmup candles (360 × 15min = 3.75 days)

    end_date = datetime.utcnow()
    etf_start = end_date - timedelta(days=days)
    htf_start = end_date - timedelta(days=days + htf_warmup_days)  # H4 fetches further back
    etf_warmup_td = timedelta(minutes=etf_warmup_candles * 15)
    etf_fetch_start = etf_start - etf_warmup_td  # Fetch extra M15 history for warmup

    print(f"Fetching {anchor_tf} data for {symbol} (with {htf_warmup_days}-day warm-up buffer)...")
    htf_data = get_data(symbol, anchor_tf, htf_start, end_date)

    print(f"Fetching {etf} data for {symbol} (with {etf_warmup_candles}-candle warm-up buffer)...")
    etf_data = get_data(symbol, etf, etf_fetch_start, end_date)

    if htf_data.empty or etf_data.empty:
        print("Error fetching data. Exiting.")
        return

    specs = get_symbol_specs(symbol) or {}
    point_value = float(specs.get("point") or 0.01)
    spread_points = float(specs.get("spread") or 0.0)
 
    print("Generating Signals...")
    strategy_df = generate_signals_refined(
        htf_data,
        etf_data,
        anchor_swing_window=7,
        execution_swing_window=1,
        entry_retracement=entry_retracement,
        sweep_mode="prev_bar",
        internal_structure_lookback_bars=1,
        max_bos_wait_bars=8
    )

    # Trim warmup: only keep rows from the actual backtest start onwards
    strategy_df = strategy_df[strategy_df.index >= pd.Timestamp(etf_start)]
    
    # Save a sample of the data to verify
    strategy_df.tail(100).to_csv("strategy_data_tail.csv")
    
    print("\n--- Diagnostic: Anchor Bias Distribution in ETF data ---")
    print(strategy_df['bias'].value_counts(dropna=False))
    print("----------------------------------------------------\n")

    print("Running Backtest...")
    trades_df = run_backtest(
        strategy_df, 
        initial_balance=10000.0, 
        risk_per_trade=0.01, 
        spread_points=spread_points,
        slippage_points=5,
        commission_per_unit=0.07,
        point_value=point_value
    )

    print("Creating Trade Plot...")
    plot_results(strategy_df, trades_df, symbol)

    # Monte Carlo simulation on trade PnLs
    print("Running Monte Carlo simulation...")
    final_balances = monte_carlo_simulation(trades_df, initial_balance=10000.0, n_sims=1000)
    plot_return_distribution(final_balances, initial_balance=10000.0)

if __name__ == "__main__":
    main()
