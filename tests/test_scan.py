import unittest

from src.scan import best_condition, leaf_categories


class LeafCategoriesTest(unittest.TestCase):
    def test_collects_only_leaves_with_full_path(self):
        tree = [
            {
                "id": "root",
                "label": "家電",
                "children": [
                    {
                        "id": "game",
                        "label": "ゲーム",
                        "children": [
                            {"id": "ps", "label": "PlayStation"},
                            {"id": "sw", "label": "Switch"},
                        ],
                    },
                    {"id": "deck", "label": "Steam Deck"},
                ],
            }
        ]
        self.assertEqual(
            leaf_categories(tree),
            [
                ("ps", "/家電/ゲーム/PlayStation"),
                ("sw", "/家電/ゲーム/Switch"),
                ("deck", "/家電/Steam Deck"),
            ],
        )

    def test_handles_empty_tree(self):
        self.assertEqual(leaf_categories(None), [])
        self.assertEqual(leaf_categories([]), [])


class BestConditionTest(unittest.TestCase):
    def test_picks_highest_paying_condition(self):
        item = {
            "goodsKbDetails": [
                {"kbDetailName": "新品未使用", "kbDetailPrice": 176500},
                {"kbDetailName": "来店", "kbDetailPrice": 177200},
            ]
        }
        self.assertEqual(best_condition(item), ("来店", 177200))

    def test_ignores_rows_without_price(self):
        item = {
            "goodsKbDetails": [
                {"kbDetailName": "要相談", "kbDetailPrice": None},
                {"kbDetailName": "新品未使用", "kbDetailPrice": 81600},
            ]
        }
        self.assertEqual(best_condition(item), ("新品未使用", 81600))

    def test_returns_none_without_any_price(self):
        self.assertIsNone(best_condition({"goodsKbDetails": []}))
        self.assertIsNone(best_condition({}))


if __name__ == "__main__":
    unittest.main()
