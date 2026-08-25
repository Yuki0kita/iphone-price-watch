"""買取X Open APIから、複数店舗を横断した最高買取価格を取得する。

買取1丁目だけを見ていても「その店の価格」しか分からない。実際にいくら受け取れるかは
一番高く買う店の価格で決まるため、横断の最高値を別途取りにいく。

無料プランは30リクエスト/日・1リクエスト/秒。監視3商品なら1日1回の実行で足りる。
APIキー（環境変数 KTX_API_KEY）が無い場合は何もしない。

    python3 -m src.kaitorix 4549995649154   # 疎通確認
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

API_BASE = "https://kaitorix.app/open/api"
API_KEY_ENV = "KTX_API_KEY"

# 全プラン共通で1リクエスト/秒の制限があるため、少し余裕を持たせる。
MIN_REQUEST_INTERVAL_SECONDS = 1.1

# 取得値の妥当性チェック。src.fetcher と同じ考え方。
MIN_PLAUSIBLE_PRICE = 1_000
MAX_PLAUSIBLE_PRICE = 1_000_000

# ダッシュボードに残す上位何店舗ぶんか。全店舗を持つとJSONが無駄に膨らむ。
KEPT_QUOTES = 5

_last_request_at = 0.0


class KaitoriXError(RuntimeError):
    pass


@dataclass
class Quote:
    store: str
    price: int
    url: str


@dataclass
class MarketPrice:
    jan: str
    name: str
    max_price: int
    quotes: list[Quote]

    @property
    def best(self) -> Quote | None:
        return self.quotes[0] if self.quotes else None


def _throttle() -> None:
    global _last_request_at
    elapsed = time.monotonic() - _last_request_at
    if 0 < elapsed < MIN_REQUEST_INTERVAL_SECONDS:
        time.sleep(MIN_REQUEST_INTERVAL_SECONDS - elapsed)
    _last_request_at = time.monotonic()


def _to_price(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        price = int(str(value).replace(",", "").strip())
    except ValueError:
        return None
    return price if MIN_PLAUSIBLE_PRICE <= price <= MAX_PLAUSIBLE_PRICE else None


def parse_product(payload: object, jan: str) -> MarketPrice:
    """商品レスポンスから店舗ごとの買取価格を取り出し、高い順に並べる。"""
    if not isinstance(payload, dict):
        raise KaitoriXError("APIレスポンスがJSONオブジェクトではありません。")

    raw_quotes = payload.get("prices")
    if not isinstance(raw_quotes, list):
        raise KaitoriXError(
            f"店舗別価格(prices)が見つかりません。仕様が変わった可能性があります: keys={sorted(payload)}"
        )

    quotes: list[Quote] = []
    for row in raw_quotes:
        if not isinstance(row, dict):
            continue
        price = _to_price(row.get("price"))
        if price is None:
            continue
        quotes.append(
            Quote(
                store=str(row.get("store") or "").strip() or "不明",
                price=price,
                url=str(row.get("url") or "").strip(),
            )
        )

    if not quotes:
        raise KaitoriXError(f"有効な買取価格が1件もありませんでした: JAN {jan}")

    quotes.sort(key=lambda q: q.price, reverse=True)

    # max_price が返っていればそれを使い、無ければ店舗別価格の最大値で代用する。
    max_price = _to_price(payload.get("max_price")) or quotes[0].price
    return MarketPrice(
        jan=str(payload.get("jan") or jan),
        name=str(payload.get("name") or "").strip(),
        max_price=max_price,
        quotes=quotes,
    )


def fetch_market_price(jan: str, api_key: str, timeout: int = 20) -> MarketPrice:
    if not api_key:
        raise KaitoriXError("APIキーが設定されていません。")

    _throttle()
    request = urllib.request.Request(
        f"{API_BASE}/product/{urllib.parse.quote(jan)}",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "iphone-price-watch/1.0 (personal price monitor)",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise KaitoriXError("APIキーが無効です（HTTP 401）。") from e
        if e.code == 404:
            raise KaitoriXError(f"商品が見つかりません: JAN {jan}（HTTP 404）") from e
        if e.code == 429:
            raise KaitoriXError("1日のリクエスト上限に達しました（HTTP 429）。") from e
        raise KaitoriXError(f"リクエストが失敗しました: HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise KaitoriXError(f"APIに接続できませんでした: {e.reason}") from e

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as e:
        raise KaitoriXError(f"レスポンスをJSONとして解釈できませんでした: {e}") from e

    return parse_product(payload, jan)


def main() -> int:
    if len(sys.argv) != 2:
        print("使い方: python3 -m src.kaitorix <JANコード>", file=sys.stderr)
        return 2

    api_key = os.getenv(API_KEY_ENV, "").strip()
    if not api_key:
        print(f"環境変数 {API_KEY_ENV} にAPIキーを設定してください。", file=sys.stderr)
        return 2

    try:
        market = fetch_market_price(sys.argv[1], api_key)
    except KaitoriXError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1

    print(f"{market.name or market.jan}  最高 ¥{market.max_price:,}")
    for quote in market.quotes[:KEPT_QUOTES]:
        print(f"  ¥{quote.price:>9,}  {quote.store}")
    print(f"\n{len(market.quotes)}店舗の価格を取得")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
