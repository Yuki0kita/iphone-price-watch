"""買取1丁目から、指定した商品状態の買取価格を取得する。

買取1丁目のページはVite製のSPAで、サーバーが返すHTMLには価格も商品名も含まれない。
そのため画面が使っているJSON APIを同じ形で呼ぶ。依存を増やさないため標準ライブラリのみを使う。

商品の種類によってAPIも状態名も違う。

    携帯（iPhone等）  /api/keitai/getKeitaiItem   状態名「未開封」
    家電・トレカ       /api/goods/listPage         状態名「新品未使用」「シュリンク有」など

どちらを使うかは設定の source で決める。状態名は商品ごとに condition で指定する。
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

KEITAI_ENDPOINT = "https://www.1-chome.com/api/keitai/getKeitaiItem"
GOODS_ENDPOINT = "https://www.1-chome.com/api/goods/listPage"
PRODUCT_URL_RE = re.compile(r"/productDetail/(\d+)/(\d+)")

SOURCE_KEITAI = "keitai"
SOURCE_GOODS = "goods"
SOURCE_MARKET = "market"

# 携帯カテゴリの既定の状態名。家電・トレカは商品ごとに指定する。
DEFAULT_CONDITION = "未開封"

# 一覧APIは全件返すと重いため、keywordで絞ったうえでこの件数だけ見る。
GOODS_PAGE_SIZE = 50

# 取得値の妥当性チェック。桁落ち・単位違い・APIの仕様変更を検知するための範囲。
MIN_PLAUSIBLE_PRICE = 1_000
MAX_PLAUSIBLE_PRICE = 1_000_000

API_SUCCESS_CODE = 200
USER_AGENT = "iphone-price-watch/1.0 (personal price monitor)"


@dataclass
class Product:
    name: str
    unopened_price: int
    url: str


def _get_json(url: str, timeout: int) -> Any:
    request = urllib.request.Request(
        url,
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
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise ValueError(f"APIレスポンスをJSONとして解釈できませんでした: {e}") from e


def price_for_condition(details: Any, condition: str) -> int:
    """商品状態の一覧から、指定した状態の買取価格を取り出す。"""
    if not isinstance(details, list) or not details:
        raise ValueError("商品状態の一覧が取得できませんでした。")

    for detail in details:
        if not isinstance(detail, dict):
            continue
        if str(detail.get("kbDetailName") or "").strip() != condition:
            continue

        raw_price = detail.get("kbDetailPrice")
        if isinstance(raw_price, bool) or not isinstance(raw_price, (int, float)):
            raise ValueError(f"「{condition}」の価格が数値ではありません: {raw_price!r}")

        price = int(raw_price)
        if not MIN_PLAUSIBLE_PRICE <= price <= MAX_PLAUSIBLE_PRICE:
            raise ValueError(
                f"「{condition}」の価格が想定範囲外です: {price}"
                f"（{MIN_PLAUSIBLE_PRICE:,}〜{MAX_PLAUSIBLE_PRICE:,}円を想定）"
            )
        return price

    available = [str(d.get("kbDetailName")) for d in details if isinstance(d, dict)]
    raise ValueError(f"「{condition}」の価格が見つかりませんでした。取得できた状態: {available}")


# --- 携帯カテゴリ（iPhone等） ---------------------------------------------


def product_ids_from_url(url: str) -> tuple[int, int]:
    """商品ページURLから keitaiItemId と keitaiItemKbId を取り出す。"""
    match = PRODUCT_URL_RE.search(url)
    if not match:
        raise ValueError(
            f"商品ページURLの形式が想定と違います（/productDetail/<itemId>/<kbId> が必要）: {url}"
        )
    return int(match.group(1)), int(match.group(2))


def api_url(item_id: int, kb_id: int) -> str:
    return f"{KEITAI_ENDPOINT}?keitaiItemId={item_id}&keitaiItemKbId={kb_id}"


def parse_payload(payload: Any, url: str = "", condition: str = DEFAULT_CONDITION) -> Product:
    """携帯カテゴリのレスポンスから商品名と買取価格を取り出す。"""
    if not isinstance(payload, dict):
        raise ValueError("APIレスポンスがJSONオブジェクトではありません。")

    code = payload.get("code")
    if code != API_SUCCESS_CODE:
        raise ValueError(f"APIがエラーを返しました: code={code} msg={payload.get('msg')}")

    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("APIレスポンスに data がありません。仕様が変わった可能性があります。")

    name = str((data.get("keitaiItem") or {}).get("title") or "").strip()
    price = price_for_condition(data.get("keitaiKbDetails"), condition)
    return Product(name=name or "商品", unopened_price=price, url=url)


def fetch_keitai(url: str, condition: str, timeout: int) -> Product:
    item_id, kb_id = product_ids_from_url(url)
    return parse_payload(_get_json(api_url(item_id, kb_id), timeout), url=url, condition=condition)


# --- 家電・トレカカテゴリ ---------------------------------------------------


def goods_list_url(cate_code: str, keyword: str) -> str:
    query = urllib.parse.urlencode(
        {
            "accCode": "",
            "page": 1,
            "size": GOODS_PAGE_SIZE,
            "keyword": keyword,
            "isImpo": "false",
            "isCampaign": "false",
            "cateCode": cate_code,
            "kbNames": "",
            "cateName": "",
        }
    )
    return f"{GOODS_ENDPOINT}?{query}"


def parse_goods_list(payload: Any, jan: str, condition: str, url: str = "") -> Product:
    """家電・トレカの一覧レスポンスから、JANが一致する商品の買取価格を取り出す。"""
    if not isinstance(payload, dict):
        raise ValueError("APIレスポンスがJSONオブジェクトではありません。")

    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("content"), list):
        raise ValueError("一覧APIの content が取得できませんでした。仕様が変わった可能性があります。")

    wanted = jan.strip()
    for item in data["content"]:
        if not isinstance(item, dict):
            continue
        # JANの前後に不可視文字が混ざっている商品があるため、数字だけで比較する。
        if re.sub(r"\D", "", str(item.get("jan") or "")) != re.sub(r"\D", "", wanted):
            continue
        name = str(item.get("title") or "").strip()
        price = price_for_condition(item.get("goodsKbDetails"), condition)
        return Product(name=name or "商品", unopened_price=price, url=url)

    found = [str(i.get("jan")) for i in data["content"] if isinstance(i, dict)][:10]
    raise ValueError(f"JAN {jan} の商品が一覧に見つかりませんでした。取得できたJAN: {found}")


def fetch_goods(cate_code: str, keyword: str, jan: str, condition: str, url: str, timeout: int) -> Product:
    payload = _get_json(goods_list_url(cate_code, keyword), timeout)
    return parse_goods_list(payload, jan=jan, condition=condition, url=url)


# --- 入口 -----------------------------------------------------------------


def fetch_product(product: dict, timeout: int = 20) -> Product:
    """設定の source に応じて、買取1丁目から買取価格を取得する。"""
    source = str(product.get("source") or SOURCE_KEITAI)
    condition = str(product.get("condition") or DEFAULT_CONDITION)

    if source == SOURCE_KEITAI:
        return fetch_keitai(product["url"], condition, timeout)
    if source == SOURCE_GOODS:
        return fetch_goods(
            cate_code=product["cate_code"],
            keyword=product.get("keyword", ""),
            jan=product["jan"],
            condition=condition,
            url=product.get("url", ""),
            timeout=timeout,
        )
    raise ValueError(f"買取1丁目から取得できない source です: {source}")
