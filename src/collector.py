"""価格を取得して履歴に追記し、条件に合えばメールで通知する。

GitHub Actionsから1時間ごとに実行される想定。状態はすべて docs/data/ 配下のJSONに持つ。
"""
from __future__ import annotations

import json
import os
import smtplib
import ssl
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from src.apple import AppleError
from src.apple import fetch_prices as fetch_apple_prices
from src.apple import price_for as apple_price_for
from src.fetcher import SOURCE_MARKET, Product, fetch_product
from src.kaitorix import API_KEY_ENV as KTX_API_KEY_ENV
from src.kaitorix import KEPT_QUOTES, KaitoriXError, Quote, fetch_market_price
from src.mobasute import PRICE_TABLE_URL as MOBASUTE_URL
from src.mobasute import STORE_NAME as MOBASUTE_STORE
from src.mobasute import MobasuteBlocked, MobasuteError, fetch_price_table, price_for
from src.profit import Profit, calculate
from src.retail import APP_ID_ENV as YAHOO_APP_ID_ENV
from src.retail import RetailAuthError, RetailError, fetch_offers, pick_offer

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "products.json"
HISTORY_PATH = ROOT / "docs" / "data" / "history.json"
ALERT_STATE_PATH = ROOT / "docs" / "data" / "alert_state.json"
STATUS_PATH = ROOT / "docs" / "data" / "status.json"
MARKET_PATH = ROOT / "docs" / "data" / "market.json"
RETAIL_PATH = ROOT / "docs" / "data" / "retail.json"

SHOP_NAME = "買取1丁目"
KTX_SOURCE = "kaitorix"
MOBASUTE_SOURCE = "mobasute"

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
SMTP_TIMEOUT_SECONDS = 20


@dataclass
class Alert:
    product_id: str
    product_name: str
    price: int
    previous_price: int | None
    reasons: list[str]
    url: str
    best_price: int
    best_store: str
    cooldown_key: str


def best_offer(price: int, market: Quote | None) -> tuple[int, str]:
    """買取1丁目と他店の最高値を比べ、実際に受け取れる最高額とその店を返す。"""
    if market and market.price > price:
        return market.price, market.store
    return price, SHOP_NAME


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)


def parse_timestamp(value: str) -> datetime:
    """履歴のタイムスタンプをJST付きのdatetimeにする。"""
    ts = datetime.fromisoformat(str(value))
    return ts.replace(tzinfo=JST) if ts.tzinfo is None else ts


def get_product_history(history: list[dict], product_id: str) -> list[dict]:
    return [row for row in history if row.get("product_id") == product_id and row.get("ok", True)]


def should_record(
    previous: dict | None, current_price: int, now_iso: str, retail_price: int | None = None
) -> bool:
    """価格が動いた時と、1日1回の生存記録だけを残す（履歴の肥大化を防ぐ）。

    買取だけでなく仕入も見る。「値上がり前に買って値上がり後に売る」判断には
    仕入価格の推移が要るため、こちらが動いた時も記録する。
    """
    if previous is None:
        return True
    if int(previous["price"]) != current_price:
        return True
    if retail_price is not None and previous.get("retail_price") != retail_price:
        return True
    prev_date = str(previous["timestamp"])[:10]
    return prev_date != now_iso[:10]


def alert_reasons(
    product: dict,
    price: int,
    previous_rows: list[dict],
    cfg: dict,
    now: datetime,
    market: Quote | None = None,
    profit: Profit | None = None,
) -> list[str]:
    """通知条件に当てはまる理由を列挙する。

    priceは買取1丁目の価格、previous_rowsは今回より前の記録のみ。
    marketが渡された場合、目標価格の判定は「実際に受け取れる最高額」で行う。
    profitが渡された場合、仕入れて売る余地があるかも判定する。
    """
    reasons: list[str] = []
    best_price, best_store = best_offer(price, market)

    target = product.get("target_price")
    if target is not None and best_price >= int(target):
        where = "" if best_store == SHOP_NAME else f"（{best_store} ¥{best_price:,}）"
        reasons.append(f"目標価格 ¥{int(target):,} 以上{where}")

    # 同じ商品でも店によって数万円変わる。売る前に一番高い店を知らせる。
    if market:
        gap = market.price - price
        if gap >= int(cfg["market_gap_yen"]):
            reasons.append(f"{market.store}が¥{gap:,}高い（¥{market.price:,}）")

    # 仕入れて売り抜ける余地があるとき。査定減額バッファを引いた後の金額で判定する。
    if profit and profit.safe_profit >= int(cfg["min_safe_profit_yen"]):
        reasons.append(
            f"判定{profit.grade} 安全利益 ¥{profit.safe_profit:,}"
            f"（仕入 ¥{profit.retail_price:,} → 買取 ¥{profit.buyback_price:,}）"
        )

    previous_price = int(previous_rows[-1]["price"]) if previous_rows else None
    rise_yen = int(cfg["rise_yen"])
    if previous_price is not None and price - previous_price >= rise_yen:
        reasons.append(f"前回比 +¥{price - previous_price:,}")

    window_days = int(cfg["new_high_window_days"])
    cutoff = now - timedelta(days=window_days)
    window_prices: list[int] = []
    for row in previous_rows:
        try:
            if parse_timestamp(row["timestamp"]) >= cutoff:
                window_prices.append(int(row["price"]))
        except (ValueError, KeyError, TypeError):
            continue
    # 記録が少ないうちは「高値更新」が毎回成立してしまうため、最低点数を満たすまで判定しない。
    if len(window_prices) >= int(cfg["min_history_points_for_high"]) and price > max(window_prices):
        reasons.append(f"{window_days}日高値を更新")

    return reasons


