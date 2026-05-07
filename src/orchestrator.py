"""Command dispatch hub — parses WeChat text and routes to handlers."""

import re
import logging
from datetime import datetime
import pandas as pd
from src.config import get
from src.data.etf_pool import discover_and_screen_etfs, classify_etf_market, map_etf_to_index
from src.data.etf_pe import compute_etf_pe, resolve_etf_pe_percentile
from src.engine.scorer import score_industry, ScoreInput

log = logging.getLogger("invest.orchestrator")

# PE bands for indices without history — loaded from config, with defaults
def _pe_bands() -> dict:
    return {
        "SPX": get("pe_bands.SPX", [15, 35]),
        "NDX": get("pe_bands.NDX", [20, 40]),
        "DJIA": get("pe_bands.DJIA", [15, 30]),
        "HSI": get("pe_bands.HSI", [8, 18]),
        "NKY": get("pe_bands.NKY", [15, 30]),
        "DAX": get("pe_bands.DAX", [12, 25]),
    }

# PE command: three positional values for SPX, NDX, HSI
PE_INDEX_ORDER = ["SPX", "NDX", "HSI"]


def handle(text: str, repo) -> dict:
    """Main entry point. Parse and dispatch. Returns {status, result} dict for OpenClaw."""
    text = text.strip()

    if m := re.match(r"^pe\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)", text, re.IGNORECASE):
        return _handle_pe(float(m.group(1)), float(m.group(2)), float(m.group(3)), repo)
    elif re.match(r"^scan\b", text, re.IGNORECASE):
        return _handle_scan(repo)
    elif re.match(r"^status\b", text, re.IGNORECASE):
        return _handle_status(repo)
    elif m := re.match(r"^买入\s+(\d{6})\s+(\d+(?:\.\d+)?)\s*(万)?", text):
        return _handle_buy(m.group(1), m.group(2), m.group(3), repo)
    elif m := re.match(r"^卖出\s+(\d{6})\s+(全部|(\d+(?:\.\d+)?))", text):
        amount_str = "全部" if m.group(2) == "全部" else m.group(2)
        return _handle_sell(m.group(1), amount_str, repo)
    elif re.match(r"^reset\b", text, re.IGNORECASE):
        return _handle_reset(repo)
    else:
        return _handle_l1(text, repo)


def _handle_pe(spx: float, ndx: float, hsi: float, repo) -> dict:
    """Store user-provided PE values and compute percentiles.

    Usage: pe <SPX_PE> <NDX_PE> <HSI_PE>
    Example: pe 28.5 35.2 10.1
    """
    values = {"SPX": spx, "NDX": ndx, "HSI": hsi}
    results = {}

    for index_code, pe_val in values.items():
        repo.save_pe(index_code, pe_val)
        pct = _resolve_pe_percentile(index_code, repo)
        history_count = len(repo.get_pe_history(index_code))

        results[index_code] = {
            "pe": pe_val,
            "percentile": pct,
            "history_points": history_count,
        }

    # Build response lines
    lines = [f"PE 已记录: SPX={spx}, NDX={ndx}, HSI={hsi}"]
    for idx, r in results.items():
        pct = r["percentile"]
        pct_str = f"{pct:.0%}" if pct is not None else "—"
        n = r["history_points"]
        if n >= 5:
            lines.append(f"  {idx}: PE={r['pe']}, 分位={pct_str} (基于{n}次历史)")
        else:
            lines.append(f"  {idx}: PE={r['pe']}, 分位≈{pct_str} (绝对值+{n}次历史混合, 需≥5次切换到纯历史分位)")

    missing = _missing_pe_indices(repo)
    if missing:
        lines.append(f"  ⚠ 仍缺 PE 数据: {', '.join(missing)}")

    return {"status": "ok", "action": "pe", "message": "\n".join(lines), "detail": results}


def _resolve_band_pct(value: float, bands: dict, key: str) -> float | None:
    """Compute pseudo-percentile from absolute PE band."""
    band = bands.get(key)
    if band:
        low, high = band
        return max(0.0, min(1.0, (value - low) / (high - low)))
    return None


def _resolve_pe_percentile(index_code: str, repo) -> float | None:
    """Resolve PE percentile for an index from user-provided PE history."""
    history = repo.get_pe_history(index_code)
    if not history:
        return None

    current = history[-1]
    pct = repo.compute_pe_percentile(index_code, current)
    n = len(history)

    if pct is not None and n >= 5:
        return pct

    band = _pe_bands().get(index_code)
    if band:
        low, high = band
        band_pct = max(0.0, min(1.0, (current - low) / (high - low)))
        if pct is not None:
            w = n / 5
            return round(w * pct + (1 - w) * band_pct, 4)
        return band_pct

    return pct


