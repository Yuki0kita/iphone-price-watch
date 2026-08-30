"""仕入れ値と買取価格から、実際に残る利益を計算する。

要件定義書のルールをそのまま実装する。

    安全利益 = 最高買取価格 − 仕入価格 − 経費 − 査定減額バッファ

査定減額バッファを引くのは、掲示された買取価格がそのまま入金される保証がないため。
保証期間・付属品・購入店シールなどで実際の査定は下がる。
"""
from __future__ import annotations

from dataclasses import dataclass

# 要件定義書 §9 の判定。安全利益がいくら残るかでランクを分ける。
GRADE_THRESHOLDS = [
    (30_000, "S"),
    (20_000, "A"),
    (15_000, "B"),
    (10_000, "C"),
]
LOWEST_GRADE = "D"


@dataclass(frozen=True)
class Profit:
    buyback_price: int
    retail_price: int
    extra_cost: int
    risk_buffer: int
    cash_profit: int
    safe_profit: int
    grade: str
    buy_threshold: int
    required_rise: int


def grade_for(safe_profit: int) -> str:
    for threshold, grade in GRADE_THRESHOLDS:
        if safe_profit >= threshold:
            return grade
    return LOWEST_GRADE


def calculate(
    *,
    buyback_price: int,
    retail_price: int,
    extra_cost: int = 0,
    risk_buffer: int = 3_000,
    minimum_profit: int = 10_000,
) -> Profit:
    """買取価格と仕入価格から利益と判定を出す。

    buy_threshold は「この金額以下で買えば最低利益が残る」上限。
    required_rise は「いま仕入れて保有した場合、買取があといくら上がれば最低利益が出るか」。
    値上げ前に確保して値上がり後に売る戦略では、こちらが判断材料になる。
    """
    cash_profit = buyback_price - retail_price - extra_cost
    safe_profit = cash_profit - risk_buffer
    required_rise = max(minimum_profit - safe_profit, 0)
    return Profit(
        buyback_price=buyback_price,
        retail_price=retail_price,
        extra_cost=extra_cost,
        risk_buffer=risk_buffer,
        cash_profit=cash_profit,
        safe_profit=safe_profit,
        grade=grade_for(safe_profit),
        buy_threshold=buyback_price - extra_cost - risk_buffer - minimum_profit,
        required_rise=required_rise,
    )
