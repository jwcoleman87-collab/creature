"""
market_watcher
==============
Fetches live and historical SPY data from Alpaca.
Calculates the Opening Range and monitors for breakouts.
"""

import os
from datetime import datetime, time, date, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from alpaca.data.enums import DataFeed

_client = StockHistoricalDataClient(
    api_key=os.getenv("ALPACA_API_KEY"),
    secret_key=os.getenv("ALPACA_SECRET_KEY"),
)

SYMBOL    = "SPY"
ORB_START = time(9, 30)
ORB_END   = time(10, 0)


def get_opening_range(trade_date: date = None) -> dict:
    """
    Fetch the high and low of SPY for the 09:30-10:00 window.
    Returns: { high, low, date, ready }
    ready=False means market hasn't reached 10:00 yet or data unavailable.
    """
    trade_date = trade_date or date.today()

    try:
        ET = ZoneInfo("America/New_York")
        request = StockBarsRequest(
            symbol_or_symbols=SYMBOL,
            timeframe=TimeFrame.Minute,
            start=datetime.combine(trade_date, ORB_START, tzinfo=ET),
            end=datetime.combine(trade_date, ORB_END, tzinfo=ET),
            feed=DataFeed.IEX,
        )
        bars = _client.get_stock_bars(request)

        if not bars or not bars.data or SYMBOL not in bars.data or len(bars.data[SYMBOL]) == 0:
            print("[MarketWatcher] No bars returned for opening range window.")
            return {"high": 0.0, "low": 0.0, "date": str(trade_date), "ready": False}

        spy_bars = bars.data[SYMBOL]
        highs = [b.high for b in spy_bars]
        lows  = [b.low for b in spy_bars]

        orb_high = max(highs)
        orb_low  = min(lows)

        print(f"[MarketWatcher] Opening Range: HIGH={orb_high:.2f} | LOW={orb_low:.2f} ({len(spy_bars)} bars)")
        return {"high": orb_high, "low": orb_low, "date": str(trade_date), "ready": True}

    except Exception as e:
        print(f"[MarketWatcher] Error fetching opening range: {e}")
        return {"high": 0.0, "low": 0.0, "date": str(trade_date), "ready": False}


def get_latest_bar() -> dict:
    """
    Fetch the most recent completed 5-minute candle for SPY.
    Returns: { close, high, low, volume, time }
    """
    try:
        request = StockBarsRequest(
            symbol_or_symbols=SYMBOL,
            timeframe=TimeFrame(5, TimeFrameUnit.Minute),
            limit=1,
            feed=DataFeed.IEX,
        )
        bars = _client.get_stock_bars(request)

        if not bars or not bars.data or SYMBOL not in bars.data or len(bars.data[SYMBOL]) == 0:
            print("[MarketWatcher] No bars returned for latest bar.")
            return {"close": 0.0, "high": 0.0, "low": 0.0, "volume": 0, "time": datetime.now().isoformat()}

        bar = bars.data[SYMBOL][-1]
        return {
            "close":  float(bar.close),
            "high":   float(bar.high),
            "low":    float(bar.low),
            "volume": int(bar.volume),
            "time":   str(bar.timestamp),
        }

    except Exception as e:
        print(f"[MarketWatcher] Error fetching latest bar: {e}")
        return {"close": 0.0, "high": 0.0, "low": 0.0, "volume": 0, "time": datetime.now().isoformat()}


def get_volume_average(lookback_bars: int = 20) -> float:
    """
    Return the average volume of the last N five-minute candles.
    Used to confirm breakout conviction (breakout bar must exceed this).
    """
    try:
        request = StockBarsRequest(
            symbol_or_symbols=SYMBOL,
            timeframe=TimeFrame(5, TimeFrameUnit.Minute),
            limit=lookback_bars,
            feed=DataFeed.IEX,
        )
        bars = _client.get_stock_bars(request)

        if not bars or not bars.data or SYMBOL not in bars.data or len(bars.data[SYMBOL]) == 0:
            print("[MarketWatcher] No bars returned for volume average.")
            return 0.0

        spy_bars = bars.data[SYMBOL]
        avg_vol = sum(b.volume for b in spy_bars) / len(spy_bars)
        print(f"[MarketWatcher] Volume average ({len(spy_bars)} bars): {avg_vol:.0f}")
        return avg_vol

    except Exception as e:
        print(f"[MarketWatcher] Error fetching volume average: {e}")
        return 0.0


def get_twenty_day_ma() -> float:
    """
    Return the 20-day simple moving average of SPY daily closes.
    Used for direction filter: long above MA, short below MA.
    """
    try:
        request = StockBarsRequest(
            symbol_or_symbols=SYMBOL,
            timeframe=TimeFrame.Day,
            limit=20,
            feed=DataFeed.IEX,
        )
        bars = _client.get_stock_bars(request)

        if not bars or not bars.data or SYMBOL not in bars.data or len(bars.data[SYMBOL]) == 0:
            print("[MarketWatcher] No bars returned for 20-day MA.")
            return 0.0

        spy_bars = bars.data[SYMBOL]
        closes = [float(b.close) for b in spy_bars]
        ma = sum(closes) / len(closes)
        print(f"[MarketWatcher] 20-day MA: {ma:.2f} ({len(closes)} days)")
        return ma

    except Exception as e:
        print(f"[MarketWatcher] Error fetching 20-day MA: {e}")
        return 0.0
