"""Position lifecycle tracker — daily MTM, score-based alerts, daily summary."""

import logging
from datetime import datetime

log = logging.getLogger("invest.tracker")


def update_positions(repo) -> dict:
    """Update current_value and unrealized_pnl_pct for all positions.

    Fetches latest ETF prices from akshare fund_etf_spot_em.
    Increments holding_months on month boundaries.
    """
    positions = repo.get_positions()
    if not positions:
        return {"updated": 0, "errors": []}

    # Fetch current prices for all ETFs in one batch
    price_map = _fetch_price_map()

    updated = 0
    errors = []
    now = datetime.now()

    for p in positions:
        try:
            code = p.get("etf_code", "")
            cost = float(p.get("cost_basis", 0))
            current_price = price_map.get(code)

            if current_price and cost > 0:
                # ETF price is per share; value = price × shares
                # Simplified: use price ratio as return proxy
                current_value = cost * (current_price / _entry_price(code, repo))
            elif current_price:
                current_value = cost
            else:
                current_value = cost  # no price data, keep cost

            pnl_pct = round((current_value - cost) / cost, 4) if cost > 0 else 0

            months = p.get("holding_months", 0)
            # Increment at month boundaries
            updated_at_str = p.get("updated_at", "")
            if updated_at_str:
                try:
                    last_update = datetime.fromisoformat(updated_at_str.replace("Z", ""))
                    if last_update.month != now.month or last_update.year != now.year:
                        months += 1
                except (ValueError, TypeError):
                    pass

            repo.conn.execute(
                """UPDATE position
                   SET current_value = ?, unrealized_pnl_pct = ?, holding_months = ?,
                       updated_at = ?
                   WHERE etf_code = ?""",
                (round(current_value, 2), pnl_pct, months, now.isoformat(), code),
            )
            updated += 1
        except Exception as e:
            errors.append(f"{p.get('etf_code', '?')}: {e}")

    repo.conn.commit()
    return {"updated": updated, "errors": errors}


def _fetch_price_map() -> dict[str, float]:
    """Fetch current ETF prices from akshare. Returns {code: price}."""
    try:
        import akshare as ak
        spot = ak.fund_etf_spot_em()
        cols = spot.columns.tolist()
        code_col = cols[0]
        price_col = cols[2]  # 最新价
        price_map = {}
        for _, row in spot.iterrows():
            code = str(row[code_col]).replace("sz", "").replace("sh", "")
            try:
                price_map[code] = float(row[price_col])
            except (ValueError, TypeError):
                pass
        return price_map
    except Exception as e:
        log.warning("ETF price fetch failed: %s", e)
        return {}


def _entry_price(code: str, repo) -> float:
    """Get entry price for a position (from trade_log)."""
    try:
        row = repo.conn.execute(
            "SELECT price FROM trade_log WHERE etf_code = ? AND action = 'buy' ORDER BY executed_at DESC LIMIT 1",
            (code,),
        ).fetchone()
        if row:
            return float(row[0])
    except Exception:
        pass
    return 1.0


def check_alerts(repo) -> list[dict]:
    """Run score-based alert checks on current positions.

    Runs a scan to get current score signals, then flags positions
    where sell signals appear or build-up should be paused/resumed.
    """
    alerts = []

    # Run scan for latest signals
    try:
        from src.orchestrator import handle
        result = handle("scan", repo)
        recommendations = result.get("recommendations", [])
        build_up = result.get("build_up", {})

        # Sell alerts from scored ETFs we hold
        held_codes = {p.get("etf_code") for p in repo.get_positions()}
        for r in recommendations:
            if r["code"] in held_codes and r["action"] == "sell":
                alerts.append({
                    "code": r["code"],
                    "name": r.get("name", ""),
                    "type": "sell_signal",
                    "severity": "warning",
                    "message": f"{r['code']} {r.get('name','')}: 卖出信号 — PE {r.get('pe_percentile',0.5):.0%}, 评分 {r['score']}",
                    "action": "review_sell",
                })

        # Build-up status change alerts
        if build_up.get("status") == "deploying" and build_up.get("current_batch", 0) > 0:
            alerts.append({
                "code": "BUILD_UP",
                "name": "建仓",
                "type": "build_up_deploying",
                "severity": "info",
                "message": f"建仓进行中 — 第{build_up.get('current_batch',0)}/{build_up.get('total_batches',4)}批",
                "action": "continue_deploy",
            })

        if build_up.get("status") == "paused":
            alerts.append({
                "code": "BUILD_UP",
                "name": "建仓",
                "type": "build_up_paused",
                "severity": "warning",
                "message": f"建仓已暂停 — PE>50%",
                "action": "wait",
            })

    except Exception as e:
        log.warning("Alert check scan failed: %s", e)

    # Log to DB
    for a in alerts:
        repo.log_alert(a["type"], a["severity"], a["message"])

    return alerts


def generate_daily_summary(repo) -> dict:
    """Generate daily summary for OpenClaw to present as WeChat message."""
    positions = repo.get_positions()
    alerts = check_alerts(repo)

    # Build-up status
    from src.build_up import get_build_up_status
    bu = get_build_up_status(repo)

    # Positions summary
    pos_summary = []
    total_value = 0
    total_pnl = 0
    for p in positions:
        value = float(p.get("current_value", 0))
        cost = float(p.get("cost_basis", 0))
        pnl = value - cost
        total_value += value
        total_pnl += pnl
        pos_summary.append({
            "code": p.get("etf_code"),
            "market": p.get("market", ""),
            "value": round(value),
            "cost": round(cost),
            "pnl": round(pnl),
            "pnl_pct": round(p.get("unrealized_pnl_pct", 0) * 100, 1),
            "months": p.get("holding_months", 0),
        })

    return {
        "date": datetime.now().isoformat(),
        "positions": pos_summary,
        "position_count": len(positions),
        "total_value": round(total_value),
        "total_pnl": round(total_pnl),
        "total_pnl_pct": round(total_pnl / max(sum(float(p.get("cost_basis", 0)) for p in positions), 1) * 100, 1),
        "alerts": alerts,
        "build_up": bu,
    }
