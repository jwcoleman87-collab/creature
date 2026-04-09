"""
CREATURE — Phase 0 Test Script
================================
This script confirms that:
  1. Your .env file is loaded correctly
  2. Your Alpaca API keys are valid
  3. You can connect to the paper trading account
  4. You can pull real SPY candle data

Run this with:
    python phase0_test.py

If it prints a table of SPY candles, Phase 0 is complete.
"""

import os
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone
import pandas as pd

# ── Load API keys from .env file ──────────────────────────────────────────────
load_dotenv()

API_KEY    = os.getenv("ALPACA_API_KEY")
API_SECRET = os.getenv("ALPACA_SECRET_KEY")
BASE_URL   = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

# ── Sanity check: make sure keys were loaded ──────────────────────────────────
print("\n==============================")
print("  CREATURE — Phase 0 Test")
print("==============================\n")

if not API_KEY or API_KEY == "YOUR_KEY_ID_HERE":
    print("ERROR: ALPACA_API_KEY is missing or still set to the placeholder.")
    print("Open your .env file and paste in your real Alpaca paper trading key.")
    exit(1)

if not API_SECRET or API_SECRET == "YOUR_SECRET_HERE":
    print("ERROR: ALPACA_SECRET_KEY is missing or still set to the placeholder.")
    print("Open your .env file and paste in your real Alpaca paper trading secret.")
    exit(1)

print(f"API key loaded:  {API_KEY[:6]}...{API_KEY[-4:]}")
print(f"Base URL:        {BASE_URL}")
print()

# ── Connect to Alpaca ─────────────────────────────────────────────────────────
try:
    from alpaca.trading.client import TradingClient
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.enums import DataFeed
except ImportError:
    print("ERROR: alpaca-py is not installed.")
    print("Run this first:  pip install -r requirements.txt")
    exit(1)

print("Connecting to Alpaca paper trading account...")

try:
    trading_client = TradingClient(API_KEY, API_SECRET, paper=True)
    account = trading_client.get_account()
except Exception as e:
    print(f"\nERROR: Could not connect to Alpaca.")
    print(f"Detail: {e}")
    print("\nCheck that your API key and secret are correct and that you are")
    print("using your PAPER trading keys, not live keys.")
    exit(1)

print(f"\nConnected! Account details:")
print(f"  Account number : {account.account_number}")
print(f"  Status         : {account.status}")
print(f"  Cash           : ${float(account.cash):,.2f}")
print(f"  Portfolio value: ${float(account.portfolio_value):,.2f}")
print(f"  Buying power   : ${float(account.buying_power):,.2f}")

# ── Pull SPY candles ──────────────────────────────────────────────────────────
print(f"\nPulling SPY 5-minute candles for the last 5 trading days...")

data_client = StockHistoricalDataClient(API_KEY, API_SECRET)

end   = datetime.now(timezone.utc)
start = end - timedelta(days=7)   # 7 calendar days to ensure we get 5 trading days

request = StockBarsRequest(
    symbol_or_symbols="SPY",
    timeframe=TimeFrame.Minute,
    start=start,
    end=end,
    limit=100,
    feed=DataFeed.IEX,
)

try:
    bars = data_client.get_stock_bars(request)
    df = bars.df
except Exception as e:
    print(f"\nERROR: Could not pull SPY data.")
    print(f"Detail: {e}")
    exit(1)

if df.empty:
    print("\nWARNING: No data returned. Market may be closed right now.")
    print("Try again during US market hours (9:30am–4:00pm Eastern Time).")
else:
    # Flatten multi-index if present
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs("SPY", level="symbol")

    df = df.tail(20)  # Show last 20 candles
    df.index = df.index.tz_convert("America/New_York")

    print(f"\nLast {len(df)} SPY candles (New York time):\n")
    print(f"{'Time':<25} {'Open':>8} {'High':>8} {'Low':>8} {'Close':>8} {'Volume':>10}")
    print("-" * 72)
    for ts, row in df.iterrows():
        print(f"{str(ts):<25} {row['open']:>8.2f} {row['high']:>8.2f} {row['low']:>8.2f} {row['close']:>8.2f} {int(row['volume']):>10,}")

print("\n==============================")
print("  Phase 0 COMPLETE")
print("  Alpaca is connected.")
print("  SPY data is flowing.")
print("  Ready for Phase 1.")
print("==============================\n")
