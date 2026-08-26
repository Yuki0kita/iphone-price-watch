import unittest

from src.retail import RetailError, parse_offers, pick_offer

JAN = "4521329431529"


def payload(*hits):
    return {"hits": list(hits)}


def hit(price, name="商品", store="店", in_stock=True):
    return {"price": price, "name": name, "url": "https://example.com", "inStock": in_stock,
            "seller": {"name": store}}


class ParseOffersTest(unittest.TestCase):
    def test_sorts_by_price(self):
        offers = parse_offers(payload(hit(21000), hit(1180), hit(15000)), JAN)
        self.assertEqual([o.price for o in offers], [1180, 15000, 21000])

    def test_skips_unusable_prices(self):
        offers = parse_offers(payload(hit(None), hit("x"), hit(True), hit(21000)), JAN)
        self.assertEqual([o.price for o in offers], [21000])

    def test_rejects_empty_results(self):
        with self.assertRaises(RetailError):
            parse_offers(payload(), JAN)

    def test_rejects_broken_payload(self):
        for value in [None, [], {"foo": 1}]:
            with self.subTest(value=value):
                with self.assertRaises(RetailError):
                    parse_offers(value, JAN)


class PickOfferTest(unittest.TestCase):
    def test_skips_listings_below_minimum(self):
        """同じJANのバラ売りを掴まないようにする。

        実例: 買取21,000円のポケモンカードBOXのJANで「1パック 1,180円」が最安に出た。
        """
        offers = parse_offers(payload(hit(1180, "1パック ..."), hit(15800, "BOX")), JAN)
        picked = pick_offer(offers, min_price=10500)
        self.assertEqual(picked.price, 15800)

    def test_returns_none_when_everything_is_too_cheap(self):
        offers = parse_offers(payload(hit(1180), hit(3500)), JAN)
        self.assertIsNone(pick_offer(offers, min_price=10500))

    def test_prefers_in_stock_over_cheaper_sold_out(self):
        offers = parse_offers(payload(hit(15000, in_stock=False), hit(16000, in_stock=True)), JAN)
        self.assertEqual(pick_offer(offers, min_price=10000).price, 16000)

    def test_falls_back_to_sold_out_when_nothing_in_stock(self):
        """在庫が無くても相場の目安にはなるので、価格自体は返す。"""
        offers = parse_offers(payload(hit(15000, in_stock=False)), JAN)
        self.assertEqual(pick_offer(offers, min_price=10000).price, 15000)

    def test_accepts_price_exactly_at_minimum(self):
        offers = parse_offers(payload(hit(10500)), JAN)
        self.assertEqual(pick_offer(offers, min_price=10500).price, 10500)

    def test_keeps_normal_arbitrage_margins(self):
        """まっとうな価格差は除外しない（買取177,200に対し定価137,980は78%）。"""
        offers = parse_offers(payload(hit(137980)), JAN)
        self.assertIsNotNone(pick_offer(offers, min_price=int(177200 * 0.5)))


if __name__ == "__main__":
    unittest.main()
