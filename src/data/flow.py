"""Fund flow signal: 10-day MA of 主力净流入-净占比.

Daily snapshots from fund_etf_spot_em (all ETFs, one API call) → cached in
pe_history as FLOW:{code}:{date}. MA computed on-the-fly from stored entries.
- Capture: once per day (sentinel check skips re-fetch)
- Bootstrap: stock_individual_fund_flow backfills history on first ETF use
- MA window: 10 trading days (~2 weeks), aligned with months-long holding period
"""

import logging
from datetime import date, datetime
import pandas as pd

log = logging.getLogger("invest.data.flow")

MA_DAYS = 10  # trading days (~2 weeks), matched to months-long holding
BUY_THRESHOLD = 3.0      # 5-day MA > 3%  → signal +1
SELL_THRESHOLD = -3.0    # 5-day MA < -3% → signal -1


def _today() -> str:
    return date.today().isoformat()


def capture_daily_flow(repo) -> dict[str, float]:
    """Fetch today's flow snapshot for all ETFs (one API call). Cached per day.

    Returns {code: flow_pct}.
    """
    today = _today()
    sentinel = f"FLOW_SENTINEL:{today}"
    if repo.get_pe_history(sentinel):
        return _load_today(repo, today)

    try:
        import akshare as ak
        df = ak.fund_etf_spot_em()
        code_col = df.columns[0]
        flow_col = df.columns[20]  # 主力净流入-净占比

        result = {}
        for _, row in df.iterrows():
            code = str(row[code_col]).replace("sz", "").replace("sh", "")
            flow_pct = float(row[flow_col]) if pd.notna(row[flow_col]) else 0.0
            result[code] = flow_pct
            repo.save_pe(f"FLOW:{code}:{today}", flow_pct)

        repo.save_pe(sentinel, 1.0)
        log.info("Flow: captured %d ETFs for %s", len(result), today)
        return result
    except Exception as e:
        log.warning("Flow capture failed: %s", e)
        return {}


def _load_today(repo, today: str) -> dict[str, float]:
    """Load cached today flow from pe_history."""
    result = {}
    try:
        rows = repo.conn.execute(
            "SELECT index_code, pe_value FROM pe_history WHERE index_code LIKE ?",
            (f"FLOW:%:{today}",),
        ).fetchall()
        for row in rows:
            parts = row[0].split(":")
            if len(parts) >= 3:
                result[parts[1]] = row[1]
    except Exception:
        pass
    return result


def compute_flow_signal(etf_code: str, flow_today: float, repo) -> tuple[float, int]:
    """Compute 5-day MA and signal for an ETF.

    Pulls recent FLOW:{code}:* entries from pe_history for MA.
    Bootstraps from stock_individual_fund_flow on first use.
    Returns (ma_pct, signal).
    """
    history = _load_history(etf_code, repo)
    if len(history) < MA_DAYS:
        history = _bootstrap(etf_code, flow_today, repo)

    ma = round(sum(history) / len(history), 2) if history else flow_today

    signal = 0
    if ma > BUY_THRESHOLD:
        signal = 1
    elif ma < SELL_THRESHOLD:
        signal = -1

    return ma, signal


def _load_history(etf_code: str, repo) -> list[float]:
    """Load last MA_DAYS flow values from pe_history."""
    rows = repo.conn.execute(
        "SELECT pe_value FROM pe_history WHERE index_code LIKE ? ORDER BY recorded_at DESC LIMIT ?",
        (f"FLOW:{etf_code}:%", MA_DAYS),
    ).fetchall()
    vals = [r[0] for r in rows if r[0] and abs(r[0]) < 1000]
    vals.reverse()
    return vals


def _bootstrap(etf_code: str, flow_today: float, repo) -> list[float]:
    """Backfill history from stock_individual_fund_flow, then store."""
    try:
        import akshare as ak
        market = "sz" if etf_code.startswith(("1", "5")) else "sh"
        df = ak.stock_individual_fund_flow(stock=etf_code, market=market)
        if df is not None and not df.empty:
            flow_col = df.columns[4]  # 主力净流入-净占比
            date_col = df.columns[0]
            stored = 0
            for _, row in df.iterrows():
                d = str(row[date_col])[:10]
                flow_pct = float(row[flow_col]) if pd.notna(row[flow_col]) else 0.0
                repo.save_pe(f"FLOW:{etf_code}:{d}", flow_pct)
                stored += 1
            log.info("Flow %s: bootstrapped %d historical points", etf_code, stored)
    except Exception as e:
        log.debug("Flow bootstrap failed for %s: %s", etf_code, e)

    # Re-read history (now includes bootstrapped + today's capture)
    return _load_history(etf_code, repo)
