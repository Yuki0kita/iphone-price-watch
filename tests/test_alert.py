import unittest
from datetime import datetime, timedelta

from src.collector import (
    JST,
    SHOP_NAME,
    best_offer,
    best_quote,
    cached_quotes,
    evaluate_alert,
    merge_quotes,
    mobasute_quote,
    should_check_market,
    should_record,
    trim_history,
)
from src.kaitorix import Quote
from src.profit import calculate

NOW = datetime(2026, 8, 18, 17, 0, tzinfo=JST)
CFG = {
    "rise_yen": 3000,
    "new_high_window_days": 30,
    "min_history_points_for_high": 7,
    "cooldown_hours": 12,
    "market_gap_yen": 5000,
    "min_safe_profit_yen": 15000,
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


def reasons_for(price, previous_rows, product=None, state=None, market=None, profit=None):
    alert = evaluate_alert(product or PRODUCT, price, previous_rows, CFG, state or {}, NOW, market, profit)
    return alert.reasons if alert else None


def quote(price, store="他店買取"):
    return Quote(store=store, price=price, url="https://example.com")


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
        state = {PRODUCT["id"]: {"key": "240000", "price": 240000, "sent_at": (NOW - timedelta(hours=11)).isoformat()}}
        self.assertIsNone(reasons_for(240000, [], product, state))

    def test_allows_after_cooldown(self):
        product = {**PRODUCT, "target_price": 240000}
        state = {PRODUCT["id"]: {"key": "240000", "price": 240000, "sent_at": (NOW - timedelta(hours=12)).isoformat()}}
        self.assertIsNotNone(reasons_for(240000, [], product, state))

    def test_allows_when_price_changed(self):
        product = {**PRODUCT, "target_price": 240000}
        state = {PRODUCT["id"]: {"key": "240000", "price": 240000, "sent_at": (NOW - timedelta(hours=1)).isoformat()}}
        self.assertIsNotNone(reasons_for(241000, [], product, state))


class MultipleReasonsTest(unittest.TestCase):
    def test_lists_every_matching_reason(self):
        product = {**PRODUCT, "target_price": 240000}
        history = rows(*[220000] * 6, 227000)
        self.assertEqual(
            reasons_for(240000, history, product),
            ["目標価格 ¥240,000 以上", "前回比 +¥13,000", "30日高値を更新"],
        )


class BestOfferTest(unittest.TestCase):
    def test_uses_shop_price_when_no_market(self):
        self.assertEqual(best_offer(138000, None), (138000, SHOP_NAME))

    def test_uses_market_when_higher(self):
        self.assertEqual(best_offer(138000, quote(145000)), (145000, "他店買取"))

    def test_keeps_shop_when_market_is_equal(self):
        """同額なら手間の少ない既知の店を優先する。"""
        self.assertEqual(best_offer(138000, quote(138000)), (138000, SHOP_NAME))

    def test_keeps_shop_when_market_is_lower(self):
        self.assertEqual(best_offer(138000, quote(130000)), (138000, SHOP_NAME))


class MarketAlertTest(unittest.TestCase):
    def test_target_reached_only_at_another_store(self):
        """買取1丁目が目標未達でも、他店が到達していれば通知する。"""
        product = {**PRODUCT, "target_price": 139800}
        reasons = reasons_for(138000, [], product, market=quote(141000))
        self.assertEqual(reasons, ["目標価格 ¥139,800 以上（他店買取 ¥141,000）"])

    def test_target_not_reached_anywhere(self):
        product = {**PRODUCT, "target_price": 139800}
        self.assertIsNone(reasons_for(138000, [], product, market=quote(139000)))

    def test_gap_fires_at_exactly_threshold(self):
        self.assertEqual(reasons_for(138000, [], market=quote(143000)), ["他店買取が¥5,000高い（¥143,000）"])

    def test_gap_silent_one_yen_below_threshold(self):
        self.assertIsNone(reasons_for(138000, [], market=quote(142999)))

    def test_gap_silent_when_market_is_lower(self):
        self.assertIsNone(reasons_for(138000, [], market=quote(130000)))

    def test_cooldown_uses_best_price(self):
        """他店の最高額が変わらない限り、毎日の横断チェックで再通知しない。"""
        state = {PRODUCT["id"]: {"key": "143000", "price": 143000, "sent_at": (NOW - timedelta(hours=11)).isoformat()}}
        self.assertIsNone(reasons_for(138000, [], state=state, market=quote(143000)))

    def test_cooldown_released_when_best_price_moves(self):
        state = {PRODUCT["id"]: {"key": "143000", "price": 143000, "sent_at": (NOW - timedelta(hours=1)).isoformat()}}
        self.assertIsNotNone(reasons_for(138000, [], state=state, market=quote(144000)))


class ProfitAlertTest(unittest.TestCase):
    def profit(self, retail, buyback=176500):
        return calculate(buyback_price=buyback, retail_price=retail, extra_cost=500, risk_buffer=3000)

    def test_fires_at_exactly_threshold(self):
        """安全利益がちょうど15,000円で通知する。"""
        reasons = reasons_for(176500, [], profit=self.profit(158000))
        self.assertEqual(len(reasons), 1)
        self.assertIn("判定B 安全利益 ¥15,000", reasons[0])

    def test_silent_one_yen_below_threshold(self):
        self.assertIsNone(reasons_for(176500, [], profit=self.profit(158001)))

    def test_silent_on_reverse_spread(self):
        """仕入れのほうが高い場合は通知しない。"""
        self.assertIsNone(reasons_for(176500, [], profit=self.profit(190000)))

    def test_reports_grade_s_for_large_spread(self):
        reasons = reasons_for(176500, [], profit=self.profit(130000))
        self.assertIn("判定S", reasons[0])

    def test_cooldown_released_when_retail_price_moves(self):
        """買取が同じでも仕入れが動いたら通知しなおす。"""
        state = {PRODUCT["id"]: {"key": "176500/158000", "sent_at": (NOW - timedelta(hours=1)).isoformat()}}
        self.assertIsNone(reasons_for(176500, [], state=state, profit=self.profit(158000)))
        self.assertIsNotNone(reasons_for(176500, [], state=state, profit=self.profit(150000)))


class MergeQuotesTest(unittest.TestCase):
    def ktx(self, price, store="買取X店"):
        return [{"store": store, "price": price, "url": "", "source": "kaitorix"}]

    def test_sorts_all_sources_by_price(self):
        quotes = merge_quotes(self.ktx(141000), [mobasute_quote(139000)])
        self.assertEqual([q["price"] for q in quotes], [141000, 139000])
        self.assertEqual(quotes[1]["source"], "mobasute")

    def test_mobasute_can_win(self):
        quotes = merge_quotes(self.ktx(135000), [mobasute_quote(139000)])
        self.assertEqual(best_quote(quotes).store, "モバステ")

    def test_works_without_mobasute_price(self):
        self.assertEqual(len(merge_quotes(self.ktx(141000), [])), 1)

    def test_works_without_ktx(self):
        quotes = merge_quotes([], [mobasute_quote(139000)])
        self.assertEqual(best_quote(quotes).price, 139000)

    def test_no_quotes_means_no_best(self):
        self.assertIsNone(best_quote(merge_quotes([], [])))

    def test_cached_quotes_filters_by_source(self):
        """モバステの価格は毎回取り直すため、キャッシュとして持ち越さない。"""
        market = {
            "products": {
                "p1": {
                    "quotes": [
                        {"store": "買取X店", "price": 141000, "source": "kaitorix"},
                        {"store": "モバステ", "price": 139000, "source": "mobasute"},
                    ]
                }
            }
        }
        cached = cached_quotes(market, "p1", "kaitorix")
        self.assertEqual([q["store"] for q in cached], ["買取X店"])

    def test_cached_quotes_handles_missing_product(self):
        self.assertEqual(cached_quotes({}, "p1", "kaitorix"), [])


class ShouldCheckMarketTest(unittest.TestCase):
    def test_checks_when_never_run(self):
        self.assertTrue(should_check_market({}, NOW.isoformat()))

    def test_skips_when_already_checked_today(self):
        market = {"checked_at": (NOW - timedelta(hours=6)).isoformat()}
        self.assertFalse(should_check_market(market, NOW.isoformat()))

    def test_checks_once_the_date_changes(self):
        market = {"checked_at": (NOW - timedelta(days=1)).isoformat()}
        self.assertTrue(should_check_market(market, NOW.isoformat()))


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
