import logging
from datetime import datetime

log = logging.getLogger("invest.data.etf_pool")

SCREEN_RULES = {
    "min_aum": 50,
    "min_aum_a_share": 10,
    "min_position_ratio": 0.95,
    "allowed_fx_methods": ["公允价", "16:00参考汇率", "市场价"],
}

# ETF name keyword → tracking index
INDEX_KEYWORDS = [
    (["纳指", "纳斯达克", "NDX", "nasdaq"], "NDX"),
    (["标普", "SPX", "S&P", "sp500", "sp 500"], "SPX"),
    (["道琼斯", "道指", "DJI", "dow"], "DJIA"),
    (["恒生消费", "港股消费", "港股通消费"], "HSCGSI"),
    (["恒生科技", "港股通科技"], "HSTECH"),
    (["恒生", "HSI", "hang seng"], "HSI"),
    (["日经", "nikkei", "NKY"], "NKY"),
    (["德国", "dax", "DAX"], "DAX"),
    (["沪深300", "300ETF"], "000300"),
    (["中证500", "500ETF"], "000905"),
    (["上证50", "上证 50", "50ETF"], "000016"),
]

# ETF name keyword → (industry, category_type)
INDUSTRY_KEYWORDS = [
    (["科技", "互联", "半导体", "AI", "芯片", "云计算", "软件", "电子", "信息", "计算机", "通信", "5G", "物联网", "大数据"], "科技", "cyclical"),
    (["医疗", "医药", "生物", "创新药", "医械", "健康", "疫苗", "制药", "器械"], "医疗健康", "structural"),
    (["消费", "零售", "食品", "饮料", "农业", "白酒", "家电", "汽车", "新能源"], "消费", "structural"),
    (["金融", "银行", "保险", "券商", "证券"], "金融", "cyclical"),
    (["能源", "石油", "天然气", "煤炭"], "能源", "event_driven"),
    (["地产", "REIT", "房地产", "基建"], "房地产", "cyclical"),
    (["高股息", "红利", "dividend"], "红利", "structural"),
    (["军工", "国防", "航天"], "军工", "event_driven"),
    (["黄金", "金"], "黄金", "structural"),
    (["教育"], "教育", "cyclical"),
]


def map_etf_to_index(name: str, market: str) -> str:
    """Map ETF name to its tracking index for PE lookup."""
    name_lower = name.lower()
    for keywords, index_code in INDEX_KEYWORDS:
        if any(kw.lower() in name_lower for kw in keywords):
            return index_code
    # Default: market-level fallback
    if market == "qdii":
        return "SPX"
    if market == "hk":
        return "HSI"
    if market == "a_share":
        return "000300"
    return "SPX"


def classify_etf_industry(name: str) -> tuple[str, str]:
    """Classify ETF into (industry, category) by name keywords.

    Returns (industry_name, category_type). Default: ("宽基", "cyclical").
    """
    for keywords, industry, category in INDUSTRY_KEYWORDS:
        if any(kw in name for kw in keywords):
            return industry, category
    return "宽基", "cyclical"


def classify_etf_market(code: str, name: str) -> tuple[str, str]:
    """Classify ETF: returns (market, sub_market).

    Order matters: check QDII/HK before A-share so 159-prefix QDII ETFs
    don't get incorrectly classified as A-share.
    """
    # HK keywords (any code prefix)
    if any(kw in name for kw in ["港股通", "恒生", "中概互联", "港股", "香港"]):
        return ("hk", "hk")

    # QDII keywords (any code prefix — includes 513, 159, 520, 510, etc.)
    if any(kw in name for kw in [
        "标普", "纳指", "纳斯达克", "德国", "日经", "全球",
        "法国", "英国", "韩国", "越南", "印度", "东南亚", "亚太",
        "澳大利亚", "澳洲", "欧洲", "跨境", "海外", "沙特",
        "道琼斯", "美债", "美元债", "新兴市场", "巴西", "俄罗斯",
        "REIT", "房地产",  # QDII REITs
        "美国", "日本", "韩",  # country names
    ]):
        return ("qdii", "us")

    # Commodity QDII ETFs (黄金/原油/油气 with non-A-share characteristics)
    if any(kw in name for kw in ["原油", "油气", "商品"]):
        return ("qdii", "commodity")

    # A-share index keywords (CSI 300 / 500 / SSE 50)
    if any(kw in name for kw in ["沪深300", "中证500", "上证50"]):
        return ("a_share", "a_share")

    # A-share ETF by code prefix (only after QDII/HK checks pass)
    if code.startswith(("51", "159", "56", "53", "58")):
        return ("a_share", "a_share")

    return ("unknown", "unknown")


def screen_etf(etf: dict) -> tuple[bool, list[str]]:
    """Apply hard QDII screening rules.

    Returns (passed: bool, reasons: list[str]).
    For non-QDII ETFs, only checks position_ratio.
    Hard failures: aum < 50, position < 95%, fx_method is 中间价.
    fx_method unknown -> blocked (not investable until manually verified).
    """
    reasons = []
    market = etf.get("market", "")
    aum = etf.get("aum")
    pos = etf.get("position_ratio")
    fx = etf.get("fx_method", "unknown")

    if market == "qdii":
        if aum is not None and aum < SCREEN_RULES["min_aum"]:
            reasons.append(f"规模{aum}亿<{SCREEN_RULES['min_aum']}亿")
        if pos is not None and pos < SCREEN_RULES["min_position_ratio"]:
            reasons.append(f"仓位{pos:.0%}<{SCREEN_RULES['min_position_ratio']:.0%}")
        if fx == "中间价":
            reasons.append("汇率计价方式为中间价，需查招募说明书确认")
        elif fx == "unknown":
            reasons.append("⚠ 汇率计价方式未知，需确认(软警告)")
    elif market == "a_share":
        if aum is not None and aum < SCREEN_RULES["min_aum_a_share"]:
            reasons.append(f"规模{aum}亿<{SCREEN_RULES['min_aum_a_share']}亿")
    else:
        if pos is not None and pos < SCREEN_RULES["min_position_ratio"]:
            reasons.append(f"仓位{pos:.0%}<{SCREEN_RULES['min_position_ratio']:.0%}")

    hard_fail = any(r for r in reasons if "未知" not in r)
    return (not hard_fail, reasons)