def _resolve_etf_pe(etf_code: str, industry: str, repo) -> tuple[float | None, dict]:
    """Resolve PE percentile for an ETF from holdings data.

    Returns (percentile_or_None, meta_dict).
    meta includes: source, coverage_pct, etf_pe_value.
    """
    meta = {"source": "none", "coverage_pct": 0, "etf_pe_value": None}

    # Check stored ETF PE history
    history = repo.get_pe_history(f"ETF:{etf_code}")
    if history:
        current = history[-1]
        n = len(history)
        bands = {k: tuple(v) for k, v in get("etf_pe_bands", {}).items()}
        band_pct = _resolve_band_pct(current, bands, industry)

        meta["etf_pe_value"] = current
        meta["source"] = "etf_stored"
        meta["history_points"] = n
        # Assume coverage was good since compute_etf_pe succeeded earlier
        if meta["coverage_pct"] == 0:
            meta["coverage_pct"] = 80.0

        if n >= 3:
            pct = repo.compute_pe_percentile(f"ETF:{etf_code}", current)
            if pct is not None:
                meta["source"] = "etf_percentile"
                return pct, meta
        # n < 3: percentile is meaningless, use band directly
        if band_pct is not None:
            meta["source"] = "etf_band"
            return band_pct, meta
        return None, meta

    # Fresh computation
    try:
        result = compute_etf_pe(etf_code, repo)
        if result:
            etf_pe = result["pe"]
            coverage = result["total_weight_pct"]
            meta["etf_pe_value"] = etf_pe
            meta["coverage_pct"] = coverage
            meta["source"] = "etf_fresh"

            bands = {k: tuple(v) for k, v in get("etf_pe_bands", {}).items()}
            band_pct = _resolve_band_pct(etf_pe, bands, industry)
            if band_pct is not None:
                meta["source"] = "etf_fresh_band"
                return band_pct, meta
            return None, meta
    except Exception:
        pass

    return None, meta


def _missing_pe_indices(repo) -> list[str]:
    """Check which QDII/HK indices have no PE data at all."""
    missing = []
    for idx in ["SPX", "NDX", "HSI"]:
        history = repo.get_pe_history(idx)
        if not history:
            missing.append(idx)
    return missing


