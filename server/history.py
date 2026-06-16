"""history.py — 実行されたコマンドの履歴（純粋ロジック・テスト可）。

各エントリに以下を残す:
  - ts     : 実行時刻（epoch 秒。表示時に localtime で整形）
  - via    : 呼び元のツール名（loophole_shell / loophole_gui / loophole:run など）。リクエストの
             `via` ラベルから取る。付いていなければ "?"
  - cmd    : loophole のコマンド名（run / spawn / clipboard_set ...）
  - target : そのコマンドが「何に作用したか」の1行要約（実行コマンド・起動した
             ソフト・対象パスなど）
  - ok     : 成否

ソケットにも Windows にも依存しない純粋ロジックなので、Mac で単体テストできる
（tests/test_history.py）。ビューア（viewer.py の /log）がこのリングバッファを
読んで表に描く。clock を注入できるのでタイムスタンプもテスト可能。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List

# target が異常に長いコマンドでも履歴が膨れないよう 1 行を切り詰める上限。
_MAX_TARGET = 200


def caller_of(args: Dict[str, Any]) -> str:
    """リクエスト args から呼び元ツール名を取り出す。無ければ "?"。"""
    via = args.get("via")
    if isinstance(via, str) and via.strip():
        return via.strip()
    return "?"


def _preview(text: str, n: int = 40) -> str:
    # 改行は 1 行プレビュー用に 1 個の空白へ畳む（CRLF が二重空白にならないように）。
    one = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ")
    return '"' + one[:n] + ("…" if len(one) > n else "") + '"'


def _truncate(s: str, n: int = _MAX_TARGET) -> str:
    return s if len(s) <= n else s[:n] + "…"


def summarize_target(cmd: str, args: Dict[str, Any]) -> str:
    """コマンドが作用した対象を 1 行に要約する（純粋関数）。

    run    : シェル文字列 or argv を連結（= 実行したコマンド）
    spawn  : argv を連結（= 起動したソフト）
    clipboard_set : 先頭 40 文字のプレビュー
    screenshot    : 保存パス、無ければ "(inline)"
    read/write_file : 対象パス
    send_keys     : 送ったキー列（例 "keys: ctrl+s"）
    find_files    : "pattern in root"
    ime_set       : 変更した軸（例 "set off hiragana"）
    """
    if cmd == "run":
        if isinstance(args.get("command"), str):
            t = args["command"]
        elif isinstance(args.get("argv"), list):
            t = " ".join(str(a) for a in args["argv"])
        else:
            t = ""
    elif cmd == "spawn":
        argv = args.get("argv")
        t = " ".join(str(a) for a in argv) if isinstance(argv, list) else ""
    elif cmd == "clipboard_set":
        text = args.get("text")
        t = f"set {_preview(text)}" if isinstance(text, str) else "set"
    elif cmd == "clipboard_get":
        t = "(read clipboard)"
    elif cmd == "screenshot":
        path = args.get("path")
        t = path if isinstance(path, str) and path else "(inline)"
    elif cmd in ("read_file", "write_file"):
        path = args.get("path")
        t = path if isinstance(path, str) else ""
    elif cmd == "send_keys":
        k = args.get("keys")
        if isinstance(k, list):
            t = "keys: " + " ".join(str(x) for x in k)
        elif isinstance(k, str):
            t = "keys: " + k
        else:
            t = "keys"
    elif cmd == "find_files":
        root = args.get("root")
        pattern = args.get("pattern")
        if isinstance(root, str) and isinstance(pattern, str):
            t = f"{pattern} in {root}"
        elif isinstance(pattern, str):
            t = pattern
        else:
            t = ""
    elif cmd == "list_windows":
        pat = args.get("pattern")
        t = f"filter {_preview(pat)}" if isinstance(pat, str) and pat else "(all windows)"
    elif cmd == "activate_window":
        hwnd = args.get("hwnd")
        title = args.get("title")
        if isinstance(hwnd, int) and not isinstance(hwnd, bool):
            t = f"hwnd {hwnd}"
        elif isinstance(title, str):
            t = f"title {_preview(title)}"
        else:
            t = ""
    elif cmd == "ime_get":
        t = "(read IME state)"
    elif cmd == "ime_set":
        parts = []
        if isinstance(args.get("open"), bool):
            parts.append("on" if args["open"] else "off")
        if isinstance(args.get("mode"), str):
            parts.append(args["mode"])
        if isinstance(args.get("roman"), bool):
            parts.append("roman" if args["roman"] else "kana")
        conv = args.get("conversion")
        if isinstance(conv, int) and not isinstance(conv, bool):
            parts.append(f"conv={conv}")
        t = "set " + " ".join(parts) if parts else "set"
    else:
        t = ""
    return _truncate(t)


def format_ts(ts: float) -> str:
    """epoch 秒を表示用のローカル時刻文字列にする。"""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


class History:
    """直近 capacity 件の実行履歴を保持するスレッドセーフなリングバッファ。"""

    def __init__(self, capacity: int = 200, clock: Callable[[], float] = time.time):
        self._entries: Deque[Dict[str, Any]] = deque(maxlen=capacity)
        self._clock = clock
        self._lock = threading.Lock()
        self._seq = 0

    def record(self, cmd: str, args: Dict[str, Any], ok: bool = True) -> Dict[str, Any]:
        """1 コマンドを履歴に追加する。token/via 等のメタは target には出さない。"""
        with self._lock:
            self._seq += 1
            entry = {
                "seq": self._seq,
                "ts": self._clock(),
                "via": caller_of(args),
                "cmd": cmd,
                "target": summarize_target(cmd, args),
                "ok": bool(ok),
            }
            self._entries.append(entry)
        return entry

    def entries(self) -> List[Dict[str, Any]]:
        """古い順のスナップショット（呼び出し側で reverse して新しい順に出す）。"""
        with self._lock:
            return list(self._entries)

    def as_display(self) -> List[Dict[str, Any]]:
        """ts を整形した表示用エントリ列（viewer の /log.json 用）。"""
        return [
            {"seq": e["seq"], "time": format_ts(e["ts"]), "via": e["via"],
             "cmd": e["cmd"], "target": e["target"], "ok": e["ok"]}
            for e in self.entries()
        ]
