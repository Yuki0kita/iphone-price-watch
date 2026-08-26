"""Apple公式ストアの購入ページから、容量ごとの定価を取得する。

Apple製品の仕入れ元はApple公式であって、転売業者ではない。
Yahoo!ショッピングの最安値を仕入価格に使うと、出品者価格で判定してしまい
「定価で買えば利益が出る商品」を見落とす。

    iPhone 17 256GB   Apple公式 142,800 / Yahoo!最安 153,409

購入ページのHTMLに商品データがJSONで埋まっているため、1ページ取得すれば
その機種の全容量ぶんの価格が手に入る。robots.txt は購入ページを許可している
（拒否されているのは /shop/browse/overlay/ 配下のみ）。

    python3 -m src.apple https://www.apple.com/jp/shop/buy-iphone/iphone-17
"""
from __future__ import annotations

import re
import sys
import urllib.error
import urllib.request

# 商品オブジェクトから機種と容量を、価格ブロックから金額を拾い、partNumberで突き合わせる。
# 1ページにPro と Pro Max が同居するため、容量だけでは特定できない。
PART_MODEL_RE = re.compile(
    r'"partNumber":"([A-Z0-9]+J/A)"'
    r'(?:(?!"partNumber").){0,4000}?"productLocatorFamily":"([a-z0-9]+)"'
    r'(?:(?!"partNumber").){0,4000}?"dimensionCapacity":"([0-9]+(?:gb|tb))"',
    re.S,
)
PART_PRICE_RE = re.compile(
    r'"partNumber":"([A-Z0-9]+J/A)"(?:(?!"partNumber").){0,4000}?"currentPrice":\{"amount":"<span>([0-9,]+)円',
    re.S,
)

CAPACITY_IN_NAME_RE = re.compile(r"(\d+)\s*(GB|TB)", re.IGNORECASE)

MIN_PLAUSIBLE_PRICE = 10_000
MAX_PLAUSIBLE_PRICE = 3_000_000

# ブラウザ以外を弾くページがあるため、一般的なUAで取得する。
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class AppleError(RuntimeError):
    pass


def config_key(product_name: str) -> str | None:
    """「iPhone 17 Pro Max 512GB」→「iphone17promax:512gb」。

    容量を取り除いた残りを機種名とみなし、Apple側の productLocatorFamily に合わせる。
    """
    match = CAPACITY_IN_NAME_RE.search(product_name)
    if not match:
        return None
    capacity = f"{int(match.group(1))}{match.group(2).lower()}"
    model = re.sub(r"[^a-z0-9]", "", product_name[: match.start()].lower())
    return f"{model}:{capacity}" if model else None


def parse_prices(html: str) -> dict[str, int]:
    """購入ページから {機種:容量: 定価} を作る。"""
    models = {part: (family, capacity) for part, family, capacity in PART_MODEL_RE.findall(html)}
    prices: dict[str, int] = {}

    for part, amount in PART_PRICE_RE.findall(html):
        model = models.get(part)
        if not model:
            continue
        price = int(amount.replace(",", ""))
        if not MIN_PLAUSIBLE_PRICE <= price <= MAX_PLAUSIBLE_PRICE:
            continue
        key = f"{model[0]}:{model[1]}"
        # 同じ構成でキャリア違いが並ぶ。定価は同一のはずだが、念のため安いほうを採る。
        prices[key] = min(price, prices.get(key, price))

    if not prices:
        raise AppleError("購入ページから価格を読み取れませんでした。ページ構造が変わった可能性があります。")
    return prices


def fetch_prices(url: str, timeout: int = 30) -> dict[str, int]:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept-Language": "ja-JP,ja;q=0.9"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            html = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise AppleError(f"Apple公式ページの取得に失敗しました: HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise AppleError(f"Apple公式ページに接続できませんでした: {e.reason}") from e
    return parse_prices(html.replace("\\u002F", "/"))


def price_for(prices: dict[str, int], product_name: str, key: str | None = None) -> int | None:
    lookup = key or config_key(product_name)
    return prices.get(lookup) if lookup else None


def main() -> int:
    if len(sys.argv) != 2:
        print("使い方: python3 -m src.apple <購入ページURL>", file=sys.stderr)
        return 2
    try:
        prices = fetch_prices(sys.argv[1])
    except AppleError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1
    for key, price in sorted(prices.items(), key=lambda kv: kv[1]):
        print(f"{key:<26}  ¥{price:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
