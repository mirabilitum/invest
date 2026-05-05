"""ETF PE computation from fund holdings via East Money push2 API.

Pipeline:
  1. F10 holdings page → stock codes + weights
  2. push2 API → PE per stock
  3. Weighted average → ETF PE
"""

import logging
import re
import time
import requests
from datetime import datetime

log = logging.getLogger("invest.data.etf_pe")

# push2 API secid prefix by exchange
# US stocks (NVDA, AAPL): 105
# HK stocks (00700): 116
# Shanghai A-share (600519): 1
# Shenzhen A-share (000858): 0
EXCHANGE_PREFIX = {
    "us": "105",
    "hk": "116",
    "sh": "1",
    "sz": "0",
}

PUSH2_FIELDS = "f57,f58,f43,f162,f163,f164,f165"


def _classify_stock(code: str) -> str:
    """Determine exchange for a stock code. Returns 'us', 'hk', 'sh', or 'sz'."""
    if re.match(r"^[A-Za-z]", code):
        return "us"
    if re.match(r"^0\d{4}$", code):
        return "hk"
    if re.match(r"^[36]\d{5}$", code):
        return "sh"
    if re.match(r"^[0123]\d{5}$", code):
        return "sz"
    return "us"


def _make_secid(code: str) -> str:
    """Build push2 secid from stock code."""
    exchange = _classify_stock(code)
    prefix = EXCHANGE_PREFIX.get(exchange, "105")
    return f"{prefix}.{code}"


def _parse_f10_holdings(html_content: str) -> list[dict]:
    """Parse fund holdings table from F10 page HTML.

    Returns list of {code, name, weight_pct}.
    """
    # Extract table rows
    tr_pattern = r"<tr[^>]*>(.*?)</tr>"
    td_pattern = r"<td[^>]*>(.*?)</td>"

    rows = []
    for tr_match in re.finditer(tr_pattern, html_content, re.DOTALL):
        cells = re.findall(td_pattern, tr_match.group(1))
        if len(cells) >= 7:
            # Clean HTML tags from each cell
            cleaned = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
            rows.append(cleaned)

    holdings = []
    for row in rows:
        if len(row) < 7:
            continue
        # Column 0: index, Col 1: stock code, Col 2: name, Col 6: weight
        code = row[1].strip()
        name = row[2].strip()
        weight_str = row[6].replace("%", "").strip()

        if not code or not re.match(r"^[A-Za-z0-9.]+$", code):
            continue

        try:
            weight = float(weight_str) / 100.0
        except (ValueError, TypeError):
            continue

        if weight > 0:
            holdings.append({"code": code, "name": name, "weight": weight})

    return holdings


def fetch_etf_holdings(etf_code: str) -> list[dict]:
    """Fetch top holdings for a QDII/HK ETF from F10 page.

    Returns list of {code, name, weight}. Empty on failure.
    """
    now = datetime.now()
    year = now.year
    month = ((now.month - 1) // 3) * 3 + 1  # Start of current quarter
    if month == 1:
        month = 3  # Use last quarter's data early in quarter

    url = (
        f"https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
        f"?type=jjcc&code={etf_code}&topline=10&year={year}&month={month}"
    )

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
        "Referer": f"https://fundf10.eastmoney.com/ccmx_{etf_code}.html",
    }

    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.encoding = "utf-8"
        holdings = _parse_f10_holdings(r.text)
        if holdings:
            log.info("ETF %s: %d holdings parsed", etf_code, len(holdings))
        else:
            log.debug("ETF %s: no holdings found in F10", etf_code)
        return holdings
    except Exception as e:
        log.warning("ETF %s holdings fetch failed: %s", etf_code, e)
        return []


def fetch_stock_pe(stock_code: str) -> float | None:
    """Fetch PE (TTM) for a single stock via push2 API.

    Returns PE value or None.
    """
    secid = _make_secid(stock_code)
    url = (
        f"https://push2.eastmoney.com/api/qt/stock/get"
        f"?secid={secid}&fields={PUSH2_FIELDS}"
    )

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://quote.eastmoney.com/",
    }

    try:
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        d = data.get("data")
        if not d:
            return None

        # f164 = PE TTM (scaled by 100), f163 = PE static, f162 = PE dynamic
        pe_ttm = d.get("f164")
        pe_static = d.get("f163")

        # Prefer TTM, fall back to static
        pe = pe_ttm or pe_static
        if pe and pe != 0:
            return pe / 100.0
        return None
    except Exception as e:
        log.debug("Stock %s PE fetch failed: %s", stock_code, e)
        return None


def compute_etf_pe(etf_code: str, repo=None) -> dict | None:
    """Compute ETF PE from holdings + stock PEs.

    Returns {pe, holdings_used, total_weight, missing_pe_count} or None.
    If repo is provided, stores result in pe_history.
    """
    holdings = fetch_etf_holdings(etf_code)
    if not holdings:
        return None

    weighted_pe = 0.0
    total_weight = 0.0
    missing = 0

    for i, h in enumerate(holdings):
        # Rate limit between push2 calls
        if i > 0:
            time.sleep(0.15)

        pe = fetch_stock_pe(h["code"])
        if pe is not None and pe > 0:
            weighted_pe += pe * h["weight"]
            total_weight += h["weight"]
        else:
            missing += 1

    if total_weight == 0:
        return None

    etf_pe = round(weighted_pe / total_weight, 2)

    result = {
        "code": etf_code,
        "pe": etf_pe,
        "holdings_used": len(holdings) - missing,
        "total_weight_pct": round(total_weight * 100, 1),
        "missing_pe_count": missing,
        "fetched_at": datetime.now().isoformat(),
    }

    if repo:
        repo.save_pe(f"ETF:{etf_code}", etf_pe)

    return result


def resolve_etf_pe_percentile(etf_code: str, repo) -> float | None:
    """Resolve PE percentile for an ETF from stored history.

    1. Compute fresh ETF PE (if possible)
    2. Look up stored history
    3. Compute percentile or return None
    """
    history = repo.get_pe_history(f"ETF:{etf_code}")
    if history:
        current = history[-1]
        pct = repo.compute_pe_percentile(f"ETF:{etf_code}", current)
        if pct is not None and len(history) >= 3:
            return pct
        # < 3 points: return current PE value for band estimation
        # (caller should blend with absolute band)
        if pct is not None:
            return pct

    # Try to compute fresh
    result = compute_etf_pe(etf_code, repo)
    if result:
        # Single data point — return for band blending
        history = repo.get_pe_history(f"ETF:{etf_code}")
        if history:
            return repo.compute_pe_percentile(f"ETF:{etf_code}", history[-1])
    return None
