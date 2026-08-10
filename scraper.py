"""
bushikaku.net の「月間最安値カレンダー」から価格を取得するモジュール。
(個別の便ごとの情報は扱わない。日付ごとの最安値のみ)

【重要】このサイトの見た目や内部構造が変わると、この取得処理が
動かなくなる可能性があります。もし急に価格が取れなくなったら、
サイトのリニューアルが原因の可能性が高いです。

取得先:
  https://www.bushikaku.net/search/kyoto_tokyo/202608/ のような
  月間カレンダーページ。1回のアクセスでその月全部の最安値が取れる。
"""
import re
import time
from datetime import date, timedelta

import requests
from bs4 import BeautifulSoup

BASE = "https://www.bushikaku.net"

ROUTES = {
    "iki": "kyoto_tokyo",     # 行き: 京都 → 東京
    "kaeri": "tokyo_kyoto",   # 帰り: 東京 → 京都
}
ROUTE_LABELS = {
    "iki": "京都→東京",
    "kaeri": "東京→京都",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}

REQUEST_INTERVAL_SEC = 1.5  # サイトへの負荷を抑えるため、リクエストの間隔を空ける
_last_request_time = 0.0


def _polite_get(url: str) -> str | None:
    global _last_request_time
    wait = REQUEST_INTERVAL_SEC - (time.time() - _last_request_time)
    if wait > 0:
        time.sleep(wait)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        _last_request_time = time.time()
        if resp.status_code != 200:
            print(f"[scraper] WARN status={resp.status_code} url={url}")
            return None
        return resp.text
    except requests.RequestException as e:
        print(f"[scraper] ERROR fetch failed url={url} err={e}")
        return None


def get_month_calendar(route_slug: str, year: int, month: int) -> dict[str, int]:
    """
    その月の「日付ごとの最安値カレンダー」を取得する。
    戻り値: {"2026-08-15": 15000, ...} (円)  取れなかった日は含まれない。
    """
    yyyymm = f"{year:04d}{month:02d}"
    url = f"{BASE}/search/{route_slug}/{yyyymm}/"
    html = _polite_get(url)
    result: dict[str, int] = {}
    if not html:
        return result

    soup = BeautifulSoup(html, "html.parser")
    pattern = re.compile(rf"^/search/{re.escape(route_slug)}/(\d{{8}})/?$")
    for a in soup.find_all("a", href=True):
        m = pattern.match(a["href"])
        if not m:
            continue
        yyyymmdd = m.group(1)

        # 【重要】このリンクの中には「日付の数字(例:10)」と「価格(例:3,500円)」が
        # 別々の要素として隙間なく入っている。a.get_text() で丸ごと取ると
        # "10" + "3,500円" が "103,500円" のようにくっついて誤読してしまうため、
        # "円" を含むテキスト部分だけをピンポイントで取り出す。
        price_node = a.find(string=re.compile("円"))
        if not price_node:
            continue
        price_m = re.search(r"([\d,]+)\s*円", price_node)
        if not price_m:
            continue
        price = int(price_m.group(1).replace(",", ""))
        d = f"{yyyymmdd[0:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"
        result[d] = price
    return result


def get_prices_for_dates(route_key: str, dates: list[str]) -> dict[str, int]:
    """
    指定した日付リスト(["2026-08-15", ...])の最安値をまとめて取得する。
    必要な月のカレンダーページだけを重複なく取得して結合する。
    """
    if not dates:
        return {}
    route_slug = ROUTES[route_key]
    months_needed = set()
    for d in dates:
        y, m, _ = d.split("-")
        months_needed.add((int(y), int(m)))

    merged: dict[str, int] = {}
    for (y, m) in sorted(months_needed):
        merged.update(get_month_calendar(route_slug, y, m))

    return {d: merged[d] for d in dates if d in merged}


def get_30day_window() -> list[str]:
    """今日から30日分の日付文字列リストを返す。"""
    start = date.today()
    return [(start + timedelta(days=i)).isoformat() for i in range(30)]
