"""
Discordの「Botトークン」を使ってREST APIを直接叩くための薄いラッパー。
常時接続(WebSocket)は使わず、GitHub Actionsの実行中だけ通信して終了する。
"""
import os
import time

import requests

API_BASE = "https://discord.com/api/v10"


class DiscordClient:
    def __init__(self, token: str | None = None):
        self.token = token or os.environ["DISCORD_BOT_TOKEN"]
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bot {self.token}",
            "Content-Type": "application/json",
        })

    def _request(self, method: str, path: str, **kwargs):
        url = f"{API_BASE}{path}"
        for attempt in range(3):
            resp = self.session.request(method, url, timeout=20, **kwargs)
            if resp.status_code == 429:
                # レート制限にかかった場合は指定秒数待って再試行
                retry_after = resp.json().get("retry_after", 1)
                print(f"[discord] rate limited, waiting {retry_after}s")
                time.sleep(float(retry_after) + 0.5)
                continue
            if resp.status_code >= 400:
                print(f"[discord] ERROR {method} {path} -> {resp.status_code} {resp.text[:300]}")
            return resp
        return resp

    def get_messages_after(self, channel_id: str, after_id: str | None, limit: int = 50):
        """
        after_id より新しいメッセージを古い順に取得する。
        (Discord APIは新しい順にしか返さないため、こちらで並び替える)
        """
        params = {"limit": limit}
        if after_id:
            params["after"] = after_id
        resp = self._request("GET", f"/channels/{channel_id}/messages", params=params)
        if resp.status_code != 200:
            return []
        messages = resp.json()
        messages.sort(key=lambda m: int(m["id"]))  # 古い順に並び替え
        return messages

    def send_message(self, channel_id: str, content: str):
        """
        Discordの1メッセージは2000文字までなので、長い場合は分割して送る。
        """
        chunks = _split_message(content)
        for chunk in chunks:
            self._request("POST", f"/channels/{channel_id}/messages", json={"content": chunk})


def _split_message(content: str, limit: int = 1900) -> list[str]:
    if len(content) <= limit:
        return [content]
    lines = content.split("\n")
    chunks = []
    current = ""
    for line in lines:
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks
