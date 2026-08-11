"""
GitHub Actions から5分ごとに実行されるメイン処理。

やること:
  1. 京都→東京・東京→京都 の30日分 + 監視登録されている日付の最安値を取得
  2. 30日分について、前回との差分をチェックし「価格変更ログ」に通知
  3. 「安い日TOP5」を投稿(毎回)
  4. 監視登録されている日付の価格をチェックし、変わっていたら履歴つきで通知
  5. コマンド用チャンネルの新着メッセージを読み、コマンドを実行して返信
"""
import os
from datetime import datetime, date
from zoneinfo import ZoneInfo

import scraper
import storage
import commands
from discord_client import DiscordClient

JST = ZoneInfo("Asia/Tokyo")


def now_utc_iso() -> str:
    return datetime.utcnow().isoformat()


def fmt_jst(iso_utc: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_utc).replace(tzinfo=ZoneInfo("UTC")).astimezone(JST)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso_utc


def fmt_yen(n: int) -> str:
    return f"{n:,}円"


# ---------------------------------------------------------------------
# 0. 必要な価格をまとめて取得(30日分 + 監視登録されている日付)
# ---------------------------------------------------------------------
def collect_all_prices():
    watches = storage.load("watch_list.json", [])
    window_dates = scraper.get_30day_window()

    all_prices = {}
    for route_key in scraper.ROUTES:
        watch_dates = [w["date"] for w in watches if w["route"] == route_key]
        needed_dates = sorted(set(window_dates) | set(watch_dates))
        try:
            all_prices[route_key] = scraper.get_prices_for_dates(route_key, needed_dates)
        except Exception as e:
            print(f"[main] ERROR get_prices_for_dates({route_key}): {e}")
            all_prices[route_key] = {}

    return all_prices, window_dates


# ---------------------------------------------------------------------
# 1〜2. 30日分の最安値の更新と、変更検知
# ---------------------------------------------------------------------
def update_calendar_and_detect_changes(client: DiscordClient, price_log_channel: str,
                                        all_prices: dict, window_dates: list[str]):
    calendar_latest = storage.load("calendar_latest.json", {})
    changes_log = storage.load("calendar_changes.json", [])
    new_change_messages = []

    for route_key in scraper.ROUTES:
        route_prices = all_prices.get(route_key, {})
        new_window_prices = {d: route_prices[d] for d in window_dates if d in route_prices}
        if not new_window_prices:
            print(f"[main] WARN {route_key} の価格が1件も取得できませんでした")
            continue

        old_prices = calendar_latest.get(route_key, {})
        for date_str, new_price in sorted(new_window_prices.items()):
            old_price = old_prices.get(date_str)
            if old_price is not None and old_price != new_price:
                ts = now_utc_iso()
                changes_log.append({
                    "route": route_key,
                    "date": date_str,
                    "old": old_price,
                    "new": new_price,
                    "timestamp": ts,
                })
                new_change_messages.append(
                    f"・{scraper.ROUTE_LABELS[route_key]} {date_str}: "
                    f"{fmt_yen(old_price)} → {fmt_yen(new_price)}"
                )

        calendar_latest[route_key] = new_window_prices

    storage.save("calendar_latest.json", calendar_latest)

    # 変更履歴ログは35日より古いものを削除して肥大化を防ぐ
    cutoff = date.today().toordinal() - 35
    changes_log = [
        c for c in changes_log
        if date.fromisoformat(c["date"]).toordinal() >= cutoff
    ]
    storage.save("calendar_changes.json", changes_log)

    if new_change_messages:
        text = "**📉 最安値 変更検知**\n" + "\n".join(new_change_messages)
        client.send_message(price_log_channel, text)


