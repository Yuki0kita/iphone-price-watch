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

from src.fetcher import SOURCE_MARKET, Product, fetch_product
from src.kaitorix import API_KEY_ENV as KTX_API_KEY_ENV
from src.kaitorix import KEPT_QUOTES, KaitoriXError, Quote, fetch_market_price

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "products.json"
HISTORY_PATH = ROOT / "docs" / "data" / "history.json"
ALERT_STATE_PATH = ROOT / "docs" / "data" / "alert_state.json"
STATUS_PATH = ROOT / "docs" / "data" / "status.json"
MARKET_PATH = ROOT / "docs" / "data" / "market.json"

SHOP_NAME = "買取1丁目"

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


def should_record(previous: dict | None, current_price: int, now_iso: str) -> bool:
    """価格が動いた時と、1日1回の生存記録だけを残す（履歴の肥大化を防ぐ）。"""
    if previous is None:
        return True
    if int(previous["price"]) != current_price:
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
) -> list[str]:
    """通知条件に当てはまる理由を列挙する。

    priceは買取1丁目の価格、previous_rowsは今回より前の記録のみ。
    marketが渡された場合、目標価格の判定は「実際に受け取れる最高額」で行う。
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


def is_in_cooldown(state: dict, price: int, cooldown_hours: int, now: datetime) -> bool:
    """同じ価格のまま通知が連打されるのを防ぐ。"""
    if state.get("price") != price:
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
) -> Alert | None:
    reasons = alert_reasons(product, price, previous_rows, cfg, now, market)
    if not reasons:
        return None

    # 連打防止は「受け取れる最高額」で判定する。他店の値が動かない限り再通知しない。
    best_price, best_store = best_offer(price, market)
    if is_in_cooldown(alert_state.get(product["id"], {}), best_price, int(cfg["cooldown_hours"]), now):
        return None

    return Alert(
        product_id=product["id"],
        product_name=product["name"],
        price=price,
        previous_price=int(previous_rows[-1]["price"]) if previous_rows else None,
        reasons=reasons,
        url=product["url"],
        best_price=best_price,
        best_store=best_store,
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


def collect_market(products: list[dict], api_key: str, now_iso: str, timeout: int) -> dict:
    """設定にJANがある商品について、37店舗を横断した最高買取価格を取得する。"""
    entries: dict[str, dict] = {}
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

        best = market.best
        entries[product["id"]] = {
            "jan": market.jan,
            "max_price": market.max_price,
            "store": best.store if best else "",
            "url": best.url if best else "",
            "quotes": [
                {"store": q.store, "price": q.price, "url": q.url} for q in market.quotes[:KEPT_QUOTES]
            ],
        }
        print(f"MARKET {product['name']}: ¥{market.max_price:,} ({best.store if best else '-'})")

    return {"checked_at": now_iso, "products": entries, "errors": errors}


def market_quote(market: dict, product_id: str) -> Quote | None:
    entry = (market.get("products") or {}).get(product_id)
    if not entry:
        return None
    return Quote(store=entry.get("store") or "他店", price=int(entry["max_price"]), url=entry.get("url") or "")


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
    records_changed = False

    # 他店の横断チェック。APIキーが無い、または今日すでに実行済みなら前回の結果を使う。
    market: dict = load_json(MARKET_PATH, {})
    api_key = os.getenv(KTX_API_KEY_ENV, "").strip()
    if api_key and should_check_market(market, now_iso):
        market = collect_market(products, api_key, now_iso, int(settings["timeout_seconds"]))
        errors.extend(market["errors"])
    elif not api_key:
        print(f"{KTX_API_KEY_ENV} が未設定のため、他店の横断チェックをスキップしました。")

    for index, product in enumerate(products):
        if index:
            time.sleep(float(settings["request_interval_seconds"]))
        quote = market_quote(market, product["id"])
        try:
            # 買取1丁目が扱っていない商品は、買取X経由の最高額だけで記録する。
            if str(product.get("source") or "") == SOURCE_MARKET:
                if not quote:
                    print(f"SKIP {product['name']}: 買取Xの価格が無いため記録できません。")
                    continue
                price, shop = quote.price, quote.store
                quote = None  # 同じ値を「他店」として二重に扱わない
            else:
                price = collect_one(product, timeout=int(settings["timeout_seconds"])).unopened_price
                shop = SHOP_NAME

            previous_rows = get_product_history(history, product["id"])
            previous = previous_rows[-1] if previous_rows else None

            if should_record(previous, price, now_iso):
                history.append(
                    {
                        "timestamp": now_iso,
                        "product_id": product["id"],
                        "product_name": product["name"],
                        "price": price,
                        "shop": shop,
                        "url": product.get("url", ""),
                        "ok": True,
                    }
                )
                records_changed = True

            alert = evaluate_alert(
                product,
                price,
                previous_rows,
                settings["alert"],
                alert_state,
                now,
                quote,
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
            alert_state[a.product_id] = {"price": a.best_price, "sent_at": now_iso, "reasons": a.reasons}

    save_json(HISTORY_PATH, history)
    save_json(ALERT_STATE_PATH, alert_state)
    if market:
        save_json(MARKET_PATH, market)
    save_json(
        STATUS_PATH,
        {
            "checked_at": now_iso,
            "products_checked": len(products),
            "records_changed": records_changed,
            "alerts": len(alerts),
            "email_sent": email_sent,
            "errors": errors,
            "market_checked_at": market.get("checked_at"),
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
