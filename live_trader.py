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
SYMBOL = "XAUUSD"
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
    """Check if the bot currently has any open positions or pending orders."""
    positions = mt5.positions_get(symbol=symbol)
    if positions is None:
        return False
        
    for pos in positions:
        if pos.magic == magic_number:
            return True
            
    # Also check pending orders
    orders = mt5.orders_get(symbol=symbol)
    if orders:
        for order in orders:
            if order.magic == magic_number:
                return True
                
    return False

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
    Send a market order to MT5 with SL and TP.
    """
    # 1 check if symbol is available
    symbol_info = mt5.symbol_info(symbol)
    if not symbol_info.visible:
        if not mt5.symbol_select(symbol, True):
            logging.error(f"Failed to select symbol {symbol}")
            return False
            
    # Get current price
    bid, ask = get_current_price(symbol)
    if bid is None:
        return False
        
    order_type = mt5.ORDER_TYPE_BUY if action == 1 else mt5.ORDER_TYPE_SELL
    price = ask if action == 1 else bid
    
    # Check if the theoretical entry price has already been surpassed
    # For a limit order logic (retracement), if price is already past the entry level 
    # we just take a market order. If it hasn't reached it, we should place a Limit order.
    # For simplicity of this live execution, we assume the signal means "entry condition met NOW".
    
    # Use the theoretical entry level for risk sizing so that live trades
    # match the backtest R:R profile as closely as possible.
    lots = calculate_lot_size(symbol, entry, sl, risk_dollars)
    
    # --- Validate/adjust stops to avoid TRADE_RETCODE_INVALID_STOPS (10016)
    # For BUY:  sl < price < tp
    # For SELL: tp < price < sl
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        logging.error(f"Symbol info unavailable for {symbol}. Cannot validate stops.")
        return False

    digits = getattr(symbol_info, "digits", 2)
    point = getattr(symbol_info, "point", 0.01)
    stops_level_points = getattr(symbol_info, "trade_stops_level", 0) or 0
    min_stop_dist = stops_level_points * point

    sl = float(sl)
    tp = float(tp)

    def _round(x: float) -> float:
        return float(round(x, digits))

    if action == 1:
        # BUY
        if not (sl < price < tp):
            logging.error(
                f"Skipping BUY: invalid stop sides. Need sl < price < tp, got "
                f"price={price}, sl={sl}, tp={tp}."
            )
            return False

        # Enforce broker minimum stop distance (if any)
        if min_stop_dist > 0:
            if (price - sl) < min_stop_dist:
                sl = price - min_stop_dist
            if (tp - price) < min_stop_dist:
                tp = price + min_stop_dist

    else:
        # SELL
        if not (tp < price < sl):
            logging.error(
                f"Skipping SELL: invalid stop sides. Need tp < price < sl, got "
                f"price={price}, sl={sl}, tp={tp}."
            )
            return False

        if min_stop_dist > 0:
            if (sl - price) < min_stop_dist:
                sl = price + min_stop_dist
            if (price - tp) < min_stop_dist:
                tp = price - min_stop_dist

    sl = _round(sl)
    tp = _round(tp)

    # Pick a filling mode that the symbol supports, if available.
    # Some brokers/symbols reject IOC; in that case this will fall back gracefully.
    filling_mode = mt5.ORDER_FILLING_IOC
    if symbol_info is not None:
        fm = getattr(symbol_info, "filling_mode", None)
        if fm in (mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN):
            filling_mode = fm

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": float(lots),
        "type": order_type,
        "price": price,
        "sl": float(sl),
        "tp": float(tp),
        "deviation": 20,
        "magic": MAGIC_NUMBER,
        "comment": "Fib Bot Trade",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": filling_mode,
    }
    
    logging.info(f"Sending Order: {request}")
    
    result = mt5.order_send(request)
    
    if result.retcode != mt5.TRADE_RETCODE_DONE:
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

    logging.info(f"Order Successfully Placed! Ticket: {result.order}")

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
                "BUY" if action == 1 else "SELL",
                float(lots),
                float(price),
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
        
    # Prevent overlapping trades
    if has_open_positions(SYMBOL, MAGIC_NUMBER):
        logging.info("Currently in an active position. Waiting for outcome.")
        return
        
    # Fetch Data (We need enough for the H4 7-window rolling logic + ETF 3-window)
    # A 6-month lookback is more than enough to establish trends
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=60)
    
    htf_data = get_data(SYMBOL, HTF, start_date, end_date)
    etf_data = get_data(SYMBOL, ETF, start_date, end_date)
    
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
        logging.info("Market is ranging or establishing swings. No trade.")
        return
        
    swing_range = sh - sl
    if swing_range <= 0:
        return
        
    # Evaluate Entry based on the most recent CLOSED ETF candle, mirroring backtest logic
    candle_open = current_row['open']
    candle_low = current_row['low']
    candle_high = current_row['high']
    
    if htf_trend == 1:
        # Uptrend: Look for retracement DOWN to Fib level
        entry_level = sh - (swing_range * FIB_LEVEL)
        
        # Backtest condition: candle_low <= entry_level and candle_open > entry_level
        if candle_low <= entry_level and candle_open > entry_level:
            # If the same candle broke below swing low, structure is invalidated – skip this signal
            if candle_low <= sl:
                logging.info(
                    f"Long signal invalidated on closed candle: low broke below swing low. "
                    f"Low={candle_low:.2f} <= SL(swing low)={sl:.2f}. Waiting for new structure."
                )
                return

            # Now get current market price to actually execute.
            # Require price to still be reasonably close to the theoretical entry level;
            # otherwise we consider the trade "missed" and skip it.
            bid, ask = get_current_price(SYMBOL)
            if bid is None:
                return

            # If price has already moved too far away from the fib entry level,
            # skip the trade instead of chasing and destroying R:R.
            symbol_info = mt5.symbol_info(SYMBOL)
            if symbol_info is None:
                logging.error(f"Symbol info unavailable for {SYMBOL}. Skipping.")
                return

            point = getattr(symbol_info, "point", 0.01)
            max_slippage_points = 10  # configurable: max distance from entry_level
            if abs(bid - entry_level) > max_slippage_points * point:
                logging.info(
                    f"Long signal missed: current price too far from entry level. "
                    f"EntryLevel={entry_level:.2f}, Bid={bid:.2f}, "
                    f"MaxSlippagePoints={max_slippage_points}"
                )
                return

            # Broker stop constraints (minimum SL/TP distance from current price)
            stops_level_points = getattr(symbol_info, "trade_stops_level", 0) or 0
            min_stop_dist = stops_level_points * point

            # Ensure stops are not too close for the broker
            if min_stop_dist > 0 and ((ask - sl) < min_stop_dist or (sh - ask) < min_stop_dist):
                logging.info(
                    f"Long signal skipped: stops too close for broker. "
                    f"MinStop={min_stop_dist:.5f}, ask-sl={(ask - sl):.5f}, tp-ask={(sh - ask):.5f}."
                )
                return

            logging.info(
                f"Long condition met on closed candle. "
                f"Open={candle_open:.2f}, Low={candle_low:.2f}, EntryLevel={entry_level:.2f}, "
                f"CurrentBid={bid:.2f}, CurrentAsk={ask:.2f}"
            )
            execute_trade(SYMBOL, 1, entry_level, sl, sh, RISK_DOLLARS)
        else:
            logging.info(
                f"Uptrend active. No long trigger on last closed candle. "
                f"EntryLevel={entry_level:.2f}, CandleOpen={candle_open:.2f}, CandleLow={candle_low:.2f}"
            )
            
    elif htf_trend == -1:
        # Downtrend: Look for retracement UP to Fib level
        entry_level = sl + (swing_range * FIB_LEVEL)
        
        # Backtest condition: candle_high >= entry_level and candle_open < entry_level
        if candle_high >= entry_level and candle_open < entry_level:
            # If the same candle broke above swing high, structure is invalidated – skip this signal
            if candle_high >= sh:
                logging.info(
                    f"Short signal invalidated on closed candle: high broke above swing high. "
                    f"High={candle_high:.2f} >= SL(swing high)={sh:.2f}. Waiting for new structure."
                )
                return

            bid, ask = get_current_price(SYMBOL)
            if bid is None:
                return

            symbol_info = mt5.symbol_info(SYMBOL)
            if symbol_info is None:
                logging.error(f"Symbol info unavailable for {SYMBOL}. Skipping.")
                return

            point = getattr(symbol_info, "point", 0.01)

            max_slippage_points = 10
            if abs(ask - entry_level) > max_slippage_points * point:
                logging.info(
                    f"Short signal missed: current price too far from entry level. "
                    f"EntryLevel={entry_level:.2f}, Ask={ask:.2f}, "
                    f"MaxSlippagePoints={max_slippage_points}"
                )
                return
            stops_level_points = getattr(symbol_info, "trade_stops_level", 0) or 0
            min_stop_dist = stops_level_points * point

            if min_stop_dist > 0 and ((sh - bid) < min_stop_dist or (bid - sl) < min_stop_dist):
                logging.info(
                    f"Short signal skipped: stops too close for broker. "
                    f"MinStop={min_stop_dist:.5f}, sl-bid={(sh - bid):.5f}, bid-tp={(bid - sl):.5f}."
                )
                return

            logging.info(
                f"Short condition met on closed candle. "
                f"Open={candle_open:.2f}, High={candle_high:.2f}, EntryLevel={entry_level:.2f}, "
                f"CurrentBid={bid:.2f}, CurrentAsk={ask:.2f}"
            )
            execute_trade(SYMBOL, -1, entry_level, sh, sl, RISK_DOLLARS)
        else:
            logging.info(
                f"Downtrend active. No short trigger on last closed candle. "
                f"EntryLevel={entry_level:.2f}, CandleOpen={candle_open:.2f}, CandleHigh={candle_high:.2f}"
            )

def main():
    logging.info("Starting Live Trader Bot...")
    
    while True:
        try:
            analyze_and_trade()
        except Exception as e:
            logging.error(f"Fatal error in main loop: {e}")
            
        logging.info(f"Sleeping for {POLL_INTERVAL_SEC / 60} minutes...")
        time.sleep(POLL_INTERVAL_SEC)

if __name__ == "__main__":
    main()
