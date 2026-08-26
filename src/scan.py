"""買取1丁目の全カテゴリを走査し、「買取価格が高い商品」の候補を洗い出す。

    python3 -m src.scan                 # 家電・トレカ・お酒を走査
    python3 -m src.scan --min-gap 20000
    python3 -m src.scan --root R0000002 # 家電だけ

重要: 一覧APIが返す `price` は**発売時の定価**で、現在の定価ではない。
商品登録時点の値のまま固定されるため、値上げ・値下げが反映されない。

    Steam Deck 有機EL 1TB   API 99,800 → 現在 167,980（2回の値上げ）
    PlayStation5 Pro       API 119,980 → 現在 137,980

そのため、このコマンドの出力は**利益の計算ではなく候補の洗い出し**にしか使えない。
実際に利益が出るかは、現在の実売価格と突き合わせて判断する（src/retail.py）。
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request

from src.fetcher import GOODS_ENDPOINT, USER_AGENT

CATEGORY_TREE_ENDPOINT = "https://www.1-chome.com/api/keitai/getAllCateTreeList"

# 走査するカテゴリツリーの根。携帯は別APIなので含めない。
DEFAULT_ROOTS = {
    "R0000002": "家電",
    "4IE7zlA4XkiWSxy8": "トレカ",
    "R0000003": "お酒",
}

PAGE_SIZE = 100
REQUEST_INTERVAL_SECONDS = 0.35


def get_json(url: str, timeout: int = 30) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))["data"]


def leaf_categories(nodes: list | None, path: str = "") -> list[tuple[str, str]]:
    leaves: list[tuple[str, str]] = []
    for node in nodes or []:
        child_path = f"{path}/{node['label']}"
        if node.get("children"):
            leaves += leaf_categories(node["children"], child_path)
        else:
            leaves.append((node["id"], child_path))
    return leaves


def list_products(cate_code: str) -> list[dict]:
    query = urllib.parse.urlencode(
        {
            "accCode": "",
            "page": 1,
            "size": PAGE_SIZE,
            "keyword": "",
            "isImpo": "false",
            "isCampaign": "false",
            "cateCode": cate_code,
            "kbNames": "",
            "cateName": "",
        }
    )
    return get_json(f"{GOODS_ENDPOINT}?{query}").get("content") or []


def best_condition(item: dict) -> tuple[str, int] | None:
    """一番高く買い取ってもらえる状態と、その価格。"""
    options = [
        (str(d.get("kbDetailName") or ""), int(d["kbDetailPrice"]))
        for d in (item.get("goodsKbDetails") or [])
        if isinstance(d, dict) and isinstance(d.get("kbDetailPrice"), int)
    ]
    return max(options, key=lambda o: o[1]) if options else None


def scan(roots: dict[str, str]) -> list[dict]:
    categories: list[tuple[str, str]] = []
    for root in roots:
        try:
            categories += leaf_categories(get_json(f"{CATEGORY_TREE_ENDPOINT}?cateCode={root}"))
        except Exception:
            continue

    found: list[dict] = []
    for cate_code, path in categories:
        try:
            items = list_products(cate_code)
        except Exception:
            continue
        for item in items:
            launch_price = item.get("price")
            best = best_condition(item)
            if not isinstance(launch_price, int) or launch_price <= 0 or not best:
                continue
            condition, buyback = best
            found.append(
                {
                    "title": str(item.get("title") or "").strip(),
                    "jan": str(item.get("jan") or "").strip(),
                    "cate_code": cate_code,
                    "category": path,
                    "condition": condition,
                    "buyback_price": buyback,
                    "launch_price": launch_price,
                    "gap": buyback - launch_price,
                }
            )
        time.sleep(REQUEST_INTERVAL_SECONDS)
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description="買取価格が高い商品の候補を洗い出す")
    parser.add_argument("--min-gap", type=int, default=10000, help="買取価格 − 発売時定価 の下限")
    parser.add_argument("--root", action="append", help="走査するカテゴリツリーの根（複数可）")
    parser.add_argument("--json", action="store_true", help="JSONで出力する")
    args = parser.parse_args()

    roots = {r: r for r in args.root} if args.root else DEFAULT_ROOTS
    rows = [r for r in scan(roots) if r["gap"] >= args.min_gap]
    rows.sort(key=lambda r: -r["gap"])

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return

    print("※ 定価は「発売時」の値。値上げは反映されないため、候補の洗い出しにのみ使う。\n")
    for r in rows:
        print(
            f"+{r['gap']:>8,}  買取{r['buyback_price']:>9,} / 発売時定価{r['launch_price']:>9,}"
            f"  {r['title'][:32]:<34} [{r['condition'][:10]}]"
        )
    print(f"\n{len(rows)}件（現在の定価と実売価格を必ず確認してから判断する）")


if __name__ == "__main__":
    main()
