# Groww AI Observation Bot

This bot is for observation / paper trading only.

It scans selected Indian stocks/ETFs, gives BUY/HOLD/SELL signals, simulates trades using paper capital, and logs results for review.

## Features

- Scans multiple symbols
- Uses EMA9, EMA21, RSI, VWAP, volume and news risk
- Uses paper capital, default ₹5000
- Script decides how many instruments can be selected
- No real order is placed
- Records signals and paper trade results

## Files Generated

After running, the bot creates:

```text
data/signals_log.csv
data/paper_trades.csv
data/daily_summary.csv
data/paper_state.json
