"""
backtest.py  –  1:1 backtesting engine for live_trader.py

Anti-cheat guarantees
─────────────────────
1. NO LOOK-AHEAD BIAS
   A signal emitted on bar[i] (the "closed candle") is stored as a
   pending order and can only be *filled* from bar[i+1] onward.
   This mirrors live_trader's `current_row = strategy_df.iloc[-2]`.

2. NO INTRABAR FILL ILLUSION
   When a bar's range covers the limit entry price, the fill is at
   exactly `entry_level` (not the open or some random price).

3. NO INTRABAR EXIT ILLUSION
   If a bar's range touches both SL and TP after fill, the SL is
   assumed to have been hit first (conservative, real-world default).

4. ONE TRADE / ONE PENDING AT A TIME
   Mirrors `has_open_positions` and `sync_pending_order` behaviour.
   A new signal cancels any outstanding pending order (same as
   `cancel_all_bot_pending_orders` inside `sync_pending_order`).

Trade direction is INVERTED relative to the strategy signal,
matching the live_trader.py change requested (setup_dir 1 → SELL,
setup_dir -1 → BUY), with SL/TP swapped accordingly.
"""

import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from data_loader import initialize_mt5, get_data
from strategy import generate_signals_refined

# ── Configuration (mirror live_trader.py) ─────────────────────────────────────
SYMBOL            = input("Enter trading symbol (e.g. BTCUSD, XAUUSD): ").upper() or "XAUUSD"
HTF               = "H4"
ETF               = "M15"

HTF_WINDOW        = 7
ETF_WINDOW        = 3
ENTRY_RETRACEMENT = 0.71

INITIAL_BALANCE   = 10_000.0
RISK_PER_TRADE    = 0.01          # 1% per trade
RISK_DOLLARS      = INITIAL_BALANCE * RISK_PER_TRADE

# Backtest window (increase for more history)
ETF_LOOKBACK_DAYS  = 180
HTF_WARMUP_DAYS    = 60
ETF_WARMUP_CANDLES = 360          # 15-min candles kept as warmup buffer

# Contract size used for PnL and lot calculation.
# Override per symbol if needed (XAUUSD=100, BTCUSD=1, etc.)
CONTRACT_SIZE_MAP = {
    "XAUUSD": 100.0,
    "BTCUSD": 1.0,
}
# ──────────────────────────────────────────────────────────────────────────────


# ── Helpers ───────────────────────────────────────────────────────────────────

def _contract_size(symbol: str) -> float:
    return CONTRACT_SIZE_MAP.get(symbol, 100.0)


def _calc_lots(entry: float, sl: float, risk_dollars: float, symbol: str) -> float:
    """
    Mirror of live_trader.calculate_lot_size.
    lots = risk_dollars / (sl_distance * contract_size)
    Clamped to minimum 0.01.
    """
    sl_dist = abs(entry - sl)
    if sl_dist == 0:
        return 0.01
    cs   = _contract_size(symbol)
    lots = risk_dollars / (sl_dist * cs)
    lots = max(0.01, round(lots, 2))
    return lots


def _calc_pnl(direction: int, entry: float, exit_price: float,
              lots: float, symbol: str) -> float:
    """
    PnL in account currency (USD).
    direction: 1 = long, -1 = short
    """
    cs = _contract_size(symbol)
    if direction == 1:
        return (exit_price - entry) * lots * cs
    else:
        return (entry - exit_price) * lots * cs


# ── Core backtester ───────────────────────────────────────────────────────────

