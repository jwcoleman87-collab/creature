"""
crypto_scanner
==============
Scans the crypto universe using a regime-switched MMR hybrid strategy.

  TRENDING  (ADX > 25) → Momentum Long:      Z-score of 4h return > threshold
  RANGING   (ADX < 20) → Mean Rev Long:      BB position < 20% AND RSI < 35
  CONFLICTED (20–25)   → Either signal fires (weakest confirmation)

Alpaca spot crypto is long-only — no shorting available.
Score = Technical + Sentiment (Fear & Greed contrarian) + Affinity (learned per-coin).
"""

import os
import math
import time
import requests
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

from alpaca.data.historical import CryptoHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame
from core.constitution import get

_client = CryptoHistoricalDataClient(
    api_key=os.getenv("ALPACA_API_KEY"),
    secret_key=os.getenv("ALPACA_SECRET_KEY"),
)

_sentiment_cache: dict = {"adj": 0.0, "ts": 0.0, "value": None, "label": "Unknown"}


@dataclass
class SignalCandidate:
    symbol:           str
    direction:        str    # always "long" — Alpaca spot cannot short
    setup_type:       str    # "momentum_long" | "mean_reversion_long"
    regime:           str    # "trending" | "ranging" | "conflicted"
    entry_price:      float
    stop_price:       float
    target_price:     float
    breakeven_price:  float
    atr:              float
    final_score:      float
    raw_technical:    float
    sentiment_adj:    float
    affinity_bonus:   float
    volume_confirmed: bool


# ── Data fetching ──────────────────────────────────────────────────────────────

def get_all_bars(symbols: list, limit: int = 200) -> dict:
    """Fetch 1h bars for all symbols in one batched request."""
    try:
        req  = CryptoBarsRequest(symbol_or_symbols=symbols, timeframe=TimeFrame.Hour, limit=limit)
        resp = _client.get_crypto_bars(req)
        result = {}
        for sym in symbols:
            try:
                bars = resp[sym]
                if bars:
                    result[sym] = bars
            except (KeyError, TypeError):
                pass
        return result
    except Exception as e:
        print(f"[Scanner] Bar fetch error: {e}")
        return {}


def get_latest_bars(symbols: list) -> dict:
    """Fetch the most recent completed 1h bar for each symbol."""
    try:
        req  = CryptoBarsRequest(symbol_or_symbols=symbols, timeframe=TimeFrame.Hour, limit=2)
        resp = _client.get_crypto_bars(req)
        result = {}
        for sym in symbols:
            try:
                bars = resp[sym]
                if bars:
                    b = bars[-1]
                    result[sym] = {
                        "close":  float(b.close),
                        "high":   float(b.high),
                        "low":    float(b.low),
                        "volume": int(b.volume),
                    }
            except (KeyError, TypeError):
                pass
        return result
    except Exception as e:
        print(f"[Scanner] Latest bar fetch error: {e}")
        return {}


# ── Sentiment ──────────────────────────────────────────────────────────────────

def fetch_sentiment() -> float:
    """
    Fetch Fear & Greed Index from alternative.me (free, no auth).
    Contrarian: extreme fear → +adj (long signal), extreme greed → -adj.
    Cached for 1 hour.
    """
    global _sentiment_cache
    ttl = get("crypto.sentiment.cache_ttl_seconds", 3600)
    if time.time() - _sentiment_cache["ts"] < ttl:
        return _sentiment_cache["adj"]

    try:
        resp  = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        data  = resp.json()["data"][0]
        value = int(data["value"])
        label = data["value_classification"]

        fear_thresh  = get("crypto.sentiment.extreme_fear_threshold",  25)
        greed_thresh = get("crypto.sentiment.extreme_greed_threshold", 75)
        max_adj      = get("crypto.sentiment.max_score_adjustment",   5.0)

        if value <= fear_thresh:
            adj = max_adj * (1.0 - value / fear_thresh)      # fear → buy
        elif value >= greed_thresh:
            adj = -max_adj * (value - greed_thresh) / (100 - greed_thresh)  # greed → caution
        else:
            adj = 0.0

        _sentiment_cache = {"adj": adj, "ts": time.time(), "value": value, "label": label}
        print(f"[Scanner] Fear & Greed: {value} ({label}) → adj={adj:+.2f}")
        return adj
    except Exception as e:
        print(f"[Scanner] Sentiment fetch failed (using 0): {e}")
        return 0.0


def get_sentiment_info() -> dict:
    """Return the latest cached sentiment data for the dashboard."""
    return {
        "fear_greed": _sentiment_cache.get("value"),
        "label":      _sentiment_cache.get("label", "Unknown"),
        "adj":        _sentiment_cache.get("adj", 0.0),
    }


# ── Technical indicators ───────────────────────────────────────────────────────

def _compute_atr(bars: list, period: int = 14) -> float:
    if len(bars) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i].high, bars[i].low, bars[i-1].close
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / period


def _compute_rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(abs(min(d, 0.0)))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + ag / al))


