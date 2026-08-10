"""
コマンド用チャンネルに投稿されたメッセージを解釈して実行する。

対応コマンド:
  !help                                   … コマンド一覧を表示
  !list <行き|帰り> <日付>                 … その日の便一覧と便IDを表示
  !watch <行き|帰り> <日付> <便ID>         … その便の価格変更監視を開始
  !unwatch <行き|帰り> <日付> <便ID>       … 監視を解除
  !watches                                … 現在の監視一覧を表示

日付は "2026-08-15" か "8/15" の形式で入力できる。
"""
import re
from datetime import date, datetime, timedelta

import scraper
import storage

HELP_TEXT = """\
**🚌 バス価格監視ボット コマンド一覧**
(このチャンネルに打ち込んでください。反応まで最大5分かかります)

`!help`
  この一覧を表示します

`!list 行き 8/15`
  8/15の「京都→東京」の便を一覧表示します(便IDつき)
  行きの代わりに「帰り」で東京→京都になります

`!watch 行き 8/15 489949`
  8/15の京都→東京・便ID 489949 の価格変更監視を開始します
  (便IDは !list で確認してください)

`!unwatch 行き 8/15 489949`
  上記の監視を解除します

`!watches`
  現在登録されている監視の一覧を表示します

---
別チャンネルでは以下を自動投稿しています:
・両便の安い日TOP5(1日1回)
・両便の最安値が変わった時の変更ログ(自動)
・監視登録した便の価格が変わった時の通知(自動・変更履歴つき)
"""

DIRECTION_ALIASES = {
    "行き": "iki", "いき": "iki", "iki": "iki",
    "帰り": "kaeri", "かえり": "kaeri", "kaeri": "kaeri",
}


def parse_date(text: str) -> str | None:
    """'2026-08-15' か '8/15' を 'YYYY-MM-DD' に変換する。"""
    text = text.strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", text)
    if m:
        y, mo, d = map(int, m.groups())
        try:
            return date(y, mo, d).isoformat()
        except ValueError:
            return None

    m = re.match(r"^(\d{1,2})/(\d{1,2})$", text)
    if m:
        mo, d = map(int, m.groups())
        today = date.today()
        # 直近の未来日になる年を自動で選ぶ(年をまたぐ指定に対応するため)
        for add_year in (0, 1):
            try:
                candidate = date(today.year + add_year, mo, d)
            except ValueError:
                continue
            if candidate >= today - timedelta(days=1):
                return candidate.isoformat()
        return None
    return None


def _fmt_yen(n: int) -> str:
    return f"{n:,}円"


def handle_command(content: str) -> str | None:
    """
    メッセージ本文を受け取り、コマンドなら実行して返信文を返す。
    コマンドでなければ None を返す(=無視する)。
    """
    content = content.strip()
    if not content.startswith("!"):
        return None

    parts = content.split()
    cmd = parts[0][1:].lower()
    args = parts[1:]

    if cmd == "help":
        return HELP_TEXT

    if cmd == "list":
        return _cmd_list(args)

    if cmd == "watch":
        return _cmd_watch(args)

    if cmd == "unwatch":
        return _cmd_unwatch(args)

    if cmd == "watches":
        return _cmd_watches()

    return f"❓ 知らないコマンドです: `!{cmd}`\n`!help` でコマンド一覧を見られます。"


