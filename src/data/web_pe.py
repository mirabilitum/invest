"""Multi-source PE fetcher — siblisresearch (HSI) + Yahoo Finance (SPX/NDX).

Web-scraped fallback when danjuanfunds API is unreachable.
"""

import logging
import re
import time
from datetime import datetime

import requests

log = logging.getLogger("invest.data.web_pe")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"


def _get(url, **kw):
    """GET with proxy bypass, matching index_pe.py behaviour."""
    kw.setdefault("headers", {"User-Agent": UA})
    kw.setdefault("timeout", 15)
    kw.setdefault("proxies", {"http": None, "https": None})  # bypass system proxy
    return requests.get(url, **kw)


# ── siblisresearch (HSI) ──────────────────────────────────────────────

SIBLIS_HSI_URL = "https://siblisresearch.com/data/hang-seng-cape/"


def fetch_siblis_hsi() -> dict | None:
    """Scrape HSI PE (TTM), forward PE, and CAPE from siblisresearch.com.

    Returns {pe, forward_pe, cape, pe_percentile, name, source, history, fetched_at}
    or None on failure.
    """
    try:
        r = _get(SIBLIS_HSI_URL)
        r.raise_for_status()

        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", r.text, re.DOTALL)
        history = []

        for row in rows:
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL)
            clean = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
            if clean and re.match(r"\d{1,2}/\d{2}/\d{4}", clean[0]):
                history.append({
                    "date": clean[0],
                    "pe": float(clean[2]),
                    "forward_pe": float(clean[4]) if len(clean) > 4 and clean[4] else None,
                    "cape": float(clean[6]) if len(clean) > 6 and clean[6] else None,
                })

        if not history:
            log.warning("siblisresearch: no data rows found")
            return None

        latest = history[0]

        # Compute rough percentile from available semi-annual history
        pe_values = [h["pe"] for h in history]
        pct = round(sum(1 for v in pe_values if v < latest["pe"]) / len(pe_values), 4)

        log.info("siblisresearch HSI: PE=%.2f pct=%.1f%% (%d history points)",
                 latest["pe"], pct * 100, len(history))

        return {
            "pe": latest["pe"],
            "pe_percentile": pct,
            "forward_pe": latest.get("forward_pe"),
            "cape": latest.get("cape"),
            "name": "恒生指数",
            "source": "siblisresearch",
            "history": history,
            "fetched_at": datetime.now().isoformat(),
        }
    except Exception as e:
        log.warning("siblisresearch HSI fetch failed: %s", e)
        return None


# ── Yahoo Finance (SPX/NDX via SPY/QQQ) ───────────────────────────────

YAHOO_QUOTE_URL = "https://query2.finance.yahoo.com/v7/finance/quote"
YAHOO_CRUMB_URL = "https://query2.finance.yahoo.com/v1/test/getcrumb"


def _yahoo_session():
    """Create a fresh session with Yahoo cookies (fc.yahoo.com → crumb)."""
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    s.trust_env = False  # bypass system proxy
    try:
        s.get("https://fc.yahoo.com/", timeout=10)  # sets required cookie
    except Exception:
        pass
    return s


def fetch_yahoo_pe(symbol: str) -> dict | None:
    """Fetch trailing PE from Yahoo Finance for a US-listed ETF (SPY/QQQ).

    Returns {pe, name, source, fetched_at} or None.
    """
    try:
        s = _yahoo_session()
        crumb_r = s.get(YAHOO_CRUMB_URL, timeout=10)
        crumb = crumb_r.text.strip()

        # crumb must be a plain string, not JSON error
        if not crumb or crumb.startswith("{"):
            log.warning("yahoo %s: invalid crumb response", symbol)
            return None

        r = s.get(YAHOO_QUOTE_URL, params={"symbols": symbol, "crumb": crumb}, timeout=15)
        data = r.json()
        results = data.get("quoteResponse", {}).get("result", [])
        if not results:
            log.warning("yahoo %s: no results in response", symbol)
            return None

        result = results[0]
        pe = result.get("trailingPE") or result.get("forwardPE")
        if not pe:
            log.warning("yahoo %s: no PE field available", symbol)
            return None

        name = result.get("shortName", symbol)
        log.info("yahoo %s: PE=%.2f (%s)", symbol, float(pe), name)

        return {
            "pe": float(pe),
            "pe_percentile": None,
            "name": name,
            "source": "yahoo",
            "fetched_at": datetime.now().isoformat(),
        }
    except Exception as e:
        log.warning("yahoo %s fetch failed: %s", symbol, e)
        return None


# ── Aggregator ────────────────────────────────────────────────────────

YAHOO_MAP = {"SPY": "SPX", "QQQ": "NDX"}


def fetch_web_index_pe(repo=None) -> dict[str, dict]:
    """Fetch PE from web sources for HSI, SPX, NDX.

    Stores PE values in pe_history via repo (if provided) and computes
    percentile from stored history.

    Returns {our_code: {pe, pe_percentile, name, source, ...}}
    """
    result = {}

    # HSI from siblisresearch
    try:
        hsi_data = fetch_siblis_hsi()
        if hsi_data:
            hsi_data.pop("history", None)  # internal use only
            if repo:
                repo.save_pe("HSI", hsi_data["pe"])
                pct = repo.compute_pe_percentile("HSI", hsi_data["pe"])
                if pct is not None:
                    hsi_data["pe_percentile"] = round(pct, 4)
            result["HSI"] = hsi_data
    except Exception as e:
        log.warning("web HSI fallback failed: %s", e)

    # SPX/NDX from Yahoo
    for ysym, our_code in YAHOO_MAP.items():
        try:
            y_data = fetch_yahoo_pe(ysym)
            if y_data:
                if repo:
                    repo.save_pe(our_code, y_data["pe"])
                    pct = repo.compute_pe_percentile(our_code, y_data["pe"])
                    if pct is not None:
                        y_data["pe_percentile"] = round(pct, 4)
                result[our_code] = y_data
            time.sleep(2)  # avoid rate limiting
        except Exception as e:
            log.warning("web %s fallback failed: %s", e)

    return result
