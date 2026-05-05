---
name: invest
description: 投资监控 Agent — ETF 筛选、三信号评分、动态分批建仓、持仓跟踪
version: 2.0.0
author: invest-agent
---

## 概述

投资监控 Agent，辅助管理 100 万 RMB 投资组合。纯计算 Skill — 数据拉取、规则引擎、评分卡。LLM 润色和推送由 OpenClaw 负责。

## 触发方式

### 用户命令（微信 → ClawBot → OpenClaw → Skill）

| 命令 | 说明 |
|------|------|
| `scan` | ETF 发现 → 三信号评分（PE+资金流+波动率）→ 动态权重 → 建仓建议 |
| `pe <SPX> <NDX> <HSI>` | 录入三大指数 PE（A 股自动拉取，不占此命令） |
| `status` | 持仓盈亏 + 建仓分批进度 + 告警 |
| `买入 <代码> <金额>` | 录买入 + 推进建仓批次 |
| `卖出 <代码> <金额/全部>` | 录卖出 |
| `reset` | 重置新一轮建仓 |

自由文本自动记录为 L1 信息输入。

### Cron 定时（OpenClaw 侧配置）

```json
{
  "cron": [
    {"schedule": "0 17 * * 1-5", "agent": "invest", "prompt": "执行每日持仓更新"},
    {"schedule": "0 10 * * 6",   "agent": "invest", "prompt": "生成周报"},
    {"schedule": "0 10 1-7 * 6", "agent": "invest", "prompt": "生成月报调仓建议"}
  ]
}
```

### 每日更新内部流程

1. `tracker.update_positions(repo)` — 更新持仓市值和盈亏
2. `tracker.check_alerts(repo)` — 检测卖出信号/建仓窗口变化
3. 有告警 → 推微信

## 评分系统（v2.0）

```
total = pe_signal + flow_signal + vol_signal

pe_signal:   PE 分位 < 30% → +1, > 70% → -1
flow_signal: 主力净流入 > 3% → +1, < -3% → -1
vol_signal:  恐慌波动 + 低 PE → +1

buy:  total >= 2  |  sell: total <= -1
```

## 建仓策略（v2.0）

- PE < 30% 触发窗口 → 分 4 批部署
- 每批间隔 ≥ 2 周
- 分配按动态权重（便宜程度 × 资金确认）
- PE > 50% 自动暂停

## 安装

```bash
pip install -r requirements.txt
cp .env.example .env  # 可选
openclaw skill install --path ./invest-skill
openclaw skill enable invest
```
