from data_loader import initialize_mt5, get_data
from strategy import generate_signals
from backtest import run_backtest, plot_results, monte_carlo_simulation, plot_return_distribution
from datetime import datetime, timedelta
import pandas as pd
import pytz

def main():
    if not initialize_mt5():
        return

    symbol = "XAUUSD"
    htf = "H4"
    etf = "M15"
    fib_level = 0.786
    days = 365

    # Fetch data for the last 6 months
    # mt5.copy_rates_range expects datetimes. If we provide timezone aware, we shouldn't localize again in get_data.
    # Actually, it's safer to just provide naive UTC datetimes to get_data.
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)

    print(f"Fetching {htf} data for {symbol}...")
    htf_data = get_data(symbol, htf, start_date, end_date)
    
    print(f"Fetching {etf} data for {symbol}...")
    etf_data = get_data(symbol, etf, start_date, end_date)

    if htf_data.empty or etf_data.empty:
        print("Error fetching data. Exiting.")
        return
 
    print("Generating Signals...")
    strategy_df = generate_signals(htf_data, etf_data, fib_level=fib_level)
    
    # Save a sample of the data to verify
    strategy_df.tail(100).to_csv("strategy_data_tail.csv")
    
    print("\n--- Diagnostic: HTF Trend Distribution in ETF data ---")
    print(strategy_df['htf_trend'].value_counts(dropna=False))
    
    print("\n--- Diagnostic: Trend Changes ---")
    prev_t = None
    for idx, row in strategy_df.iterrows():
        t = row['htf_trend']
        if t != prev_t and pd.notna(t):
            print(f"[{idx}] Trend shifted to: {t}")
            prev_t = t
    print("----------------------------------------------------\n")

    print("Running Backtest...")
    trades_df = run_backtest(strategy_df, initial_balance=10000.0, risk_per_trade=0.01, fib_level=fib_level)

    print("Creating Trade Plot...")
    plot_results(strategy_df, trades_df, symbol)

    # Monte Carlo simulation on trade PnLs
    print("Running Monte Carlo simulation...")
    final_balances = monte_carlo_simulation(trades_df, initial_balance=10000.0, n_sims=1000)
    plot_return_distribution(final_balances, initial_balance=10000.0)

if __name__ == "__main__":
    main()
