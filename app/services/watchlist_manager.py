"""
Expert-curated watchlist, news sentiment scanner, and ATM options symbol generator.

Expert sources:
  - Robert Kiyosaki (Rich Dad Poor Dad): hard assets, commodities, REITs, cash flow
  - Warren Buffett: durable moat, value compounding
  - Industry consensus (CNBC, analyst upgrades): AI/tech growth
  - Options specialists (tastytrade style): high-liquidity, high-IV contracts
"""

import asyncio
from datetime import date, timedelta
from typing import Any, Dict, List

import yfinance as yf

# ---------------------------------------------------------------------------
# Expert-curated symbols
# ---------------------------------------------------------------------------

EXPERT_PICKS: Dict[str, Dict[str, str]] = {
    # Robert Kiyosaki — hard assets, commodities, cash-flow real estate
    "GLD":  {"name": "SPDR Gold Trust ETF",          "category": "commodity",    "expert": "Robert Kiyosaki", "rationale": "Gold as real money; hard assets preserve wealth vs fiat currency"},
    "SLV":  {"name": "iShares Silver Trust",          "category": "commodity",    "expert": "Robert Kiyosaki", "rationale": "Silver undervalued vs gold; dual industrial + monetary demand"},
    "GDX":  {"name": "VanEck Gold Miners ETF",        "category": "commodity",    "expert": "Robert Kiyosaki", "rationale": "Leveraged gold exposure via miners; cash-flowing hard asset businesses"},
    "XLE":  {"name": "Energy Select Sector SPDR",     "category": "commodity",    "expert": "Robert Kiyosaki", "rationale": "Oil & gas as strategic energy assets; inflation and debasement hedge"},
    "O":    {"name": "Realty Income Corp",             "category": "reit",         "expert": "Robert Kiyosaki", "rationale": "Monthly dividend REIT; passive cash flow from real estate assets"},
    "VNQ":  {"name": "Vanguard Real Estate ETF",      "category": "reit",         "expert": "Robert Kiyosaki", "rationale": "Diversified REIT portfolio; real assets beat paper assets long-term"},
    "MSTR": {"name": "MicroStrategy Inc",              "category": "crypto_proxy", "expert": "Robert Kiyosaki", "rationale": "Bitcoin corporate treasury proxy; digital gold hedge against dollar"},
    # Warren Buffett — durable moat, value, compounding capital
    "AAPL":  {"name": "Apple Inc",                    "category": "value",        "expert": "Warren Buffett",  "rationale": "Ecosystem moat; strongest consumer brand globally; capital return engine"},
    "KO":    {"name": "Coca-Cola Co",                 "category": "value",        "expert": "Warren Buffett",  "rationale": "Irreplaceable brand; 60+ year dividend growth; near-zero disruption risk"},
    "OXY":   {"name": "Occidental Petroleum",         "category": "value",        "expert": "Warren Buffett",  "rationale": "Low-cost oil producer; Buffett owns >25%; aggressive shareholder returns"},
    "AXP":   {"name": "American Express Co",          "category": "value",        "expert": "Warren Buffett",  "rationale": "Premium closed-loop card network; high-spend cardholder moat"},
    "BAC":   {"name": "Bank of America",              "category": "value",        "expert": "Warren Buffett",  "rationale": "Interest rate beneficiary; largest US deposit franchise by volume"},
    "CVX":   {"name": "Chevron Corp",                 "category": "value",        "expert": "Warren Buffett",  "rationale": "Integrated energy major; strong free cash flow and consistent dividends"},
    # AI / Tech Growth — CNBC analyst consensus, institutional upgrades
    "NVDA":  {"name": "NVIDIA Corp",                  "category": "growth",       "expert": "Industry Consensus", "rationale": "AI GPU monopoly; data center secular demand; #1 analyst price target raises"},
    "MSFT":  {"name": "Microsoft Corp",               "category": "growth",       "expert": "Industry Consensus", "rationale": "Azure cloud + Copilot AI monetization; most diversified platform leader"},
    "META":  {"name": "Meta Platforms",               "category": "growth",       "expert": "Industry Consensus", "rationale": "Social ad duopoly; Llama AI investment paying off in margins and engagement"},
    "AMZN":  {"name": "Amazon.com Inc",               "category": "growth",       "expert": "Industry Consensus", "rationale": "AWS cloud growth + ad revenue acceleration; retail margin expansion"},
    "GOOGL": {"name": "Alphabet Inc",                 "category": "growth",       "expert": "Industry Consensus", "rationale": "Search + YouTube moat; Gemini AI; Waymo autonomous optionality"},
    "TSLA":  {"name": "Tesla Inc",                    "category": "growth",       "expert": "Industry Consensus", "rationale": "EV + energy + robotics; highest retail options volume on market"},
    "PLTR":  {"name": "Palantir Technologies",        "category": "growth",       "expert": "Industry Consensus", "rationale": "AI data platform; US gov + commercial SaaS growth; S&P 500 inclusion momentum"},
    # High-liquidity options plays — tastytrade / Tom Sosnoff / CBOE style
    "SPY":   {"name": "S&P 500 ETF (SPDR)",          "category": "index_etf",    "expert": "Options Specialists", "rationale": "Highest global options volume; 0DTE/weekly contracts; market benchmark"},
    "QQQ":   {"name": "Invesco Nasdaq-100 ETF",      "category": "index_etf",    "expert": "Options Specialists", "rationale": "Tech-heavy high-beta index; liquid weeklies; ideal momentum plays"},
    "IWM":   {"name": "iShares Russell 2000 ETF",    "category": "index_etf",    "expert": "Options Specialists", "rationale": "Small-cap vol plays; mean-reversion iron condors; high IV relative value"},
    "IBIT":  {"name": "iShares Bitcoin ETF",          "category": "crypto_etf",   "expert": "Options Specialists", "rationale": "Bitcoin spot exposure with options; extremely high IV for premium capture"},
}

