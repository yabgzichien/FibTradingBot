import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime
import pytz

def initialize_mt5():
    if not mt5.initialize():
        print(f"initialize() failed, error code = {mt5.last_error()}")
        return False
    print(f"MetaTrader5 version {mt5.version()} connected.")
    return True

def get_data(symbol, timeframe, start_date, end_date):
    timezone = pytz.timezone("UTC")
    utc_from = timezone.localize(start_date)
    utc_to = timezone.localize(end_date)
     
    tf_map = {
        'M1': mt5.TIMEFRAME_M1,
        'M5': mt5.TIMEFRAME_M5,
        'M15': mt5.TIMEFRAME_M15,
        'H1': mt5.TIMEFRAME_H1,
        'H4': mt5.TIMEFRAME_H4,
        'D1': mt5.TIMEFRAME_D1
    }
    
    mt5_tf = tf_map.get(timeframe)
    if mt5_tf is None:
        raise ValueError(f"Invalid timeframe: {timeframe}")

    rates = mt5.copy_rates_range(symbol, mt5_tf, utc_from, utc_to)
    
    if rates is None or len(rates) == 0:
        print(f"Failed to fetch data for {symbol} on {timeframe}. Error: {mt5.last_error()}")
        return pd.DataFrame()
        
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    df.set_index('time', inplace=True)
    return df


def get_symbol_specs(symbol: str):
    info = mt5.symbol_info(symbol)
    if info is None:
        return None
    return {
        "point": float(getattr(info, "point", 0.0) or 0.0),
        "spread": float(getattr(info, "spread", 0.0) or 0.0),
        "trade_contract_size": float(getattr(info, "trade_contract_size", 0.0) or 0.0),
        "trade_tick_size": float(getattr(info, "trade_tick_size", 0.0) or 0.0),
        "trade_tick_value": float(getattr(info, "trade_tick_value", 0.0) or 0.0),
    }
