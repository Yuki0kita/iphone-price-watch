"""買取1丁目の商品データから「未開封」買取価格を取得する。

商品ページ (https://www.1-chome.com/productDetail/<itemId>/<kbId>) はVite製のSPAで、
サーバーが返すHTMLには価格も商品名も含まれない。そのため画面が使っているJSON APIを
同じ形で1回だけ呼ぶ。依存を増やさないため標準ライブラリのみを使う。
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

API_ENDPOINT = "https://www.1-chome.com/api/keitai/getKeitaiItem"
PRODUCT_URL_RE = re.compile(r"/productDetail/(\d+)/(\d+)")

# 監視対象の商品状態。買取1丁目は同じ商品に「未開封」「開封済未使用品」などを持つ。
UNOPENED_CONDITION = "未開封"

# 取得値の妥当性チェック。桁落ち・単位違い・APIの仕様変更を検知するための範囲。
MIN_PLAUSIBLE_PRICE = 10_000
MAX_PLAUSIBLE_PRICE = 1_000_000

API_SUCCESS_CODE = 200
USER_AGENT = "iphone-price-watch/1.0 (personal price monitor)"


@dataclass
class Product:
    name: str
    unopened_price: int
    url: str


def product_ids_from_url(url: str) -> tuple[int, int]:
    """商品ページURLから keitaiItemId と keitaiItemKbId を取り出す。"""
    match = PRODUCT_URL_RE.search(url)
    if not match:
        raise ValueError(
            f"商品ページURLの形式が想定と違います（/productDetail/<itemId>/<kbId> が必要）: {url}"
        )
    return int(match.group(1)), int(match.group(2))


def api_url(item_id: int, kb_id: int) -> str:
    return f"{API_ENDPOINT}?keitaiItemId={item_id}&keitaiItemKbId={kb_id}"


def parse_payload(payload: Any, url: str = "") -> Product:
    """APIレスポンス(JSON)から商品名と未開封価格を取り出す。"""
    if not isinstance(payload, dict):
        raise ValueError("APIレスポンスがJSONオブジェクトではありません。")

    code = payload.get("code")
    if code != API_SUCCESS_CODE:
        raise ValueError(f"APIがエラーを返しました: code={code} msg={payload.get('msg')}")

    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("APIレスポンスに data がありません。仕様が変わった可能性があります。")

    item = data.get("keitaiItem") or {}
    name = str(item.get("title") or "").strip()

    details = data.get("keitaiKbDetails")
    if not isinstance(details, list) or not details:
        raise ValueError("商品状態の一覧(keitaiKbDetails)が取得できませんでした。")

    for detail in details:
        if not isinstance(detail, dict):
            continue
        if str(detail.get("kbDetailName") or "").strip() != UNOPENED_CONDITION:
            continue

        raw_price = detail.get("kbDetailPrice")
        if not isinstance(raw_price, (int, float)) or isinstance(raw_price, bool):
            raise ValueError(f"未開封価格が数値ではありません: {raw_price!r}")

        price = int(raw_price)
        if not MIN_PLAUSIBLE_PRICE <= price <= MAX_PLAUSIBLE_PRICE:
            raise ValueError(
                f"未開封価格が想定範囲外です: {price}"
                f"（{MIN_PLAUSIBLE_PRICE:,}〜{MAX_PLAUSIBLE_PRICE:,}円を想定）"
            )
        return Product(name=name or "iPhone", unopened_price=price, url=url)

    available = [str(d.get("kbDetailName")) for d in details if isinstance(d, dict)]
    raise ValueError(
        f"「{UNOPENED_CONDITION}」の価格が見つかりませんでした。取得できた状態: {available}"
    )


def fetch_product(url: str, timeout: int = 20) -> Product:
    item_id, kb_id = product_ids_from_url(url)
    request = urllib.request.Request(
        api_url(item_id, kb_id),
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Accept-Language": "ja,en;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raise ValueError(f"APIへのリクエストが失敗しました: HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise ValueError(f"APIに接続できませんでした: {e.reason}") from e

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as e:
        raise ValueError(f"APIレスポンスをJSONとして解釈できませんでした: {e}") from e

    return parse_payload(payload, url=url)
