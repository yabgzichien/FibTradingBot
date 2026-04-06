# EA Bridge Guide (MT5 EA + Python)

This project uses an MT5 Expert Advisor (EA) plus a Python “bridge” script to run your strategy **without** the Python `MetaTrader5` package.

The goal is long-term stability: the EA stays inside MT5 and does all broker/terminal operations, while Python only computes signals and writes “commands” for the EA to execute.

## Components

### 1) MT5 Expert Advisor (Execution + Data Export)

File: [AntigravityBridgeEA.mq5](file:///c:/Users/yang/Desktop/xmum/my-own/antigravity_test_trading_bot/trading_bot/ea/AntigravityBridgeEA.mq5)

Responsibilities:
- Exports OHLCV candles (rates) for configured symbols and timeframes into MT5 **Common Files**
- Reads a commands CSV (written by Python) and executes those commands:
  - Cancels bot pending orders
  - Places/replaces BUY_LIMIT / SELL_LIMIT orders with SL/TP

Runs on a timer (`OnTimer`) at a fixed interval (e.g. 60 seconds).

### 2) Python Bridge (Signals + Command Writer)

File: [ea_bridge.py](file:///c:/Users/yang/Desktop/xmum/my-own/antigravity_test_trading_bot/trading_bot/ea_bridge.py)

Responsibilities:
- Reads the exported rates CSV files produced by the EA
- Runs your strategy logic (`generate_signals_refined`) to find the latest setup
- Appends command rows into `ag_commands.csv` for the EA to execute

Python never sends orders directly to MT5. It only writes command instructions to disk.

## Shared Folder (IPC)

Both the EA and Python read/write in the MT5 “Common Files” folder:

`%APPDATA%\MetaQuotes\Terminal\Common\Files`

Example (your machine):

`C:\Users\yang\AppData\Roaming\MetaQuotes\Terminal\Common\Files`

This is the communication channel between Python and the EA.

## Files Created in Common\Files

### Rates exports (written by EA)

For each symbol and timeframe, the EA writes:
- `ag_rates_<SYMBOL>_<TF>.csv`

Examples:
- `ag_rates_BTCUSD_H4.csv`
- `ag_rates_BTCUSD_M15.csv`
- `ag_rates_XAUUSD_H4.csv`
- `ag_rates_XAUUSD_M15.csv`

Columns:
- `time` (Unix seconds)
- `open, high, low, close`
- `tick_volume, spread, real_volume`

### Command queue (written by Python, read by EA)

Python writes:
- `ag_commands.csv`

The file is append-only. Each row is a command for the EA to execute.

## Command Protocol (ag_commands.csv)

Header fields:
- `ts`: timestamp in UTC (string)
- `symbol`: symbol name (e.g. BTCUSD)
- `cmd`: command type
- `dir`: 1 for long, -1 for short (used only for PLACE_LIMIT)
- `entry`: limit entry price
- `sl`: stop loss price
- `tp`: take profit price
- `risk_usd`: dollar risk target used by the EA to compute lot size
- `magic`: magic number used to tag orders placed by this bot
- `replace_tol_points`: tolerance for “keep existing pending order vs replace”
- `max_pending_bars`: used by Python for lifecycle decisions (EA currently focuses on sync/replace)
- `client_tag`: tag written by Python for debugging (e.g. py_setup_123)

Supported `cmd` values:

### CANCEL_ALL
Meaning:
- Cancel all bot-owned pending orders for `symbol` + `magic`.

When Python writes it:
- When there is **no valid setup** on the latest closed execution candle.

What EA does:
- Scans current pending orders and removes BUY_LIMIT / SELL_LIMIT orders matching the symbol + magic.

### PLACE_LIMIT
Meaning:
- Ensure exactly one bot-owned pending limit order exists that matches the given `dir/entry/sl/tp`.

When Python writes it:
- When a valid setup exists on the latest closed execution candle.

What EA does:
- If a “close enough” pending already exists (within `replace_tol_points`), keep it.
- Otherwise:
  - cancel existing bot pending orders for that symbol + magic
  - place a new BUY_LIMIT (dir=1) or SELL_LIMIT (dir=-1)
  - compute lots based on `risk_usd` and SL distance using MT5 tick value/size
  - enforce broker minimum stop distance (`SYMBOL_TRADE_STOPS_LEVEL`)

## How the EA Prevents Re-processing Commands

The EA tracks the last processed command line using a terminal global variable:
- `AG_CMD_LAST_LINE`

This prevents the EA from executing the same command row multiple times across timer ticks.

## How to Run

### Step 1: Install and Compile the EA

1. Copy [AntigravityBridgeEA.mq5](file:///c:/Users/yang/Desktop/xmum/my-own/antigravity_test_trading_bot/trading_bot/ea/AntigravityBridgeEA.mq5) into your MT5 data folder:
   - `MQL5/Experts/`
2. Open MetaEditor and compile.
3. In MT5, attach the EA to any chart and enable Algo Trading.

### Step 2: Confirm Rates Export

In `Common\Files`, confirm the EA is updating the `ag_rates_*` CSV files.

### Step 3: Run Python Bridge

Run from this project folder:

```bash
python ea_bridge.py
```

Expected behavior:
- Python prints status logs (what it loaded, whether a setup exists, and what command it wrote)
- `ag_commands.csv` is created (if missing) and appended with command rows
- EA reads and executes those commands on its timer tick

## Expected Outputs (What You Should See)

- In `Common\Files`:
  - continuously updated `ag_rates_*` files
  - `ag_commands.csv` growing over time
- In MT5 Trade tab:
  - pending orders appearing/disappearing for BTCUSD/XAUUSD (magic number = configured `magic`)
  - positions opening when entry is hit and closing via SL/TP normally

## Notes / Common Issues

- If `ag_commands.csv` does not appear:
  - Python bridge isn’t running, or crashes before startup
  - The EA and Python are not using the same Common Files folder
- If Python fails to read rates CSV:
  - Some MT5 CSVs are UTF-16; Python bridge auto-detects BOM and retries
  - Some terminals use `;` as separator; Python bridge uses separator sniffing

