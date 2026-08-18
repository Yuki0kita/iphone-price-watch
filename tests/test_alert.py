import unittest
from datetime import datetime, timedelta

from src.collector import JST, evaluate_alert, should_record, trim_history

NOW = datetime(2026, 8, 18, 17, 0, tzinfo=JST)
CFG = {
    "rise_yen": 3000,
    "new_high_window_days": 30,
    "min_history_points_for_high": 7,
    "cooldown_hours": 12,
}
PRODUCT = {
    "id": "iphone-17-pro-max-512",
    "name": "iPhone 17 Pro Max 512GB",
    "url": "https://www.1-chome.com/productDetail/1371/1927",
    "target_price": None,
}


def rows(*prices, days_ago_start=10):
    """古い順に1日1点の履歴を作る。"""
    return [
        {"timestamp": (NOW - timedelta(days=days_ago_start - i)).isoformat(), "price": p, "ok": True}
        for i, p in enumerate(prices)
    ]


def reasons_for(price, previous_rows, product=None, state=None):
    alert = evaluate_alert(product or PRODUCT, price, previous_rows, CFG, state or {}, NOW)
    return alert.reasons if alert else None


class TargetPriceTest(unittest.TestCase):
    def test_fires_at_exactly_target(self):
        product = {**PRODUCT, "target_price": 240000}
        self.assertEqual(reasons_for(240000, [], product), ["目標価格 ¥240,000 以上"])

    def test_silent_just_below_target(self):
        product = {**PRODUCT, "target_price": 240000}
        self.assertIsNone(reasons_for(239999, [], product))

    def test_no_target_means_no_alert(self):
        self.assertIsNone(reasons_for(999999, []))


class RiseTest(unittest.TestCase):
    def test_fires_at_exactly_threshold(self):
        self.assertEqual(reasons_for(230000, rows(227000)), ["前回比 +¥3,000"])

    def test_silent_one_yen_below_threshold(self):
        self.assertIsNone(reasons_for(229999, rows(227000)))

    def test_silent_on_decline(self):
        self.assertIsNone(reasons_for(220000, rows(227000)))

    def test_compares_against_latest_row_only(self):
        """途中で下がっていても、比較対象は直前の記録。"""
        self.assertIsNone(reasons_for(228000, rows(200000, 227000)))


class NewHighTest(unittest.TestCase):
    def test_fires_when_exceeding_window_high(self):
        history = rows(*[220000] * 6, 227000)
        self.assertEqual(reasons_for(227500, history), ["30日高値を更新"])

    def test_silent_when_equal_to_window_high(self):
        history = rows(*[220000] * 6, 227000)
        self.assertIsNone(reasons_for(227000, history))

    def test_silent_until_minimum_points(self):
        """記録が6点しかない間は高値更新を判定しない（7点が閾値）。"""
        self.assertIsNone(reasons_for(221000, rows(*[220000] * 6)))
        self.assertEqual(reasons_for(221000, rows(*[220000] * 7)), ["30日高値を更新"])

    def test_ignores_rows_outside_window(self):
        """30日より前の高値は判定に使わない。"""
        old = [
            {"timestamp": (NOW - timedelta(days=40)).isoformat(), "price": 300000, "ok": True}
            for _ in range(7)
        ]
        recent = rows(*[220000] * 7)
        self.assertEqual(reasons_for(221000, old + recent), ["30日高値を更新"])

    def test_ignores_broken_rows(self):
        history = rows(*[220000] * 7) + [{"timestamp": "壊れた値", "price": 999999}]
        self.assertIn("30日高値を更新", reasons_for(221000, history))


class CooldownTest(unittest.TestCase):
    def test_suppresses_same_price_within_cooldown(self):
        product = {**PRODUCT, "target_price": 240000}
        state = {PRODUCT["id"]: {"price": 240000, "sent_at": (NOW - timedelta(hours=11)).isoformat()}}
        self.assertIsNone(reasons_for(240000, [], product, state))

    def test_allows_after_cooldown(self):
        product = {**PRODUCT, "target_price": 240000}
        state = {PRODUCT["id"]: {"price": 240000, "sent_at": (NOW - timedelta(hours=12)).isoformat()}}
        self.assertIsNotNone(reasons_for(240000, [], product, state))

    def test_allows_when_price_changed(self):
        product = {**PRODUCT, "target_price": 240000}
        state = {PRODUCT["id"]: {"price": 240000, "sent_at": (NOW - timedelta(hours=1)).isoformat()}}
        self.assertIsNotNone(reasons_for(241000, [], product, state))


class MultipleReasonsTest(unittest.TestCase):
    def test_lists_every_matching_reason(self):
        product = {**PRODUCT, "target_price": 240000}
        history = rows(*[220000] * 6, 227000)
        self.assertEqual(
            reasons_for(240000, history, product),
            ["目標価格 ¥240,000 以上", "前回比 +¥13,000", "30日高値を更新"],
        )


class ShouldRecordTest(unittest.TestCase):
    def test_records_first_observation(self):
        self.assertTrue(should_record(None, 227000, NOW.isoformat()))

    def test_records_price_change(self):
        previous = {"timestamp": NOW.isoformat(), "price": 226000}
        self.assertTrue(should_record(previous, 227000, NOW.isoformat()))

    def test_skips_same_price_same_day(self):
        previous = {"timestamp": (NOW - timedelta(hours=3)).isoformat(), "price": 227000}
        self.assertFalse(should_record(previous, 227000, NOW.isoformat()))

    def test_keeps_one_heartbeat_per_day(self):
        previous = {"timestamp": (NOW - timedelta(days=1)).isoformat(), "price": 227000}
        self.assertTrue(should_record(previous, 227000, NOW.isoformat()))


class TrimHistoryTest(unittest.TestCase):
    def test_drops_rows_older_than_retention(self):
        history = [
            {"timestamp": (NOW - timedelta(days=181)).isoformat(), "price": 1},
            {"timestamp": (NOW - timedelta(days=179)).isoformat(), "price": 2},
        ]
        self.assertEqual([r["price"] for r in trim_history(history, NOW, 180)], [2])

    def test_keeps_unparsable_rows(self):
        history = [{"timestamp": "壊れた値", "price": 1}]
        self.assertEqual(len(trim_history(history, NOW, 180)), 1)


if __name__ == "__main__":
    unittest.main()