def _compute_bb_position(closes: list, period: int = 20, std_mult: float = 2.0) -> float:
    """0 = at lower band, 100 = at upper band."""
    if len(closes) < period:
        return 50.0
    window = closes[-period:]
    mean = sum(window) / period
    std  = math.sqrt(sum((x - mean) ** 2 for x in window) / period)
    if std == 0:
        return 50.0
    upper = mean + std_mult * std
    lower = mean - std_mult * std
    return max(0.0, min(100.0, (closes[-1] - lower) / (upper - lower) * 100.0))


def _compute_adx(bars: list, period: int = 14) -> float:
    if len(bars) < period * 2 + 1:
        return 20.0

    tr_list, pdm_list, ndm_list = [], [], []
    for i in range(1, len(bars)):
        h, l   = bars[i].high, bars[i].low
        ph, pl, pc = bars[i-1].high, bars[i-1].low, bars[i-1].close
        tr  = max(h - l, abs(h - pc), abs(l - pc))
        pdm = max(h - ph, 0.0) if (h - ph) > (pl - l) else 0.0
        ndm = max(pl - l, 0.0) if (pl - l) > (h - ph) else 0.0
        tr_list.append(tr);  pdm_list.append(pdm);  ndm_list.append(ndm)

    def wilder(data: list) -> list:
        s = sum(data[:period])
        out = [s]
        for v in data[period:]:
            s = s - (s / period) + v
            out.append(s)
        return out

    atr_s = wilder(tr_list)
    pdm_s = wilder(pdm_list)
    ndm_s = wilder(ndm_list)

    dx_list = []
    for a, p, n in zip(atr_s, pdm_s, ndm_s):
        if a == 0:
            continue
        pdi = (p / a) * 100.0
        ndi = (n / a) * 100.0
        s   = pdi + ndi
        if s == 0:
            continue
        dx_list.append(abs(pdi - ndi) / s * 100.0)

    if len(dx_list) < period:
        return 20.0
    return sum(dx_list[-period:]) / period


def _compute_z_score_4h(bars: list, lookback: int = 42) -> float:
    """Z-score of the current 4h return vs its rolling distribution."""
    if len(bars) < lookback + 4:
        return 0.0
    returns = [
        (bars[i].close - bars[i - 4].close) / bars[i - 4].close
        for i in range(4, len(bars))
        if bars[i - 4].close > 0
    ]
    if len(returns) < lookback:
        return 0.0
    window = returns[-lookback:]
    mean = sum(window) / len(window)
    std  = math.sqrt(sum((r - mean) ** 2 for r in window) / len(window))
    if std == 0:
        return 0.0
    return (returns[-1] - mean) / std


def _compute_volume_ratio(bars: list, lookback: int = 20) -> float:
    if len(bars) < lookback + 1:
        return 1.0
    avg = sum(b.volume for b in bars[-(lookback + 1):-1]) / lookback
    if avg == 0:
        return 1.0
    return bars[-1].volume / avg


# ── Affinity from brain ────────────────────────────────────────────────────────

def _get_affinity(symbol: str) -> float:
    """Returns score adjustment based on historical win rate for this coin."""
    try:
        from core.journal import get_asset_score
        row = get_asset_score(symbol)
        if not row or row["total_trades"] < get("learning.brain.coin_affinity.min_trades_for_opinion", 3):
            return 0.0
        if row.get("hard_blocked"):
            return -999.0
        wr = row["win_rate"]
        if wr >= get("learning.brain.coin_affinity.boost_if_wr_above", 0.60):
            return 2.0
        if wr < get("learning.brain.coin_affinity.hard_block_if_wr_below", 0.25) and row["total_trades"] >= 5:
            return -999.0
        if wr < get("learning.brain.coin_affinity.penalise_if_wr_below", 0.35):
            return -2.0
        return 0.5
    except Exception:
        return 0.0


# ── Scoring ────────────────────────────────────────────────────────────────────

