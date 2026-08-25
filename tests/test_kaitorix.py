import unittest

from src.kaitorix import (
    MAX_PLAUSIBLE_PRICE,
    MIN_PLAUSIBLE_PRICE,
    KaitoriXError,
    parse_product,
)

JAN = "4549995649154"


def payload(prices, **extra):
    return {"jan": JAN, "name": "iPhone 17 256GB", "prices": prices, **extra}


class ParseProductTest(unittest.TestCase):
    def test_sorts_stores_by_price(self):
        market = parse_product(
            payload(
                [
                    {"store": "A買取", "price": 138000, "url": "https://a.example"},
                    {"store": "C買取", "price": 145000, "url": "https://c.example"},
                    {"store": "B買取", "price": 141000, "url": "https://b.example"},
                ],
                max_price=145000,
            ),
            JAN,
        )
        self.assertEqual([q.store for q in market.quotes], ["C買取", "B買取", "A買取"])
        self.assertEqual(market.max_price, 145000)
        self.assertEqual(market.best.store, "C買取")

    def test_falls_back_to_highest_quote_without_max_price(self):
        """max_priceが返らなくても店舗別価格から最高額を決める。"""
        market = parse_product(payload([{"store": "A買取", "price": 141000}]), JAN)
        self.assertEqual(market.max_price, 141000)

    def test_accepts_comma_separated_price(self):
        market = parse_product(payload([{"store": "A買取", "price": "141,000"}]), JAN)
        self.assertEqual(market.max_price, 141000)

    def test_skips_unusable_quotes(self):
        market = parse_product(
            payload(
                [
                    {"store": "A買取", "price": None},
                    {"store": "B買取", "price": "-"},
                    {"store": "C買取", "price": 138000},
                    "壊れた行",
                ]
            ),
            JAN,
        )
        self.assertEqual([q.store for q in market.quotes], ["C買取"])

    def test_price_boundaries(self):
        for price in [MIN_PLAUSIBLE_PRICE, MAX_PLAUSIBLE_PRICE]:
            with self.subTest(price=price):
                market = parse_product(payload([{"store": "A買取", "price": price}]), JAN)
                self.assertEqual(market.max_price, price)
        for price in [MIN_PLAUSIBLE_PRICE - 1, MAX_PLAUSIBLE_PRICE + 1]:
            with self.subTest(price=price):
                with self.assertRaises(KaitoriXError):
                    parse_product(payload([{"store": "A買取", "price": price}]), JAN)

    def test_missing_store_name_falls_back(self):
        market = parse_product(payload([{"price": 138000}]), JAN)
        self.assertEqual(market.best.store, "不明")

    def test_rejects_missing_prices_field(self):
        with self.assertRaisesRegex(KaitoriXError, "prices"):
            parse_product({"jan": JAN, "name": "iPhone 17 256GB"}, JAN)

    def test_rejects_empty_quotes(self):
        with self.assertRaises(KaitoriXError):
            parse_product(payload([]), JAN)

    def test_rejects_non_dict_payload(self):
        for value in [None, [], "ok"]:
            with self.subTest(value=value):
                with self.assertRaises(KaitoriXError):
                    parse_product(value, JAN)


if __name__ == "__main__":
    unittest.main()