# ---------------------------------------------------------------------
# 3. 安い日TOP5(価格チェックのたびに毎回投稿)
# ---------------------------------------------------------------------
def post_top5(client: DiscordClient, top5_channel: str):
    now_str = datetime.utcnow().replace(tzinfo=ZoneInfo("UTC")).astimezone(JST).strftime("%Y-%m-%d %H:%M")
    calendar_latest = storage.load("calendar_latest.json", {})
    lines = [f"**🏆 両便 安い日 TOP5**（{now_str} 時点）"]
    for route_key in scraper.ROUTES:
        prices = calendar_latest.get(route_key, {})
        if not prices:
            continue
        top5 = sorted(prices.items(), key=lambda kv: kv[1])[:5]
        lines.append(f"\n__{scraper.ROUTE_LABELS[route_key]}__")
        for i, (d, p) in enumerate(top5, 1):
            lines.append(f"{i}. {d} — {fmt_yen(p)}")

    if len(lines) > 1:
        client.send_message(top5_channel, "\n".join(lines))


# ---------------------------------------------------------------------
# 4. 監視登録されている日付の価格チェック(便単位ではなく日付単位)
# ---------------------------------------------------------------------
def check_watches(client: DiscordClient, watch_notify_channel: str, all_prices: dict):
    watches = storage.load("watch_list.json", [])
    if not watches:
        return
    watch_prices = storage.load("watch_prices.json", {})

    for w in watches:
        key = w["id"]
        current_price = all_prices.get(w["route"], {}).get(w["date"])
        if current_price is None:
            continue  # 取得できなかった場合は次回に再挑戦

        history = watch_prices.get(key, [])
        last_price = history[-1]["price"] if history else None

        if last_price is not None and last_price != current_price:
            ts = now_utc_iso()
            history.append({"timestamp": ts, "price": current_price})
            watch_prices[key] = history

            history_lines = [
                f"　{fmt_jst(h['timestamp'])}　{fmt_yen(h['price'])}" for h in history
            ]
            text = (
                f"**🔔 価格変更通知**\n"
                f"{scraper.ROUTE_LABELS[w['route']]} {w['date']}\n"
                f"{fmt_yen(last_price)} → {fmt_yen(current_price)}\n\n"
                f"**これまでの価格推移**\n" + "\n".join(history_lines)
            )
            client.send_message(watch_notify_channel, text)
        elif last_price is None:
            watch_prices[key] = [{"timestamp": now_utc_iso(), "price": current_price}]

    storage.save("watch_prices.json", watch_prices)


# ---------------------------------------------------------------------
# 5. コマンド処理
# ---------------------------------------------------------------------
def process_commands(client: DiscordClient, command_channel: str):
    state = storage.load("last_message_id.json", {"id": None})
    messages = client.get_messages_after(command_channel, state.get("id"))
    if not messages:
        return

    for msg in messages:
        state["id"] = msg["id"]  # 処理済みとして必ず更新(コマンドでなくても)
        if msg.get("author", {}).get("bot"):
            continue  # ボット自身の発言は無視
        reply = commands.handle_command(msg.get("content", ""))
        if reply:
            client.send_message(command_channel, reply)

    storage.save("last_message_id.json", state)


def main():
    token = os.environ["DISCORD_BOT_TOKEN"]
    command_channel = os.environ["COMMAND_CHANNEL_ID"]
    price_log_channel = os.environ["PRICE_LOG_CHANNEL_ID"]
    top5_channel = os.environ["TOP5_CHANNEL_ID"]
    `watch_notify_channel = os.environ.get("WATCH_NOTIFY_CHANNEL_ID") or command_channel`

    client = DiscordClient(token)

    # コマンド処理を先に行う(!watch した直後の回でも、すぐ後の価格取得に反映されるように)
    process_commands(client, command_channel)

    all_prices, window_dates = collect_all_prices()
    update_calendar_and_detect_changes(client, price_log_channel, all_prices, window_dates)
    post_top5(client, top5_channel)
    check_watches(client, watch_notify_channel, all_prices)


if __name__ == "__main__":
    main()