def score_symbol(symbol: str, bars: list, sentiment_adj: float) -> SignalCandidate | None:
    """Score a single symbol. Returns None if no tradeable signal found."""
    if len(bars) < 60:
        return None

    closes = [b.close for b in bars]
    entry  = closes[-1]
    if entry <= 0:
        return None

    atr       = _compute_atr(bars)
    rsi       = _compute_rsi(closes)
    bb_pos    = _compute_bb_position(closes)
    adx       = _compute_adx(bars)
    z_score   = _compute_z_score_4h(bars)
    vol_ratio = _compute_volume_ratio(bars)

    if atr <= 0:
        return None

    # ── Regime ────────────────────────────────────────────────────────────────
    trend_th  = get("crypto.strategy.sub_strategies.momentum.adx_trending_threshold",    25)
    range_th  = get("crypto.strategy.sub_strategies.mean_reversion.adx_ranging_threshold", 20)

    if adx >= trend_th:
        regime = "trending"
    elif adx <= range_th:
        regime = "ranging"
    else:
        regime = "conflicted"

    # ── Signal (long only) ─────────────────────────────────────────────────────
    z_thresh    = get("crypto.strategy.sub_strategies.momentum.z_score_entry_threshold", 1.5)
    rsi_os      = get("crypto.strategy.sub_strategies.mean_reversion.rsi_oversold",      35)
    min_vol_r   = get("crypto.strategy.sub_strategies.volume.min_vol_ratio",             1.2)

    mom_signal = z_score >= z_thresh
    mr_signal  = (bb_pos <= 20.0 and rsi <= rsi_os)

    # ── Extreme Fear override ──────────────────────────────────────────────────
    # When sentiment is strongly fearful, unlock mean reversion even in a
    # trending (crashing) market. The crowd is panic-selling — the creature
    # fades the crowd. Sentiment adj > 1.0 = strong contrarian buy signal.
    extreme_fear_override = sentiment_adj > 1.0   # F&G well below fear threshold

    print(f"[Scanner] {symbol}: ADX={adx:.1f} RSI={rsi:.1f} BB={bb_pos:.1f} "
          f"Z={z_score:.2f} vol={vol_ratio:.2f} regime={regime} "
          f"mom={mom_signal} mr={mr_signal} fear_override={extreme_fear_override}")

    if regime == "trending" and extreme_fear_override:
        # In extreme fear, check mean reversion regardless of ADX trend
        signal_ok  = mr_signal
        setup_type = "mean_reversion_long"
        regime     = "crisis_bounce"
    elif regime == "trending":
        signal_ok  = mom_signal
        setup_type = "momentum_long"
    elif regime == "ranging":
        signal_ok  = mr_signal
        setup_type = "mean_reversion_long"
    else:
        signal_ok  = mom_signal or mr_signal
        setup_type = "momentum_long" if mom_signal else "mean_reversion_long"

    if not signal_ok:
        return None

    # ── Technical score ────────────────────────────────────────────────────────
    if setup_type == "momentum_long":
        sig_pts = min(abs(z_score) / 2.0, 3.0)
    else:
        sig_pts = min((100.0 - bb_pos) / 33.0, 3.0)

    vol_pts  = min(vol_ratio / 2.0, 2.0)
    atr_pts  = 1.0 if (atr / entry) > 0.005 else 0.0
    raw_tech = sig_pts + vol_pts + atr_pts

    # ── Affinity ───────────────────────────────────────────────────────────────
    affinity = _get_affinity(symbol)
    if affinity <= -100.0:
        return None  # hard blocked

    # ── Final score ────────────────────────────────────────────────────────────
    final = raw_tech + sentiment_adj + affinity
    # In crisis_bounce regime (extreme fear override), waive the score threshold —
    # the backtest gate is the real judge. Don't block a contrarian setup on score alone.
    min_score = 0.0 if regime == "crisis_bounce" else get("risk.crypto.min_score_to_backtest", 3.0)
    if final < min_score:
        return None

    # ── Stop / target ──────────────────────────────────────────────────────────
    atr_mult    = get("risk.crypto.stop_loss_atr_multiplier", 1.5)
    floor_pct   = get("risk.crypto.stop_loss_pct",            0.01)
    stop_dist   = max(atr * atr_mult, entry * floor_pct)
    stop_price  = round(entry - stop_dist, 8)
    target      = round(entry + stop_dist * 2.0, 8)
    be_price    = round(entry + stop_dist,       8)

    return SignalCandidate(
        symbol=symbol,       direction="long",
        setup_type=setup_type,  regime=regime,
        entry_price=entry,   stop_price=stop_price,
        target_price=target, breakeven_price=be_price,
        atr=atr,             final_score=round(final, 3),
        raw_technical=round(raw_tech, 3),
        sentiment_adj=round(sentiment_adj, 3),
        affinity_bonus=round(affinity, 3),
        volume_confirmed=(vol_ratio >= min_vol_r),
    )


# ── Main scan ──────────────────────────────────────────────────────────────────

def scan(bars_data: dict = None) -> list:
    """
    Scan all pairs. Optionally pass pre-fetched bars_data to avoid double fetch.
    Returns list of SignalCandidates sorted by score descending.
    """
    universe = get("crypto.universe", [])
    if not bars_data:
        bars_data = get_all_bars(universe, limit=200)
    if not bars_data:
        print("[Scanner] No bar data. Check API connection.")
        return []

    sentiment_adj = fetch_sentiment()
    candidates    = []

    for symbol in universe:
        bars = bars_data.get(symbol)
        if not bars or len(bars) < 60:
            continue
        try:
            c = score_symbol(symbol, bars, sentiment_adj)
            if c:
                candidates.append(c)
                print(f"[Scanner] {symbol}: score={c.final_score:.2f} | {c.setup_type} | regime={c.regime} | vol_ok={c.volume_confirmed}")
        except Exception as e:
            print(f"[Scanner] Error on {symbol}: {e}")

    candidates.sort(key=lambda x: x.final_score, reverse=True)
    return candidates