def cooldown_key(best_price: int, profit: Profit | None) -> str:
    """再通知するかの判断材料。買取と仕入れのどちらかが動いたら通知しなおす。"""
    return f"{best_price}" if profit is None else f"{best_price}/{profit.retail_price}"


def is_in_cooldown(state: dict, key: str, cooldown_hours: int, now: datetime) -> bool:
    """同じ状況のまま通知が連打されるのを防ぐ。"""
    if str(state.get("key")) != key:
        return False
    sent_at_raw = state.get("sent_at")
    if not sent_at_raw:
        return False
    try:
        sent_at = parse_timestamp(sent_at_raw)
    except ValueError:
        return False
    return now - sent_at < timedelta(hours=cooldown_hours)


def evaluate_alert(
    product: dict,
    price: int,
    previous_rows: list[dict],
    cfg: dict,
    alert_state: dict,
    now: datetime,
    market: Quote | None = None,
    profit: Profit | None = None,
) -> Alert | None:
    reasons = alert_reasons(product, price, previous_rows, cfg, now, market, profit)
    if not reasons:
        return None

    # 買取も仕入れも動いていないなら再通知しない。
    best_price, best_store = best_offer(price, market)
    key = cooldown_key(best_price, profit)
    if is_in_cooldown(alert_state.get(product["id"], {}), key, int(cfg["cooldown_hours"]), now):
        return None

    return Alert(
        product_id=product["id"],
        product_name=product["name"],
        price=price,
        previous_price=int(previous_rows[-1]["price"]) if previous_rows else None,
        reasons=reasons,
        url=product.get("url", ""),
        best_price=best_price,
        best_store=best_store,
        cooldown_key=key,
    )


def build_email_body(alerts: list[Alert]) -> str:
    lines = ["未開封の買取価格が通知条件に一致しました。", ""]
    for a in alerts:
        lines.append(a.product_name)
        lines.append(f"{SHOP_NAME}: ¥{a.price:,}")
        if a.previous_price is not None:
            lines.append(f"前回価格: ¥{a.previous_price:,} ({a.price - a.previous_price:+,}円)")
        if a.best_store != SHOP_NAME:
            lines.append(f"最高額: ¥{a.best_price:,}（{a.best_store}）")
        lines.append("判定: " + " / ".join(a.reasons))
        lines.append(a.url)
        lines.append("")
    return "\n".join(lines)


def send_email(alerts: list[Alert]) -> bool:
    username = os.getenv("SMTP_USERNAME", "").strip()
    app_password = os.getenv("SMTP_APP_PASSWORD", "").strip()
    notify_to = os.getenv("NOTIFY_TO", "").strip()
    if not username or not app_password or not notify_to:
        print("メール通知の設定がないため送信をスキップしました。")
        return False

    msg = EmailMessage()
    msg["From"] = username
    msg["To"] = notify_to
    msg["Subject"] = f"[iPhone価格] 高値通知 {len(alerts)}件"
    msg.set_content(build_email_body(alerts))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
        smtp.login(username, app_password)
        smtp.send_message(msg)
    return True


def trim_history(history: list[dict], now: datetime, history_days: int) -> list[dict]:
    cutoff = now - timedelta(days=history_days)
    kept: list[dict] = []
    for row in history:
        try:
            if parse_timestamp(row["timestamp"]) >= cutoff:
                kept.append(row)
        except (ValueError, KeyError, TypeError):
            # 壊れた行は判断できないので捨てずに残し、次の実行で気づけるようにする。
            kept.append(row)
    return kept


