from dataclasses import dataclass, field
from src.config import get


@dataclass
class Portfolio:
    positions: list[dict] = field(default_factory=list)
    cash_pct: float = 0.0


def check_all_constraints(portfolio: Portfolio, total_value: float = 1_000_000) -> list[dict]:
    """Run ALL hard constraints. Returns list of {rule, severity, message, action}."""
    violations = []
    max_single = get("positions.max_single_etf_pct", 0.25)
    min_cash = get("positions.min_cash_pct", 0.05)
    max_turnover = get("positions.max_monthly_turnover_pct", 0.20)
    etf_min = get("positions.etf_count_min", 5)
    etf_max = get("positions.etf_count_max", 12)

    # 1. Single ETF limit
    for p in portfolio.positions:
        if p.get("value_pct", 0) > max_single:
            violations.append({
                "rule": "single_etf_limit",
                "severity": "error",
                "message": f"{p['code']}: {p['value_pct']:.1%} > {max_single:.1%}",
                "action": f"reduce_{p['code']}",
            })

    # 2. Market totals
    market_totals = {}
    for p in portfolio.positions:
        m = p.get("market", "unknown")
        market_totals[m] = market_totals.get(m, 0) + p.get("value_pct", 0)

    # Per-market caps (QDII 50%, HK 50%)
    market_limits = {
        "qdii": get("markets.qdii.max_allocation", 0.50),
        "hk": get("markets.hk.max_allocation", 0.50),
        "a_share": get("markets.a_share.max_allocation", 0.06),
    }
    for m, limit in market_limits.items():
        if market_totals.get(m, 0) > limit:
            violations.append({
                "rule": f"market_limit_{m}",
                "severity": "error",
                "message": f"Market {m}: {market_totals[m]:.1%} > {limit:.1%}",
                "action": f"reduce_{m}",
            })

    # 3. Cash floor
    if portfolio.cash_pct < min_cash:
        violations.append({
            "rule": "cash_floor",
            "severity": "error",
            "message": f"Cash {portfolio.cash_pct:.1%} < {min_cash:.1%}",
            "action": "raise_cash",
        })

    # 4. ETF count range
    n = len(portfolio.positions)
    if n < etf_min:
        violations.append({
            "rule": "etf_count_min",
            "severity": "warning",
            "message": f"Only {n} ETFs, below minimum {etf_min}",
            "action": "consider_add",
        })
    if n > etf_max:
        violations.append({
            "rule": "etf_count_max",
            "severity": "warning",
            "message": f"{n} ETFs, above maximum {etf_max}",
            "action": "consider_merge",
        })

    # 5. A-share cap (CONSTRAINTS §4: satellite 10-20%, A-share <= 30% of satellite = max 6%)
    ashare_total = sum(p.get("value_pct", 0) for p in portfolio.positions if p.get("market") == "a_share")
    ashare_max = get("markets.a_share.max_allocation", 0.06)
    if ashare_total > ashare_max:
        violations.append({
            "rule": "ashare_cap",
            "severity": "error",
            "message": f"A-share total {ashare_total:.1%} > {ashare_max:.1%}",
            "action": "reduce_ashare",
        })

    # 6. Turnover limit
    proposed = getattr(portfolio, "proposed_turnover_pct", None)
    if proposed is not None and proposed > max_turnover:
        violations.append({
            "rule": "turnover_limit",
            "severity": "error",
            "message": f"Proposed turnover {proposed:.1%} > {max_turnover:.1%}",
            "action": "scale_down_trades",
        })

    return violations


def check_stop_triggers(positions: list[dict]) -> list[dict]:
    """Check stop-loss and take-profit for each position by category."""
    thresholds = {
        "structural": get("scoring.stop_loss.structural", 0.15),
        "cyclical": get("scoring.stop_loss.cyclical", 0.12),
        "event_driven": get("scoring.stop_loss.event_driven", 0.10),
    }
    take_thresholds = {
        "structural": get("scoring.take_profit.structural", 0.30),
        "cyclical": get("scoring.take_profit.cyclical", 0.20),
        "event_driven": get("scoring.take_profit.event_driven", 0.15),
    }
    triggers = []
    for p in positions:
        pnl = p.get("unrealized_pnl_pct", 0)
        cat = p.get("category_type", "cyclical")
        stop = thresholds.get(cat, 0.12)
        take = take_thresholds.get(cat, 0.20)
        if pnl <= -stop:
            triggers.append({"code": p["code"], "type": "stop_loss", "pnl": pnl, "threshold": stop})
        elif pnl >= take:
            triggers.append({"code": p["code"], "type": "take_profit", "pnl": pnl, "threshold": take})
    return triggers


