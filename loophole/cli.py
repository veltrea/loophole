#!/usr/bin/env python3
"""loophole.py — loophole を叩く Mac/クライアント側 CLI。

前提: SSH のポートフォワードで 対象 Windows のループバックに繋いでおく:
    ssh -o ProxyJump=none -o IdentitiesOnly=yes -i ~/.ssh/id_ed25519 \\
        -L 9999:127.0.0.1:9999 -N <ssh-user>@<host> &

使い方:
    python3 loophole.py ping
    python3 loophole.py hello
    python3 loophole.py run -- cmd /c ver
    python3 loophole.py shell "echo %USERNAME% & ver"
    python3 loophole.py gui "C:/Program Files/Mozilla Firefox/firefox.exe" https://example.com
    python3 loophole.py clip-set "クリップボードに入れる文字列"
    python3 loophole.py clip-get
    python3 loophole.py shot /tmp/shot.png         # エージェント側で撮って scp で回収
    python3 loophole.py read "C:/path/report.txt"
    python3 loophole.py write "C:/path/x.txt" "内容"
    python3 loophole.py keys ctrl+s                  # ショートカット送出（複数: keys win+r enter）
    python3 loophole.py find "C:/Users" "*.txt"       # ファイル名検索（部分一致は --substring）
    python3 loophole.py windows                        # 開いているウィンドウ一覧（絞り込み: windows Notepad）
    python3 loophole.py activate Notepad              # タイトル部分一致で前面化（HWND 指定は --hwnd 12345）
    python3 loophole.py ime-get                         # 前面ウィンドウの IME 状態を読む
    python3 loophole.py ime-set --off                  # IME を切る（直接入力＝type が IME に化けない）
    python3 loophole.py ime-set --on --mode hiragana   # IME を ON にしてひらがな入力へ

JSON をそのまま見たいときは --json。別ホスト/ポート/トークンは --host/--port/--token。
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
from typing import Any, Dict, Optional

from . import protocol


class Client:
    def __init__(self, host: str, port: int, token: Optional[str], via: Optional[str] = None):
        self._host = host
        self._port = port
        self._token = token
        # via: 呼び元ツール名。設定すると ping/hello 以外のリクエストに付与され、
        # エージェント側の実行履歴（/log）に「誰が叩いたか」として残る。
        self._via = via
        self._counter = 0

    def call(self, cmd: str, args: Optional[Dict[str, Any]] = None, timeout: float = 60.0) -> Dict[str, Any]:
        args = dict(args or {})
        if self._token and cmd not in ("ping", "hello"):
            args["token"] = self._token
        if self._via and cmd not in ("ping", "hello"):
            args["via"] = self._via
        self._counter += 1
        request = protocol.make_request(self._counter, cmd, args)

        with socket.create_connection((self._host, self._port), timeout=timeout) as sock:
            sock.sendall(protocol.encode_message(request))
            buffer = protocol.LineBuffer()
            sock.settimeout(timeout)
            while True:
                data = sock.recv(65536)
                if not data:
                    raise ConnectionError("connection closed before a full reply arrived")
                for line in buffer.push(data):
                    return protocol.decode_message(line)


def _print_result(response: Dict[str, Any], as_json: bool) -> int:
    if as_json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return 0 if response.get("ok") else 1
    if not response.get("ok"):
        print(f"ERROR: {response.get('error')}", file=sys.stderr)
        return 1
    result = response.get("result")
    # run の結果は人間が読みやすいよう整形
    if isinstance(result, dict) and "exit_code" in result:
        if result.get("stdout"):
            print(result["stdout"])
        if result.get("stderr"):
            print(result["stderr"], file=sys.stderr)
        return int(result["exit_code"] or 0)
    if isinstance(result, dict) and set(result.keys()) == {"text"}:
        print(result["text"])
        return 0
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="loophole client")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--token", default=None)
    parser.add_argument("--json", action="store_true", help="print the raw JSON response")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ping")
    sub.add_parser("hello")

    p_run = sub.add_parser("run", help="run argv without a shell (everything after -- is argv)")
    p_run.add_argument("argv", nargs=argparse.REMAINDER)

    p_shell = sub.add_parser("shell", help="run a one-liner via cmd.exe /S /C")
    p_shell.add_argument("command")
    p_shell.add_argument("--encoding", default="auto")

    p_gui = sub.add_parser("gui", help="spawn a GUI/long-running process and return its pid")
    p_gui.add_argument("argv", nargs=argparse.REMAINDER)

    p_clipset = sub.add_parser("clip-set")
    p_clipset.add_argument("text")
    sub.add_parser("clip-get")

    p_shot = sub.add_parser("shot", help="capture a screenshot to a path ON the agent host")
    p_shot.add_argument("remote_path")

    p_read = sub.add_parser("read")
    p_read.add_argument("path")
    p_read.add_argument("--encoding", default="auto")

    p_write = sub.add_parser("write")
    p_write.add_argument("path")
    p_write.add_argument("text")

    p_keys = sub.add_parser("keys", help="send keyboard shortcuts, e.g. keys ctrl+s")
    p_keys.add_argument("keys", nargs="+", help="one or more strokes, e.g. win+r enter")

    p_find = sub.add_parser("find", help="search files by name under a directory")
    p_find.add_argument("root")
    p_find.add_argument("pattern")
    p_find.add_argument("--substring", action="store_true", help="substring match instead of glob")
    p_find.add_argument("--max", type=int, default=200, dest="max_results")
    p_find.add_argument("--depth", type=int, default=None, dest="max_depth", help="0 = root only")
    p_find.add_argument("--dirs", action="store_true", dest="include_dirs",
                        help="also match directory names")

    p_windows = sub.add_parser("windows", help="list open top-level windows")
    p_windows.add_argument("pattern", nargs="?", default=None,
                           help="optional title substring filter")

    p_activate = sub.add_parser("activate",
                                help="bring a window to the front by title substring or --hwnd")
    p_activate.add_argument("title", nargs="?", default=None, help="title substring")
    p_activate.add_argument("--hwnd", type=int, default=None,
                            help="exact window handle (from `windows`)")

    sub.add_parser("ime-get", help="read the foreground window's IME state")

    p_imeset = sub.add_parser("ime-set", help="change the foreground window's IME state")
    g_open = p_imeset.add_mutually_exclusive_group()
    g_open.add_argument("--on", dest="open", action="store_true", default=None,
                        help="turn IME on (Japanese input mode)")
    g_open.add_argument("--off", dest="open", action="store_false",
                        help="turn IME off (direct input; type won't be eaten by the IME)")
    p_imeset.add_argument("--mode", default=None,
                          choices=["hiragana", "katakana", "katakana-half",
                                   "alphanumeric", "alphanumeric-full"],
                          help="conversion mode when IME is on")
    g_roman = p_imeset.add_mutually_exclusive_group()
    g_roman.add_argument("--roman", dest="roman", action="store_true", default=None,
                         help="roman (romaji) input")
    g_roman.add_argument("--kana", dest="roman", action="store_false",
                         help="kana input")
    p_imeset.add_argument("--conversion", type=int, default=None,
                          help="raw conversion bitfield (power users; overrides --mode/--roman)")

    args = parser.parse_args(argv)
    # 呼び元ラベル（実行履歴 /log に "loophole:<サブコマンド>" として残る）。
    client = Client(args.host, args.port, args.token, via=f"loophole:{args.cmd}")

    try:
        if args.cmd == "ping":
            resp = client.call("ping")
        elif args.cmd == "hello":
            resp = client.call("hello")
        elif args.cmd == "run":
            argv_list = args.argv[1:] if args.argv and args.argv[0] == "--" else args.argv
            resp = client.call("run", {"argv": argv_list})
        elif args.cmd == "shell":
            resp = client.call("run", {"command": args.command, "encoding": args.encoding})
        elif args.cmd == "gui":
            argv_list = args.argv[1:] if args.argv and args.argv[0] == "--" else args.argv
            resp = client.call("spawn", {"argv": argv_list})
        elif args.cmd == "clip-set":
            resp = client.call("clipboard_set", {"text": args.text})
        elif args.cmd == "clip-get":
            resp = client.call("clipboard_get")
        elif args.cmd == "shot":
            resp = client.call("screenshot", {"path": args.remote_path, "data": False})
        elif args.cmd == "read":
            resp = client.call("read_file", {"path": args.path, "encoding": args.encoding})
        elif args.cmd == "write":
            resp = client.call("write_file", {"path": args.path, "text": args.text})
        elif args.cmd == "keys":
            resp = client.call("send_keys", {"keys": args.keys})
        elif args.cmd == "find":
            fargs = {"root": args.root, "pattern": args.pattern,
                     "match": "substring" if args.substring else "glob",
                     "max_results": args.max_results, "include_dirs": args.include_dirs}
            if args.max_depth is not None:
                fargs["max_depth"] = args.max_depth
            resp = client.call("find_files", fargs)
        elif args.cmd == "windows":
            wargs = {}
            if args.pattern:
                wargs["pattern"] = args.pattern
            resp = client.call("list_windows", wargs)
        elif args.cmd == "activate":
            if args.hwnd is not None:
                resp = client.call("activate_window", {"hwnd": args.hwnd})
            elif args.title:
                resp = client.call("activate_window", {"title": args.title})
            else:
                parser.error("activate needs a title substring or --hwnd")
                return 2
        elif args.cmd == "ime-get":
            resp = client.call("ime_get")
        elif args.cmd == "ime-set":
            iargs: Dict[str, Any] = {}
            if args.open is not None:
                iargs["open"] = args.open
            if args.mode is not None:
                iargs["mode"] = args.mode
            if args.roman is not None:
                iargs["roman"] = args.roman
            if args.conversion is not None:
                iargs["conversion"] = args.conversion
            if not iargs:
                parser.error("ime-set needs at least one of --on/--off, --mode, "
                             "--roman/--kana, --conversion")
                return 2
            resp = client.call("ime_set", iargs)
        else:
            parser.error(f"unknown command {args.cmd}")
            return 2
    except (ConnectionError, socket.error, OSError) as exc:
        print(f"connection error: {exc}\n"
              f"is the SSH port-forward up?  ssh -L {args.port}:127.0.0.1:{args.port} ...",
              file=sys.stderr)
        return 3

    return _print_result(resp, args.json)


if __name__ == "__main__":
    raise SystemExit(main())
