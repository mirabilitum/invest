import logging
import os
import pandas as pd
from datetime import datetime
from src.data.calendar import is_trading_day

log = logging.getLogger("invest.data.market")

PE_FIELD_MAP = {"市盈率1": "pe", "市盈率2": "pe2", "市盈率": "pe", "pe": "pe", "PE": "pe"}


def _safe_float(val, default=None):
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _map_columns(df: pd.DataFrame, field_map: dict) -> dict:
    result = {}
    for col in df.columns:
        if col in field_map:
            result[field_map[col]] = col
    missing = set(field_map.values()) - set(result.keys())
    if missing:
        log.debug("Unmapped fields: %s", missing)
    return result


def fetch_index_valuation(index_code: str, market: str) -> dict | None:
    """Fetch PE and PE percentile for a major index.

    A-share: stock_zh_index_hist_csindex (full history, 3500+ records).
    HK: stock_hk_index_daily_em (price only, no PE).
    QDII/US: index_global_spot_em (price only, no PE).
    """
    if market == "a_share":
        return _fetch_a_share(index_code)

    if market == "hk":
        return _fetch_hk_index(index_code)

    if market == "qdii":
        return _fetch_global_index(index_code)

    return None


def _fetch_a_share(index_code: str) -> dict | None:
    """Fetch A-share index PE percentile via stock_zh_index_hist_csindex.

    Blends 5-year rolling (60% weight, responsive to recent regime)
    with full-history (40% weight, anchors against long-term baseline).
    """
    try:
        import akshare as ak

        df = ak.stock_zh_index_hist_csindex(symbol=index_code, start_date="20050101", end_date="20500101")
        if df is None or df.empty:
            return None

        df["date"] = pd.to_datetime(df["日期"])
        df = df.sort_values("date")
        pe_col = "滚动市盈率"
        df["pe"] = pd.to_numeric(df[pe_col], errors="coerce")
        df = df.dropna(subset=["pe"])

        if df.empty:
            return None

        current_pe = float(df["pe"].iloc[-1])
        current_date = df["date"].iloc[-1]

        # Full-history percentile
        full_pct = round((df["pe"] < current_pe).sum() / len(df["pe"]), 4)

        # 5-year rolling percentile
        five_yr_ago = current_date - pd.DateOffset(years=5)
        recent = df[df["date"] >= five_yr_ago]
        if len(recent) >= 12:
            rolling_pct = round((recent["pe"] < current_pe).sum() / len(recent["pe"]), 4)
        else:
            rolling_pct = full_pct

        # Blend: 60% rolling (responsive) + 40% full (anchor)
        blended_pct = round(0.6 * rolling_pct + 0.4 * full_pct, 4)

        name_map = {"000300": "沪深300", "000905": "中证500", "000016": "上证50"}
        return {
            "code": index_code,
            "name": name_map.get(index_code, index_code),
            "market": "a_share",
            "pe": round(current_pe, 2),
            "pe_percentile": blended_pct,
            "full_pct": full_pct,
            "rolling_5yr_pct": rolling_pct,
            "fetched_at": datetime.now().isoformat(),
            "source": "csindex_blended",
        }
    except Exception as e:
        log.warning("A-share index hist fetch failed for %s: %s", index_code, e)

    return _fetch_a_share_tushare(index_code)


def _fetch_a_share_tushare(index_code: str) -> dict | None:
    """Fallback: fetch A-share index PE via tushare."""
    try:
        import tushare as ts

        token = os.environ.get("TUSHARE_TOKEN", "")
        if not token:
            log.debug("TUSHARE_TOKEN not set, skipping tushare fallback")
            return None

        ts.set_token(token)
        pro = ts.pro_api()
        ts_map = {"000300": "000300.SH", "000905": "000905.SH"}
        ts_code = ts_map.get(index_code, index_code)
        df = pro.index_dailybasic(ts_code=ts_code, fields="ts_code,trade_date,pe,pb")
        if df is None or df.empty:
            return None
        latest = df.iloc[0]
        pe_val = _safe_float(latest.get("pe"))
        return {
            "code": index_code,
            "name": ts_code,
            "market": "a_share",
            "pe": pe_val,
            "pe_percentile": None,
            "fetched_at": datetime.now().isoformat(),
            "source": "tushare",
        }
    except Exception as e:
        log.warning("Tushare fallback failed for %s: %s", index_code, e)
        return None


def _fetch_hk_index(index_code: str) -> dict | None:
    """Fetch HK index data. Price only — PE not available from this API."""
    try:
        import akshare as ak

        df = ak.stock_hk_index_daily_em(symbol=index_code)
        if df is None or df.empty:
            return None

        latest = df.iloc[-1]
        close = _safe_float(latest.get("close", latest.get("收盘", 0)))
        return {
            "code": index_code,
            "name": str(latest.get("name", index_code)),
            "market": "hk",
            "pe": close,  # Price as proxy since no PE
            "pe_percentile": None,
            "fetched_at": datetime.now().isoformat(),
        }
    except Exception as e:
        log.warning("HK index fetch failed for %s: %s", index_code, e)
        return None


def _fetch_global_index(index_code: str) -> dict | None:
    """Fetch global index (US/Europe) spot data. Price only — no PE."""
    try:
        import akshare as ak

        df = ak.index_global_spot_em()
        if df is None or df.empty:
            return None

        keyword_map = {
            "SPX": "标普500",
            "NDX": "纳斯达克",
            "DJI": "道琼斯",
        }
        keyword = keyword_map.get(index_code, index_code)

        match = df[df["名称"].str.contains(keyword, na=False)]
        if match.empty:
            log.warning("Global index %s not found in spot data", index_code)
            return None

        row = match.iloc[0]
        close = _safe_float(row.get("最新价", 0))
        change_pct = _safe_float(row.get("涨跌幅", 0))

        return {
            "code": index_code,
            "name": str(row.get("名称", index_code)),
            "market": "qdii",
            "pe": close,  # Price as proxy since no free PE source
            "pe_percentile": None,
            "change_pct": change_pct,
            "fetched_at": datetime.now().isoformat(),
        }
    except Exception as e:
        log.warning("Global index fetch failed for %s: %s", index_code, e)
        return None


def fetch_all_index_valuations() -> list[dict]:
    """Fetch valuations for all tracked indices. Returns list, empty on total failure."""
    import time

    INDICES = [
        ("000300", "a_share"),
        ("000905", "a_share"),
        ("000016", "a_share"),
        ("SPX", "qdii"),
        ("NDX", "qdii"),
        ("HSI", "hk"),
    ]
    results = []
    for code, market in INDICES:
        data = fetch_index_valuation(code, market)
        if data:
            results.append(data)
        else:
            log.warning("Missing valuation data for %s (%s)", code, market)
        time.sleep(0.5)
    return results
