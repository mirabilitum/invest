"""CLI entry point for Claude Code / Codex.

Usage:
    python -m src.cli scan
    python -m src.cli status
    python -m src.cli "pe 28.5 35.2 10.1"
    python -m src.cli "买入 510300 5万"
    python -m src.cli "卖出 510300 全部"
    python -m src.cli reset
"""

import sys
from src.init import load_env, init_db, ensure_build_up


def format_result(result: dict) -> str:
    """Format orchestrator result as readable terminal output."""
    action = result.get("action", "")
    lines = []

    if result.get("message"):
        lines.append(result["message"])

    if action == "scan":
        lines.append("")
        recs = result.get("recommendations", [])
        for mkt, label in [("a_share", "A股"), ("qdii", "QDII"), ("hk", "HK")]:
            items = [r for r in recs if r["market"] == mkt]
            if not items:
                continue
            lines.append(f"── {label} ──")
            for r in items:
                pe = f'{r.get("pe_percentile", 0.5):.0%}'
                lines.append(
                    f"  {r['code']} {r['name']:<28s} "
                    f"score={r['score']:>2d} {r['action']:<5s} PE={pe}"
                )
            lines.append("")

        bu = result.get("build_up", {})
        lines.append(f"建仓: {bu.get('status', '?')} — {bu.get('message', '')}")
        weights = result.get("market_weights", {})
        if weights:
            w_str = " ".join(f"{m}:{p:.0%}" for m, p in weights.items())
            lines.append(f"动态权重: {w_str}")
        lines.append(f"总通过: {result.get('total_found', 0)}")

        idx = result.get("index_pe", {})
        if idx:
            pe_str = " ".join(f"{k}={v:.0%}" for k, v in idx.items())
            lines.append(f"指数PE: {pe_str}")

        if result.get("note"):
            lines.append(result["note"])

    elif action == "status":
        lines.append("")
        lines.append(f"持仓: {result.get('total_positions', 0)}只 "
                     f"市值={result.get('total_value', 0):,.0f} "
                     f"盈亏={result.get('total_pnl', 0):+,.0f}")
        for p in result.get("positions", []):
            lines.append(f"  {p['code']} {p['market']:<6s} "
                         f"成本={p['cost_basis']:,.0f} 现值={p['current_value']:,.0f} "
                         f"盈亏={p['pnl_pct']:+.1f}% {p['holding_months']}月")

        bu = result.get("build_up", {})
        if bu:
            lines.append(f"\n建仓: {bu.get('status')} "
                         f"第{bu.get('current_batch', 0)}/{bu.get('total_batches', 4)}批 "
                         f"已部署{bu.get('total_deployed_pct', 0):.0f}%")

        alerts = result.get("alerts", [])
        if alerts:
            lines.append(f"\n告警: {len(alerts)}条")
            for a in alerts:
                lines.append(f"  [{a['severity']}] {a['message']}")

    elif action == "buy":
        bu = result.get("build_up", {})
        lines.append(f"{result['code']} {result['name']} "
                     f"金额={result['amount']:,.0f} 市场={result['market']}")

    elif action == "sell":
        lines.append(f"{result['code']} 卖出={result['amount']:,.0f} "
                     f"已实现盈亏={result.get('realized_pnl', 0):+,.0f}")

    elif action == "reset":
        lines.append(f"轮次={result.get('round', '?')} {result.get('message', '')}")

    elif action == "pe":
        lines.append(result.get("message", ""))

    return "\n".join(lines)


def main():
    load_env()
    conn, repo = init_db()
    ensure_build_up(repo)

    from src.orchestrator import handle

    if len(sys.argv) > 1:
        text = " ".join(sys.argv[1:])
    else:
        print("Usage: python -m src.cli <command>")
        print("  scan | status | 'pe SPX NDX HSI' | '买入 <code> <amount>' | reset")
        sys.exit(1)

    result = handle(text, repo)
    print(format_result(result))


if __name__ == "__main__":
    main()
