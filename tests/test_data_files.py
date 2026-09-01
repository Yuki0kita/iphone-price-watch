"""データファイルが壊れていないことを確認する。

collector は起動時にこれらを読むため、壊れていると実行そのものが失敗する。
実際にコンフリクトマーカーが残ったJSONをpushしてワークフローが落ちたことがある。
テストで先に落とせば、pushする前に気づける。
"""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = {
    "config/products.json": dict,
    "docs/data/history.json": list,
    "docs/data/alert_state.json": dict,
    "docs/data/status.json": dict,
}
OPTIONAL_FILES = {
    "docs/data/market.json": dict,
    "docs/data/retail.json": dict,
}
CONFLICT_MARKERS = ("<<<<<<<", "=======", ">>>>>>>")


class DataFilesTest(unittest.TestCase):
    def test_every_data_file_parses(self):
        for name, expected in {**REQUIRED_FILES, **OPTIONAL_FILES}.items():
            path = ROOT / name
            if name in OPTIONAL_FILES and not path.exists():
                continue
            with self.subTest(file=name):
                text = path.read_text(encoding="utf-8")
                for marker in CONFLICT_MARKERS:
                    self.assertNotIn(marker, text, f"{name} にコンフリクトマーカーが残っています")
                self.assertIsInstance(json.loads(text), expected)

    def test_required_files_exist(self):
        for name in REQUIRED_FILES:
            with self.subTest(file=name):
                self.assertTrue((ROOT / name).exists(), f"{name} がありません")

    def test_config_has_the_settings_the_collector_reads(self):
        config = json.loads((ROOT / "config/products.json").read_text(encoding="utf-8"))
        settings = config["settings"]
        for key in ("request_interval_seconds", "timeout_seconds", "history_days"):
            self.assertIn(key, settings)
        for key in ("rise_yen", "new_high_window_days", "min_history_points_for_high",
                    "cooldown_hours", "market_gap_yen", "min_safe_profit_yen"):
            self.assertIn(key, settings["alert"])
        for key in ("risk_buffer_yen", "extra_cost_yen", "minimum_profit_yen", "min_retail_ratio"):
            self.assertIn(key, settings["profit"])

    def test_product_ids_are_unique(self):
        config = json.loads((ROOT / "config/products.json").read_text(encoding="utf-8"))
        ids = [p["id"] for p in config["products"]]
        self.assertEqual(len(ids), len(set(ids)), "商品IDが重複しています")

    def test_every_product_has_a_usable_source(self):
        config = json.loads((ROOT / "config/products.json").read_text(encoding="utf-8"))
        for product in config["products"]:
            with self.subTest(product=product["id"]):
                source = product.get("source", "keitai")
                if source == "keitai":
                    self.assertIn("/productDetail/", product["url"])
                elif source == "goods":
                    self.assertTrue(product.get("cate_code") and product.get("jan"))
                    self.assertTrue(product.get("condition"))
                else:
                    self.assertEqual(source, "market")
                    self.assertTrue(product.get("jan"))


if __name__ == "__main__":
    unittest.main()
