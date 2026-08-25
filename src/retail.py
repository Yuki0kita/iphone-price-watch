"""Yahoo!ショッピングの商品検索APIから、仕入れ側の最安値を取得する。

買取価格だけ見ても「いくらで買えるか」が分からないため、利益は計算できない。
JANコードで完全一致検索し、在庫がある新品のうち最安の1件を仕入れ候補とする。

    python3 -m src.retail 4948872416320   # 疎通確認

制限は1クエリ/秒。Client IDは https://e.developer.yahoo.co.jp/dashboard/ で取得する。
環境変数 YAHOO_APP_ID が無い場合は何もしない。

注意: 取得できるのはYahoo!ショッピング内の最安値であり、Amazon・楽天・メーカー公式は含まない。
希少品では出品者価格が定価を大きく上回るため、利益が出ないことのほうが多い。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

API_ENDPOINT = "https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch"
APP_ID_ENV = "YAHOO_APP_ID"

# 1クエリ/秒の制限があるため、少し余裕を持たせる。
MIN_REQUEST_INTERVAL_SECONDS = 1.1

# 1JANあたり何件まで見るか。安い順に返るため、先頭から在庫ありを探すだけでよい。
SEARCH_RESULTS = 20

MIN_PLAUSIBLE_PRICE = 500
MAX_PLAUSIBLE_PRICE = 2_000_000

_last_request_at = 0.0


class RetailError(RuntimeError):
    pass


@dataclass
class RetailOffer:
    name: str
    price: int
    store: str
    url: str
    in_stock: bool


def _throttle() -> None:
    global _last_request_at
    elapsed = time.monotonic() - _last_request_at
    if 0 < elapsed < MIN_REQUEST_INTERVAL_SECONDS:
        time.sleep(MIN_REQUEST_INTERVAL_SECONDS - elapsed)
    _last_request_at = time.monotonic()


def search_url(jan: str, app_id: str) -> str:
    query = urllib.parse.urlencode(
        {
            "appid": app_id,
            "jan_code": jan,
            "results": SEARCH_RESULTS,
            "sort": "+price",
            "condition": "new",
        }
    )
    return f"{API_ENDPOINT}?{query}"


def parse_offers(payload: object, jan: str) -> RetailOffer:
    """検索結果から、在庫があるもののうち最安の1件を返す。"""
    if not isinstance(payload, dict):
        raise RetailError("APIレスポンスがJSONオブジェクトではありません。")

    hits = payload.get("hits")
    if not isinstance(hits, list):
        raise RetailError(
            f"検索結果(hits)が見つかりません。仕様が変わった可能性があります: keys={sorted(payload)}"
        )
    if not hits:
        raise RetailError(f"JAN {jan} の出品が見つかりませんでした。")

    offers: list[RetailOffer] = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        raw_price = hit.get("price")
        if isinstance(raw_price, bool) or not isinstance(raw_price, (int, float)):
            continue
        price = int(raw_price)
        if not MIN_PLAUSIBLE_PRICE <= price <= MAX_PLAUSIBLE_PRICE:
            continue
        seller = hit.get("seller")
        offers.append(
            RetailOffer(
                name=str(hit.get("name") or "").strip(),
                price=price,
                store=str((seller or {}).get("name") or "").strip() or "不明",
                url=str(hit.get("url") or "").strip(),
                in_stock=bool(hit.get("inStock", True)),
            )
        )

    if not offers:
        raise RetailError(f"JAN {jan} の有効な価格が1件もありませんでした。")

    offers.sort(key=lambda o: o.price)
    # 在庫があるものを優先し、無ければ最安を返す（在庫なしでも相場の目安にはなる）。
    for offer in offers:
        if offer.in_stock:
            return offer
    return offers[0]


def fetch_cheapest(jan: str, app_id: str, timeout: int = 20) -> RetailOffer:
    if not app_id:
        raise RetailError("Client IDが設定されていません。")

    _throttle()
    request = urllib.request.Request(
        search_url(jan, app_id),
        headers={"User-Agent": "iphone-price-watch/1.0 (personal price monitor)", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code == 401 or e.code == 403:
            raise RetailError(f"Client IDが無効です（HTTP {e.code}）。") from e
        if e.code == 429:
            raise RetailError("リクエスト制限に達しました（HTTP 429）。") from e
        raise RetailError(f"リクエストが失敗しました: HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise RetailError(f"APIに接続できませんでした: {e.reason}") from e

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as e:
        raise RetailError(f"レスポンスをJSONとして解釈できませんでした: {e}") from e

    return parse_offers(payload, jan)


def main() -> int:
    if len(sys.argv) != 2:
        print("使い方: python3 -m src.retail <JANコード>", file=sys.stderr)
        return 2

    app_id = os.getenv(APP_ID_ENV, "").strip()
    if not app_id:
        print(f"環境変数 {APP_ID_ENV} にClient IDを設定してください。", file=sys.stderr)
        return 2

    try:
        offer = fetch_cheapest(sys.argv[1], app_id)
    except RetailError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1

    print(f"最安 ¥{offer.price:,}  {offer.store}  在庫{'あり' if offer.in_stock else 'なし'}")
    print(f"  {offer.name}")
    print(f"  {offer.url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
