"""Fetch index PE and percentile from danjuanfunds (free, no-auth).

Covers 63 indices across A-share, HK, QDII markets.
"""

import logging
import time
import requests
from datetime import datetime

log = logging.getLogger("invest.data.index_pe")

DANJUAN_URL = "https://danjuanfunds.com/djapi/index_eva/dj"

# danjuanfunds code -> our internal index code (pass-through if not listed)
CODE_MAP = {
    # HK
    "HKHSI": "HSI",
    "HKHSTECH": "HSTECH",
    "HKHSCEI": "HSCEI",
    "HKHSSCNE": "HSSCNE",
    "HSFML25": "HSFML25",
    "SPHCMSHP": "SPHCMSHP",
    # QDII / global
    "SP500": "SPX",
    "NDX": "NDX",
    "GDAXI": "DAX",
    "935600": "MSCI_INDIA",
    # A-share (supplement akshare)
    "SH000300": "000300",
    "SH000905": "000905",
    "SH000016": "000016",
    "SH000922": "000922",
    "SH000015": "000015",
    "SH000010": "000180",
    "SH000852": "001000",
    "SH000688": "000688",
    "SZ399001": "399001",
    "SZ399006": "399006",
    "SZ399812": "399812",
    "SZ399975": "399975",
    "SZ399986": "399986",
    "SZ399989": "399989",
    "SZ399997": "399997",
    "SZ399998": "399998",
    "SZ399967": "399967",
    "SZ399971": "399971",
    "SZ399610": "399610",
    "SZ399550": "399550",
    "SZ399330": "399100",
    "SZ399324": "399324",
    "SZ399317": "399317",
    "SZ399702": "399702",
    "SZ399701": "399701",
    "SZ399393": "399393",
    "SZ399396": "399396",
    "SZ399417": "399417",
}


def _fetch_danjuan() -> list[dict] | None:
    """Fetch index valuation data from danjuanfunds. Returns list of index dicts or None."""
    delays = [0, 3, 6]
    for attempt, delay in enumerate(delays):
        try:
            if attempt > 0:
                time.sleep(delay)
            r = requests.get(
                DANJUAN_URL,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"},
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            items = data.get("data", {}).get("items", [])
            log.info("danjuanfunds: %d indices fetched (attempt %d)", len(items), attempt + 1)
            return items
        except Exception as e:
            if attempt < len(delays) - 1:
                log.warning("danjuanfunds attempt %d failed: %s, retrying in %ds", attempt + 1, e, delays[attempt + 1])
            else:
                log.warning("danjuanfunds all %d attempts failed: %s", len(delays), e)
    return None


def fetch_all_index_pe() -> dict[str, dict]:
    """Fetch PE data for all indices from danjuanfunds.

    Returns {our_code: {pe, pe_percentile, pb, pb_percentile, name, updated}}.
    Empty dict on failure.
    """
    items = _fetch_danjuan()
    if not items:
        return {}

    result = {}
    for item in items:
        code = item.get("index_code", "")
        our_code = CODE_MAP.get(code, code)
        result[our_code] = {
            "pe": item.get("pe"),
            "pe_percentile": item.get("pe_percentile"),
            "pb": item.get("pb"),
            "pb_percentile": item.get("pb_percentile"),
            "roe": item.get("roe"),
            "yield_pct": item.get("yeild"),
            "name": item.get("name", ""),
            "updated": item.get("date", ""),
            "source": "danjuanfunds",
            "fetched_at": datetime.now().isoformat(),
        }

    pe_val = result.get("HSTECH", {}).get("pe") or 0
    hsi_pct = (result.get("HSI", {}).get("pe_percentile") or 0) * 100
    spx_pct = (result.get("SPX", {}).get("pe_percentile") or 0) * 100
    log.info("danjuanfunds: HSTECH PE=%.1f HSI pct=%.0f%% SPX pct=%.0f%%", pe_val, hsi_pct, spx_pct)
    return result


def store_index_pe(repo) -> dict[str, dict]:
    """Fetch index PE and store in pe_history. Returns the fetched data."""
    data = fetch_all_index_pe()

    # Supplement missing HSI/SPX/NDX from web sources
    web_targets = {"HSI", "SPX", "NDX"}
    existing = set(data.keys())
    missing = web_targets - existing

    if missing:
        log.info("danjuanfunds missing: %s, trying web fallback", missing)
        try:
            from src.data.web_pe import fetch_web_index_pe
            web_data = fetch_web_index_pe(repo)
            for code in missing:
                if code in web_data:
                    data[code] = web_data[code]
                    log.info("web_pe: supplemented %s PE=%.2f pct=%.1f%%",
                             code, web_data[code]["pe"],
                             (web_data[code].get("pe_percentile") or 0) * 100)
        except Exception as e:
            log.warning("web_pe fallback failed: %s", e)

    for code, info in data.items():
        if info["pe"] and info["pe"] > 0:
            repo.save_pe(code, info["pe"])
    return data
