import pandas as pd
import numpy as np

def find_swings(df, window=5):
    """
    Finds swing highs and swing lows in a dataframe.
    A swing high is the highest high within a local window (before and after).
    A swing low is the lowest low within a local window.
    """
    df = df.copy()
    
    rolling_max = df['high'].rolling(window=2*window+1, center=True).max()
    rolling_min = df['low'].rolling(window=2*window+1, center=True).min()
    
    is_swing_high = (df['high'] == rolling_max)
    is_swing_low = (df['low'] == rolling_min)
    
    df['is_swing_high'] = is_swing_high.shift(window).fillna(False)
    df['is_swing_low'] = is_swing_low.shift(window).fillna(False)
    
    df['swing_high_val'] = np.where(df['is_swing_high'], df['high'].shift(window), np.nan)
    df['swing_low_val'] = np.where(df['is_swing_low'], df['low'].shift(window), np.nan)
    
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
    htf_trend = htf_trend.sort_index().shift(1)
    
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

def generate_signals_refined(
    anchor_df,
    execution_df,
    anchor_swing_window=7,
    execution_swing_window=3,
    entry_retracement=0.71,
    sweep_mode="rolling",
    require_bias=True,
    sweep_lookback_bars=32,
    internal_structure_lookback_bars=16,
    max_bos_wait_bars=48,
    max_pending_bars=96
):
    """
    Refined strategy:
    - Anchor (higher TF) defines premium/discount bias using the midpoint (50%) of the latest swing range.
    - Execution (lower TF) waits for liquidity sweep, then BOS, then computes fib entry at entry_retracement.
    - Optionally tags confluence if the entry overlaps an FVG (3-candle imbalance).
    """
    anchor = find_swings(anchor_df, window=anchor_swing_window).sort_index()
    if sweep_mode == "swing":
        execution = find_swings(execution_df, window=execution_swing_window).sort_index()
    elif sweep_mode == "prev_bar":
        execution = execution_df.copy().sort_index()
    else:
        execution = execution_df.copy().sort_index()

    anchor_ctx = anchor[['last_swing_high', 'last_swing_low']].rename(
        columns={'last_swing_high': 'anchor_swing_high', 'last_swing_low': 'anchor_swing_low'}
    )
    anchor_ctx = anchor_ctx.sort_index().shift(1)
    combined = pd.merge_asof(
        execution,
        anchor_ctx,
        left_index=True,
        right_index=True,
        direction='backward'
    )

    anchor_range = combined['anchor_swing_high'] - combined['anchor_swing_low']
    combined['anchor_midpoint'] = combined['anchor_swing_low'] + (anchor_range * 0.5)
    combined['bias'] = np.where(combined['close'] > combined['anchor_midpoint'], -1, 1)

    combined['setup_id'] = np.nan
    combined['setup_dir'] = np.nan
    combined['sweep_time'] = pd.NaT
    combined['sweep_price'] = np.nan
    combined['bos_time'] = pd.NaT
    combined['bos_price'] = np.nan
    combined['entry_level'] = np.nan
    combined['sl_level'] = np.nan
    combined['tp_level'] = np.nan
    combined['fvg_low'] = np.nan
    combined['fvg_high'] = np.nan
    combined['entry_in_fvg'] = False

    active = None
    next_setup_id = 1

    highs = combined['high'].to_numpy()
    lows = combined['low'].to_numpy()
    closes = combined['close'].to_numpy()
    bias = combined['bias'].to_numpy()
    idx = combined.index
    internal_high = (
        combined['high']
        .shift(1)
        .rolling(window=internal_structure_lookback_bars, min_periods=internal_structure_lookback_bars)
        .max()
        .to_numpy()
    )
    internal_low = (
        combined['low']
        .shift(1)
        .rolling(window=internal_structure_lookback_bars, min_periods=internal_structure_lookback_bars)
        .min()
        .to_numpy()
    )

    if sweep_mode == "swing":
        last_sh = combined['last_swing_high'].to_numpy()
        last_sl = combined['last_swing_low'].to_numpy()
        roll_min_low = None
        roll_max_high = None
    else:
        last_sh = None
        last_sl = None
        if sweep_mode == "prev_bar":
            roll_min_low = None
            roll_max_high = None
        else:
            roll_min_low = (
                combined['low']
                .shift(1)
                .rolling(window=sweep_lookback_bars, min_periods=sweep_lookback_bars)
                .min()
                .to_numpy()
            )
            roll_max_high = (
                combined['high']
                .shift(1)
                .rolling(window=sweep_lookback_bars, min_periods=sweep_lookback_bars)
                .max()
                .to_numpy()
            )
        internal_high = internal_high
        internal_low = internal_low

    def _find_last_fvg(start_i: int, end_i: int, direction: int):
        fvg_low = np.nan
        fvg_high = np.nan
        if end_i - start_i < 2:
            return fvg_low, fvg_high
        for j in range(start_i + 2, end_i + 1):
            if direction == 1:
                if lows[j] > highs[j - 2]:
                    fvg_low = float(highs[j - 2])
                    fvg_high = float(lows[j])
            else:
                if highs[j] < lows[j - 2]:
                    fvg_low = float(highs[j])
                    fvg_high = float(lows[j - 2])
        return fvg_low, fvg_high

    for i in range(len(combined)):
        if active is None:
            allow_buy = (not require_bias) or (bias[i] == 1)
            allow_sell = (not require_bias) or (bias[i] == -1)

            if allow_buy:
                if sweep_mode == "swing":
                    if not np.isnan(last_sl[i]) and not np.isnan(internal_high[i]) and lows[i] <= last_sl[i] and closes[i] >= last_sl[i]:
                        active = {
                            'dir': 1,
                            'sweep_i': i,
                            'sweep_price': float(lows[i]),
                            'bos_level': float(internal_high[i]),
                        }
                elif sweep_mode == "prev_bar":
                    if i > 0 and not np.isnan(internal_high[i]) and lows[i] <= lows[i - 1] and closes[i] >= lows[i - 1]:
                        active = {
                            'dir': 1,
                            'sweep_i': i,
                            'sweep_price': float(lows[i]),
                            'bos_level': float(internal_high[i]),
                        }
                else:
                    if not np.isnan(roll_min_low[i]) and not np.isnan(internal_high[i]) and lows[i] < roll_min_low[i] and closes[i] > roll_min_low[i]:
                        active = {
                            'dir': 1,
                            'sweep_i': i,
                            'sweep_price': float(lows[i]),
                            'bos_level': float(internal_high[i]),
                        }

            if active is None and allow_sell:
                if sweep_mode == "swing":
                    if not np.isnan(last_sh[i]) and not np.isnan(internal_low[i]) and highs[i] >= last_sh[i] and closes[i] <= last_sh[i]:
                        active = {
                            'dir': -1,
                            'sweep_i': i,
                            'sweep_price': float(highs[i]),
                            'bos_level': float(internal_low[i]),
                        }
                elif sweep_mode == "prev_bar":
                    if i > 0 and not np.isnan(internal_low[i]) and highs[i] >= highs[i - 1] and closes[i] <= highs[i - 1]:
                        active = {
                            'dir': -1,
                            'sweep_i': i,
                            'sweep_price': float(highs[i]),
                            'bos_level': float(internal_low[i]),
                        }
                else:
                    if not np.isnan(roll_max_high[i]) and not np.isnan(internal_low[i]) and highs[i] > roll_max_high[i] and closes[i] < roll_max_high[i]:
                        active = {
                            'dir': -1,
                            'sweep_i': i,
                            'sweep_price': float(highs[i]),
                            'bos_level': float(internal_low[i]),
                        }
            if active is not None:
                continue
            continue

        if i <= active['sweep_i']:
            continue
        if max_bos_wait_bars is not None and (i - active['sweep_i']) > max_bos_wait_bars:
            active = None
            continue

        if require_bias:
            if (active['dir'] == 1 and bias[i] != 1) or (active['dir'] == -1 and bias[i] != -1):
                active = None
                continue

        if active['dir'] == 1:
            if closes[i] >= active['bos_level']:
                bos_price = float(closes[i])
                sweep_price = float(active['sweep_price'])
                entry_level = bos_price - ((bos_price - sweep_price) * entry_retracement)
                sl_level = sweep_price
                tp_level = bos_price
                fvg_low, fvg_high = _find_last_fvg(active['sweep_i'], i, direction=1)
                entry_in_fvg = False
                if not np.isnan(fvg_low) and not np.isnan(fvg_high):
                    entry_in_fvg = (min(fvg_low, fvg_high) <= entry_level <= max(fvg_low, fvg_high))

                combined.at[idx[i], 'setup_id'] = next_setup_id
                combined.at[idx[i], 'setup_dir'] = 1
                combined.at[idx[i], 'sweep_time'] = idx[active['sweep_i']]
                combined.at[idx[i], 'sweep_price'] = sweep_price
                combined.at[idx[i], 'bos_time'] = idx[i]
                combined.at[idx[i], 'bos_price'] = bos_price
                combined.at[idx[i], 'entry_level'] = entry_level
                combined.at[idx[i], 'sl_level'] = sl_level
                combined.at[idx[i], 'tp_level'] = tp_level
                combined.at[idx[i], 'fvg_low'] = fvg_low
                combined.at[idx[i], 'fvg_high'] = fvg_high
                combined.at[idx[i], 'entry_in_fvg'] = bool(entry_in_fvg)

                next_setup_id += 1
                active = None
        else:
            if closes[i] <= active['bos_level']:
                bos_price = float(closes[i])
                sweep_price = float(active['sweep_price'])
                entry_level = bos_price + ((sweep_price - bos_price) * entry_retracement)
                sl_level = sweep_price
                tp_level = bos_price
                fvg_low, fvg_high = _find_last_fvg(active['sweep_i'], i, direction=-1)
                entry_in_fvg = False
                if not np.isnan(fvg_low) and not np.isnan(fvg_high):
                    entry_in_fvg = (min(fvg_low, fvg_high) <= entry_level <= max(fvg_low, fvg_high))

                combined.at[idx[i], 'setup_id'] = next_setup_id
                combined.at[idx[i], 'setup_dir'] = -1
                combined.at[idx[i], 'sweep_time'] = idx[active['sweep_i']]
                combined.at[idx[i], 'sweep_price'] = sweep_price
                combined.at[idx[i], 'bos_time'] = idx[i]
                combined.at[idx[i], 'bos_price'] = bos_price
                combined.at[idx[i], 'entry_level'] = entry_level
                combined.at[idx[i], 'sl_level'] = sl_level
                combined.at[idx[i], 'tp_level'] = tp_level
                combined.at[idx[i], 'fvg_low'] = fvg_low
                combined.at[idx[i], 'fvg_high'] = fvg_high
                combined.at[idx[i], 'entry_in_fvg'] = bool(entry_in_fvg)

                next_setup_id += 1
                active = None

    if max_pending_bars is not None and max_pending_bars > 0:
        cols_to_ffill = ['setup_id', 'setup_dir', 'entry_level', 'sl_level', 'tp_level']
        combined[cols_to_ffill] = combined[cols_to_ffill].ffill(limit=max_pending_bars)

    return combined
