# Trading Logic Explanation (XAUUSD Bot)

This file explains your bot's trading logic in simple terms, including why it can print:

`Uptrend active. No long trigger on last closed candle.`

---

## 1) Big Picture: What the bot does

The bot uses:

- **H4 (HTF)** to decide trend direction.
- **M15 (ETF)** to decide trade entries.
- **Fibonacci retracement** level (currently `0.786`) for entry.
- Swing levels for **SL** and **TP**.

---

## 2) Step-by-step logic

1. Fetch H4 and M15 candles.
2. Detect recent swing highs/lows on both timeframes.
3. Determine HTF trend:
   - Uptrend (`htf_trend = 1`)
   - Downtrend (`htf_trend = -1`)
   - Neutral (`0`, no trade)
4. On M15, compute:
   - `sh = last_swing_high`
   - `sl = last_swing_low`
   - `range = sh - sl`
5. Compute fib entry:
   - **Long side**: `entry = sh - (range * fib_level)`
   - **Short side**: `entry = sl + (range * fib_level)`
6. Check if the **last closed M15 candle** gives a valid trigger.
7. If valid, place order with fixed risk sizing, then manage with TP/SL.

---

## 3) Entry, SL, TP rules

### Long setup (HTF uptrend)

- Entry: `entry = sh - (range * fib_level)`
- SL: `sl = last_swing_low`
- TP: `tp = last_swing_high`
- Must satisfy: `SL < Entry < TP`

### Short setup (HTF downtrend)

- Entry: `entry = sl + (range * fib_level)`
- SL: `sl = last_swing_high`
- TP: `tp = last_swing_low`
- Must satisfy: `TP < Entry < SL`

---

## 4) Why you see "No long trigger on last closed candle"

For a **long** trigger, your bot needs BOTH conditions on the last closed M15 candle:

1. `candle_low <= entry_level`
2. `candle_open > entry_level`

If either condition fails, it logs:

`Uptrend active. No long trigger on last closed candle.`

### Your example

- `EntryLevel = 4981.35`
- `CandleOpen = 4997.00`
- `CandleLow = 4994.38`

Check conditions:

- `candle_open > entry_level` -> `4997.00 > 4981.35` ✅ true
- `candle_low <= entry_level` -> `4994.38 <= 4981.35` ❌ false

Because the candle low **never touched or went below** the entry level, no long trade is triggered.

---

## 5) Candle-by-candle visual idea (long case)

Assume `entry_level = 4981.35`:

- Candle A: `open=4998`, `low=4990` -> low is still above entry -> **No trigger**
- Candle B: `open=4995`, `low=4984` -> low still above entry -> **No trigger**
- Candle C: `open=4993`, `low=4981` -> low touched entry and open was above -> **Trigger**
- Candle D: `open=4979`, `low=4974` -> opened below entry, not the expected retrace pattern -> **No trigger**

---

## 6) Why this can happen many times in a row

In an uptrend, price may stay above the fib entry level without retracing deeply enough.  
So the bot keeps seeing "trend is up" but "entry candle condition not met yet."

That is normal behavior for this strict entry rule.

---

## 7) One important live-vs-backtest note

Your current live bot checks a closed-candle trigger first, then verifies current market constraints (slippage, broker minimum stops) before order send.

So even after a valid candle trigger, a trade can still be skipped if:

- price moved too far from entry level, or
- broker stop-distance constraints are not met.

