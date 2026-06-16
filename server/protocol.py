"""protocol.py — loophole の JSONL プロトコル（純粋ロジック）。

loophole は「対話デスクトップセッションに常駐し、TCP 経由でコマンド実行・
クリップボード・スクリーンショット・GUI 起動を代行する」小さなサーバー。
SSH のセッション 0（非対話）では GUI に触れない問題を回避するために作る。

このモジュールはソケットや subprocess に一切依存しない純粋ロジックなので、
Mac でも Windows でも単体テストできる（tests/test_protocol.py）。

フレーミング:
  1 メッセージ = 1 行の JSON + "\n"（JSONL / NDJSON）。
  **Content-Length ヘッダーは付けない**（LSP ではなく MCP stdio と同じ流儀）。

リクエスト:  {"id": <任意>, "cmd": "run", "args": {...}}
レスポンス:  成功 {"id": <同じ>, "ok": true, "result": {...}}
             失敗 {"id": <同じ>, "ok": false, "error": "..."}
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterator, Optional, Tuple


class ProtocolError(Exception):
    """1 行が JSON として壊れている / 必須フィールド欠落などの不正。"""


def encode_message(obj: Dict[str, Any]) -> bytes:
    """辞書を JSONL の 1 行（末尾改行つき UTF-8 バイト列）にする。"""
    # ensure_ascii=False で日本語をそのまま UTF-8 で出す。改行は \n 1 個だけ。
    line = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return (line + "\n").encode("utf-8")


def decode_message(line: bytes | str) -> Dict[str, Any]:
    """JSONL の 1 行を辞書にする。壊れていれば ProtocolError。"""
    if isinstance(line, bytes):
        text = line.decode("utf-8", errors="replace")
    else:
        text = line
    text = text.strip()
    if not text:
        raise ProtocolError("empty line")
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"invalid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise ProtocolError("top-level JSON must be an object")
    return obj


def make_request(request_id: Any, cmd: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"id": request_id, "cmd": cmd, "args": args or {}}


def make_ok(request_id: Any, result: Any) -> Dict[str, Any]:
    return {"id": request_id, "ok": True, "result": result}


def make_error(request_id: Any, message: str) -> Dict[str, Any]:
    return {"id": request_id, "ok": False, "error": message}


def parse_request(obj: Dict[str, Any]) -> Tuple[Any, str, Dict[str, Any]]:
    """リクエスト辞書から (id, cmd, args) を取り出す。cmd 欠落は ProtocolError。"""
    if "cmd" not in obj:
        raise ProtocolError("request is missing 'cmd'")
    cmd = obj["cmd"]
    if not isinstance(cmd, str):
        raise ProtocolError("'cmd' must be a string")
    args = obj.get("args", {})
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ProtocolError("'args' must be an object")
    return obj.get("id"), cmd, args


class LineBuffer:
    """ソケットから届く生バイトを溜め、完成した行を 1 つずつ取り出すバッファ。

    TCP はストリームなので 1 回の recv が行の途中で切れたり、複数行まとまって
    届いたりする。push() で受信バイトを足し、iter で取り出せた行を回す。
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def push(self, data: bytes) -> Iterator[bytes]:
        self._buf.extend(data)
        while True:
            newline = self._buf.find(b"\n")
            if newline < 0:
                break
            line = bytes(self._buf[:newline])
            del self._buf[: newline + 1]
            yield line

    @property
    def pending(self) -> int:
        """まだ行になっていない残りバイト数（過大入力の検知などに使う）。"""
        return len(self._buf)


def decode_output(raw: bytes, encoding: str = "auto") -> str:
    """子プロセスの出力バイトを文字列へ復号する。

    Windows の多数派（cmd 内部コマンド・レガシー exe）は CP932 を吐き、モダンな
    ツールは UTF-8 を吐く。万能な単一復号は無いので戦略を選べるようにする
    （skill windows-cmd-japanese-encoding §3）:
      - "auto"  : UTF-8 として厳密デコード → 失敗したら CP932 で復号し直す
      - "utf-8" : UTF-8 固定（不正バイトは置換）
      - "cp932" : CP932 固定
    先頭の UTF-8 BOM は剥がす。
    """
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]

    enc = encoding.lower()
    if enc in ("utf-8", "utf8"):
        return raw.decode("utf-8", errors="replace")
    if enc in ("cp932", "shift_jis", "sjis", "ansi", "oem"):
        # 厳密 shift_jis ではなく必ず CP932（Windows-31J）で復号する
        return raw.decode("cp932", errors="replace")

    # auto: まず UTF-8 を厳密に試し、ダメなら CP932
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp932", errors="replace")
