#!/usr/bin/env python3
"""OpenClaw invest Skill entry point.

OpenClaw calls this script with user message as stdin or argv[1].
Outputs JSON for OpenClaw's LLM to format into a WeChat reply.
"""

import sys
import json

from src.init import find_project_root, load_env, init_db, ensure_build_up

# Auto-detect project root (no hardcoded paths)
PROJECT_ROOT = find_project_root()
sys.path.insert(0, str(PROJECT_ROOT))

load_env()

from src.orchestrator import handle
from src.tracker import update_positions, check_alerts, generate_daily_summary
from src.build_up import get_build_up_status


def handle_message(text: str, repo) -> dict:
    """Route to orchestrator or internal cron handlers."""
    text = text.strip()

    if text == "执行每日持仓更新":
        result = update_positions(repo)
        alerts = check_alerts(repo)
        summary = generate_daily_summary(repo)
        return {
            "status": "ok",
            "action": "daily_update",
            "updated": result["updated"],
            "alerts_count": len(alerts),
            "summary": summary,
        }

    if text == "生成周报":
        summary = generate_daily_summary(repo)
        return {
            "status": "ok",
            "action": "weekly_report",
            "summary": summary,
            "instruction": "请基于以上数据生成周报：各市场PE分位、持仓盈亏、建仓进度、风险提示。",
        }

    if text == "生成月报调仓建议":
        bu = get_build_up_status(repo)
        positions = repo.get_positions()
        return {
            "status": "ok",
            "action": "monthly_report",
            "build_up": bu,
            "positions": [
                {"code": p.get("etf_code"), "market": p.get("market"),
                 "pnl_pct": p.get("unrealized_pnl_pct"), "months": p.get("holding_months")}
                for p in positions
            ],
            "instruction": "请基于以上数据生成月报调仓建议：评分排序、调仓代码和比例、已实现收益结算。",
        }

    return handle(text, repo)


def main():
    conn, repo = init_db()
    ensure_build_up(repo)

    # OpenClaw passes message as arg or stdin
    if len(sys.argv) > 1:
        text = " ".join(sys.argv[1:])
    else:
        text = sys.stdin.read().strip()

    if not text:
        print(json.dumps({"status": "error", "message": "No input"}, ensure_ascii=False))
        return

    result = handle_message(text, repo)
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
