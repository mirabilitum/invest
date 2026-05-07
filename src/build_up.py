"""Dynamic batch build-up state machine.

Build-up proceeds in 4 batches, triggered by PE < 30% windows.
Allocation weights are dynamic — driven by scan scoring, not fixed.
"""

import json
import logging

log = logging.getLogger("invest.build_up")

BATCH_SIZES = [0.30, 0.30, 0.25, 0.15]  # % of total capital per batch
MIN_DAYS_BETWEEN_BATCHES = 14  # must wait 2 weeks between batches


def compute_market_weight(score: int, pe_pct: float, flow_sig: int) -> float:
    """Compute how attractive a market is for deployment.

    cheap_factor:  1.0 if PE < 30%, 0.5 if 30-50%, 0 otherwise
    flow_factor:   1.5 if institutions buying, 0.5 if selling, 1.0 if neutral
    """
    if pe_pct < 0.30:
        cheap = 1.0
    else:
        cheap = 0.0

    if flow_sig > 0:
        flow = 1.5
    elif flow_sig < 0:
        flow = 0.5
    else:
        flow = 1.0

    return round(cheap * flow, 4)


def compute_dynamic_weights(scored_results: list[dict]) -> dict[str, float]:
    """Compute per-market allocation weights from scan results.

    Dynamic PE/flow signal, capped by config market limits.
    Returns {market: weight_pct} normalized to sum to 1.0.
    Returns empty dict if no market is deployable.
    """
    from src.config import get as _cfg

    market_scores = {}

    for r in scored_results:
        mkt = r["market"]
        if mkt not in market_scores:
            market_scores[mkt] = {"total_weight": 0.0, "count": 0}

        pe = r.get("pe_percentile", 0.5)
        flow = r.get("signal_core", 0)
        w = compute_market_weight(r["score"], pe, flow)
        market_scores[mkt]["total_weight"] += w
        market_scores[mkt]["count"] += 1

    # Average weight per market
    raw_weights = {}
    for mkt, data in market_scores.items():
        avg = data["total_weight"] / max(data["count"], 1)
        if avg > 0:
            raw_weights[mkt] = avg

    total = sum(raw_weights.values())
    if total <= 0:
        return {}

    # Normalize
    weights = {mkt: w / total for mkt, w in raw_weights.items()}

    # Apply per-market caps from config, redistribute excess proportionally
    caps = {
        "qdii": _cfg("markets.qdii.max_allocation", 0.50),
        "hk": _cfg("markets.hk.max_allocation", 0.50),
        "a_share": _cfg("markets.a_share.max_allocation", 0.30),
    }

    for _ in range(3):
        excess = 0.0
        capped = {}
        for mkt, w in weights.items():
            cap = caps.get(mkt, 1.0)
            if w > cap:
                excess += w - cap
                capped[mkt] = cap
            else:
                capped[mkt] = w

        if excess <= 0.001:
            weights = capped
            break

        # Redistribute excess to uncapped markets
        uncapped = {m: w for m, w in capped.items() if w < caps.get(m, 1.0) - 0.001}
        if not uncapped:
            weights = capped
            break

        uncapped_total = sum(uncapped.values())
        if uncapped_total <= 0:
            weights = capped
            break

        for mkt in uncapped:
            capped[mkt] += excess * (capped[mkt] / uncapped_total)
        weights = capped

    return {mkt: round(w, 4) for mkt, w in weights.items()}


def get_build_up_status(repo) -> dict:
    """Get human-readable build-up status."""
    state = repo.get_build_up_state()
    if not state:
        return {"status": "not_started", "message": "未初始化，请发 reset"}

    status = state["status"]
    batch = state["current_batch"]
    total_batches = state["total_batches"]
    deployed = state.get("filled_amount", 0) or 0
    capital = state.get("total_capital", 1_000_000)
    weights = {}
    deployed_per_mkt = {}
    try:
        weights = json.loads(state.get("market_weights", "{}"))
        deployed_per_mkt = json.loads(state.get("market_deployed", "{}"))
    except (json.JSONDecodeError, TypeError):
        pass

    if status == "done":
        return {
            "status": "done",
            "round": state["round_id"],
            "message": f"建仓完成，部署 {deployed:.0f}/{capital:.0f}",
            "deployed": deployed,
            "deployed_pct": round(deployed / capital * 100, 1) if capital else 0,
        }

    if status == "waiting":
        return {
            "status": "waiting",
            "round": state["round_id"],
            "message": "等待 PE < 30% 建仓窗口",
            "total_capital": capital,
            "total_batches": total_batches,
        }

    if status == "paused":
        return {
            "status": "paused",
            "round": state["round_id"],
            "current_batch": batch,
            "total_batches": total_batches,
            "deployed": deployed,
            "deployed_pct": round(deployed / capital * 100, 1) if capital else 0,
            "market_weights": weights,
            "market_deployed": deployed_per_mkt,
            "message": f"PE 窗口关闭，已暂停在第 {batch}/{total_batches} 批 ({deployed:.0f}/{capital:.0f})",
        }

    # deploying
    batch_sizes = json.loads(state.get("batch_sizes", "[0.30,0.30,0.25,0.15]"))
    completed_pct = sum(batch_sizes[:batch - 1]) if batch > 1 else 0
    current_batch_pct = batch_sizes[batch - 1] if batch <= len(batch_sizes) else 0
    batch_target = capital * current_batch_pct

    return {
        "status": "deploying",
        "round": state["round_id"],
        "current_batch": batch,
        "total_batches": total_batches,
        "batch_target": round(batch_target),
        "batch_progress_pct": round(deployed / max(batch_target, 1) * 100, 1) if batch_target else 0,
        "total_deployed": deployed,
        "total_deployed_pct": round(deployed / capital * 100, 1) if capital else 0,
        "market_weights": weights,
        "market_deployed": deployed_per_mkt,
        "next_batch_eligible_after": _next_batch_date(state),
    }


def _next_batch_date(state: dict) -> str:
    """Compute when the next batch can be deployed (14 days after last)."""
    from datetime import datetime, timedelta
    last = state.get("last_deploy_date")
    if last:
        try:
            dt = datetime.fromisoformat(last.replace("Z", ""))
            return (dt + timedelta(days=MIN_DAYS_BETWEEN_BATCHES)).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            pass
    return "anytime"


def should_pause(pe_percentile: float = None, volatility: float = None) -> tuple[bool, str]:
    """Check if build-up should pause based on market conditions.

    Pause if PE > 50% (window closed) or volatility > panic threshold.
    """
    if pe_percentile is not None and pe_percentile > 0.50:
        return True, f"PE分位{pe_percentile:.0%}>50%，暂停建仓"
    if volatility is not None and volatility > 30:
        return True, f"波动率{volatility}>30，暂停建仓"
    return False, ""
