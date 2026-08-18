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

from src.fetcher import Product, fetch_product

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "products.json"
HISTORY_PATH = ROOT / "docs" / "data" / "history.json"
ALERT_STATE_PATH = ROOT / "docs" / "data" / "alert_state.json"
STATUS_PATH = ROOT / "docs" / "data" / "status.json"

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
) -> list[str]:
    """今回の価格が通知条件に当てはまる理由を列挙する。previous_rowsは今回より前の記録のみ。"""
    reasons: list[str] = []

    target = product.get("target_price")
    if target is not None and price >= int(target):
        reasons.append(f"目標価格 ¥{int(target):,} 以上")

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
) -> Alert | None:
    reasons = alert_reasons(product, price, previous_rows, cfg, now)
    if not reasons:
        return None
    if is_in_cooldown(alert_state.get(product["id"], {}), price, int(cfg["cooldown_hours"]), now):
        return None

    return Alert(
        product_id=product["id"],
        product_name=product["name"],
        price=price,
        previous_price=int(previous_rows[-1]["price"]) if previous_rows else None,
        reasons=reasons,
        url=product["url"],
    )


def build_email_body(alerts: list[Alert]) -> str:
    lines = ["買取1丁目の未開封価格が通知条件に一致しました。", ""]
    for a in alerts:
        lines.append(a.product_name)
        lines.append(f"現在価格: ¥{a.price:,}")
        if a.previous_price is not None:
            lines.append(f"前回価格: ¥{a.previous_price:,} ({a.price - a.previous_price:+,}円)")
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
    return fetch_product(product["url"], timeout=timeout)


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

    for index, product in enumerate(products):
        if index:
            time.sleep(float(settings["request_interval_seconds"]))
        try:
            fetched = collect_one(product, timeout=int(settings["timeout_seconds"]))
            previous_rows = get_product_history(history, product["id"])
            previous = previous_rows[-1] if previous_rows else None

            if should_record(previous, fetched.unopened_price, now_iso):
                history.append(
                    {
                        "timestamp": now_iso,
                        "product_id": product["id"],
                        "product_name": product["name"],
                        "price": fetched.unopened_price,
                        "url": product["url"],
                        "ok": True,
                    }
                )
                records_changed = True

            alert = evaluate_alert(
                product,
                fetched.unopened_price,
                previous_rows,
                settings["alert"],
                alert_state,
                now,
            )
            if alert:
                alerts.append(alert)
            print(f"OK {product['name']}: ¥{fetched.unopened_price:,}")
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
            alert_state[a.product_id] = {"price": a.price, "sent_at": now_iso, "reasons": a.reasons}

    save_json(HISTORY_PATH, history)
    save_json(ALERT_STATE_PATH, alert_state)
    save_json(
        STATUS_PATH,
        {
            "checked_at": now_iso,
            "products_checked": len(products),
            "records_changed": records_changed,
            "alerts": len(alerts),
            "email_sent": email_sent,
            "errors": errors,
            "products": [
                {"id": p["id"], "name": p["name"], "url": p["url"], "target_price": p.get("target_price")}
                for p in products
            ],
        },
    )

    # 全滅した時だけ失敗扱いにする（1機種の一時エラーで通知が止まらないように）。
    return 1 if products and len(errors) >= len(products) else 0


if __name__ == "__main__":
    raise SystemExit(main())