# ---------------------------------------------------------------------------
# News sentiment scoring
# ---------------------------------------------------------------------------

_BULLISH = frozenset({
    "beat", "beats", "surge", "surges", "record", "upgrade", "upgrades", "buy",
    "outperform", "strong", "growth", "profit", "profits", "revenue", "rally",
    "gain", "gains", "high", "positive", "exceed", "exceeds", "top", "best",
    "breakthrough", "partnership", "deal", "launch", "bullish", "raised", "rises",
    "rise", "soars", "soar", "jumps", "jump", "expands", "expansion",
})

_BEARISH = frozenset({
    "miss", "misses", "fall", "falls", "decline", "declines", "downgrade",
    "downgrades", "sell", "underperform", "weak", "loss", "losses", "cut", "cuts",
    "drop", "drops", "bearish", "plunge", "plunges", "low", "negative", "concern",
    "concerns", "risk", "risks", "warning", "warnings", "recall", "investigation",
    "lawsuit", "fraud", "below", "shrinks", "shrink", "slumps", "slump",
})


def _score_headline(title: str) -> int:
    words = set(title.lower().split())
    return len(words & _BULLISH) - len(words & _BEARISH)


def _fetch_sentiment_for(symbol: str) -> Dict[str, Any]:
    pick = EXPERT_PICKS.get(symbol, {})
    base = {
        "symbol": symbol,
        "name": pick.get("name", symbol),
        "category": pick.get("category", "other"),
        "expert": pick.get("expert", ""),
        "rationale": pick.get("rationale", ""),
    }
    try:
        ticker = yf.Ticker(symbol.replace(".", "-"))
        news = ticker.news or []
        score = 0
        headlines: List[str] = []
        for item in news[:10]:
            content = item.get("content") or {}
            title = (
                item.get("title")
                or content.get("title")
                or ""
            )
            if not title:
                continue
            headlines.append(title[:100])
            score += _score_headline(title)
        return {
            **base,
            "news_score": score,
            "articles_scanned": min(len(news), 10),
            "sentiment": "bullish" if score > 1 else "bearish" if score < -1 else "neutral",
            "sample_headlines": headlines[:3],
        }
    except Exception:
        return {
            **base,
            "news_score": 0,
            "articles_scanned": 0,
            "sentiment": "neutral",
            "sample_headlines": [],
        }


