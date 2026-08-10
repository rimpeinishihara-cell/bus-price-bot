"""
data/ フォルダの中の JSON ファイルを読み書きするための共通関数。
すべてのファイルは GitHub リポジトリの中にそのまま保存され、
GitHub Actions が実行されるたびに読み込み → 更新 → 書き込み → git commit されます。
"""
import json
import os

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def _path(filename: str) -> str:
    return os.path.join(DATA_DIR, filename)


def load(filename: str, default):
    """JSON ファイルを読み込む。存在しない場合は default を返す。"""
    path = _path(filename)
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            # 壊れていた場合も落とさず初期値を返す
            return default


def save(filename: str, data) -> None:
    """JSON ファイルを保存する(見やすいように整形して保存)。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    path = _path(filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