def _cmd_list(args: list[str]) -> str:
    if len(args) < 2:
        return "使い方: `!list 行き 8/15`"
    direction = DIRECTION_ALIASES.get(args[0])
    if not direction:
        return "1つ目は「行き」か「帰り」を指定してください。"
    date_str = parse_date(args[1])
    if not date_str:
        return "日付の形式が正しくありません。例: `8/15` または `2026-08-15`"

    buses = scraper.get_day_detail(direction, date_str)
    if not buses:
        return f"{scraper.ROUTE_LABELS[direction]} {date_str} の便情報が取得できませんでした。"

    buses.sort(key=lambda b: b["price_min"])
    lines = [f"**{scraper.ROUTE_LABELS[direction]} {date_str} の便一覧**(安い順・上位20件)"]
    for b in buses[:20]:
        price_range = _fmt_yen(b["price_min"])
        if b.get("price_max") and b["price_max"] != b["price_min"]:
            price_range += f"〜{_fmt_yen(b['price_max'])}"
        lines.append(f"・`{b['bus_id']}` {b['name']} — {price_range}")
    lines.append("\n監視するには: `!watch 行き/帰り 日付 便ID`")
    return "\n".join(lines)


def _watch_key(direction: str, date_str: str, bus_id: str) -> str:
    return f"{direction}:{date_str}:{bus_id}"


def _cmd_watch(args: list[str]) -> str:
    if len(args) < 3:
        return "使い方: `!watch 行き 8/15 489949`"
    direction = DIRECTION_ALIASES.get(args[0])
    if not direction:
        return "1つ目は「行き」か「帰り」を指定してください。"
    date_str = parse_date(args[1])
    if not date_str:
        return "日付の形式が正しくありません。例: `8/15` または `2026-08-15`"
    bus_id = args[2]

    bus = scraper.find_bus_by_id(direction, date_str, bus_id)
    if not bus:
        return (
            f"便ID `{bus_id}` が {scraper.ROUTE_LABELS[direction]} {date_str} で見つかりません。"
            f"`!list {args[0]} {args[1]}` で便IDを確認してください。"
        )

    watches = storage.load("watch_list.json", [])
    key = _watch_key(direction, date_str, bus_id)
    if any(_watch_key(w["route"], w["date"], w["bus_id"]) == key for w in watches):
        return "すでに監視登録されています。"

    watches.append({
        "id": key,
        "route": direction,
        "date": date_str,
        "bus_id": bus_id,
        "bus_name": bus["name"],
        "created_at": datetime.utcnow().isoformat(),
    })
    storage.save("watch_list.json", watches)

    prices = storage.load("watch_prices.json", {})
    prices[key] = [{"timestamp": datetime.utcnow().isoformat(), "price": bus["price_min"]}]
    storage.save("watch_prices.json", prices)

    return (
        f"✅ 監視を開始しました\n"
        f"{scraper.ROUTE_LABELS[direction]} {date_str} 「{bus['name']}」\n"
        f"現在価格: {_fmt_yen(bus['price_min'])}"
    )


def _cmd_unwatch(args: list[str]) -> str:
    if len(args) < 3:
        return "使い方: `!unwatch 行き 8/15 489949`"
    direction = DIRECTION_ALIASES.get(args[0])
    if not direction:
        return "1つ目は「行き」か「帰り」を指定してください。"
    date_str = parse_date(args[1])
    if not date_str:
        return "日付の形式が正しくありません。"
    bus_id = args[2]
    key = _watch_key(direction, date_str, bus_id)

    watches = storage.load("watch_list.json", [])
    new_watches = [w for w in watches if _watch_key(w["route"], w["date"], w["bus_id"]) != key]
    if len(new_watches) == len(watches):
        return "該当する監視登録が見つかりませんでした。"
    storage.save("watch_list.json", new_watches)

    prices = storage.load("watch_prices.json", {})
    prices.pop(key, None)
    storage.save("watch_prices.json", prices)

    return "🗑️ 監視を解除しました。"


def _cmd_watches() -> str:
    watches = storage.load("watch_list.json", [])
    if not watches:
        return "現在、監視登録はありません。"
    lines = ["**現在の監視一覧**"]
    for w in watches:
        lines.append(
            f"・`{w['route']}` {scraper.ROUTE_LABELS[w['route']]} {w['date']} "
            f"「{w['bus_name']}」 便ID:`{w['bus_id']}`"
        )
    return "\n".join(lines)
