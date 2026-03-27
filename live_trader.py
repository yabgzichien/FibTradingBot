import time
import logging
import pytz
from datetime import datetime, timedelta
import os
import csv
import pandas as pd
import numpy as np
import MetaTrader5 as mt5

from data_loader import initialize_mt5, get_data
from strategy import generate_signals

# Configure standard logging to console and file
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("live_trader.log"),
        logging.StreamHandler()
    ]
)

# ── Configuration ─────────────────────────────────────────────────────────────
SYMBOL = "BTCUSD"
HTF = "H4"
ETF = "M15"

# Rank 1 Strategy Parameters
FIB_LEVEL = 0.786
HTF_WINDOW = 7
ETF_WINDOW = 3

# Risk Management
INITIAL_BALANCE = 10000.0
RISK_PER_TRADE_PCT = 0.01  # 1% of initial balance = $100 Risk per trade
RISK_DOLLARS = INITIAL_BALANCE * RISK_PER_TRADE_PCT

# MT5 Details
MAGIC_NUMBER = 1001001  # Unique identifier for trades placed by this bot
POLL_INTERVAL_SEC = 60  # Check every 1 minute
ORDER_REPLACE_TOL_POINTS = 10  # Reuse existing pending if within this tolerance
ETF_LOOKBACK_DAYS = 7          # How many days of M15 data to fetch
HTF_WARMUP_DAYS = 60           # Extra H4 history for swing/trend warm-up

# ──────────────────────────────────────────────────────────────────────────────

def _autotrading_preflight_check():
    """
    Validate MT5 terminal/account state required for trading.
    Returns True if trading should be possible, else False with a clear log message.
    """
    term = mt5.terminal_info()
    acct = mt5.account_info()

    if term is None:
        logging.error("MT5 terminal_info() returned None. Is MT5 initialized and running?")
        return False

    # In MT5 retcode terms:
    # 10027 = TRADE_RETCODE_CLIENT_DISABLES_AT (autotrading disabled by client terminal)
    if hasattr(term, "trade_allowed") and not term.trade_allowed:
        logging.error(
            "Autotrading is DISABLED in the MT5 terminal (retcode 10027). "
            "Fix: enable the 'Algo Trading/AutoTrading' button in MT5 and ensure "
            "Tools -> Options -> Expert Advisors -> 'Allow algorithmic trading' is checked."
        )
        return False

    if acct is None:
        logging.error("MT5 account_info() returned None. Are you logged in to a trading account?")
        return False

    if hasattr(acct, "trade_allowed") and not acct.trade_allowed:
        logging.error(
            "Trading is not allowed for the current account. Check broker permissions / account type."
        )
        return False

    return True


def get_current_price(symbol):
    """Get the current bid/ask price for a symbol."""
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        logging.error(f"Failed to get tick for {symbol}")
        return None, None
    return tick.bid, tick.ask

def has_open_positions(symbol, magic_number):
    """Check if the bot currently has any open market positions."""
    positions = mt5.positions_get(symbol=symbol)
    if positions is None:
        return False
        
    for pos in positions:
        if pos.magic == magic_number:
            return True

    return False


def get_bot_pending_orders(symbol, magic_number):
    """Return pending orders for this symbol and bot magic."""
    orders = mt5.orders_get(symbol=symbol)
    if not orders:
        return []
    return [o for o in orders if getattr(o, "magic", None) == magic_number]


def cancel_pending_order(order_ticket):
    """Cancel a pending order by ticket."""
    request = {
        "action": mt5.TRADE_ACTION_REMOVE,
        "order": int(order_ticket),
    }
    result = mt5.order_send(request)
    if result is None:
        logging.error(f"Failed to cancel pending order {order_ticket}: order_send returned None")
        return False
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        logging.error(
            f"Failed to cancel pending order {order_ticket}. "
            f"retcode={result.retcode}, comment={getattr(result, 'comment', '')}"
        )
        return False
    logging.info(f"Canceled stale pending order ticket={order_ticket}")
    return True


def cancel_all_bot_pending_orders(symbol, magic_number, reason=""):
    """Cancel all pending orders belonging to this bot for this symbol."""
    pending = get_bot_pending_orders(symbol, magic_number)
    if not pending:
        return
    if reason:
        logging.info(f"Cancelling {len(pending)} pending order(s): {reason}")
    for order in pending:
        cancel_pending_order(order.ticket)