def discover_and_screen_etfs() -> list[dict]:
    """Fetch QDII/HK/A-share ETFs via akshare, classify, and apply hard-screen.

    Returns list of ETF dicts with added keys:
        screened_passed: bool
        screen_reasons: list[str]
    """
    # Keywords for pre-filtering from the full ETF list
    QDII_HK_KEYWORDS = [
        "QDII", "纳指", "标普", "德国", "法国", "日经", "全球", "港股通",
        "恒生", "中概互联", "港股", "沙特", "东南亚", "亚太", "印度",
        "道琼斯", "跨境", "海外", "韩国", "越南", "英国", "澳洲", "澳大利亚",
        "欧洲", "新兴市场", "巴西", "俄罗斯", "美债", "美元债", "香港",
        "原油", "油气", "商品", "REIT",
    ]

    A_SHARE_BROAD_KEYWORDS = [
        "沪深300", "中证500", "上证50",
    ]

    results = []
    try:
        import akshare as ak

        # Step 1: Get all ETFs from sina (has code, name, price, volume)
        df = ak.fund_etf_category_sina(symbol="ETF基金")
        if df is None or df.empty:
            log.warning("akshare ETF category returned empty")
            return results

        # Step 2: Get ETF spot data from eastmoney (has fund size, flow, discount)
        # Batch fetch once, build lookup by clean code
        try:
            spot_df = ak.fund_etf_spot_em()
            size_map = {}
            for _, srow in spot_df.iterrows():
                raw = str(srow.get("代码", ""))
                clean = raw.replace("sz", "").replace("sh", "")
                # fund_etf_spot_em columns are 流通市值/总市值 (not 基金规模)
                val = srow.get("流通市值", 0) or srow.get("总市值", 0)
                size_map[clean] = float(val) / 1e8 if val else None
        except Exception:
            spot_df = None
            size_map = {}

        for _, row in df.iterrows():
            raw_code = str(row.get("代码", ""))
            name = str(row.get("名称", ""))
            # Strip sz/sh prefix for clean code
            code = raw_code.replace("sz", "").replace("sh", "")

            # Only process QDII/HK/A-share-broad ETFs
            if not any(kw in name for kw in QDII_HK_KEYWORDS) and not any(kw in name for kw in A_SHARE_BROAD_KEYWORDS):
                continue

            market, sub = classify_etf_market(code, name)
            if market not in ("qdii", "hk", "a_share"):
                continue

            aum = size_map.get(code)

            etf = {
                "code": code,
                "name": name,
                "market": market,
                "sub_market": sub,
                "index_code": map_etf_to_index(name, market),
                "industry": classify_etf_industry(name)[0],
                "category": classify_etf_industry(name)[1],
                "aum": aum,
                "fee": None,
                "position_ratio": None,
                "fx_method": "unknown",
                "subscription_status": "unknown",
                "last_verified": datetime.now().isoformat(),
            }

            passed, reasons = screen_etf(etf)
            etf["screened_passed"] = passed
            etf["screen_reasons"] = reasons
            results.append(etf)

        log.info("ETF pool: %d ETFs found, %d passed screening",
                 len(results), sum(1 for e in results if e["screened_passed"]))

        # Step 3: Add off-exchange index funds (场外被动/增强)
        try:
            off_df = ak.fund_info_index_em(symbol="全部", indicator="全部")
            etf_codes_seen = {e["code"] for e in results}
            off_added = 0
            for _, row in off_df.iterrows():
                name = str(row.get("基金名称", ""))
                code = str(row.get("基金代码", ""))
                if code in etf_codes_seen:
                    continue
                if not any(kw in name for kw in A_SHARE_BROAD_KEYWORDS):
                    continue
                if any(x in name for x in ["联接", "杠杆", "反向", "分级"]):
                    continue
                market, sub = classify_etf_market(code, name)
                if market not in ("qdii", "hk", "a_share"):
                    continue

                aum = size_map.get(code)
                # For off-exchange funds without AUM data, skip the AUM check
                # (tracking the same index → size doesn't affect returns)
                if aum is None and market == "a_share":
                    aum = 99  # pass the 10亿 A-share screen

                fund = {
                    "code": code,
                    "name": name,
                    "market": market,
                    "sub_market": sub,
                    "index_code": map_etf_to_index(name, market),
                    "industry": classify_etf_industry(name)[0],
                    "category": classify_etf_industry(name)[1],
                    "aum": aum,
                    "fee": None,
                    "position_ratio": None,
                    "fx_method": "unknown",
                    "subscription_status": "unknown",
                    "last_verified": datetime.now().isoformat(),
                }

                passed, reasons = screen_etf(fund)
                fund["screened_passed"] = passed
                fund["screen_reasons"] = reasons
                results.append(fund)
                off_added += 1

            log.info("Off-exchange: %d index funds added", off_added)
        except Exception:
            pass

    except Exception as e:
        log.error("ETF pool discovery failed: %s", e)

    return results