def collect_one(product: dict, timeout: int) -> Product:
    return fetch_product(product, timeout=timeout)


def should_check_market(market: dict, now_iso: str) -> bool:
    """他店の横断チェックは1日1回だけ。無料プランは30リクエスト/日のため。"""
    checked_at = market.get("checked_at")
    return not checked_at or str(checked_at)[:10] != now_iso[:10]


def collect_ktx_quotes(products: list[dict], api_key: str, timeout: int) -> tuple[dict, list[dict]]:
    """設定にJANがある商品について、買取Xで37店舗を横断した価格を取得する。"""
    quotes: dict[str, list[dict]] = {}
    errors: list[dict] = []

    for product in products:
        jan = str(product.get("jan") or "").strip()
        if not jan:
            continue
        try:
            market = fetch_market_price(jan, api_key, timeout=timeout)
        except KaitoriXError as e:
            errors.append({"product_id": product["id"], "jan": jan, "error": str(e)})
            print(f"ERROR market {product['name']}: {e}", file=sys.stderr)
            continue

        quotes[product["id"]] = [
            {"store": q.store, "price": q.price, "url": q.url, "source": KTX_SOURCE}
            for q in market.quotes[:KEPT_QUOTES]
        ]
        print(f"MARKET {product['name']}: ¥{market.max_price:,}（{market.best.store if market.best else '-'}）")

    return quotes, errors


def cached_quotes(market: dict, product_id: str, source: str) -> list[dict]:
    """前回の結果を使い回す。取りに行かなかった／行けなかった店の価格を消さないため。"""
    entry = (market.get("products") or {}).get(product_id) or {}
    return [q for q in (entry.get("quotes") or []) if q.get("source") == source]


def mobasute_quote(price: int) -> dict:
    return {"store": MOBASUTE_STORE, "price": price, "url": MOBASUTE_URL, "source": MOBASUTE_SOURCE}


def merge_quotes(*quote_lists: list[dict]) -> list[dict]:
    """他店の価格を1つのリストにまとめ、高い順に並べる。"""
    quotes = [q for lst in quote_lists for q in lst]
    quotes.sort(key=lambda q: -int(q["price"]))
    return quotes


def collect_apple_prices(products: list[dict], timeout: int) -> tuple[dict, list[dict]]:
    """Apple公式の購入ページから定価を取る。1ページで全構成ぶん取れるためURL単位でまとめる。"""
    tables: dict[str, dict[str, int]] = {}
    errors: list[dict] = []
    for url in sorted({str(p.get("apple_url") or "").strip() for p in products} - {""}):
        try:
            tables[url] = fetch_apple_prices(url, timeout=timeout)
            print(f"Apple公式: {len(tables[url])}構成の定価を取得しました（{url.rsplit('/', 1)[-1]}）")
        except AppleError as e:
            errors.append({"source": "apple", "url": url, "error": str(e)})
            print(f"ERROR Apple公式 {url}: {e}", file=sys.stderr)
    return tables, errors


def official_retail(product: dict, apple_tables: dict) -> dict | None:
    """Apple製品はApple公式の定価を仕入価格とする。転売価格で判定しないため。"""
    url = str(product.get("apple_url") or "").strip()
    if not url:
        return None
    price = apple_price_for(apple_tables.get(url, {}), product["name"], product.get("apple_key"))
    if price is None:
        return None
    return {
        "jan": str(product.get("jan") or ""),
        "name": f"{product['name']}（Apple公式）",
        "price": int(price),
        "store": "Apple公式",
        "url": url,
        "in_stock": True,
        "source": "apple",
    }


def collect_retail(product: dict, app_id: str, buyback_price: int, cfg: dict, timeout: int) -> dict | None:
    """仕入れ側の最安値を取得する。JANが無い商品と、キー未設定時は何もしない。"""
    jan = str(product.get("jan") or "").strip()
    if not app_id or not jan:
        return None

    # 買取価格に対して安すぎる出品は別商品（バラ売り・付属品など）とみなして除外する。
    min_price = int(buyback_price * float(cfg["min_retail_ratio"]))
    offer = pick_offer(
        fetch_offers(jan, app_id, timeout=timeout), min_price, product.get("retail_match")
    )
    if offer is None:
        return None
    return {
        "source": "yahoo",
        "jan": jan,
        "name": offer.name,
        "price": offer.price,
        "store": offer.store,
        "url": offer.url,
        "in_stock": offer.in_stock,
    }