def sync_pending_order(symbol, action, entry, sl, tp, risk_dollars):
    """
    Keep one up-to-date pending order:
    - if an equivalent pending exists, keep it
    - otherwise cancel stale pendings and place a new one
    """
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        logging.error(f"Symbol info unavailable for {symbol}. Cannot sync pending orders.")
        return False

    point = getattr(symbol_info, "point", 0.01)
    tol = ORDER_REPLACE_TOL_POINTS * point
    desired_type = mt5.ORDER_TYPE_BUY_LIMIT if action == 1 else mt5.ORDER_TYPE_SELL_LIMIT

    pending = get_bot_pending_orders(symbol, MAGIC_NUMBER)
    for order in pending:
        if (
            order.type == desired_type
            and abs(float(order.price_open) - float(entry)) <= tol
            and abs(float(order.sl) - float(sl)) <= tol
            and abs(float(order.tp) - float(tp)) <= tol
        ):
            logging.info(
                f"Keeping existing pending order ticket={order.ticket} (already close to desired setup)."
            )
            return True

    # No equivalent order found -> cancel stale and replace
    if pending:
        cancel_all_bot_pending_orders(
            symbol, MAGIC_NUMBER, reason="setup changed; replacing with latest structure"
        )

    return execute_trade(symbol, action, entry, sl, tp, risk_dollars)

def calculate_lot_size(symbol, entry_price, sl_price, risk_dollars):
    """
    Calculate the appropriate lot size based on fixed dollar risk and SL distance.
    This assumes account currency = quote currency (e.g. USD account, XAUUSD pair).
    """
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        logging.error(f"Symbol {symbol} not found.")
        return 0.01
        
    # Distance in points
    sl_points = abs(entry_price - sl_price) / symbol_info.point
    
    # Tick value is the value of 1 point move for 1 standard lot
    tick_value = symbol_info.trade_tick_value
    tick_size = symbol_info.trade_tick_size
    
    if sl_points == 0 or tick_value == 0:
        return symbol_info.volume_min
        
    # Risk = Lots * sl_points * tick_value
    # Lots = Risk / (sl_points * tick_value)
    lots = risk_dollars / (sl_points * tick_value)
    
    # Round to nearest allowed volume step
    step = symbol_info.volume_step
    lots = round(lots / step) * step
    
    # Bound by min/max lots
    lots = max(lots, symbol_info.volume_min)
    lots = min(lots, symbol_info.volume_max)
    
    return lots

