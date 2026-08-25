import unittest

from src.profit import calculate, grade_for


class GradeTest(unittest.TestCase):
    def test_boundaries(self):
        """要件定義書の判定境界をそのまま守る。"""
        cases = [
            (30000, "S"), (29999, "A"),
            (20000, "A"), (19999, "B"),
            (15000, "B"), (14999, "C"),
            (10000, "C"), (9999, "D"),
            (0, "D"), (-50000, "D"),
        ]
        for profit, expected in cases:
            with self.subTest(profit=profit):
                self.assertEqual(grade_for(profit), expected)


class CalculateTest(unittest.TestCase):
    def test_matches_requirement_example(self):
        """要件定義書 §2 の例: 買取10万 / 仕入7.8万 / 経費2千 / バッファ5千 → 安全利益1.5万。"""
        p = calculate(
            buyback_price=100000, retail_price=78000, extra_cost=2000, risk_buffer=5000
        )
        self.assertEqual(p.cash_profit, 20000)
        self.assertEqual(p.safe_profit, 15000)
        self.assertEqual(p.grade, "B")

    def test_buy_threshold_leaves_minimum_profit(self):
        """上限価格で買えば、ちょうど最低利益が残る。"""
        p = calculate(
            buyback_price=176500, retail_price=0, extra_cost=500,
            risk_buffer=3000, minimum_profit=10000,
        )
        self.assertEqual(p.buy_threshold, 163000)

        at_threshold = calculate(
            buyback_price=176500, retail_price=163000, extra_cost=500, risk_buffer=3000
        )
        self.assertEqual(at_threshold.safe_profit, 10000)

    def test_reports_loss_without_clamping(self):
        """逆ザヤはそのまま負の値で出す。丸めて隠さない。"""
        p = calculate(buyback_price=257500, retail_price=281600, extra_cost=500, risk_buffer=3000)
        self.assertEqual(p.cash_profit, -24600)
        self.assertEqual(p.safe_profit, -27600)
        self.assertEqual(p.grade, "D")

    def test_risk_buffer_is_subtracted_from_cash_profit(self):
        p = calculate(buyback_price=50000, retail_price=40000, extra_cost=0, risk_buffer=3000)
        self.assertEqual(p.cash_profit, 10000)
        self.assertEqual(p.safe_profit, 7000)


if __name__ == "__main__":
    unittest.main()
