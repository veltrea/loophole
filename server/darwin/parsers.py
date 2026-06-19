"""parsers.py — darwin backend の純粋ロジック（osascript 出力のパース等）。

OS API には触らない。Linux の linux/parsers.py と同じ位置づけで、各能力モジュールから
切り離してテストできるようにする。
"""

from __future__ import annotations

from typing import Any, Dict, List


def parse_window_listing(text: str, visible_only: bool) -> List[Dict[str, Any]]:
    """AppleScript が吐いた 1 行 1 ウィンドウのタブ区切り出力をパースする。

    入力形式: `<pid>\t<title>\t<minimized 0/1>` を改行で並べた文字列。

    戻り値: list_windows() の契約に揃えた dict のリスト。hwnd は (pid << 16) | index
    の合成 ID で、同一プロセス内で順番に index が振られる。

    visible_only=True ならタイトル空のウィンドウを落とす（Win32 / X11 と同じ流儀）。
    破損行（タブが足りない / pid が数字でない）は黙って落とす（ベストエフォート列挙）。
    """
    out: List[Dict[str, Any]] = []
    # (pid, この pid 内での累積 index) を保つ
    index_by_pid: Dict[int, int] = {}
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        pid_text, title, min_text = parts[0], parts[1], parts[2]
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        idx = index_by_pid.get(pid, 0)
        index_by_pid[pid] = idx + 1
        if visible_only and not title.strip():
            continue
        # 合成 hwnd: pid を上位 16bit、index を下位 16bit。max pid 65535 想定（macOS の pid_t は
        # 32bit だが現実的に当面 5 桁台までしか出ない。将来溢れる場合はこの仕様を見直す）。
        hwnd = (pid << 16) | (idx & 0xFFFF)
        out.append({
            "hwnd": hwnd,
            "title": title,
            "pid": pid,
            "minimized": min_text.strip() == "1",
        })
    return out
