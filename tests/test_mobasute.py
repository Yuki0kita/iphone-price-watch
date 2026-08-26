import unittest
from pathlib import Path

from src.mobasute import MobasuteError, normalize_model, parse_price_table, price_for

FIXTURE = Path(__file__).parent / "fixtures" / "mobasute_price_table.html"


class NormalizeModelTest(unittest.TestCase):
    def test_absorbs_spacing_and_case(self):
        """設定側の「iPhone 17 256GB」とモバステ側の「iPhone17 256GB」を同じ扱いにする。"""
        for name in ["iPhone 17 256GB", "iPhone17 256GB", "iphone17　256gb", " iPhone 17  256 GB "]:
            with self.subTest(name=name):
                self.assertEqual(normalize_model(name), "iphone17256gb")

    def test_keeps_different_models_distinct(self):
        self.assertNotEqual(normalize_model("iPhone 17 256GB"), normalize_model("iPhone 17 512GB"))
        self.assertNotEqual(normalize_model("iPhone 17 256GB"), normalize_model("iPhone 17 Pro 256GB"))


class ParsePriceTableTest(unittest.TestCase):
    def setUp(self):
        self.table = parse_price_table(FIXTURE.read_text(encoding="utf-8"))

    def test_reads_unopened_price(self):
        """実際の価格表HTMLから未開封価格を取り出す。中古価格は拾わない。"""
        self.assertEqual(price_for(self.table, "iPhone 17 256GB"), 139000)
        self.assertEqual(price_for(self.table, "iPhone 17 Pro Max 512GB"), 227500)

    def test_unknown_model_returns_none(self):
        self.assertIsNone(price_for(self.table, "PlayStation 5 Pro"))

    def test_rejects_page_without_price_table(self):
        for html in ["", "<html><body>メンテナンス中</body></html>"]:
            with self.subTest(html=html):
                with self.assertRaises(MobasuteError):
                    parse_price_table(html)

    def test_skips_rows_without_unopened_price(self):
        """中古のみの行は無視する（未開封価格が無い機種がある）。"""
        html = (
            '<div class="p-priceTable__inner"><div class="p-priceTable__name"><span>iPhone 8</span></div>'
            '<span class="price price--used">10,000円</span></div>'
            '<div class="p-priceTable__inner"><div class="p-priceTable__name"><span>iPhone 17 256GB</span></div>'
            '<span class="price price--unopened">139,000円</span></div>'
        )
        table = parse_price_table(html)
        self.assertEqual(list(table), ["iphone17256gb"])

    def test_skips_implausible_price(self):
        html = (
            '<div class="p-priceTable__inner"><div class="p-priceTable__name"><span>iPhone X</span></div>'
            '<span class="price price--unopened">1円</span></div>'
            '<div class="p-priceTable__inner"><div class="p-priceTable__name"><span>iPhone 17 256GB</span></div>'
            '<span class="price price--unopened">139,000円</span></div>'
        )
        self.assertEqual(list(parse_price_table(html)), ["iphone17256gb"])


if __name__ == "__main__":
    unittest.main()
