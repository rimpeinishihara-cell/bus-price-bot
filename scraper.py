"""
bushikaku.net から価格情報を取得するモジュール。

【重要】このサイトの見た目や内部構造が変わると、この取得処理が
動かなくなる可能性があります。もし急に価格が取れなくなったら、
サイトのリニューアルが原因の可能性が高いです。

取得先は2種類:
1. 月間カレンダーページ  … 1ヶ月分の「その日の最安値」が1回のアクセスでまとめて取れる
   例: https://www.bushikaku.net/search/kyoto_tokyo/202608/
2. 日別詳細ページ        … その日に走る便を1本ずつ(バス会社・便名・料金帯)取得できる
   例: https://www.bushikaku.net/search/kyoto_tokyo/20260815/
   ※ こちらは「監視登録されている(便・日付)」の分だけ取得する
      (30日分すべてを毎回取ると、サイトへの負荷が大きくブロックされる恐れがあるため)
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
    # 普通のブラウザからのアクセスに見せるためのUser-Agent
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}

REQUEST_INTERVAL_SEC = 1.5  # サイトへの負荷を抑えるため、リクエストの間隔を空ける
_last_request_time = 0.0


def _polite_get(url: str) -> str | None:
    """サイトに優しい間隔でGETし、HTML文字列を返す。失敗したらNoneを返す。"""
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
    # href="/search/kyoto_tokyo/20260815/" のようなリンクをすべて探す
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


def get_30day_prices(route_key: str, start: date | None = None) -> dict[str, int]:
    """
    今日から30日分の最安値をまとめて取得する。
    月をまたぐ場合は必要な月のカレンダーページを複数回取得して結合する。
    """
    route_slug = ROUTES[route_key]
    if start is None:
        start = date.today()
    end = start + timedelta(days=29)  # 今日を含めて30日

    months_needed = set()
    d = start
    while d <= end:
        months_needed.add((d.year, d.month))
        d += timedelta(days=1)

    merged: dict[str, int] = {}
    for (y, m) in sorted(months_needed):
        merged.update(get_month_calendar(route_slug, y, m))

    # 対象期間の30日分だけに絞る
    out = {}
    d = start
    while d <= end:
        key = d.isoformat()
        if key in merged:
            out[key] = merged[key]
        d += timedelta(days=1)
    return out


def get_day_detail(route_key: str, date_str: str) -> list[dict]:
    """
    指定した1日の、すべての便の情報を取得する。
    戻り値: [{"bus_id": "489949", "name": "...", "company": "...",
              "price_min": 3680, "price_max": 9800}, ...]
    """
    route_slug = ROUTES[route_key]
    yyyymmdd = date_str.replace("-", "")
    url = f"{BASE}/search/{route_slug}/{yyyymmdd}/"
    html = _polite_get(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    buses = []
    seen_ids = set()

    for a in soup.find_all("a", href=True):
        m = re.match(r"^/bus/(\d+)/?$", a["href"])
        if not m:
            continue
        bus_id = m.group(1)
        name = a.get_text(strip=True)
        if not name or bus_id in seen_ids:
            continue

        # このリンクの近く(親要素のテキスト)から料金帯を探す
        price_min = None
        price_max = None
        container = a
        for _ in range(6):  # 親をたどりながら価格情報を探す
            if container.parent is None:
                break
            container = container.parent
            block_text = container.get_text(" ", strip=True)
            price_m = re.search(r"¥\s*([\d,]+)\s*(?:〜|~)\s*¥?\s*([\d,]+)", block_text)
            if price_m:
                price_min = int(price_m.group(1).replace(",", ""))
                price_max = int(price_m.group(2).replace(",", ""))
                break
            if len(block_text) > 4000:
                break  # 広すぎる範囲まで探さない(誤検出防止)

        if price_min is None:
            continue  # 料金が見つからなかった便はスキップ

        seen_ids.add(bus_id)
        buses.append({
            "bus_id": bus_id,
            "name": name,
            "price_min": price_min,
            "price_max": price_max,
        })

    return buses


def find_bus_by_id(route_key: str, date_str: str, bus_id: str) -> dict | None:
    """指定した便IDの現在の情報を取得する(監視の価格チェック用)。"""
    for bus in get_day_detail(route_key, date_str):
        if bus["bus_id"] == bus_id:
            return bus
    return None
