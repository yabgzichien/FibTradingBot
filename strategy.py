import pandas as pd
import numpy as np

def find_swings(df, window=5):
    """
    Finds swing highs and swing lows in a dataframe.
    A swing high is the highest high within a local window (before and after).
    A swing low is the lowest low within a local window.
    """
    df = df.copy()
    
    # Calculate rolling max and min
    rolling_max = df['high'].rolling(window=2*window+1, center=True).max()
    rolling_min = df['low'].rolling(window=2*window+1, center=True).min()
    
    # Identify swings
    df['is_swing_high'] = df['high'] == rolling_max
    df['is_swing_low'] = df['low'] == rolling_min
    
    # Fill boolean columns with False where NaN (due to rolling window)
    df['is_swing_high'] = df['is_swing_high'].fillna(False)
    df['is_swing_low'] = df['is_swing_low'].fillna(False)
    
    # Store the swing values
    df['swing_high_val'] = np.where(df['is_swing_high'], df['high'], np.nan)
    df['swing_low_val'] = np.where(df['is_swing_low'], df['low'], np.nan)
    
    # Forward fill the last swing high and low values
    df['last_swing_high'] = df['swing_high_val'].ffill()
    df['last_swing_low'] = df['swing_low_val'].ffill()
    
    return df

def determine_trend(df):
    """
    Determines the market structure trend based on recent swings.
    Uptrend: Higher Highs and Higher Lows
    Downtrend: Lower Highs and Lower Lows
    """
    df = df.copy()
    
    # Need to keep track of previous swings to determine trend
    highs = df[df['is_swing_high']]['high']
    lows = df[df['is_swing_low']]['low']
    
    # Create columns to store previous swing values
    df['prev_swing_high'] = highs.shift(1)
    df['prev_swing_low'] = lows.shift(1)
    
    # Forward fill so every row knows the current and previous swings
    df['prev_swing_high'] = df['prev_swing_high'].ffill()
    df['prev_swing_low'] = df['prev_swing_low'].ffill()
    
    # Basic logic: 
    # Uptrend = current swing high > previous swing high AND current swing low > previous swing low
    # Downtrend = current swing high < previous swing high AND current swing low < previous swing low
    # 1 for Uptrend, -1 for Downtrend, 0 for Neutral/Ranging
    
    conditions_up = (df['last_swing_high'] > df['prev_swing_high']) & (df['last_swing_low'] > df['prev_swing_low'])
    conditions_down = (df['last_swing_high'] < df['prev_swing_high']) & (df['last_swing_low'] < df['prev_swing_low'])
    
    df['trend'] = 0
    df.loc[conditions_up, 'trend'] = 1
    df.loc[conditions_down, 'trend'] = -1
    
    # Forward fill the trend so it persists until a new trend is established
    # Replace 0 with NaN first, then ffill, then fillna with 0 for the beginning
    df['trend'] = df['trend'].replace(0, np.nan)
    df['trend'] = df['trend'].ffill().fillna(0)
    
    return df

def generate_signals(htf_df, etf_df, fib_level=0.618):
    """
    Combines HTF trend and ETF swings to generate signals.
    """
    htf = find_swings(htf_df, window=7)
    htf = determine_trend(htf)
    
    etf = find_swings(etf_df, window=3) # optimized window for significant swings
    
    # We need to map the HTF trend to the ETF dataframe.
    # Since ETF is a lower timeframe, multiple ETF rows correspond to one HTF row.
    # We can use pd.merge_asof or reindex/ffill.
    
    htf_trend = htf[['trend']].rename(columns={'trend': 'htf_trend'})
    
    # Merge HTF trend into ETF based on time
    # ETF time must be sorted. We use merge_asof to get the latest available HTF trend for each ETF timestamp.
    etf = etf.sort_index()
    htf_trend = htf_trend.sort_index()
    
    combined = pd.merge_asof(etf, htf_trend, left_index=True, right_index=True, direction='backward')
    
    # Initialize signal columns
    combined['signal'] = 0  # 1 for Long, -1 for Short
    combined['entry_price'] = np.nan
    combined['sl'] = np.nan
    combined['tp'] = np.nan
    
    # Strategy Logic:
    # If HTF in Uptrend (1): look for Long
    #   We need an Impulse move up (from swing low to swing high)
    #   Calculate 61.8% retracement level down from the high
    #   If current price touches that level -> Long. TP = last_swing_high, SL = last_swing_low
    
    # If HTF in Downtrend (-1): look for Short
    #   We need an Impulse move down (from swing high to swing low)
    #   Calculate 61.8% retracement level up from the low
    #   If current price touches that level -> Short. TP = last_swing_low, SL = last_swing_high
    
    # This is a vectorized approximation. A loop is better for exact execution but slower.
    # For backtesting, we'll implement a loop in the backtest engine that uses these pre-calculated swings and trends.
    
    return combined

