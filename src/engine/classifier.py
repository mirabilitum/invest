from dataclasses import dataclass
from datetime import datetime


@dataclass
class ClassificationInput:
    industry: str
    market: str
    revenue_vol: float
    gdp_corr: float
    moat_depth: str
    moat_durability: int
    lifecycle: str


def classify_industry(inp: ClassificationInput) -> dict:
    """5-dimension classification → 4-type output (CONSTRAINTS v1.4 §3).

    Scoring rules:
    1. Lifecycle = 衰退期 → avoid (override all others)
    2. Revenue vol: <8% → structural, 8-20% → cyclical, >20% → event_driven
    3. GDP corr: <0.3 → structural, 0.3-0.6 → cyclical, >0.6 or <-0.3 → event_driven
    4. Moat depth: 高 → structural, 中 → cyclical, 低 → event_driven
    5. Moat durability: 7-9 → structural, 4-6 → cyclical, 3 → event_driven

    Each dimension votes for a type. Final type = mode of votes.
    Ties broken by lifecycle stage (成长期 > 成熟期 > 衰退期).
    """
    votes = {"structural": 0, "cyclical": 0, "event_driven": 0}

    # Lifecycle check first
    if inp.lifecycle == "衰退期":
        return _make_result(inp, "avoid", votes)

    # Dimension 1: Revenue volatility
    if inp.revenue_vol < 0.08:
        votes["structural"] += 1
    elif inp.revenue_vol <= 0.20:
        votes["cyclical"] += 1
    else:
        votes["event_driven"] += 1

    # Dimension 2: GDP correlation
    if 0 <= inp.gdp_corr < 0.3:
        votes["structural"] += 1
    elif 0.3 <= inp.gdp_corr <= 0.6:
        votes["cyclical"] += 1
    else:
        votes["event_driven"] += 1

    # Dimension 3: Moat depth
    if inp.moat_depth == "高":
        votes["structural"] += 1
    elif inp.moat_depth == "中":
        votes["cyclical"] += 1
    else:
        votes["event_driven"] += 1

    # Dimension 4: Moat durability
    if inp.moat_durability >= 7:
        votes["structural"] += 1
    elif inp.moat_durability >= 4:
        votes["cyclical"] += 1
    else:
        votes["event_driven"] += 1

    # Determine winner: most votes, tie-break by lifecycle
    max_votes = max(votes.values())
    winners = [k for k, v in votes.items() if v == max_votes]

    if len(winners) == 1:
        category = winners[0]
    else:
        lifecycle_rank = {"成长期": "structural", "成熟期": "cyclical", "衰退期": "avoid"}
        category = lifecycle_rank.get(inp.lifecycle, "cyclical")

    return _make_result(inp, category, votes)


def _make_result(inp: ClassificationInput, category: str, votes: dict) -> dict:
    return {
        "industry": inp.industry,
        "market": inp.market,
        "category_type": category,
        "revenue_vol": inp.revenue_vol,
        "gdp_corr": inp.gdp_corr,
        "moat_depth": inp.moat_depth,
        "moat_durability": inp.moat_durability,
        "lifecycle": inp.lifecycle,
        "vote_detail": votes,
        "rule_version": "v1.4",
        "rated_at": datetime.now().isoformat(),
    }
