import logging

log = logging.getLogger("invest.engine.rebalance")


def compute_rebalance(positions: list[dict], total_value: float, max_turnover_pct: float = 0.20) -> list[dict]:
    """Generate rebalance suggestions based on current vs target allocation.

    Skips positions with <2% difference. Caps total turnover at max_turnover_pct.
    Minimum trade size: 5000.
    """
    suggestions = []
    total_turnover = 0.0
    limit = total_value * max_turnover_pct

    for p in positions:
        diff = p["target_pct"] - p["current_pct"]
        if abs(diff) < 0.02:
            continue
        amount = abs(diff) * total_value
        direction = "buy" if diff > 0 else "sell"

        if total_turnover + amount > limit:
            amount = limit - total_turnover
        if amount < 5000:
            continue

        suggestions.append({
            "code": p["code"],
            "direction": direction,
            "amount": round(amount, -3),
            "reason": f"{p['current_pct']:.1%} -> {p['target_pct']:.1%}",
        })
        total_turnover += amount
        if total_turnover >= limit:
            break

    return suggestions


def forced_exit_orders(forced_exits: list[dict], positions: list[dict]) -> list[dict]:
    """Generate sell-all orders for avoid-zone positions. No P&L consideration — just exit."""
    pos_map = {p["code"]: p for p in positions}
    orders = []
    for fe in forced_exits:
        pos = pos_map.get(fe["code"])
        if pos:
            orders.append({
                "code": fe["code"],
                "action": "sell_all",
                "current_value": pos.get("current_value", 0),
                "reason": fe["reason"],
                "severity": "critical",
            })
            log.warning("FORCED EXIT: %s — %s", fe["code"], fe["reason"])
    return orders


def compute_dca(cash_amount: float, weights: dict, pe_percentiles: dict) -> list[dict]:
    """Conditional DCA: PE > 70% skip, PE < 15% double (1.5x multiplier)."""
    allocations = []
    for code, weight in weights.items():
        pe = pe_percentiles.get(code, 0.5)
        if pe > 0.70:
            continue
        m = 1.5 if pe < 0.15 else 1.0
        allocations.append({"code": code, "amount": round(cash_amount * weight * m, -3)})
    return allocations
