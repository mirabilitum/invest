from dataclasses import dataclass
from src.config import get


@dataclass
class ScoreInput:
    category: str
    pe_percentile: float
    flow_pct: float            # 主力净流入净占比 (%)
    volatility: float          # current VIX / iVIX / VHSI value
    vol_normal_max: float      # threshold for elevated vol
    vol_panic: float           # threshold for panic
    market: str = "qdii"


def _signal(value: float, buy: float, sell: float) -> int:
    if value >= sell:
        return 1
    if value <= buy:
        return -1
    return 0


def score_industry(inp: ScoreInput) -> dict:
    """Score an ETF across three signals: PE, fund flow, volatility.

    pe_signal:    low PE percentile → buy (+1), high → sell (-1)
    flow_signal:  net institutional inflow > 3% → +1, outflow > 3% → -1
    vol_signal:   panic vol + low PE → buy the dip (+1)
                  panic vol + high PE → exit (-1)

    total = pe_signal + flow_signal + vol_signal  (range: -3 to +3)
    buy  when total >= 2 (at least 2 of 3 bullish)
    sell when total <= -2
    """
    pe_pct = inp.pe_percentile if inp.pe_percentile is not None else 0.5

    # ── PE signal ──────────────────────────────────────────────
    if inp.category == "structural":
        buy_below = get("scoring.pe_signals.structural.buy_below", 0.30)
        sell_above = get("scoring.pe_signals.structural.sell_above", 0.70)
    elif inp.category == "cyclical":
        buy_below = get("scoring.pe_signals.cyclical.buy_below", 0.25)
        sell_above = get("scoring.pe_signals.cyclical.sell_above", 0.65)
    else:
        buy_below = 0.30
        sell_above = 0.70

    pe_signal = 0
    if pe_pct < buy_below:
        pe_signal = 1
    elif pe_pct > sell_above:
        pe_signal = -1

    # ── Fund flow signal ───────────────────────────────────────
    flow = inp.flow_pct
    flow_signal = 0
    if flow > 3.0:
        flow_signal = 1
    elif flow < -3.0:
        flow_signal = -1

    # ── Volatility signal ──────────────────────────────────────
    vol_signal = 0
    if inp.volatility >= inp.vol_panic:
        # Panic: if PE is low → buy the dip; if PE is high → exit
        if pe_signal == 1:
            vol_signal = 1
        elif pe_signal == -1:
            vol_signal = -1
    elif inp.volatility >= inp.vol_normal_max:
        # Elevated vol + low PE → opportunity
        if pe_signal == 1:
            vol_signal = 1

    # ── Total ──────────────────────────────────────────────────
    total = pe_signal + flow_signal + vol_signal

    if total >= 2:
        action = "buy"
    elif total <= -1:
        action = "sell"
    elif inp.category == "event_driven" and total <= 0:
        action = "skip"
    else:
        action = "hold"

    return {
        "signal_pe": pe_signal,
        "signal_core": flow_signal,
        "signal_sentiment": vol_signal,
        "total_score": total,
        "action": action,
        "a_share_gated": 0,
    }
