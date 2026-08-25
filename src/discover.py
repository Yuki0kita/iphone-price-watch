"""監視対象を追加するための補助コマンド。

買取1丁目のカテゴリを1回だけ呼び、商品名・JAN・買取価格・状態名を並べる。
`--json` を付ければ config/products.json にそのまま貼れる形で出力する。

    python3 -m src.discover                          # iPhone一覧
    python3 -m src.discover --cate playstation       # PlayStation本体
    python3 -m src.discover --cate mac --keyword mini
    python3 -m src.discover --cate 20399103 --json   # カテゴリコード直指定

カテゴリコードは買取1丁目の一覧ページを開き、通信中の listPage の cateCode で調べられる。
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
import urllib.parse
import urllib.request

from src.fetcher import (
    DEFAULT_CONDITION,
    GOODS_ENDPOINT,
    SOURCE_GOODS,
    SOURCE_KEITAI,
    USER_AGENT,
)

KEITAI_LIST_ENDPOINT = "https://www.1-chome.com/api/keitai/listPage"
PAGE_SIZE = 200

# よく使うカテゴリの近道。値は (source, cateCode, 商品ページ用URL)。
KNOWN_CATEGORIES = {
    "iphone": (SOURCE_KEITAI, "RGNg976kptBN7UjF", ""),
    "playstation": (SOURCE_GOODS, "20480828", "https://www.1-chome.com/electricAppliance"),
    "switch2": (SOURCE_GOODS, "bBNHyqptq0nqvbcg", "https://www.1-chome.com/electricAppliance"),
    "switch": (SOURCE_GOODS, "20050031", "https://www.1-chome.com/electricAppliance"),
    "camera": (SOURCE_GOODS, "20845941", "https://www.1-chome.com/electricAppliance"),
    "compact-camera": (SOURCE_GOODS, "20010003", "https://www.1-chome.com/electricAppliance"),
    "mac": (SOURCE_GOODS, "R0000002", "https://www.1-chome.com/electricAppliance"),
    "pokemon": (SOURCE_GOODS, "IIzyMdayU5wp7T4G", "https://www.1-chome.com/tradeCards"),
    "onepiece": (SOURCE_GOODS, "SEbO7gSBevo6KsPE", "https://www.1-chome.com/tradeCards"),
}

PRODUCT_PAGE_BASE = "https://www.1-chome.com/productDetail"


def product_id(title: str) -> str:
    """商品名から設定用のIDを作る（iPhone 17 Pro Max 512GB -> iphone-17-pro-max-512gb）。"""
    normalized = unicodedata.normalize("NFKC", title).lower()
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", normalized)).strip("-")


def fetch_list(source: str, cate_code: str, keyword: str, timeout: int = 30) -> list[dict]:
    endpoint = KEITAI_LIST_ENDPOINT if source == SOURCE_KEITAI else GOODS_ENDPOINT
    query = urllib.parse.urlencode(
        {
            "accCode": "",
            "page": 1,
            "size": PAGE_SIZE,
            "keyword": keyword,
            "isImpo": "false",
            "isCampaign": "false",
            "cateCode": cate_code,
            "kbNames": "",
            "cateName": "",
        }
    )
    request = urllib.request.Request(
        f"{endpoint}?{query}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload["data"]["content"]


def conditions_of(item: dict) -> list[tuple[str, int]]:
    details = item.get("goodsKbDetails") or []
    return [
        (str(d.get("kbDetailName") or "").strip(), int(d["kbDetailPrice"]))
        for d in details
        if isinstance(d, dict) and isinstance(d.get("kbDetailPrice"), (int, float))
    ]


def to_entry(item: dict, source: str, cate_code: str, page_url: str, keyword: str) -> dict | None:
    conditions = conditions_of(item)
    if not conditions:
        return None

    # 状態名はカテゴリごとに違う。既定があればそれを、無ければ最も高い状態を初期値にする。
    names = [name for name, _ in conditions]
    condition = DEFAULT_CONDITION if DEFAULT_CONDITION in names else max(conditions, key=lambda c: c[1])[0]
    price = dict(conditions)[condition]

    entry = {
        "id": product_id(item["title"]),
        "name": item["title"].strip(),
        "target_price": None,
        "enabled": True,
        "_price": price,
        "_conditions": conditions,
    }
    if source == SOURCE_KEITAI:
        entry["url"] = f"{PRODUCT_PAGE_BASE}/{item['goodsId']}/{item['allGoodsKbId']}"
    else:
        entry.update(
            {
                "source": SOURCE_GOODS,
                "cate_code": cate_code,
                "keyword": keyword or item["title"].split()[0],
                "jan": str(item.get("jan") or "").strip(),
                "condition": condition,
                "url": page_url,
            }
        )
    return entry


def main() -> None:
    parser = argparse.ArgumentParser(description="買取1丁目の一覧から監視設定を作る")
    parser.add_argument("--cate", default="iphone", help=f"カテゴリ名 {sorted(KNOWN_CATEGORIES)} またはカテゴリコード")
    parser.add_argument("--keyword", default="", help="商品名の絞り込み（例: mini）")
    parser.add_argument("--json", action="store_true", help="products.json に貼れる形で出力する")
    args = parser.parse_args()

    if args.cate in KNOWN_CATEGORIES:
        source, cate_code, page_url = KNOWN_CATEGORIES[args.cate]
    else:
        source, cate_code, page_url = SOURCE_GOODS, args.cate, "https://www.1-chome.com/electricAppliance"

    items = fetch_list(source, cate_code, args.keyword)
    entries = [e for e in (to_entry(i, source, cate_code, page_url, args.keyword) for i in items) if e]
    entries.sort(key=lambda e: -e["_price"])

    if args.json:
        cleaned = [{k: v for k, v in e.items() if not k.startswith("_")} for e in entries]
        print(json.dumps(cleaned, ensure_ascii=False, indent=2))
        return

    for e in entries:
        states = " / ".join(f"{n} ¥{p:,}" for n, p in e["_conditions"])
        print(f"¥{e['_price']:>9,}  {e['name'][:40]:<42} {states}")
    print(f"\n{len(entries)}件（--json で products.json 用の設定を出力）")


if __name__ == "__main__":
    main()
