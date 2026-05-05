import logging
import numpy as np

log = logging.getLogger("invest.data.volatility")


def fetch_implied_volatility(market: str) -> float | None:
    """Fetch VIX/VHSI/iVIX. Returns None on failure."""
    try:
        import akshare as ak
        if market == "us":
            # VIX via akshare (may not be available, falls back to historical)
            df = ak.index_vix()
            if df is not None and not df.empty:
                return float(df.iloc[-1].get("close", 0))
        elif market == "hk":
            df = ak.stock_hk_index_daily_em(symbol="VHSI")
            if df is not None and not df.empty:
                return float(df.iloc[-1].get("close", 0))
        elif market == "a_share":
            # iVIX via SSE 50 ETF options QVIX
            df = ak.index_option_50etf_qvix()
            if df is not None and not df.empty:
                return float(df.iloc[-1].get("close", 0))
    except Exception as e:
        log.warning("Implied volatility fetch failed for %s: %s", market, e)
    return None


def compute_historical_volatility(returns: list[float], annualize: int = 252) -> float:
    """Compute annualized historical volatility from daily returns."""
    if len(returns) < 5:
        return 0.0
    return float(np.std(returns) * np.sqrt(annualize))


def fetch_volatility_with_fallback(market: str, prices: list[float] = None) -> tuple[float, str]:
    """Fetch volatility, falling back to historical if implied unavailable.

    Returns (value, source) where source is 'implied' | 'historical' | 'zero'.
    Logs an alert when falling back.
    """
    iv = fetch_implied_volatility(market)
    if iv is not None:
        return (iv, "implied")

    if prices and len(prices) >= 5:
        returns = [prices[i] / prices[i-1] - 1 for i in range(1, len(prices))]
        hv = compute_historical_volatility(returns)
        log.warning("Volatility %s: using historical vol %.4f (implied unavailable)", market, hv)
        return (hv, "historical")

    log.error("Volatility %s: no data available, returning 0", market)
    return (0.0, "zero")
