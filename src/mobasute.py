"""モバステ（pastec.net）の買取価格表から、未開封の買取価格を取得する。

モバステはiPhone・スマホ専門の買取店。買取価格表がサーバー側で描画されているため、
1ページ取得するだけで全機種の未開封価格が手に入る。機種ごとにリクエストを投げる必要がない。

robots.txt が /api/ を明示的に拒否しているため、内部APIは使わず公開ページだけを読む。

    python3 -m src.mobasute            # 全機種
    python3 -m src.mobasute 17 256GB   # 絞り込み
"""
from __future__ import annotations

import re
import sys
import urllib.error
import urllib.request

PRICE_TABLE_URL = "https://pastec.net/iphone"
STORE_NAME = "モバステ"

# 買取価格表の1行。機種名と未開封価格が同じブロックに入っている。
ROW_SPLIT_RE = re.compile(r'class="p-priceTable__inner"')
MODEL_RE = re.compile(r'class="p-priceTable__name">\s*(?:<[^>]+>\s*)*?<span>([^<]+)</span>')
UNOPENED_PRICE_RE = re.compile(r'class="price price--unopened">\s*([0-9,]+)\s*円')

MIN_PLAUSIBLE_PRICE = 1_000
MAX_PLAUSIBLE_PRICE = 1_000_000

USER_AGENT = "iphone-price-watch/1.0 (personal price monitor)"


class MobasuteError(RuntimeError):
    pass


class MobasuteBlocked(MobasuteError):
    """この実行環境からのアクセスが拒否された（CloudFront/WAFによるIP遮断）。

    自宅などの回線からは取得できるが、GitHub Actionsのような
    データセンターのIPからは403になる。回避はせず、利用不可として扱う。
    """


def normalize_model(name: str) -> str:
    """機種名の表記ゆれを吸収する（iPhone 17 256GB と iPhone17 256GB を同じ扱いにする）。"""
    return re.sub(r"\s+", "", name).lower()


def parse_price_table(html: str) -> dict[str, int]:
    """買取価格表から {機種名: 未開封価格} を作る。未開封価格が無い行は除く。"""
    prices: dict[str, int] = {}
    for block in ROW_SPLIT_RE.split(html)[1:]:
        model_match = MODEL_RE.search(block)
        price_match = UNOPENED_PRICE_RE.search(block)
        if not model_match or not price_match:
            continue
        price = int(price_match.group(1).replace(",", ""))
        if not MIN_PLAUSIBLE_PRICE <= price <= MAX_PLAUSIBLE_PRICE:
            continue
        prices[normalize_model(model_match.group(1))] = price

    if not prices:
        raise MobasuteError("買取価格表を読み取れませんでした。ページ構造が変わった可能性があります。")
    return prices


def fetch_price_table(timeout: int = 20) -> dict[str, int]:
    request = urllib.request.Request(
        PRICE_TABLE_URL,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "ja,en;q=0.8"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            html = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 403:
            raise MobasuteBlocked("この実行環境からのアクセスが拒否されました（HTTP 403）。") from e
        raise MobasuteError(f"買取価格表の取得に失敗しました: HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise MobasuteError(f"モバステに接続できませんでした: {e.reason}") from e
    return parse_price_table(html)


def price_for(table: dict[str, int], model: str) -> int | None:
    return table.get(normalize_model(model))


def main() -> int:
    keyword = normalize_model(" ".join(sys.argv[1:]))
    try:
        table = fetch_price_table()
    except MobasuteError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1

    rows = [(m, p) for m, p in table.items() if keyword in m]
    for model, price in sorted(rows, key=lambda r: -r[1]):
        print(f"¥{price:>9,}  {model}")
    print(f"\n{len(rows)}件 / 全{len(table)}機種")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