def execute_trade(symbol, action, entry, sl, tp, risk_dollars):
    """
    Place a pending LIMIT order in MT5 with SL and TP.
    action: 1 = long (BUY_LIMIT), -1 = short (SELL_LIMIT)
    """
    # 1) Ensure symbol is available
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        logging.error(f"Symbol info unavailable for {symbol}.")
        return False
    if not symbol_info.visible:
        if not mt5.symbol_select(symbol, True):
            logging.error(f"Failed to select symbol {symbol}")
            return False

    # 2) Current price needed to validate pending order side
    bid, ask = get_current_price(symbol)
    if bid is None:
        return False

    # 3) Risk size from theoretical entry/SL
    lots = calculate_lot_size(symbol, entry, sl, risk_dollars)

    digits = getattr(symbol_info, "digits", 2)
    point = getattr(symbol_info, "point", 0.01)
    stops_level_points = getattr(symbol_info, "trade_stops_level", 0) or 0
    min_stop_dist = stops_level_points * point

    entry = float(entry)
    sl = float(sl)
    tp = float(tp)

    def _round(x: float) -> float:
        return float(round(x, digits))

    if action == 1:
        # BUY_LIMIT: sl < entry < tp and entry should be below current ask.
        if not (sl < entry < tp):
            logging.error(
                f"Skipping BUY_LIMIT: invalid levels. Need sl < entry < tp, got "
                f"entry={entry}, sl={sl}, tp={tp}."
            )
            return False
        if entry >= ask:
            logging.info(
                f"Skipping BUY_LIMIT: entry is not below current ask. "
                f"entry={entry:.2f}, ask={ask:.2f}"
            )
            return False

        # Enforce broker minimum stop distance from ENTRY for pending order.
        if min_stop_dist > 0:
            if (entry - sl) < min_stop_dist:
                sl = entry - min_stop_dist
            if (tp - entry) < min_stop_dist:
                tp = entry + min_stop_dist

        order_type = mt5.ORDER_TYPE_BUY_LIMIT

    else:
        # SELL_LIMIT: tp < entry < sl and entry should be above current bid.
        if not (tp < entry < sl):
            logging.error(
                f"Skipping SELL_LIMIT: invalid levels. Need tp < entry < sl, got "
                f"entry={entry}, sl={sl}, tp={tp}."
            )
            return False
        if entry <= bid:
            logging.info(
                f"Skipping SELL_LIMIT: entry is not above current bid. "
                f"entry={entry:.2f}, bid={bid:.2f}"
            )
            return False

        if min_stop_dist > 0:
            if (sl - entry) < min_stop_dist:
                sl = entry + min_stop_dist
            if (entry - tp) < min_stop_dist:
                tp = entry - min_stop_dist

        order_type = mt5.ORDER_TYPE_SELL_LIMIT

    entry = _round(entry)
    sl = _round(sl)
    tp = _round(tp)

    # For pending orders, RETURN is generally the safest/default filling mode.
    filling_mode = mt5.ORDER_FILLING_RETURN
    if symbol_info is not None:
        fm = getattr(symbol_info, "filling_mode", None)
        if fm in (mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN):
            filling_mode = fm

    request = {
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": symbol,
        "volume": float(lots),
        "type": order_type,
        "price": entry,
        "sl": float(sl),
        "tp": float(tp),
        "magic": MAGIC_NUMBER,
        "comment": "Fib Bot Trade",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": filling_mode,
    }
    
    logging.info(f"Sending Order: {request}")
    
    result = mt5.order_send(request)
    if result.retcode not in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED):
        # mt5.last_error() is often unrelated here; retcode/comment is the real reason.
        try:
            details = result._asdict()
        except Exception:
            details = {"retcode": getattr(result, "retcode", None), "comment": getattr(result, "comment", None)}

        logging.error(
            "Order failed. "
            f"retcode={getattr(result, 'retcode', None)} "
            f"comment={getattr(result, 'comment', None)} "
            f"request={request} "
            f"details={details}"
        )
        return False

    logging.info(
        f"Pending order placed successfully. Ticket: {result.order} | "
        f"Type: {'BUY_LIMIT' if action == 1 else 'SELL_LIMIT'} | Entry: {entry}"
    )

    # Append basic trade information to CSV for later analysis
    try:
        csv_path = "live_trades.csv"
        file_exists = os.path.exists(csv_path)
        with open(csv_path, mode="a", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow([
                    "timestamp",
                    "symbol",
                    "ticket",
                    "type",
                    "lots",
                    "entry_price",
                    "sl",
                    "tp",
                    "htf_trend",
                    "entry_level_theoretical"
                ])
            writer.writerow([
                datetime.utcnow().isoformat(),
                symbol,
                getattr(result, "order", 0),
                "BUY_LIMIT" if action == 1 else "SELL_LIMIT",
                float(lots),
                float(entry),
                float(sl),
                float(tp),
                # We cannot easily access htf_trend here; store 0 as placeholder.
                0,
                float(entry),
            ])
    except Exception as e:
        logging.error(f"Failed to append live trade to CSV: {e}")

    return True

def analyze_and_trade():
    """Main logic execution for a single polling iteration."""
    logging.info("Waking up to check market conditions...")
    
    if not initialize_mt5():
        return

    if not _autotrading_preflight_check():
        return
        
    # Prevent overlapping trades: if in a live position, cancel stale pendings and wait.
    if has_open_positions(SYMBOL, MAGIC_NUMBER):
        cancel_all_bot_pending_orders(
            SYMBOL, MAGIC_NUMBER, reason="open position exists; avoid stacking entries"
        )
        logging.info("Currently in an active position. Waiting for outcome.")
        return
        
    # H4 fetches extra history so swing detection / trend have enough warm-up candles.
    # M15 only needs the recent window — keeping it short avoids unnecessary data load.
    end_date = datetime.utcnow()
    etf_start = end_date - timedelta(days=ETF_LOOKBACK_DAYS)
    htf_start = end_date - timedelta(days=ETF_LOOKBACK_DAYS + HTF_WARMUP_DAYS)

    htf_data = get_data(SYMBOL, HTF, htf_start, end_date)
    etf_data = get_data(SYMBOL, ETF, etf_start, end_date)
    
    if htf_data is None or etf_data is None:
        logging.error("Failed to fetch data.")
        return
        
    # Generate signals
    try:
        strategy_df = generate_signals(htf_data, etf_data, FIB_LEVEL)
    except Exception as e:
        logging.error(f"Strategy generation failed: {e}")
        return
        
    # Get the MOST RECENT closed candle state
    if len(strategy_df) < 2:
        return
        
    # Use the most recently CLOSED ETF candle (avoid using the still-forming bar)
    current_row = strategy_df.iloc[-2]
    
    htf_trend = current_row.get('htf_trend', 0)
    sh = current_row['last_swing_high']
    sl = current_row['last_swing_low']
    
    # Are we in a trend?
    if htf_trend == 0 or pd.isna(sh) or pd.isna(sl):
        cancel_all_bot_pending_orders(SYMBOL, MAGIC_NUMBER, reason="no valid trend/swing setup")
        logging.info("Market is ranging or establishing swings. No trade.")
        return
        
    swing_range = sh - sl
    if swing_range <= 0:
        cancel_all_bot_pending_orders(SYMBOL, MAGIC_NUMBER, reason="invalid swing range")
        return
        
    # Evaluate/plan entry based on current HTF trend and latest swing structure.
    # In limit-order mode, we place a resting order at fib entry level.
    if htf_trend == 1:
        # Uptrend: plan BUY_LIMIT at fib retracement level.
        entry_level = sh - (swing_range * FIB_LEVEL)
        bid, ask = get_current_price(SYMBOL)
        if bid is None:
            return

        # If price is already below/at entry, BUY_LIMIT is no longer appropriate.
        if entry_level >= ask:
            cancel_all_bot_pending_orders(
                SYMBOL, MAGIC_NUMBER, reason="buy limit no longer placeable at current price"
            )
            logging.info(
                f"Uptrend setup found, but BUY_LIMIT not placeable now. "
                f"Entry={entry_level:.2f} is not below Ask={ask:.2f}. Waiting for refreshed structure."
            )
            return

        logging.info(
            f"Placing BUY_LIMIT setup. Entry={entry_level:.2f}, SL={sl:.2f}, TP={sh:.2f}, "
            f"CurrentBid={bid:.2f}, CurrentAsk={ask:.2f}"
        )
        sync_pending_order(SYMBOL, 1, entry_level, sl, sh, RISK_DOLLARS)
            
    elif htf_trend == -1:
        # Downtrend: plan SELL_LIMIT at fib retracement level.
        entry_level = sl + (swing_range * FIB_LEVEL)
        bid, ask = get_current_price(SYMBOL)
        if bid is None:
            return

        # If price is already above/at entry, SELL_LIMIT is no longer appropriate.
        if entry_level <= bid:
            cancel_all_bot_pending_orders(
                SYMBOL, MAGIC_NUMBER, reason="sell limit no longer placeable at current price"
            )
            logging.info(
                f"Downtrend setup found, but SELL_LIMIT not placeable now. "
                f"Entry={entry_level:.2f} is not above Bid={bid:.2f}. Waiting for refreshed structure."
            )
            return

        logging.info(
            f"Placing SELL_LIMIT setup. Entry={entry_level:.2f}, SL={sh:.2f}, TP={sl:.2f}, "
            f"CurrentBid={bid:.2f}, CurrentAsk={ask:.2f}"
        )
        sync_pending_order(SYMBOL, -1, entry_level, sh, sl, RISK_DOLLARS)

def main():
    logging.info("Starting Live Trader Bot...")
    
    while True:
        try:
            analyze_and_trade()
        except Exception as e:
            logging.error(f"Fatal error in main loop: {e}")
        
        logging.info("Pairs: BTCUSD")
        logging.info(f"Sleeping for {POLL_INTERVAL_SEC / 60} minutes...")
        time.sleep(POLL_INTERVAL_SEC)

if __name__ == "__main__":
    main()
