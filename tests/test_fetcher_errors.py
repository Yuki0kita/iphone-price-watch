"""取得元APIの通信失敗・仕様変更・欠損の異常系。"""
import io
import json
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from src.fetcher import _get_json, parse_goods_list, parse_payload

FIXTURE = Path(__file__).parent / "fixtures" / "product_detail.json"


def fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class GetJsonTest(unittest.TestCase):
    def _call(self, side_effect=None, body=b""):
        with mock.patch("urllib.request.urlopen", side_effect=side_effect, return_value=_Response(body)):
            return _get_json("https://example.com/api", timeout=1)

    def test_http_error(self):
        err = urllib.error.HTTPError("u", 503, "x", {}, None)
        with self.assertRaisesRegex(ValueError, "HTTP 503"):
            self._call(side_effect=err)

    def test_connection_error(self):
        with self.assertRaisesRegex(ValueError, "接続できません"):
            self._call(side_effect=urllib.error.URLError("timeout"))

    def test_html_instead_of_json(self):
        """メンテナンス画面などHTMLが返った場合。"""
        with self.assertRaisesRegex(ValueError, "JSON"):
            self._call(body=b"<html>maintenance</html>")

    def test_valid_json(self):
        self.assertEqual(self._call(body=b'{"code": 200}'), {"code": 200})


class PayloadShapeTest(unittest.TestCase):
    def test_missing_data(self):
        payload = fixture()
        payload["data"] = None
        with self.assertRaisesRegex(ValueError, "data"):
            parse_payload(payload)

    def test_missing_code(self):
        payload = fixture()
        del payload["code"]
        with self.assertRaises(ValueError):
            parse_payload(payload)

    def test_code_as_string_is_rejected(self):
        """"200" を成功扱いしない（型の仕様変更を見逃さない）。"""
        payload = fixture()
        payload["code"] = "200"
        with self.assertRaises(ValueError):
            parse_payload(payload)

    def test_details_not_list(self):
        payload = fixture()
        payload["data"]["keitaiKbDetails"] = {"kbDetailName": "未開封"}
        with self.assertRaises(ValueError):
            parse_payload(payload)

    def test_non_dict_detail_rows_are_skipped(self):
        payload = fixture()
        payload["data"]["keitaiKbDetails"].insert(0, "broken")
        self.assertEqual(parse_payload(payload).unopened_price, 227000)

    def test_missing_title_falls_back(self):
        payload = fixture()
        payload["data"]["keitaiItem"] = None
        self.assertEqual(parse_payload(payload).name, "商品")


class PriceValueTest(unittest.TestCase):
    def _with(self, price):
        payload = fixture()
        payload["data"]["keitaiKbDetails"][0]["kbDetailPrice"] = price
        return payload

    def test_rejects_zero_negative_and_null(self):
        for price in [0, -227000, None]:
            with self.subTest(price=price):
                with self.assertRaises(ValueError):
                    parse_payload(self._with(price))

    def test_rejects_string_and_bool(self):
        for price in ["227000", "227,000", True]:
            with self.subTest(price=price):
                with self.assertRaises(ValueError):
                    parse_payload(self._with(price))

    def test_rejects_extra_digit(self):
        """桁増え（227,000→2,270,000）は上限で弾く。"""
        with self.assertRaises(ValueError):
            parse_payload(self._with(2_270_000))

    def test_float_is_truncated_to_yen(self):
        self.assertEqual(parse_payload(self._with(227000.0)).unopened_price, 227000)


class GoodsListShapeTest(unittest.TestCase):
    def test_content_not_list(self):
        with self.assertRaises(ValueError):
            parse_goods_list({"data": {"content": None}}, jan="1", condition="新品未使用")

    def test_out_of_range_price(self):
        payload = {
            "data": {
                "content": [
                    {"jan": "4549995649154", "goodsKbDetails": [{"kbDetailName": "新品未使用", "kbDetailPrice": 0}]}
                ]
            }
        }
        with self.assertRaises(ValueError):
            parse_goods_list(payload, jan="4549995649154", condition="新品未使用")


if __name__ == "__main__":
    unittest.main()