def _handle_scan(repo) -> dict:
    """ETF discovery -> hard-screen -> score with per-index PE + industry category."""
    try:
        from src.data.market import fetch_all_index_valuations
        from src.data.volatility import fetch_volatility_with_fallback
        from src.config import get as cfg_get

        etfs = discover_and_screen_etfs()
        passed = [e for e in etfs if e.get("screened_passed")]

        # Fetch A-share PE from akshare (primary source for CSI 300/500/SSE50)
        a_share_pe = {}
        try:
            valuations = fetch_all_index_valuations()
            for v in valuations:
                if v["market"] == "a_share" and v.get("pe_percentile") is not None:
                    a_share_pe[v["code"]] = v["pe_percentile"]
        except Exception:
            pass

        # Build fund flow map from spot data (主力净流入-净占比)
        flow_map = {}
        try:
            import akshare as ak
            spot_df = ak.fund_etf_spot_em()
            spot_cols = spot_df.columns.tolist()
            spot_code_col = spot_cols[0]
            flow_col = spot_cols[20]  # 主力净流入-净占比
            for _, srow in spot_df.iterrows():
                code = str(srow[spot_code_col]).replace("sz", "").replace("sh", "")
                flow_val = srow[flow_col]
                flow_map[code] = float(flow_val) if pd.notna(flow_val) else 0.0
        except Exception:
            pass

        # Fetch volatility per market (once, used for all ETFs in that market)
        vol_cache = {}
        for mkt, mkt_key in [("qdii", "us"), ("hk", "hk"), ("a_share", "a_share")]:
            try:
                vol_val, vol_source = fetch_volatility_with_fallback(mkt_key)
                vol_cache[mkt] = vol_val if vol_val else 0.0
            except Exception:
                vol_cache[mkt] = 0.0

        vol_config = cfg_get("volatility", {})

        # Collect per-index PE percentiles for display (index-level only, not ETF)
        index_pe_display = {}
        for idx in PE_INDEX_ORDER:
            p = _resolve_pe_percentile(idx, repo)
            if p is not None:
                index_pe_display[idx] = round(p, 2)

        # Sample evenly from QDII, HK, and A-share markets (max 15 each)
        qdii_passed = [e for e in passed if e.get("market") == "qdii"]
        hk_passed = [e for e in passed if e.get("market") == "hk"]
        a_share_passed = [e for e in passed if e.get("market") == "a_share"]
        sample = qdii_passed[:15] + hk_passed[:15] + a_share_passed[:15]

        scored = []
        for e in sample:
            try:
                mkt = e.get("market", "qdii")
                etf_code = e.get("code", "")
                index_code = e.get("index_code", "SPX")
                category = e.get("category", "cyclical")
                industry = e.get("industry", "宽基")

                # Resolve PE percentile: ETF PE > index PE > band > 0.5
                pe_pct = None
                pe_source = "default"

                if mkt == "a_share":
                    pe_pct = a_share_pe.get(index_code)
                    pe_source = "a_share_index"
                else:
                    # 1. Try ETF PE from holdings
                    etf_pct, etf_meta = _resolve_etf_pe(etf_code, industry, repo)
                    # 2. Try index PE from user input
                    idx_pct = _resolve_pe_percentile(index_code, repo)

                    if etf_pct is not None and etf_meta.get("coverage_pct", 0) >= 50:
                        pe_pct = etf_pct
                        pe_source = etf_meta["source"]
                    elif etf_pct is not None and idx_pct is not None:
                        cov = etf_meta.get("coverage_pct", 30) / 100
                        pe_pct = round(cov * etf_pct + (1 - cov) * idx_pct, 4)
                        pe_source = f"etf_blend_{etf_meta['source']}+index"
                    elif etf_pct is not None:
                        pe_pct = etf_pct
                        pe_source = etf_meta["source"]
                    elif idx_pct is not None:
                        pe_pct = idx_pct
                        pe_source = "index_user"
                    else:
                        bands = _pe_bands()
                        if bands.get(index_code):
                            pe_source = "index_band"

                if pe_pct is None:
                    pe_pct = 0.5

                # Fund flow for this ETF
                flow_pct = flow_map.get(etf_code, 0.0)

                # Volatility for this market
                vol = vol_cache.get(mkt, 0.0)
                vol_cfg = vol_config.get(mkt, {})
                vol_normal = vol_cfg.get("normal_max", 25)
                vol_panic = vol_cfg.get("panic", 30)

                inp = ScoreInput(
                    category=category,
                    pe_percentile=pe_pct,
                    flow_pct=flow_pct,
                    volatility=vol,
                    vol_normal_max=vol_normal,
                    vol_panic=vol_panic,
                    market=mkt,
                )
                s = score_industry(inp)
                scored.append({
                    "code": e["code"],
                    "name": e.get("name", ""),
                    "market": mkt,
                    "index": index_code,
                    "industry": industry,
                    "category": category,
                    "score": s["total_score"],
                    "action": s["action"],
                    "signal_pe": s["signal_pe"],
                    "signal_core": s["signal_core"],
                    "aum": e.get("aum"),
                    "pe_percentile": round(pe_pct, 2),
                })
            except Exception:
                pass

        scored.sort(key=lambda x: (x["score"], x.get("aum") or 0), reverse=True)

        # Show top 10 per market for balanced recommendations
        qdii_top = [r for r in scored if r["market"] == "qdii"][:10]
        hk_top = [r for r in scored if r["market"] == "hk"][:10]
        a_share_top = [r for r in scored if r["market"] == "a_share"][:10]
        recommendations = qdii_top + hk_top + a_share_top
        recommendations.sort(key=lambda x: (x["score"], x.get("aum") or 0), reverse=True)

        # Dynamic market weights from scan results
        from src.build_up import compute_dynamic_weights, get_build_up_status as _bu_status
        dyn_weights = compute_dynamic_weights(scored)
        if dyn_weights:
            repo.set_build_up_weights(dyn_weights)

        # Build-up status
        bu = _bu_status(repo)
        if bu["status"] == "not_started":
            repo.init_build_up()
            bu = _bu_status(repo)

        # Check if PE window is open (any market PE < 30%)
        # Aggregate PE per market from index-level data
        market_pe = {}
        for v in valuations:
            if v["market"] == "a_share":
                market_pe["a_share"] = v.get("pe_percentile")
        for mkt, idx in [("qdii", "SPX"), ("hk", "HSI")]:
            p = _resolve_pe_percentile(idx, repo)
            if p is not None:
                market_pe[mkt] = p

        window_open = any(p is not None and p < 0.30 for p in market_pe.values())
        has_sells = any(r["action"] == "sell" for r in scored)
        bu_state = repo.get_build_up_state()

        if bu_state and window_open and bu_state["status"] == "waiting":
            repo.start_deploying()
            bu = _bu_status(repo)
        elif bu_state and window_open and bu_state["status"] == "paused":
            repo.resume_build_up()
            bu = _bu_status(repo)
        elif bu_state and not window_open and bu_state["status"] == "deploying":
            repo.pause_build_up()
            bu = _bu_status(repo)
        elif bu_state and has_sells and bu_state["status"] in ("deploying", "paused"):
            # If we're holding positions and sell signals appear, flag it
            pass

        # Check if PE input needed
        missing = _missing_pe_indices(repo)

        return {
            "status": "ok",
            "action": "scan",
            "total_found": len(passed),
            "recommendations": recommendations,
            "index_pe": index_pe_display,
            "market_weights": dyn_weights,
            "build_up": bu,
            "pe_missing": missing if missing else None,
            "pe_prompt": (
                f"请提供指数 PE: pe <SPX> <NDX> <HSI>\n"
                f"例如: pe 28.5 35.2 10.1\n"
                f"查询: quote.eastmoney.com 搜索 SPX/NDX/HSI"
            ) if missing else None,
            "note": _bu_note(bu, window_open),
        }
    except Exception as e:
        log.error("scan failed: %s", e)
        return {"status": "error", "action": "scan", "message": str(e)}


