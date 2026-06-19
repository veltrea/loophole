"""clipboard.py — Linux のクリップボード backend。

ShellClipboard（xclip/xsel/wl-clipboard へ委譲）と X11Clipboard（プロセス内で X セレクションを
所有し INCR まで対応）。build_clipboard が X11 はプロセス内所有を優先し、失敗時のみツールに倒す。
"""

from __future__ import annotations

import ctypes
import os
import queue
import select
import threading
from typing import Any, Dict, List, Optional

from common_backends import SubprocessRunner
from .parsers import clipboard_commands, decode_text
from .x11lib import (
    _lib, _CURRENT_TIME, _PROP_MODE_REPLACE, _PROPERTY_CHANGE_MASK, _PROPERTY_DELETE,
    _PROPERTY_NEW_VALUE, _PROPERTY_NOTIFY, _SELECTION_CLEAR, _SELECTION_NOTIFY,
    _SELECTION_REQUEST, _XA_ATOM, _XPropertyEvent, _XSelectionEvent, _XSelectionRequestEvent,
)

# 定義済みアトム XA_PRIMARY（PRIMARY セレクション）。Xlib の Xatom.h で常に 1。intern しても
# 同じ値になるが、定義済みアトムは round-trip 不要なので定数で持つ（_XA_ATOM=4 と同じ流儀）。
_XA_PRIMARY = 1


class _XSelectionClearEvent(ctypes.Structure):
    # 所有していたセレクションを他クライアントに奪われた通知（SelectionClear, type 29）。
    # どのセレクション（CLIPBOARD / PRIMARY）を失ったかを selection フィールドで見分ける。
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("window", ctypes.c_ulong),
        ("selection", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
    ]


class ShellClipboard:
    """X11 / Wayland のクリップボードを外部ツール経由で読み書きする。

    X11 のセレクションは「所有クライアントが生き続けて要求に応える」モデルで、自前で
    持つにはイベントループ常駐が要る。xclip / wl-copy はその常駐（フォークしてセレクション
    所有を続ける）を肩代わりするので、loophole 本体は単発呼び出しで済ませられる。
    """

    def __init__(self, server: Optional[str], runner=None):
        self._server = server
        self._runner = runner or SubprocessRunner()

    def get(self) -> str:
        last_started = False
        for argv in clipboard_commands(self._server, "get"):
            r = self._runner.run(argv, None, 5.0, None)
            if not r.started:
                continue  # そのツールが無い → 次の候補へ
            last_started = True
            # 起動できたら、空クリップボード（非 0 終了でも stdout 空）は "" を返す。
            return (r.stdout or b"").decode("utf-8", errors="replace")
        if not last_started:
            raise RuntimeError(
                "clipboard get failed: install xclip or xsel (X11) / wl-clipboard (Wayland)")
        return ""

    def set(self, text: str) -> None:
        last_started = False
        for argv in clipboard_commands(self._server, "set"):
            # feed_stdin は出力を DEVNULL に倒す＝xclip/wl-copy のデーモン化で固まらない。
            r = self._runner.feed_stdin(argv, text, 5.0)
            if not r.started:
                continue
            last_started = True
            if r.exit_code == 0:
                return
        if not last_started:
            raise RuntimeError(
                "clipboard set failed: install xclip or xsel (X11) / wl-clipboard (Wayland)")
        raise RuntimeError("clipboard set failed: the clipboard tool returned an error")


