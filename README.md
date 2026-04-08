# 🥇 GoldTradingBot

A Python-based algorithmic trading system for **XAUUSD (Gold)** and other instruments, featuring a multi-timeframe Smart Money Concepts (SMC) strategy, backtesting engine, Monte Carlo simulation, live trading via MetaTrader 5, and an animated visual replay.

---

## ✨ Features

- **Multi-Timeframe Strategy** — Uses H4 as the anchor/bias timeframe and M15 for trade execution
- **Smart Money Concepts (SMC)** — Detects Break of Structure (BOS), liquidity sweeps, and Fibonacci retracement entries (default 0.618)
- **Backtesting Engine** — Simulates historical trades with realistic spread, slippage, and commission modelling
- **Monte Carlo Simulation** — Runs 1,000 simulations on trade P&L to evaluate statistical robustness
- **Visual Replay** — Generates an interactive HTML chart animating trade entries and exits
- **Live Trader** — Connects to MetaTrader 5 for real-time signal execution
- **Strategy Optimizer** — Parameter optimisation for strategy tuning
- **Heatmap Analysis** — Visualises performance across parameter combinations
- **MT5 EA Bridge** — MQL5 Expert Advisor (`ea/`) for bridging signals to the MT5 platform

---

## 📁 Project Structure

```
GoldTradingBot/
├── main.py               # Entry point — backtest runner with interactive CLI
├── strategy.py           # SMC signal generation (BOS, sweeps, Fibonacci entries)
├── backtest.py           # Backtest engine, plotting, Monte Carlo simulation
├── data_loader.py        # MT5 data fetching and symbol specification loader
├── live_trader.py        # Live trade execution via MetaTrader 5
├── ea_bridge.py          # Python ↔ MT5 EA communication bridge
├── optimizer.py          # Strategy parameter optimiser
├── heatmap.py            # Performance heatmap visualisation
├── visual_replay.py      # Interactive HTML animated trade replay
├── test.py               # Unit/integration tests
├── ea/                   # MQL5 Expert Advisor source files
├── docs/                 # Additional documentation
└── requirements.txt      # Python dependencies
```

---

## ⚙️ Requirements

- Python 3.8+
- MetaTrader 5 terminal (Windows)
- A broker account with XAUUSD access

### Python Dependencies

```
MetaTrader5>=5.0.45
pandas>=2.0.0
numpy>=1.24.0
pytz>=2023.3
```

Install all dependencies:

```bash
pip install -r requirements.txt
```

---

## 🚀 Getting Started

### 1. Clone the repository

```bash
git clone https://github.com/yabgzichien/GoldTradingBot.git
cd GoldTradingBot
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Open MetaTrader 5

Ensure MT5 is running and logged into your broker account before running the bot. The `data_loader.py` module initialises the MT5 connection automatically.

### 4. Run a backtest

```bash
python main.py
```

You will be prompted to:

- Select trading symbols (XAUUSD, BTCUSD, both, or custom)
- Choose a date range (number of days back, or specific start/end dates)
- Optionally generate an animated visual replay

### 5. View results

After the backtest completes, output files are saved to `backtest_results/`:

- Trade summary CSV per symbol
- Trade entry/exit charts (PNG)
- Monte Carlo return distribution (PNG)
- Visual replay HTML (if enabled)

---

## 📊 Strategy Overview

The strategy uses a two-timeframe approach:

| Layer | Timeframe | Purpose |
|---|---|---|
| Anchor | H4 | Determine directional bias (bullish/bearish) |
| Execution | M15 | Time precise entries |

**Signal logic:**
1. Identify swing highs/lows on H4 to determine trend bias
2. On M15, detect liquidity sweeps of previous swing points
3. Confirm a Break of Structure (BOS) in the direction of the H4 bias
4. Enter on a Fibonacci retracement (default 0.618) of the BOS leg

**Backtest parameters (configurable in `main.py`):**

| Parameter | Default | Description |
|---|---|---|
| `anchor_tf` | H4 | Higher timeframe for bias |
| `etf` | M15 | Execution timeframe |
| `entry_retracement` | 0.618 | Fibonacci entry level |
| `anchor_swing_window` | 7 | Lookback bars for H4 swing detection |
| `max_bos_wait_bars` | 8 | Max bars to wait for BOS confirmation |
| `htf_warmup_days` | 60 | H4 warm-up period (days) |
| `etf_warmup_candles` | 360 | M15 warm-up candles (~3.75 days) |

---

## 💹 Live Trading

To run the live trader:

```bash
python live_trader.py
```

> ⚠️ **Risk Warning:** Live trading involves real financial risk. Always test thoroughly on a demo account before trading with real money. Past backtest performance does not guarantee future results.

---

## 🔧 Other Tools

**Optimizer** — Find optimal strategy parameters:
```bash
python optimizer.py
```

**Heatmap** — Visualise parameter performance:
```bash
python heatmap.py
```

---

## ⚠️ Disclaimer

This software is provided for **educational and research purposes only**. It is not financial advice. Trading foreign exchange and commodities carries significant risk and may not be suitable for all investors. Always use proper risk management and consult a licensed financial advisor before trading.

---

## 📄 License

This project is open source. See the repository for licensing details.
