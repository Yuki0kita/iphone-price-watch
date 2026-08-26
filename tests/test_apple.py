import unittest

from src.apple import AppleError, config_key, parse_prices, price_for


def product(part, family, capacity, price):
    """購入ページの商品オブジェクトと価格ブロックを、実物と同じ並びで組み立てる。"""
    return (
        f'"partNumber":"{part}","productLocatorFamily":"{family}",'
        f'"dimensionCapacity":"{capacity}",'
        f'"currentPrice":{{"amount":"<span>{price:,}円</span>","raw_amount":"{price}.00"}},'
    )


PAGE = (
    "{"
    + product("MG6A4J/A", "iphone17pro", "256gb", 194800)
    + product("MG6D4J/A", "iphone17promax", "256gb", 214800)
    + product("MG6E4J/A", "iphone17promax", "512gb", 249800)
    + "}"
)


class ConfigKeyTest(unittest.TestCase):
    def test_builds_model_and_capacity_key(self):
        cases = [
            ("iPhone 17 256GB", "iphone17:256gb"),
            ("iPhone 17 Pro 256GB", "iphone17pro:256gb"),
            ("iPhone 17 Pro Max 512GB", "iphone17promax:512gb"),
            ("iPhone 17 Pro Max 1TB", "iphone17promax:1tb"),
        ]
        for name, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(config_key(name), expected)

    def test_distinguishes_pro_from_pro_max(self):
        """1ページにProとPro Maxが同居するため、容量だけでは特定できない。"""
        self.assertNotEqual(config_key("iPhone 17 Pro 256GB"), config_key("iPhone 17 Pro Max 256GB"))

    def test_returns_none_without_capacity(self):
        self.assertIsNone(config_key("PlayStation 5 Pro"))


class ParsePricesTest(unittest.TestCase):
    def test_reads_each_configuration(self):
        prices = parse_prices(PAGE)
        self.assertEqual(prices["iphone17pro:256gb"], 194800)
        self.assertEqual(prices["iphone17promax:256gb"], 214800)
        self.assertEqual(prices["iphone17promax:512gb"], 249800)

    def test_price_for_matches_by_product_name(self):
        prices = parse_prices(PAGE)
        self.assertEqual(price_for(prices, "iPhone 17 Pro Max 512GB"), 249800)
        self.assertIsNone(price_for(prices, "iPhone 17 Pro 512GB"))

    def test_explicit_key_overrides_the_name(self):
        prices = parse_prices(PAGE)
        self.assertEqual(price_for(prices, "何か別の名前", "iphone17pro:256gb"), 194800)

    def test_keeps_the_lowest_when_carriers_repeat(self):
        """同じ構成がキャリア違いで並ぶ。定価は同一のはずだが安いほうを採る。"""
        page = PAGE + product("MG6A4J/A", "iphone17pro", "256gb", 199800)
        self.assertEqual(parse_prices(page)["iphone17pro:256gb"], 194800)

    def test_skips_implausible_price(self):
        page = "{" + product("MG000J/A", "iphone17", "256gb", 1) + product("MG6A4J/A", "iphone17pro", "256gb", 194800) + "}"
        self.assertEqual(list(parse_prices(page)), ["iphone17pro:256gb"])

    def test_rejects_page_without_prices(self):
        for html in ["", "<html>メンテナンス中</html>"]:
            with self.subTest(html=html):
                with self.assertRaises(AppleError):
                    parse_prices(html)


if __name__ == "__main__":
    unittest.main()