async def scan_news_sentiment_async(symbols: List[str]) -> List[Dict[str, Any]]:
    """Fetch and score news for each symbol concurrently (runs yfinance in thread pool)."""
    tasks = [asyncio.to_thread(_fetch_sentiment_for, sym) for sym in symbols]
    results = await asyncio.gather(*tasks)
    return sorted(results, key=lambda x: x["news_score"], reverse=True)


def scan_news_sentiment(symbols: List[str]) -> List[Dict[str, Any]]:
    """Synchronous wrapper — use scan_news_sentiment_async in async contexts."""
    results = [_fetch_sentiment_for(sym) for sym in symbols]
    return sorted(results, key=lambda x: x["news_score"], reverse=True)


# ---------------------------------------------------------------------------
# ATM options symbol generator
# ---------------------------------------------------------------------------

def _next_expiry_friday(weeks_out: int = 2) -> date:
    """Return the Nth Friday from today (standard monthly/weekly options expiry)."""
    today = date.today()
    days_to_fri = (4 - today.weekday()) % 7 or 7
    return today + timedelta(days=days_to_fri + (weeks_out - 1) * 7)


def _atm_strike(price: float) -> float:
    """Round price to the nearest standard options strike increment."""
    if price >= 500:
        inc = 5.0
    elif price >= 100:
        inc = 1.0
    else:
        inc = 0.5
    return round(round(price / inc) * inc, 3)


def _occ_symbol(root: str, expiry: date, opt_type: str, strike: float) -> str:
    """Build OCC-formatted Alpaca option symbol with O: prefix.

    Format: O:{ROOT}{YYMMDD}{C|P}{8-digit strike * 1000}
    Example: O:SPY260502C00705000  (SPY, 2026-05-02, Call, $705.00)
    """
    root_clean = root.replace(".", "").upper()
    exp_str = expiry.strftime("%y%m%d")
    strike_padded = f"{int(round(strike * 1000)):08d}"
    return f"O:{root_clean}{exp_str}{opt_type.upper()}{strike_padded}"


def get_atm_option_symbols(symbol: str, weeks_out: int = 2) -> List[str]:
    """Return [call_symbol, put_symbol] at the nearest ATM strike for the given underlying.

    Snaps to a strike that Alpaca actually lists for the chosen expiry. Without
    this, the heuristic picker can land on a strike that isn't listed (e.g.
    AMZN ~$238 has $5 weekly spacing, not $1) and every order gets rejected
    with a 404. We query Alpaca's contracts endpoint and pick the listed
    strike closest to the current price, then fall back to the heuristic only
    if the listing API is unreachable.
    """
    try:
        info = yf.Ticker(symbol.replace(".", "-")).info
        price = info.get("currentPrice") or info.get("regularMarketPrice")
        if not price:
            return []
        price  = float(price)
        expiry = _next_expiry_friday(weeks_out)

        # Try Alpaca's listed strikes first — authoritative for "does this
        # contract exist". Import here to avoid a circular import at module load.
        from app.services.alpaca_client import alpaca_client

        listed = alpaca_client.list_option_contracts(
            symbol, expiry, "call",
            strike_min=price * 0.9, strike_max=price * 1.1,
        )
        if listed:
            tradable = [c for c in listed if c.get("tradable", True)]
            pool = tradable or listed
            nearest = min(pool, key=lambda c: abs(c["strike_price"] - price))
            strike = nearest["strike_price"]
        else:
            strike = _atm_strike(price)

        return [
            _occ_symbol(symbol, expiry, "C", strike),
            _occ_symbol(symbol, expiry, "P", strike),
        ]
    except Exception:
        return []
