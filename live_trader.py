import time
import logging
import pytz
from datetime import datetime, timedelta
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
POLL_INTERVAL_SEC = 60 * 5  # Check every 5 minutes (or 15 mins)

# ──────────────────────────────────────────────────────────────────────────────

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
    
    lots = calculate_lot_size(symbol, price, sl, risk_dollars)
    
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
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    
    logging.info(f"Sending Order: {request}")
    
    result = mt5.order_send(request)
    
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        logging.error(f"Order failed, retcode={result.retcode}: {mt5.last_error()}")
        return False
        
    logging.info(f"Order Successfully Placed! Ticket: {result.order}")
    return True

def analyze_and_trade():
    """Main logic execution for a single polling iteration."""
    logging.info("Waking up to check market conditions...")
    
    if not initialize_mt5():
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
        
    # Check the latest available ETF row
    current_row = strategy_df.iloc[-1]
    
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
        
    # Current MT5 price
    bid, ask = get_current_price(SYMBOL)
    if bid is None:
        return
        
    # Evaluate Entry
    if htf_trend == 1:
        # Uptrend: Look for retracement DOWN to Fib level
        entry_level = sh - (swing_range * FIB_LEVEL)
        
        # Has price retraced enough?
        if bid <= entry_level:
            logging.info(f"Long condition met! Price {bid} <= Entry {entry_level:.2f}")
            execute_trade(SYMBOL, 1, bid, sl, sh, RISK_DOLLARS)
        else:
            logging.info(f"Uptrend active. Waiting for pullback to {entry_level:.2f}. Current Bid: {bid:.2f}")
            
    elif htf_trend == -1:
        # Downtrend: Look for retracement UP to Fib level
        entry_level = sl + (swing_range * FIB_LEVEL)
        
        # Has price retraced enough?
        if ask >= entry_level:
            logging.info(f"Short condition met! Price {ask} >= Entry {entry_level:.2f}")
            execute_trade(SYMBOL, -1, ask, sh, sl, RISK_DOLLARS)
        else:
            logging.info(f"Downtrend active. Waiting for pullback to {entry_level:.2f}. Current Ask: {ask:.2f}")

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