def run_backtest(strategy_df: pd.DataFrame,
                 symbol: str        = "XAUUSD",
                 risk_dollars: float = RISK_DOLLARS,
                 invert_trades: bool = False) -> tuple[pd.DataFrame, pd.Series]:
    """
    Walk forward through strategy_df one bar at a time.

    Parameters
    ----------
    strategy_df  : output of generate_signals_refined(), warmup already trimmed
    symbol       : trading symbol (for PnL calc)
    risk_dollars : fixed dollar risk per trade
    invert_trades: True → flip strategy direction (live_trader change)

    Returns
    -------
    trades_df    : pd.DataFrame  – one row per completed trade
    equity_curve : pd.Series     – equity value at every bar
    """
    n       = len(strategy_df)
    equity  = INITIAL_BALANCE
    trades  = []
    eq_vals = np.full(n, np.nan)

    # ── State ─────────────────────────────────────────────────────────────────
    # pending  : the limit order sitting in the market (not yet filled)
    # position : the open trade after the limit order was filled
    pending  = None   # {dir, entry, sl, tp, lots, signal_bar}
    position = None   # {dir, entry, sl, tp, lots, entry_time, entry_bar}

    for i in range(n):
        bar = strategy_df.iloc[i]
        ts  = strategy_df.index[i]
        o   = float(bar['open'])
        h   = float(bar['high'])
        l   = float(bar['low'])
        c   = float(bar['close'])

        # ── STEP 1: manage open position ──────────────────────────────────────
        if position is not None:
            dir_  = position['dir']
            entry = position['entry']
            sl    = position['sl']
            tp    = position['tp']
            lots  = position['lots']

            if dir_ == 1:          # long
                sl_hit = l <= sl
                tp_hit = h >= tp
            else:                  # short
                sl_hit = h >= sl
                tp_hit = l <= tp

            if sl_hit or tp_hit:
                # ── Intrabar ambiguity rule: SL first (conservative) ──────────
                if sl_hit and tp_hit:
                    exit_price, result = sl, "SL"
                elif tp_hit:
                    exit_price, result = tp, "TP"
                else:
                    exit_price, result = sl, "SL"

                pnl     = _calc_pnl(dir_, entry, exit_price, lots, symbol)
                equity += pnl

                trades.append({
                    "entry_time"  : position["entry_time"],
                    "exit_time"   : ts,
                    "direction"   : "BUY"  if dir_ == 1 else "SELL",
                    "entry_price" : round(entry, 5),
                    "sl"          : round(sl, 5),
                    "tp"          : round(tp, 5),
                    "exit_price"  : round(exit_price, 5),
                    "lots"        : lots,
                    "result"      : result,
                    "pnl"         : round(pnl, 2),
                    "equity"      : round(equity, 2),
                    "bars_held"   : i - position["entry_bar"],
                })
                position = None

        # ── STEP 2: try to fill pending limit order ───────────────────────────
        #    Guard: only fill if no open position exists after step 1.
        if pending is not None and position is None:
            # A signal on bar[k] → pending placed → earliest fill is bar[k+1]
            # Enforced by: pending is only set at end of loop (step 3),
            # so it is never fillable on the same bar it was created.
            dir_    = pending["dir"]
            entry   = pending["entry"]
            sl      = pending["sl"]
            tp      = pending["tp"]
            lots    = pending["lots"]
            filled  = False

            if dir_ == 1 and l <= entry:      # BUY_LIMIT: price dipped to entry
                filled = True
            elif dir_ == -1 and h >= entry:   # SELL_LIMIT: price rallied to entry
                filled = True

            if filled:
                position = {
                    "dir"       : dir_,
                    "entry"     : entry,
                    "sl"        : sl,
                    "tp"        : tp,
                    "lots"      : lots,
                    "entry_time": ts,
                    "entry_bar" : i,
                }
                pending = None

                # ── Check same-bar SL/TP after fill ───────────────────────────
                # The fill happens intrabar; we then immediately check if
                # SL or TP is also reachable within the same bar.
                if dir_ == 1:
                    sl_hit = l <= sl
                    tp_hit = h >= tp
                else:
                    sl_hit = h >= sl
                    tp_hit = l <= tp

                if sl_hit or tp_hit:
                    if sl_hit and tp_hit:
                        exit_price, result = sl, "SL"
                    elif tp_hit:
                        exit_price, result = tp, "TP"
                    else:
                        exit_price, result = sl, "SL"

                    pnl     = _calc_pnl(dir_, entry, exit_price, lots, symbol)
                    equity += pnl

                    trades.append({
                        "entry_time"  : ts,
                        "exit_time"   : ts,
                        "direction"   : "BUY"  if dir_ == 1 else "SELL",
                        "entry_price" : round(entry, 5),
                        "sl"          : round(sl, 5),
                        "tp"          : round(tp, 5),
                        "exit_price"  : round(exit_price, 5),
                        "lots"        : lots,
                        "result"      : result,
                        "pnl"         : round(pnl, 2),
                        "equity"      : round(equity, 2),
                        "bars_held"   : 0,
                    })
                    position = None

        # ── Record equity at end of this bar ──────────────────────────────────
        eq_vals[i] = equity

        # ── STEP 3: read signal from THIS bar (actionable only next bar) ──────
        # Mirrors: current_row = strategy_df.iloc[-2]  in live_trader.
        # We set `pending` here; it cannot be filled until the NEXT iteration.
        if position is not None:
            # In a live trade – don't place new pending (mirrors has_open_positions guard)
            continue

        entry_level = bar.get("entry_level", np.nan)
        setup_dir   = bar.get("setup_dir",   np.nan)
        sl_level    = bar.get("sl_level",    np.nan)
        tp_level    = bar.get("tp_level",    np.nan)

        if pd.isna(entry_level) or pd.isna(setup_dir) or \
           pd.isna(sl_level)    or pd.isna(tp_level):
            # No valid signal on this bar; existing pending survives unchanged.
            continue

        # Determine direction
        new_dir = 1 if int(setup_dir) == 1 else -1

        if invert_trades:
            # ── INVERSION (the live_trader change) ────────────────────────────
            # Flip direction AND swap SL/TP so geometry stays valid.
            new_dir               = -new_dir
            sl_level, tp_level    = tp_level, sl_level

        # Geometry validation (mirrors execute_trade checks)
        valid = (
            (new_dir ==  1 and sl_level < entry_level < tp_level) or
            (new_dir == -1 and tp_level < entry_level < sl_level)
        )
        if not valid:
            # Signal geometry is broken after inversion; skip it.
            continue

        lots = _calc_lots(float(entry_level), float(sl_level), risk_dollars, symbol)

        # Replace any existing pending (mirrors sync_pending_order replacing stale orders)
        pending = {
            "dir"       : new_dir,
            "entry"     : float(entry_level),
            "sl"        : float(sl_level),
            "tp"        : float(tp_level),
            "lots"      : lots,
            "signal_bar": i,
        }

    # ── Build outputs ─────────────────────────────────────────────────────────
    trades_df    = pd.DataFrame(trades)
    equity_curve = pd.Series(eq_vals, index=strategy_df.index, name="equity")

    return trades_df, equity_curve