def _handle_status(repo) -> dict:
    """Return current portfolio state with dynamic build-up status."""
    try:
        from src.build_up import get_build_up_status
        from src.tracker import update_positions, check_alerts

        # Refresh MTM before reporting
        update_positions(repo)

        positions = repo.get_positions()
        bu = get_build_up_status(repo)
        alerts = check_alerts(repo)

        return {
            "status": "ok",
            "action": "status",
            "total_positions": len(positions),
            "total_value": sum(p.get("current_value", 0) or 0 for p in positions),
            "total_pnl": sum(
                (p.get("current_value", 0) or 0) - (p.get("cost_basis", 0) or 0)
                for p in positions
            ),
            "positions": [
                {
                    "code": p.get("etf_code"),
                    "market": p.get("market"),
                    "cost_basis": p.get("cost_basis"),
                    "current_value": p.get("current_value"),
                    "pnl_pct": round((p.get("unrealized_pnl_pct") or 0) * 100, 1),
                    "holding_months": p.get("holding_months"),
                }
                for p in positions
            ],
            "build_up": bu,
            "alerts": alerts,
        }
    except Exception as e:
        log.error("status failed: %s", e)
        return {"status": "error", "action": "status", "message": str(e)}


def _handle_buy(code: str, amount_str: str, unit: str | None, repo) -> dict:
    """Record a buy transaction and update build-up batch progress."""
    try:
        amount = float(amount_str)
        if unit == "万":
            amount *= 10000
        amount = round(amount, 2)

        etf_name = code
        market = "unknown"
        try:
            existing = repo.conn.execute(
                "SELECT * FROM etf_pool WHERE code = ?", (code,)
            ).fetchone()
            if existing:
                etf_name = dict(existing).get("name", code)
                market = dict(existing).get("market", "unknown")
        except Exception:
            pass

        if market == "unknown":
            mkt, sub = classify_etf_market(code, etf_name)
            market = mkt

        # Write position
        repo.conn.execute(
            """INSERT INTO position (etf_code, market, industry, shares, cost_basis, current_value,
               unrealized_pnl_pct, category_type, holding_months, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 0, 'cyclical', 0, ?)""",
            (code, market, "", amount, amount, amount, datetime.now().isoformat()),
        )

        # Write trade log
        repo.conn.execute(
            """INSERT INTO trade_log (etf_code, action, shares, price, realized_pnl, reason, rule_version, human_override, executed_at)
               VALUES (?, 'buy', ?, ?, 0, '人工买入', 'v2.0', 1, ?)""",
            (code, amount, amount, datetime.now().isoformat()),
        )
        repo.conn.commit()

        # Update build-up batch progress
        state = repo.deploy_batch(market, amount)
        bu_status = "updated" if state else "no build-up active"

        return {
            "status": "ok",
            "action": "buy",
            "code": code,
            "name": etf_name,
            "amount": amount,
            "market": market,
            "build_up": {
                "batch": state.get("current_batch", 0) if state else 0,
                "deployed": state.get("filled_amount", 0) if state else 0,
            } if state else {"status": bu_status},
        }
    except Exception as e:
        log.error("buy failed: %s", e)
        return {"status": "error", "action": "buy", "code": code, "message": str(e)}


