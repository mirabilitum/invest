"""Daily markdown report: positions, recommendations, and buy candidates.

Generated after each scan, saved to data/reports/YYYY-MM-DD.md.
"""

import os
from datetime import date


REPORT_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data", "reports"))


def generate(scan_result: dict, repo) -> str:
    """Generate daily markdown report from scan result. Returns file path."""
    today = date.today().isoformat()
    os.makedirs(REPORT_DIR, exist_ok=True)

    lines = []
    lines.append(f"# 投资日报 {today}")
    lines.append("")

    # ── Portfolio status ──
    lines.append("## 持仓")
    lines.append("")
    positions = _get_positions(repo)
    if positions:
        lines.append("| 代码 | 名称 | 市场 | 成本 | 现值 | 盈亏 |")
        lines.append("|------|------|------|------|------|------|")
        for p in positions:
            lines.append(
                f"| {p['code']} | {p.get('name', '')} | {p.get('market', '')} "
                f"| {p.get('cost_basis', 0):,.0f} | {p.get('current_value', 0):,.0f} "
                f"| {p.get('pnl_pct', 0):+.1f}% |"
            )
        total_val = sum(p.get("current_value", 0) or 0 for p in positions)
        total_pnl = sum((p.get("current_value", 0) or 0) - (p.get("cost_basis", 0) or 0) for p in positions)
        lines.append(f"\n总市值: {total_val:,.0f}  |  总盈亏: {total_pnl:+,.0f}")
    else:
        lines.append("暂无持仓")
    lines.append("")

    # ── Recommendations ──
    lines.append("## 今日信号")
    lines.append("")
    recs = scan_result.get("recommendations", [])

    buys = [r for r in recs if r["action"] == "buy"]
    sells = [r for r in recs if r["action"] == "sell"]

    if buys:
        lines.append("### 买入信号")
        lines.append("")
        lines.append("| 代码 | 名称 | 市场 | 评分 | PE分位 |")
        lines.append("|------|------|------|------|--------|")
        for r in buys:
            lines.append(
                f"| {r['code']} | {r.get('name', '')} | {r.get('market', '')} "
                f"| {r['score']} | {r.get('pe_percentile', 0):.0%} |"
            )
        lines.append("")

    if sells:
        lines.append("### 卖出信号")
        lines.append("")
        lines.append("| 代码 | 名称 | 市场 | 评分 | PE分位 |")
        lines.append("|------|------|------|------|--------|")
        for r in sells:
            lines.append(
                f"| {r['code']} | {r.get('name', '')} | {r.get('market', '')} "
                f"| {r['score']} | {r.get('pe_percentile', 0):.0%} |"
            )
        lines.append("")

    if not buys and not sells:
        lines.append("今日无买卖信号")
        lines.append("")

    # ── Buy candidates (hold with high score) ──
    holds = [r for r in recs if r["action"] == "hold" and r["score"] >= 1]
    if holds:
        lines.append("### 关注（持有/接近买入）")
        lines.append("")
        lines.append("| 代码 | 名称 | 市场 | 评分 | PE分位 |")
        lines.append("|------|------|------|------|--------|")
        for r in sorted(holds, key=lambda x: x["score"], reverse=True)[:10]:
            lines.append(
                f"| {r['code']} | {r.get('name', '')} | {r.get('market', '')} "
                f"| {r['score']} | {r.get('pe_percentile', 0):.0%} |"
            )
        lines.append("")

    # ── Build-up status ──
    bu = scan_result.get("build_up", {})
    if bu:
        lines.append("## 建仓进度")
        lines.append("")
        lines.append(f"- 状态: {bu.get('status', '?')}")
        if bu.get("status") == "deploying":
            lines.append(f"- 批次: {bu.get('current_batch', 0)}/{bu.get('total_batches', 4)}")
            lines.append(f"- 已部署: {bu.get('total_deployed_pct', 0):.0f}%")
        lines.append("")

    # ── Weights ──
    weights = scan_result.get("market_weights", {})
    if weights:
        lines.append("## 市场权重")
        lines.append("")
        for mkt, w in weights.items():
            lines.append(f"- {mkt}: {w:.0%}")
        lines.append("")

    # ── Index PE ──
    idx_pe = scan_result.get("index_pe", {})
    if idx_pe:
        lines.append("## 指数 PE")
        lines.append("")
        for idx, pct in idx_pe.items():
            lines.append(f"- {idx}: {pct:.0%}")
        lines.append("")

    # ── Alerts ──
    note = scan_result.get("note", "")
    if note:
        lines.append(f"> {note}")

    md = "\n".join(lines)
    path = os.path.join(REPORT_DIR, f"{today}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)

    return path


def _get_positions(repo):
    """Get current positions with MTM values."""
    try:
        rows = repo.conn.execute(
            "SELECT etf_code, market, cost_basis, current_value, unrealized_pnl_pct FROM position"
        ).fetchall()
        result = []
        for r in rows:
            pnl = (r["current_value"] - r["cost_basis"]) / r["cost_basis"] * 100 if r["cost_basis"] else 0
            result.append({
                "code": r["etf_code"],
                "name": r["etf_code"],
                "market": r["market"],
                "cost_basis": r["cost_basis"],
                "current_value": r["current_value"],
                "pnl_pct": round(pnl, 1),
            })
        return result
    except Exception:
        return []