# ── Summary & reporting ───────────────────────────────────────────────────────

def print_summary(trades_df: pd.DataFrame, equity_curve: pd.Series):
    sep = "=" * 54
    print(f"\n{sep}")
    print(f"  Backtest Results  ·  {SYMBOL}")
    print(sep)

    if trades_df.empty:
        print("  No trades taken during the backtest period.")
        print(sep)
        return

    total  = len(trades_df)
    wins   = trades_df[trades_df["result"] == "TP"]
    losses = trades_df[trades_df["result"] == "SL"]
    wr     = len(wins) / total * 100
    net    = trades_df["pnl"].sum()
    avg_w  = wins["pnl"].mean()   if len(wins)   else 0.0
    avg_l  = losses["pnl"].mean() if len(losses) else 0.0
    rr     = abs(avg_w / avg_l)   if avg_l != 0  else float("inf")

    # Max drawdown
    peak     = equity_curve.cummax()
    dd       = (equity_curve - peak) / peak * 100
    max_dd   = dd.min()

    # Profit factor
    gross_profit = wins["pnl"].sum()   if len(wins)   else 0.0
    gross_loss   = abs(losses["pnl"].sum()) if len(losses) else 0.0
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    avg_bars = trades_df["bars_held"].mean()

    print(f"  Period              : {equity_curve.index[0].date()} → {equity_curve.index[-1].date()}")
    print(f"  Total trades        : {total}")
    print(f"  Win rate            : {wr:.1f}%  ({len(wins)}W / {len(losses)}L)")
    print(f"  Net PnL             : ${net:,.2f}")
    print(f"  Profit factor       : {pf:.2f}")
    print(f"  Avg R:R (realised)  : 1:{rr:.2f}")
    print(f"  Avg win             : ${avg_w:,.2f}")
    print(f"  Avg loss            : ${avg_l:,.2f}")
    print(f"  Max drawdown        : {max_dd:.2f}%")
    print(f"  Avg bars held       : {avg_bars:.1f}")
    print(f"  Starting equity     : ${INITIAL_BALANCE:,.2f}")
    print(f"  Final equity        : ${equity_curve.dropna().iloc[-1]:,.2f}")
    print(sep)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    if not initialize_mt5():
        sys.exit(1)

    end_date        = datetime.utcnow()
    etf_start       = end_date - timedelta(days=ETF_LOOKBACK_DAYS)
    htf_start       = end_date - timedelta(days=ETF_LOOKBACK_DAYS + HTF_WARMUP_DAYS)
    etf_warmup_td   = timedelta(minutes=ETF_WARMUP_CANDLES * 15)
    etf_fetch_start = etf_start - etf_warmup_td

    print(f"\nFetching {ETF_LOOKBACK_DAYS}-day window for {SYMBOL}...")
    htf_data = get_data(SYMBOL, HTF, htf_start, end_date)
    etf_data = get_data(SYMBOL, ETF, etf_fetch_start, end_date)

    if htf_data is None or etf_data is None or htf_data.empty or etf_data.empty:
        print("ERROR: Failed to fetch market data. Aborting.")
        sys.exit(1)

    print("Running strategy signal generation...")
    strategy_df = generate_signals_refined(
        htf_data,
        etf_data,
        anchor_swing_window   = HTF_WINDOW,
        execution_swing_window= ETF_WINDOW,
        entry_retracement     = ENTRY_RETRACEMENT,
    )

    # Trim warmup – mirrors live_trader's: strategy_df[strategy_df.index >= etf_start]
    strategy_df = strategy_df[strategy_df.index >= pd.Timestamp(etf_start)]
    print(f"Backtesting on {len(strategy_df):,} bars...")

    trades_df, equity_curve = run_backtest(
        strategy_df,
        symbol       = SYMBOL,
        risk_dollars = RISK_DOLLARS,
        invert_trades= True,   # set False to test original (non-inverted) direction
    )

    print_summary(trades_df, equity_curve)

    # ── Save outputs ──────────────────────────────────────────────────────────
    trades_df.to_csv("backtest_trades.csv", index=False)
    equity_curve.to_csv("backtest_equity.csv", header=True)
    print("Saved → backtest_trades.csv")
    print("Saved → backtest_equity.csv")


if __name__ == "__main__":
    main()