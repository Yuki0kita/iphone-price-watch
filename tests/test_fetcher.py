import json
import unittest
from pathlib import Path

from src.fetcher import (
    MAX_PLAUSIBLE_PRICE,
    MIN_PLAUSIBLE_PRICE,
    api_url,
    fetch_product,
    goods_list_url,
    parse_goods_list,
    parse_payload,
    product_ids_from_url,
)

FIXTURE = Path(__file__).parent / "fixtures" / "product_detail.json"


def payload_with_price(price):
    """未開封価格だけ差し替えたレスポンスを作る。"""
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data["data"]["keitaiKbDetails"][0]["kbDetailPrice"] = price
    return data


class ProductUrlTest(unittest.TestCase):
    def test_extracts_ids(self):
        self.assertEqual(
            product_ids_from_url("https://www.1-chome.com/productDetail/1371/1927"),
            (1371, 1927),
        )

    def test_rejects_other_urls(self):
        for url in ["https://www.1-chome.com/mobile", "https://www.1-chome.com/productDetail/1371", ""]:
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    product_ids_from_url(url)

    def test_api_url(self):
        self.assertEqual(
            api_url(1371, 1927),
            "https://www.1-chome.com/api/keitai/getKeitaiItem?keitaiItemId=1371&keitaiItemKbId=1927",
        )


class ParsePayloadTest(unittest.TestCase):
    def test_reads_unopened_price(self):
        """実レスポンスから未開封価格を取り、開封済の価格を拾わない。"""
        product = parse_payload(json.loads(FIXTURE.read_text(encoding="utf-8")), "https://example.com")
        self.assertEqual(product.name, "iPhone 17 Pro Max 512GB")
        self.assertEqual(product.unopened_price, 227000)
        self.assertEqual(product.url, "https://example.com")

    def test_rejects_error_code(self):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        data["code"] = 500
        with self.assertRaises(ValueError):
            parse_payload(data)

    def test_rejects_missing_unopened_condition(self):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        data["data"]["keitaiKbDetails"] = [d for d in data["data"]["keitaiKbDetails"] if d["kbDetailName"] != "未開封"]
        with self.assertRaisesRegex(ValueError, "未開封"):
            parse_payload(data)

    def test_rejects_empty_details(self):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        data["data"]["keitaiKbDetails"] = []
        with self.assertRaises(ValueError):
            parse_payload(data)

    def test_rejects_non_dict_payload(self):
        for payload in [None, [], "ok"]:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    parse_payload(payload)

    def test_price_boundaries(self):
        """妥当な範囲の境界値は通し、その外側は例外にする。"""
        for price in [MIN_PLAUSIBLE_PRICE, MAX_PLAUSIBLE_PRICE]:
            with self.subTest(price=price):
                self.assertEqual(parse_payload(payload_with_price(price)).unopened_price, price)
        for price in [MIN_PLAUSIBLE_PRICE - 1, MAX_PLAUSIBLE_PRICE + 1, 0]:
            with self.subTest(price=price):
                with self.assertRaises(ValueError):
                    parse_payload(payload_with_price(price))

    def test_rejects_non_numeric_price(self):
        for price in ["227,000", None, True]:
            with self.subTest(price=price):
                with self.assertRaises(ValueError):
                    parse_payload(payload_with_price(price))


def goods_payload(items):
    return {"code": 200, "data": {"content": items, "totalElements": len(items)}}


GOODS_ITEM = {
    "goodsId": 7050,
    "jan": "4902370552683",
    "title": "Nintendo Switch 2 多言語版",
    "goodsKbDetails": [
        {"kbDetailName": "新品未使用", "kbDetailPrice": 81600},
        {"kbDetailName": "来店", "kbDetailPrice": 81800},
    ],
}


class GoodsListTest(unittest.TestCase):
    def test_reads_requested_condition(self):
        """家電の状態名は「未開封」ではないため、指定した状態を読む。"""
        product = parse_goods_list(goods_payload([GOODS_ITEM]), "4902370552683", "新品未使用")
        self.assertEqual(product.name, "Nintendo Switch 2 多言語版")
        self.assertEqual(product.unopened_price, 81600)

    def test_picks_visit_store_condition_when_asked(self):
        product = parse_goods_list(goods_payload([GOODS_ITEM]), "4902370552683", "来店")
        self.assertEqual(product.unopened_price, 81800)

    def test_matches_jan_ignoring_invisible_characters(self):
        """商品によってJANに不可視文字が混ざるため、数字だけで突き合わせる。"""
        item = {**GOODS_ITEM, "jan": "‎4902370552683 "}
        product = parse_goods_list(goods_payload([item]), "4902370552683", "新品未使用")
        self.assertEqual(product.unopened_price, 81600)

    def test_picks_the_matching_jan_among_similar_items(self):
        other = {**GOODS_ITEM, "jan": "4902370552684", "title": "別モデル"}
        product = parse_goods_list(goods_payload([other, GOODS_ITEM]), "4902370552683", "新品未使用")
        self.assertEqual(product.name, "Nintendo Switch 2 多言語版")

    def test_rejects_missing_jan(self):
        with self.assertRaisesRegex(ValueError, "4902370559999"):
            parse_goods_list(goods_payload([GOODS_ITEM]), "4902370559999", "新品未使用")

    def test_rejects_unknown_condition(self):
        with self.assertRaisesRegex(ValueError, "未開封"):
            parse_goods_list(goods_payload([GOODS_ITEM]), "4902370552683", "未開封")

    def test_rejects_broken_payload(self):
        for payload in [None, {}, {"data": {}}, {"data": {"content": "x"}}]:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    parse_goods_list(payload, "4902370552683", "新品未使用")

    def test_list_url_narrows_by_keyword(self):
        url = goods_list_url("20480828", "CFI-7000B01")
        self.assertIn("cateCode=20480828", url)
        self.assertIn("keyword=CFI-7000B01", url)


class FetchProductDispatchTest(unittest.TestCase):
    def test_rejects_market_only_source(self):
        """買取X経由でしか取れない商品は、買取1丁目のfetcherを通さない。"""
        with self.assertRaisesRegex(ValueError, "market"):
            fetch_product({"id": "x", "source": "market", "jan": "4549576249797"})

    def test_rejects_unknown_source(self):
        with self.assertRaises(ValueError):
            fetch_product({"id": "x", "source": "amazon"})


if __name__ == "__main__":
    unittest.main()
