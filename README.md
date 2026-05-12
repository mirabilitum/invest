# Invest Agent v2

ETF 筛选 → 三信号评分（PE + 资金流 + 波动率）→ 动态分批建仓 → 持仓跟踪。

协助完成投资（默认为100w人民币的组合），跟踪 A 股 / QDII / HK（港股通） 三大市场宽基指数基金。

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env    # 编辑填入 API key（可选）
python -m src.cli scan   # 运行首次扫描
```

## 三种使用方式

### 1. Claude Code / Codex（CLI）

```bash
python -m src.cli scan
python -m src.cli status
python -m src.cli "pe 28.5 35.2 10.1"
python -m src.cli "买入 510300 5万"
python -m src.cli "卖出 510300 全部"
python -m src.cli reset
```

输出为终端格式，直接可读。

### 2. OpenClaw Skill

将 `invest-skill/` 目录注册为 OpenClaw Skill。OpenClaw 会将微信消息转发给 `invest-skill/scripts/run.py`，脚本返回 JSON 由 LLM 润色后推送。

```bash
openclaw skill install --path ./invest-skill
openclaw skill enable invest
```

Cron 定时任务在 OpenClaw 侧配置：

```json
{
  "cron": [
    {"schedule": "0 17 * * 1-5", "agent": "invest", "prompt": "执行每日持仓更新"},
    {"schedule": "0 10 * * 6",   "agent": "invest", "prompt": "生成周报"},
    {"schedule": "0 10 1-7 * 6", "agent": "invest", "prompt": "生成月报调仓建议"}
  ]
}
```

### 3. HTTP API

```bash
pip install fastapi uvicorn
python -m src.api               # 启动 http://127.0.0.1:7711
```

```bash
curl -X POST http://127.0.0.1:7711/invest \
  -H "Content-Type: application/json" \
  -d '{"text": "scan"}'
```

未安装 fastapi 时自动退化为 stdin 交互模式。

## 命令

| 命令 | 说明 |
|------|------|
| `scan` | ETF 发现 → 三信号评分 → 建仓建议 |
| `pe <SPX> <NDX> <HSI>` | 录入三大指数当前 PE（A 股自动拉取） |
| `status` | 持仓盈亏 + 建仓进度 + 告警 |
| `买入 <code> <金额>` | 录买入，支持"万"单位 |
| `卖出 <code> <金额/全部>` | 录卖出 |
| `reset` | 重置新一轮建仓 |

## 评分系统

```
total = pe_signal + flow_signal + vol_signal

pe_signal:   PE 分位 < 30% → +1, > 70% → -1
flow_signal: 主力净流入 10日 MA > 3% → +1, < -3% → -1
vol_signal:  恐慌波动 + 低 PE → +1（别人恐惧我贪婪）

buy:  total >= 2
sell: total <= -1
```

PE 分位数据来源（按优先级）：
1. **danjuanfunds** — 自动拉取 63 个指数 PE + 历史分位（覆盖恒生指数/科技、标普 500、纳指等），3 次重试
2. **web_pe** — danjuan 失败时自动 fallback：siblisresearch（HSI 网页抓取）+ Yahoo Finance（SPX/NDX，SPY/QQQ 代理）
3. **ETF 持仓反推** — 爬东方财富 F10 持仓 → 逐股 PE → 加权平均
4. **行业 band 估算** — 手动校准的 PE 区间，每月更新一次
5. **用户手动录入** — `pe <SPX> <NDX> <HSI>` 命令

资金流数据来源：
- `fund_etf_spot_em` 每日全量快照 → 缓存到 DB（当天不重复拉）
- 首日 `stock_individual_fund_flow` 回填历史 → 10 日 MA 即查即算
- 后续 scan 当日只算 MA，不重复拉 API

## 建仓策略

- PE < 30% 触发窗口 → 分 4 批部署（30% + 30% + 25% + 15%）
- 每批间隔 ≥ 2 周
- 分配按动态权重（便宜程度 × 资金确认）
- PE > 50% 暂停，回落后恢复
- 4 批完成 → 轮动模式

## 配置

`config.yaml` 包含所有阈值参数。`.env` 管理密钥。

## 目录结构

```
src/
  orchestrator.py  命令分发中心
  engine/
    scorer.py       三信号评分引擎
    constraints.py  组合约束
    classifier.py   行业分类
    rebalance.py    调仓计算
  data/
    reports/        每日扫描报告（YYYY-MM-DD.md）
    etf_pool.py     ETF 发现 + 筛选
    market.py       PE 数据拉取（A 股自动）
    index_pe.py     指数 PE（danjuanfunds，63指数自动拉取）
    web_pe.py       多源 PE fallback（siblisresearch + Yahoo）
    flow.py         资金流（10日 MA，每日缓存不重复拉）
    etf_pe.py       ETF PE 从持仓反推
    volatility.py   波动率（VIX/VHSI/iVIX）
    news.py         RSS 舆情扫描
    calendar.py     交易日历
  db/
    schema.py       DB 表结构
    repo.py         数据访问层
  tracker.py        每日盯市 + 告警
  build_up.py       动态分批建仓状态机
  config.py         配置读取
  init.py           初始化（加载 .env + DB + repo）
  cli.py            CLI 入口
  api.py            HTTP API 入口
invest-skill/       OpenClaw Skill 文件
```