def check_avoid_zone(positions: list[dict], classifications: list[dict]) -> list[dict]:
    """Detect positions whose industry has been classified as avoid."""
    avoid_industries = {c["industry"] for c in classifications if c.get("category_type") == "avoid"}
    forced_exits = []
    for p in positions:
        ind = p.get("industry", "")
        if ind in avoid_industries:
            forced_exits.append({
                "code": p["code"],
                "industry": ind,
                "action": "forced_exit",
                "reason": f"行业 {ind} 已被打入回避区, 立即清仓",
                "severity": "critical",
            })
    return forced_exits


def check_staged_take_profit(position: dict) -> dict | None:
    """CONSTRAINTS v1.4 Section 7.5: staged take-profit with trailing stop.

    Batch 1: sell 50% immediately when floating profit crosses threshold.
    Batch 2: hold remaining 50% with trailing stop from peak.
    """
    pnl = position.get("unrealized_pnl_pct", 0)
    cat = position.get("category_type", "cyclical")
    thresholds = {
        "structural": get("scoring.take_profit.structural", 0.30),
        "cyclical": get("scoring.take_profit.cyclical", 0.20),
        "event_driven": get("scoring.take_profit.event_driven", 0.15),
    }
    trigger = thresholds.get(cat, 0.20)
    batch1_ratio = get("scoring.staged_take_profit.batch1_ratio", 0.50)
    trailing_stop = get("scoring.staged_take_profit.trailing_stop_drawdown", 0.08)

    status = position.get("partial_close_status")

    # Batch 1: initial threshold breach
    if status is None and pnl >= trigger:
        return {
            "code": position["code"],
            "action": "staged_sell_batch1",
            "ratio": batch1_ratio,
            "reason": f"浮盈{pnl:.1%}触发止盈线{trigger:.0%}, 分批第1批卖50%",
        }

    # Batch 2: trailing stop from peak (no profit threshold re-check)
    if status == "partial_closed":
        peak = position.get("peak_unrealized_pnl", pnl)
        drawdown = peak - pnl
        if drawdown >= trailing_stop:
            return {
                "code": position["code"],
                "action": "staged_sell_batch2",
                "ratio": 1.0,
                "reason": f"移动止盈: 从峰值{peak:.1%}回撤{drawdown:.1%}>={trailing_stop:.0%}, 清仓剩余50%",
            }

    return None


def check_time_stops(positions: list[dict]) -> list[dict]:
    """CONSTRAINTS v1.4 Section 7.5: time-based exit rules."""
    triggers = []
    tp_months = get("scoring.time_stops.take_profit_months", 3)
    tp_pnl = get("scoring.time_stops.take_profit_pnl", 0.15)
    sl_months = get("scoring.time_stops.stop_loss_months", 6)

    for p in positions:
        months = p.get("holding_months", 0)
        pnl = p.get("unrealized_pnl_pct", 0)
        score = p.get("total_score", 0)

        if months >= tp_months and pnl > tp_pnl:
            triggers.append({
                "code": p["code"],
                "type": "time_take_profit",
                "months": months,
                "pnl": pnl,
                "action": "prompt_lock_in",
                "reason": f"持仓{months}月浮盈{pnl:.1%}>{tp_pnl:.0%}, 建议择机落袋",
            })

        if months >= sl_months and pnl < 0 and score <= 0:
            triggers.append({
                "code": p["code"],
                "type": "time_stop_loss",
                "months": months,
                "pnl": pnl,
                "score": score,
                "action": "prompt_reassess",
                "reason": f"持仓{months}月仍浮亏{pnl:.1%}且评分{score}<=0, 建议重新评估",
            })

    return triggers


def check_correlation(corr_matrix: dict[str, dict[str, float]]) -> list[dict]:
    """Check ETF correlation among existing holdings: > 0.85 -> merge suggestion.

    Note: < 0.7 between existing holdings is normal diversification - not a problem.
    The < 0.7 check only applies when evaluating a CANDIDATE ETF against existing holdings.
    """
    merge_threshold = get("positions.correlation_merge_threshold", 0.85)
    suggestions = []
    codes = list(corr_matrix.keys())
    for i in range(len(codes)):
        for j in range(i + 1, len(codes)):
            corr = corr_matrix[codes[i]].get(codes[j], 0)
            if corr > merge_threshold:
                suggestions.append({
                    "type": "merge",
                    "a": codes[i],
                    "b": codes[j],
                    "correlation": corr,
                })
    return suggestions
