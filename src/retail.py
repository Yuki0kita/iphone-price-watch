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
import re
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


class RetailAuthError(RetailError):
    """Client IDが受け付けられなかった。全商品で同じ結果になるため、その実行では以降を諦める。"""


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


def parse_offers(payload: object, jan: str) -> list[RetailOffer]:
    """検索結果を安い順に並べて返す。"""
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
    return offers


def matches_tokens(name: str, tokens: list[str]) -> bool:
    """出品名に必要な語がすべて含まれるか。JANの付け間違いを弾くため。"""
    haystack = re.sub(r"[\s　]", "", name).lower()
    return all(re.sub(r"[\s　]", "", t).lower() in haystack for t in tokens)


def pick_offer(
    offers: list[RetailOffer], min_price: int, tokens: list[str] | None = None
) -> RetailOffer | None:
    """仕入れ候補を1件選ぶ。

    出品者がJANを付け間違えていることがあるため、2段階で弾く。

    1. min_price を下回る出品は同じ商品ではないとみなす。
       実例: ポケモンカードのBOX（買取21,000円）のJANで「1パック 1,180円」が最安に出た。
    2. tokens が指定されていれば、出品名にその語がすべて含まれるものだけを見る。
       実例: MSI RTX 5090 のJANで、Palit の別モデルが返ってきた。

    在庫があるものを優先し、無ければ最安を返す（在庫なしでも相場の目安にはなる）。
    """
    usable = [o for o in offers if o.price >= min_price]
    if tokens:
        usable = [o for o in usable if matches_tokens(o.name, tokens)]
    if not usable:
        return None
    for offer in usable:
        if offer.in_stock:
            return offer
    return usable[0]


def fetch_offers(jan: str, app_id: str, timeout: int = 20) -> list[RetailOffer]:
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
        if e.code in (401, 403):
            detail = ""
            try:
                detail = json.loads(e.read().decode("utf-8")).get("Error", {}).get("Message", "")
            except Exception:
                pass
            raise RetailAuthError(
                f"Client IDが受け付けられませんでした（HTTP {e.code}）。{detail}"
                " シークレットではなくClient ID（アプリケーションID）を登録しているか確認してください。"
            ) from e
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
        offers = fetch_offers(sys.argv[1], app_id)
    except RetailError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1

    # 商品名まで出す。同じJANに別物（バラ売り等）が混ざっていないか目視で確認するため。
    for offer in offers[:5]:
        stock = "在庫あり" if offer.in_stock else "在庫なし"
        print(f"¥{offer.price:>9,}  {stock}  {offer.store}")
        print(f"           {offer.name[:70]}")
    print(f"\n{len(offers)}件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