def profit_for(product: dict, buyback_price: int, retail: dict | None, cfg: dict) -> Profit | None:
    if not retail:
        return None
    return calculate(
        buyback_price=buyback_price,
        retail_price=int(retail["price"]),
        extra_cost=int(product.get("extra_cost_yen", cfg["extra_cost_yen"])),
        risk_buffer=int(cfg["risk_buffer_yen"]),
        minimum_profit=int(cfg["minimum_profit_yen"]),
    )


def best_quote(quotes: list[dict]) -> Quote | None:
    if not quotes:
        return None
    top = max(quotes, key=lambda q: int(q["price"]))
    return Quote(store=str(top.get("store") or "他店"), price=int(top["price"]), url=str(top.get("url") or ""))


def main() -> int:
    config = load_json(CONFIG_PATH, {})
    settings = config["settings"]
    products = [p for p in config["products"] if p.get("enabled", True)]
    history: list[dict] = load_json(HISTORY_PATH, [])
    alert_state: dict = load_json(ALERT_STATE_PATH, {})
    now = datetime.now(JST).replace(microsecond=0)
    now_iso = now.isoformat()

    alerts: list[Alert] = []
    errors: list[dict] = []
    notes: list[dict] = []
    records_changed = False

    # 他店の横断チェック。APIキーが無い、または今日すでに実行済みなら前回の結果を使う。
    previous_market: dict = load_json(MARKET_PATH, {})
    api_key = os.getenv(KTX_API_KEY_ENV, "").strip()
    ktx_checked_at = previous_market.get("ktx_checked_at")
    ktx_quotes = {p["id"]: cached_quotes(previous_market, p["id"], KTX_SOURCE) for p in products}

    if api_key and should_check_market({"checked_at": ktx_checked_at}, now_iso):
        fresh, ktx_errors = collect_ktx_quotes(products, api_key, int(settings["timeout_seconds"]))
        ktx_quotes.update(fresh)
        ktx_checked_at = now_iso
        errors.extend(ktx_errors)
    elif not api_key:
        print(f"{KTX_API_KEY_ENV} が未設定のため、買取Xの横断チェックをスキップしました。")

    # モバステは価格表1ページに全機種が載っているため、1リクエストで済む。APIキーも不要。
    # ただしCloudFront/WAFがデータセンターのIPを拒否するため、GitHub Actionsからは403になる。
    # 取得できなかった場合は前回の値を残し、いつ取得したものかをmobasute_checked_atで示す。
    mobasute_table: dict[str, int] = {}
    mobasute_checked_at = previous_market.get("mobasute_checked_at")
    mobasute_ok = False
    try:
        mobasute_table = fetch_price_table(int(settings["timeout_seconds"]))
        mobasute_ok = True
        mobasute_checked_at = now_iso
        print(f"{MOBASUTE_STORE}: {len(mobasute_table)}機種の価格表を取得しました。")
    except MobasuteBlocked as e:
        notes.append({"source": MOBASUTE_SOURCE, "note": str(e)})
        print(f"SKIP {MOBASUTE_STORE}: {e}")
    except MobasuteError as e:
        errors.append({"source": MOBASUTE_SOURCE, "error": str(e)})
        print(f"ERROR {MOBASUTE_STORE}: {e}", file=sys.stderr)

    market_entries: dict[str, dict] = {}

    apple_tables, apple_errors = collect_apple_prices(products, int(settings["timeout_seconds"]))
    errors.extend(apple_errors)

    yahoo_app_id = os.getenv(YAHOO_APP_ID_ENV, "").strip()
    retail_entries: dict[str, dict] = {}
    if not yahoo_app_id:
        print(f"{YAHOO_APP_ID_ENV} が未設定のため、仕入れ価格の取得をスキップしました。")

    for index, product in enumerate(products):
        if index:
            time.sleep(float(settings["request_interval_seconds"]))
        if mobasute_ok:
            price = price_for(mobasute_table, product["name"])
            mobasute = [mobasute_quote(price)] if price is not None else []
        else:
            mobasute = cached_quotes(previous_market, product["id"], MOBASUTE_SOURCE)
        quotes = merge_quotes(ktx_quotes.get(product["id"], []), mobasute)
        if quotes:
            top = quotes[0]
            market_entries[product["id"]] = {
                "max_price": int(top["price"]),
                "store": top["store"],
                "url": top.get("url", ""),
                "quotes": quotes,
            }
        quote = best_quote(quotes)
        try:
            # 買取1丁目が扱っていない商品は、買取X経由の最高額だけで記録する。
            if str(product.get("source") or "") == SOURCE_MARKET:
                if not quote:
                    print(f"SKIP {product['name']}: 他店の価格が取得できないため記録できません。")
                    continue
                price, shop = quote.price, quote.store
                quote = None  # 同じ値を「他店」として二重に扱わない
            else:
                price = collect_one(product, timeout=int(settings["timeout_seconds"])).unopened_price
                shop = SHOP_NAME

            # 仕入れ側。取得できなくても買取の記録は続ける。
            best_price, _ = best_offer(price, quote)
            # Apple公式に定価があればそれを使う。無い商品だけYahoo!の実勢を見る。
            retail = official_retail(product, apple_tables)
            try:
                if retail is None:
                    retail = collect_retail(
                        product, yahoo_app_id, best_price, settings["profit"], int(settings["timeout_seconds"])
                    )
            except RetailAuthError as e:
                # 認証が通らないなら全商品で同じ結果になる。無駄に叩かず、その実行では諦める。
                yahoo_app_id = ""
                errors.append({"source": "yahoo", "error": str(e)})
                print(f"ERROR 仕入れ価格: {e}", file=sys.stderr)
            except RetailError as e:
                errors.append({"product_id": product["id"], "retail": True, "error": str(e)})
                print(f"ERROR retail {product['name']}: {e}", file=sys.stderr)

            profit = profit_for(product, best_price, retail, settings["profit"])
            if retail:
                retail_entries[product["id"]] = {
                    **retail,
                    "buyback_price": best_price,
                    "safe_profit": profit.safe_profit,
                    "cash_profit": profit.cash_profit,
                    "grade": profit.grade,
                    "buy_threshold": profit.buy_threshold,
                }
                print(
                    f"     仕入 ¥{retail['price']:,}（{retail['store']}）"
                    f" 安全利益 ¥{profit.safe_profit:,} 判定{profit.grade}"
                )

            previous_rows = get_product_history(history, product["id"])
            previous = previous_rows[-1] if previous_rows else None

            retail_price = int(retail["price"]) if retail else None
            if should_record(previous, price, now_iso, retail_price):
                row = {
                    "timestamp": now_iso,
                    "product_id": product["id"],
                    "product_name": product["name"],
                    "price": price,
                    "shop": shop,
                    "url": product.get("url", ""),
                    "ok": True,
                }
                if retail_price is not None:
                    row["retail_price"] = retail_price
                    row["retail_source"] = retail.get("source", "")
                history.append(row)
                records_changed = True

            alert = evaluate_alert(
                product,
                price,
                previous_rows,
                settings["alert"],
                alert_state,
                now,
                quote,
                profit,
            )
            if alert:
                alerts.append(alert)
            print(f"OK {product['name']}: ¥{price:,}（{shop}）")
        except Exception as e:
            errors.append({"product_id": product["id"], "name": product["name"], "error": str(e)})
            print(f"ERROR {product['name']}: {e}", file=sys.stderr)

    history = trim_history(history, now, int(settings["history_days"]))

    email_sent = False
    if alerts:
        try:
            email_sent = send_email(alerts)
        except Exception as e:
            errors.append({"notification": "email", "error": str(e)})
            print(f"ERROR email: {e}", file=sys.stderr)
        # メール未設定でも状態は残す。設定した瞬間に過去分がまとめて飛ぶのを防ぐ。
        for a in alerts:
            alert_state[a.product_id] = {
                "key": a.cooldown_key,
                "price": a.best_price,
                "sent_at": now_iso,
                "reasons": a.reasons,
            }

    save_json(HISTORY_PATH, history)
    save_json(ALERT_STATE_PATH, alert_state)
    if market_entries:
        save_json(
            MARKET_PATH,
            {
                "checked_at": now_iso,
                "ktx_checked_at": ktx_checked_at,
                "mobasute_checked_at": mobasute_checked_at,
                "products": market_entries,
            },
        )
    if yahoo_app_id:
        save_json(RETAIL_PATH, {"checked_at": now_iso, "products": retail_entries})
    save_json(
        STATUS_PATH,
        {
            "checked_at": now_iso,
            "products_checked": len(products),
            "records_changed": records_changed,
            "alerts": len(alerts),
            "email_sent": email_sent,
            "errors": errors,
            "notes": notes,
            "market_checked_at": now_iso if market_entries else None,
            "products": [
                {
                    "id": p["id"],
                    "name": p["name"],
                    "url": p.get("url", ""),
                    "source": p.get("source", "keitai"),
                    "target_price": p.get("target_price"),
                }
                for p in products
            ],
        },
    )

    # 全滅した時だけ失敗扱いにする（1機種の一時エラーで通知が止まらないように）。
    return 1 if products and len(errors) >= len(products) else 0


if __name__ == "__main__":
    raise SystemExit(main())
