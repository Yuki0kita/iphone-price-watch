"""監視対象を追加するための補助コマンド。

買取1丁目のiPhoneカテゴリを1回だけ呼び、全機種の未開封価格と、
config/products.json にそのまま貼れる形の設定を出力する。

    python -m src.discover                # 全機種
    python -m src.discover --keyword 17   # 絞り込み
    python -m src.discover --json         # 設定として貼れる形で出力
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
import urllib.parse
import urllib.request

from src.fetcher import UNOPENED_CONDITION, USER_AGENT

LIST_ENDPOINT = "https://www.1-chome.com/api/keitai/listPage"
# 買取1丁目のiPhoneカテゴリ。他カテゴリを見たい場合は --cate-code で差し替える。
IPHONE_CATE_CODE = "RGNg976kptBN7UjF"
PAGE_SIZE = 200
PRODUCT_PAGE_BASE = "https://www.1-chome.com/productDetail"


def product_id(title: str) -> str:
    """商品名から設定用のIDを作る（iPhone 17 Pro Max 512GB -> iphone-17-pro-max-512gb）。"""
    normalized = unicodedata.normalize("NFKC", title).lower()
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", normalized)).strip("-")


def fetch_list(cate_code: str, keyword: str, timeout: int = 30) -> list[dict]:
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
            "isImpoCate": "false",
        }
    )
    request = urllib.request.Request(
        f"{LIST_ENDPOINT}?{query}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload["data"]["content"]


def to_entry(item: dict) -> dict | None:
    """一覧の1件を、監視設定の1件に変換する。未開封価格がない商品は対象外。"""
    price = None
    for detail in item.get("goodsKbDetails") or []:
        if str(detail.get("kbDetailName") or "").strip() == UNOPENED_CONDITION:
            price = detail.get("kbDetailPrice")
            break
    if price is None:
        return None

    return {
        "id": product_id(item["title"]),
        "name": item["title"],
        "url": f"{PRODUCT_PAGE_BASE}/{item['goodsId']}/{item['allGoodsKbId']}",
        "target_price": None,
        "enabled": True,
        "_current_price": int(price),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="買取1丁目のiPhone一覧から監視設定を作る")
    parser.add_argument("--keyword", default="", help="商品名の絞り込み（例: 17 Pro）")
    parser.add_argument("--cate-code", default=IPHONE_CATE_CODE, help="カテゴリコード")
    parser.add_argument("--json", action="store_true", help="products.json に貼れる形で出力する")
    args = parser.parse_args()

    entries = [e for e in (to_entry(i) for i in fetch_list(args.cate_code, args.keyword)) if e]
    entries.sort(key=lambda e: -e["_current_price"])

    if args.json:
        cleaned = [{k: v for k, v in e.items() if not k.startswith("_")} for e in entries]
        print(json.dumps(cleaned, ensure_ascii=False, indent=2))
        return

    for e in entries:
        print(f"¥{e['_current_price']:>9,}  {e['name']:<28} {e['url']}")
    print(f"\n{len(entries)}件（--json で products.json 用の設定を出力）")


if __name__ == "__main__":
    main()