def _handle_sell(code: str, amount_str: str, repo) -> dict:
    """Record a sell transaction."""
    try:
        pos_rows = repo.conn.execute(
            "SELECT * FROM position WHERE etf_code = ?", (code,)
        ).fetchall()
        if not pos_rows:
            return {"status": "error", "action": "sell", "message": f"未找到持仓: {code}"}

        pos = dict(pos_rows[0])
        current_value = pos.get("current_value", 0) or pos.get("cost_basis", 0)

        if amount_str == "全部":
            sell_amount = current_value
            shares_sold = pos.get("shares", 0)
        else:
            sell_amount = round(float(amount_str), 2)
            ratio = sell_amount / current_value if current_value > 0 else 1
            shares_sold = round(pos.get("shares", 0) * ratio, 2)

        realized_pnl = round(
            sell_amount - pos.get("cost_basis", 0) * (sell_amount / current_value if current_value > 0 else 1),
            2,
        )

        repo.conn.execute(
            """INSERT INTO trade_log (etf_code, action, shares, price, realized_pnl, reason, rule_version, human_override, executed_at)
               VALUES (?, 'sell', ?, ?, ?, '人工卖出', 'v1.5', 1, ?)""",
            (code, shares_sold, sell_amount, realized_pnl, datetime.now().isoformat()),
        )

        if amount_str == "全部":
            repo.conn.execute("DELETE FROM position WHERE etf_code = ?", (code,))
        else:
            new_value = round(current_value - sell_amount, 2)
            repo.conn.execute(
                "UPDATE position SET current_value = ? WHERE etf_code = ?",
                (new_value, code),
            )

        repo.conn.commit()

        return {
            "status": "ok",
            "action": "sell",
            "code": code,
            "amount": sell_amount,
            "realized_pnl": realized_pnl,
            "remaining": None if amount_str == "全部" else round(current_value - sell_amount, 2),
        }
    except Exception as e:
        log.error("sell failed: %s", e)
        return {"status": "error", "action": "sell", "code": code, "message": str(e)}


def _handle_reset(repo) -> dict:
    """Reset build-up to a new round."""
    try:
        state = repo.reset_build_up()
        return {
            "status": "ok",
            "action": "reset",
            "round": state["round_id"],
            "phase": 1,
            "message": f"已重置为第 {state['round_id']} 轮建仓，阶段 1（40万）",
        }
    except Exception as e:
        log.error("reset failed: %s", e)
        return {"status": "error", "action": "reset", "message": str(e)}


def _handle_l1(text: str, repo) -> dict:
    """Record free-text L1 input for future scoring weight."""
    try:
        repo.conn.execute(
            "INSERT INTO alert_log (alert_type, severity, message, created_at) VALUES (?, ?, ?, ?)",
            ("L1_input", "info", text, datetime.now().isoformat()),
        )
        repo.conn.commit()
        return {"status": "ok", "action": "l1", "message": "已记录，将在下次评分时纳入参考。"}
    except Exception as e:
        return {"status": "ok", "action": "l1", "message": "已收到。"}


def _bu_note(bu: dict, has_buy: bool) -> str:
    """Generate build-up status note."""
    status = bu.get("status", "waiting")
    if status == "waiting":
        return "等待 PE < 30% 建仓窗口"
    if status == "paused":
        return f"建仓已暂停 (第{bu.get('current_batch',0)}/{bu.get('total_batches',4)}批, 已部署{bu.get('deployed_pct',0):.0f}%)"
    if status == "deploying":
        return f"建仓进行中 (第{bu.get('current_batch',0)}/{bu.get('total_batches',4)}批, 已部署{bu.get('total_deployed_pct',0):.0f}%)"
    if status == "done":
        return f"建仓完成 ({bu.get('deployed_pct', 100):.0f}%) — 进入轮动模式"
    if status == "not_started":
        return "发 reset 初始化建仓"
    return ""