class X11Clipboard:
    """X11 のクリップボードを **agent プロセス自身が所有して**読み書きする（外部ツール不要）。

    X のセレクションは「所有クライアントが生き続け、他アプリの SelectionRequest に応答し続ける」
    モデル。agent は常駐プロセスなので、専用スレッドで Display と不可視ウィンドウを持ち、その
    イベントループで CLIPBOARD と PRIMARY を所有・配信する——これで xclip/xsel 無しに（Windows の
    Win32 クリップボードと同じく zero-dep で）動く。Xlib の Display はスレッド安全でないので、
    全ての X 呼び出しをこの 1 本のスレッドに集約し、set/get はコマンドキュー越しに依頼する。

    X には独立した 2 つのセレクションがある: CLIPBOARD（Ctrl+C/V）と PRIMARY（テキスト選択 →
    中クリック貼り付け）。set のときは両方の所有者になり、SelectionRequest はどちらのセレクション
    宛でも同じ保持テキストから応答する（要求イベントの selection フィールドで区別はするが中身は
    共通）ので、loophole で入れた値が Ctrl+V でも中クリックでも貼れる。get は従来どおり CLIPBOARD
    を読む（中クリック相当の取得は不要）。

    大きなデータは X の **INCR（インクリメンタル転送）** プロトコルで授受するので、サイズ上限は
    無い。1 回のプロパティ書き込みに収まらない量（_CHUNK 超）は、所有側では INCR 型を立てて相手の
    PropertyNotify(Delete) ごとにチャンクを書き、読み取り側では INCR 応答を見たら PropertyNotify
    (NewValue) ごとにチャンクを溜める。get タイムアウトは大データでも固まらない安全弁として残す。
    """

    _CHUNK = 256 * 1024  # 1 チャンクの最大バイト数。これ超で INCR に切り替える

    def __init__(self):
        self._lib = _lib()
        self._cmd_q: "queue.Queue" = queue.Queue()
        self._get_lock = threading.Lock()   # GET は 1 件ずつ直列化する
        self._ready = threading.Event()
        self._start_error: List[Optional[str]] = [None]
        self._pipe_r, self._pipe_w = os.pipe()
        self._owned_text: Optional[str] = None
        # 現在所有しているセレクションの atom 集合（CLIPBOARD / PRIMARY）。片方だけ奪われても
        # もう片方を持っている限り _owned_text は保持し、両方失って初めて破棄する。
        self._owned_selections: set = set()
        self._pending_get: Optional[Dict[str, Any]] = None  # 応答待ちの GET（単発転送）
        # INCR 受信中の状態（こちらが大データを読むとき）。{"buf": bytearray, "cmd": dict} or None
        self._incr_get: Optional[Dict[str, Any]] = None
        # INCR 送信中の転送（こちらが所有し相手が大データを読むとき）。(requestor, prop) -> 状態
        self._incr_sends: Dict[tuple, Dict[str, Any]] = {}
        self._thread = threading.Thread(target=self._run, name="loophole-clipboard", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=5.0):
            raise RuntimeError("X11 clipboard thread did not become ready")
        if self._start_error[0]:
            raise RuntimeError(self._start_error[0])

    # ---- 公開 API（別スレッドから呼ばれる）----
    def set(self, text: str) -> None:
        done = threading.Event()
        self._cmd_q.put({"op": "set", "text": text, "done": done})
        self._wake()
        if not done.wait(timeout=5.0):
            raise RuntimeError("clipboard set timed out")

    def get(self) -> str:
        with self._get_lock:
            done = threading.Event()
            box: List[str] = [""]
            self._cmd_q.put({"op": "get", "result": box, "done": done})
            self._wake()
            # 大データ（INCR）でも固まらないよう上限を置く。ローカル X なら数 MB でも一瞬。
            if not done.wait(timeout=15.0):
                return ""  # 所有者が応答しない（ハング）→ 空で返す
            return box[0]

    def _wake(self) -> None:
        try:
            os.write(self._pipe_w, b"\x01")
        except OSError:
            pass

    # ---- イベントループ（専用スレッド）----
    def _run(self) -> None:
        lib = self._lib
        x = lib.x
        try:
            dpy = lib.open_display()
            root = x.XDefaultRootWindow(dpy)
            win = x.XCreateSimpleWindow(dpy, root, -10, -10, 1, 1, 0, 0, 0)
            self._dpy = dpy
            self._win = win
            self._A_CLIPBOARD = lib.intern(dpy, "CLIPBOARD")
            self._A_PRIMARY = _XA_PRIMARY  # 定義済みアトム（intern 不要）。中クリック貼り付け用
            self._A_UTF8 = lib.intern(dpy, "UTF8_STRING")
            self._A_STRING = lib.intern(dpy, "STRING")
            self._A_TARGETS = lib.intern(dpy, "TARGETS")
            self._A_PROP = lib.intern(dpy, "LOOPHOLE_CLIPBOARD")
            self._A_INCR = lib.intern(dpy, "INCR")
            # 自分の窓の PropertyNotify を受ける（INCR 受信のチャンク到着検知）。
            x.XSelectInput(dpy, win, _PROPERTY_CHANGE_MASK)
            xfd = x.XConnectionNumber(dpy)
        except Exception as exc:  # ディスプレイに繋げない等 → 構築側で ShellClipboard に倒す
            self._start_error[0] = str(exc)
            self._ready.set()
            return
        self._ready.set()

        self._pending_get: Optional[Dict[str, Any]] = None
        evbuf = (ctypes.c_long * 24)()
        evptr = ctypes.cast(evbuf, ctypes.c_void_p)
        while True:
            while x.XPending(dpy) > 0:
                x.XNextEvent(dpy, evptr)
                etype = ctypes.cast(evbuf, ctypes.POINTER(ctypes.c_int)).contents.value
                if etype == _SELECTION_REQUEST:
                    self._serve_request(
                        ctypes.cast(evbuf, ctypes.POINTER(_XSelectionRequestEvent)).contents)
                elif etype == _SELECTION_NOTIFY:
                    self._on_selection_notify(
                        ctypes.cast(evbuf, ctypes.POINTER(_XSelectionEvent)).contents)
                elif etype == _PROPERTY_NOTIFY:
                    self._on_property_notify(
                        ctypes.cast(evbuf, ctypes.POINTER(_XPropertyEvent)).contents)
                elif etype == _SELECTION_CLEAR:
                    self._on_selection_clear(
                        ctypes.cast(evbuf, ctypes.POINTER(_XSelectionClearEvent)).contents)
            # コマンド処理（GET が保留中なら新規 GET は溜めておき次周回で）
            requeue: List[Dict[str, Any]] = []
            try:
                while True:
                    cmd = self._cmd_q.get_nowait()
                    if cmd["op"] == "set":
                        self._owned_text = cmd["text"]
                        # CLIPBOARD（Ctrl+C/V）と PRIMARY（中クリック貼り付け）の両方を所有する。
                        # 以後どちらの SelectionRequest も同じ self._owned_text から応答する。
                        x.XSetSelectionOwner(dpy, self._A_CLIPBOARD, win, _CURRENT_TIME)
                        x.XSetSelectionOwner(dpy, self._A_PRIMARY, win, _CURRENT_TIME)
                        x.XFlush(dpy)
                        self._owned_selections = {self._A_CLIPBOARD, self._A_PRIMARY}
                        cmd["done"].set()
                    elif cmd["op"] == "get":
                        if self._pending_get is not None or self._incr_get is not None:
                            requeue.append(cmd)  # 先行 GET の応答待ち。後で処理
                            continue
                        owner = x.XGetSelectionOwner(dpy, self._A_CLIPBOARD)
                        if owner == 0:
                            cmd["result"][0] = ""
                            cmd["done"].set()
                        elif owner == win:
                            cmd["result"][0] = self._owned_text or ""
                            cmd["done"].set()
                        else:
                            x.XDeleteProperty(dpy, win, self._A_PROP)
                            x.XConvertSelection(dpy, self._A_CLIPBOARD, self._A_UTF8,
                                                self._A_PROP, win, _CURRENT_TIME)
                            x.XFlush(dpy)
                            self._pending_get = cmd
            except queue.Empty:
                pass
            for cmd in requeue:
                self._cmd_q.put(cmd)
            try:
                r, _, _ = select.select([xfd, self._pipe_r], [], [], 1.0)
            except (OSError, ValueError):
                r = []
            if self._pipe_r in r:
                try:
                    os.read(self._pipe_r, 4096)
                except OSError:
                    pass

    # ---- GET 側（こちらが読む。owner が小さく返すか INCR で返す）----
    def _on_selection_notify(self, ev) -> None:
        cmd = self._pending_get
        if cmd is None:
            return
        if int(ev.property) == 0:  # owner が変換を拒否
            self._finish_get(cmd, "")
            return
        got = self._lib.get_property(self._dpy, self._win, self._A_PROP)
        if got and got[0] == self._A_INCR:
            # 大データ: 以後 PropertyNotify(NewValue) ごとにチャンクが届く。
            self._lib.x.XDeleteProperty(self._dpy, self._win, self._A_PROP)  # ack → 送信開始
            self._lib.x.XFlush(self._dpy)
            self._incr_get = {"buf": bytearray(), "cmd": cmd}
            self._pending_get = None
        else:
            self._lib.x.XDeleteProperty(self._dpy, self._win, self._A_PROP)
            text = decode_text(got[3], is_utf8=True) if got else ""
            self._finish_get(cmd, text)

    def _finish_get(self, cmd, text: str) -> None:
        cmd["result"][0] = text
        cmd["done"].set()
        self._pending_get = None

    # ---- セレクション喪失（SelectionClear）----
    def _on_selection_clear(self, ev) -> None:
        """CLIPBOARD か PRIMARY のどちらかを奪われた。両方失って初めて保持テキストを捨てる。

        中クリック用の PRIMARY は、ユーザーが別アプリでテキストを選択しただけで奪われる。その
        とき CLIPBOARD（Ctrl+V 用）まで一緒に破棄してしまわないよう、失ったセレクションだけを
        集合から外し、まだ片方を持っているうちは _owned_text を残す。
        """
        self._owned_selections.discard(int(ev.selection))
        if not self._owned_selections:
            self._owned_text = None  # 両方のセレクションを失った

    # ---- イベント分岐: PropertyNotify（GET-INCR の受信 / SET-INCR の次チャンク要求）----
    def _on_property_notify(self, pev) -> None:
        x = self._lib.x
        if (pev.state == _PROPERTY_NEW_VALUE and self._incr_get is not None
                and int(pev.window) == self._win and int(pev.atom) == self._A_PROP):
            got = self._lib.get_property(self._dpy, self._win, self._A_PROP)
            x.XDeleteProperty(self._dpy, self._win, self._A_PROP)  # 次チャンクへ ack
            x.XFlush(self._dpy)
            if not got or got[2] == 0:  # 長さ 0 のチャンク = 終端
                buf = self._incr_get["buf"]
                cmd = self._incr_get["cmd"]
                self._incr_get = None
                cmd["result"][0] = bytes(buf).decode("utf-8", "replace")
                cmd["done"].set()
            else:
                self._incr_get["buf"] += got[3]
        elif pev.state == _PROPERTY_DELETE:
            key = (int(pev.window), int(pev.atom))
            if key in self._incr_sends:
                self._incr_send_next(key)

    # ---- SET 側（こちらが所有し、相手が読む。小さければ即時、大きければ INCR）----
    def _serve_request(self, req) -> None:
        x = self._lib.x
        dpy = self._dpy
        prop = int(req.property) or int(req.target)  # 旧式クライアントは property=0
        target = int(req.target)
        if target == self._A_TARGETS:
            atoms = (ctypes.c_ulong * 3)(self._A_TARGETS, self._A_UTF8, self._A_STRING)
            x.XChangeProperty(dpy, req.requestor, prop, _XA_ATOM, 32, _PROP_MODE_REPLACE,
                              ctypes.cast(atoms, ctypes.c_void_p), 3)
            self._send_notify(req, prop)
        elif target in (self._A_UTF8, self._A_STRING):
            enc = "utf-8" if target == self._A_UTF8 else "latin-1"
            data = (self._owned_text or "").encode(enc, errors="replace")
            if len(data) > self._CHUNK:
                # INCR: 型 INCR + 総量を立て、相手の PropertyNotify(Delete) ごとに送る。
                x.XSelectInput(dpy, req.requestor, _PROPERTY_CHANGE_MASK)
                total = (ctypes.c_ulong * 1)(len(data))
                x.XChangeProperty(dpy, req.requestor, prop, self._A_INCR, 32, _PROP_MODE_REPLACE,
                                  ctypes.cast(total, ctypes.c_void_p), 1)
                self._incr_sends[(int(req.requestor), prop)] = {
                    "target": target, "data": data, "offset": 0}
                self._send_notify(req, prop)
            else:
                self._set_prop_bytes(int(req.requestor), prop, target, data)
                self._send_notify(req, prop)
        else:
            self._send_notify(req, 0)  # 対応できない target は拒否

    def _incr_send_next(self, key) -> None:
        """相手が 1 チャンク読み終えてプロパティを削除した → 次のチャンク（無ければ終端）を書く。"""
        t = self._incr_sends[key]
        win, prop = key
        data = t["data"]
        off = t["offset"]
        if off < len(data):
            chunk = data[off:off + self._CHUNK]
            self._set_prop_bytes(win, prop, t["target"], chunk)
            t["offset"] = off + len(chunk)
        else:
            self._set_prop_bytes(win, prop, t["target"], b"")  # 長さ 0 = 終端
            del self._incr_sends[key]

    def _set_prop_bytes(self, win: int, prop: int, target: int, data: bytes) -> None:
        x = self._lib.x
        cbuf = (ctypes.c_char * len(data)).from_buffer_copy(data) if data else (ctypes.c_char * 0)()
        x.XChangeProperty(self._dpy, win, prop, target, 8, _PROP_MODE_REPLACE,
                          ctypes.cast(cbuf, ctypes.c_void_p), len(data))
        x.XFlush(self._dpy)

    def _send_notify(self, req, prop: int) -> None:
        x = self._lib.x
        buf = (ctypes.c_long * 24)()
        ev = ctypes.cast(buf, ctypes.POINTER(_XSelectionEvent)).contents
        ev.type = _SELECTION_NOTIFY
        ev.display = self._dpy
        ev.requestor = req.requestor
        ev.selection = req.selection
        ev.target = req.target
        ev.property = prop
        ev.time = req.time
        x.XSendEvent(self._dpy, req.requestor, False, 0, ctypes.cast(buf, ctypes.c_void_p))
        x.XFlush(self._dpy)


def build_clipboard(server: Optional[str], runner):
    """クリップボード backend を選ぶ。X11 はプロセス内所有（zero-dep）を優先し、構築失敗時のみ
    xclip/xsel に倒す。Wayland は wl-clipboard に委譲。"""
    if server == "x11":
        try:
            return X11Clipboard()
        except Exception:
            return ShellClipboard(server, runner)
    return ShellClipboard(server, runner)
