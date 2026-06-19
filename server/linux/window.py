"""window.py — Linux のウィンドウ列挙・前面化 backend。

X11WindowManager（EWMH）と WaylandWindowManager（sway/Hyprland IPC）。build_window が選ぶ。
"""

from __future__ import annotations

import ctypes
import json
import time
from typing import Any, Dict, List, Optional

from common_backends import SubprocessRunner, UnsupportedBackend, try_build
from .parsers import (
    decode_text, parse_hyprland_clients, parse_long_array, parse_sway_tree, wayland_compositor,
)
from .x11lib import (
    _lib, _CLIENT_MESSAGE, _SUBSTRUCTURE_NOTIFY, _SUBSTRUCTURE_REDIRECT, _XClientMessageEvent,
)


class X11WindowManager:
    """EWMH でトップレベルウィンドウを列挙し、_NET_ACTIVE_WINDOW で前面化する。Linux/X11 専用。

    _NET_CLIENT_LIST が「ウィンドウマネージャが管理する実ウィンドウ」だけを返すので、
    Win32 の EnumWindows + 可視/タイトルフィルタに相当する一覧が素直に得られる。前面化は
    EWMH 準拠 WM へ ClientMessage を送る定番手順（pager からの要求として source=2）。

    戻り値のキーは Win32 と揃えて "hwnd"（中身は X の Window ID）にしておき、handlers と
    protocol は OS 非依存のまま動く。
    """

    def __init__(self):
        self._lib = _lib()

    def list_windows(self, visible_only: bool) -> List[Dict[str, Any]]:
        lib = self._lib
        x = lib.x
        dpy = lib.open_display()
        try:
            root = x.XDefaultRootWindow(dpy)
            ids = self._client_list(dpy, root)
            out: List[Dict[str, Any]] = []
            for wid in ids:
                title = self._window_name(dpy, wid)
                if visible_only and not title:
                    continue  # タイトル無しのツール窓等は落とす（Win32 と同じ流儀）
                out.append({
                    "hwnd": int(wid),
                    "title": title,
                    "pid": self._window_pid(dpy, wid),
                    "minimized": self._window_hidden(dpy, wid),
                })
            return out
        finally:
            x.XCloseDisplay(dpy)

    def _client_list(self, dpy, root) -> List[int]:
        for name in ("_NET_CLIENT_LIST", "_NET_CLIENT_LIST_STACKING"):
            prop = self._lib.get_property(dpy, root, self._lib.intern(dpy, name))
            if prop:
                _t, _fmt, _n, data = prop
                ids = parse_long_array(data)
                if ids:
                    return ids
        return []

    def _window_name(self, dpy, wid: int) -> str:
        utf8 = self._lib.intern(dpy, "UTF8_STRING")
        net_name = self._lib.intern(dpy, "_NET_WM_NAME")
        prop = self._lib.get_property(dpy, wid, net_name, utf8)
        if prop and prop[3]:
            return decode_text(prop[3], is_utf8=True)
        wm_name = self._lib.intern(dpy, "WM_NAME")
        prop = self._lib.get_property(dpy, wid, wm_name)
        if prop and prop[3]:
            return decode_text(prop[3], is_utf8=False)
        return ""

    def _window_pid(self, dpy, wid: int) -> int:
        prop = self._lib.get_property(dpy, wid, self._lib.intern(dpy, "_NET_WM_PID"))
        if prop:
            vals = parse_long_array(prop[3])
            if vals:
                return int(vals[0])
        return 0

    def _window_hidden(self, dpy, wid: int) -> bool:
        prop = self._lib.get_property(dpy, wid, self._lib.intern(dpy, "_NET_WM_STATE"))
        if not prop:
            return False
        atoms = parse_long_array(prop[3])
        return self._lib.intern(dpy, "_NET_WM_STATE_HIDDEN") in atoms

    def activate(self, hwnd: int) -> bool:
        lib = self._lib
        x = lib.x
        dpy = lib.open_display()
        try:
            root = x.XDefaultRootWindow(dpy)
            active_atom = lib.intern(dpy, "_NET_ACTIVE_WINDOW")
            # ClientMessage を 24-long のバッファに重ねて組む（XEvent 全体幅の器が必要）。
            buf = (ctypes.c_long * 24)()
            ev = ctypes.cast(buf, ctypes.POINTER(_XClientMessageEvent)).contents
            ev.type = _CLIENT_MESSAGE
            ev.send_event = 1
            ev.display = dpy
            ev.window = hwnd
            ev.message_type = active_atom
            ev.format = 32
            ev.data_l[0] = 2   # source indication: 2 = pager / 明示的なユーザー操作
            ev.data_l[1] = 0   # timestamp（CurrentTime）
            ev.data_l[2] = 0   # 現在のアクティブウィンドウ（不明）
            mask = _SUBSTRUCTURE_NOTIFY | _SUBSTRUCTURE_REDIRECT
            x.XSendEvent(dpy, root, False, mask, ctypes.cast(buf, ctypes.c_void_p))
            x.XRaiseWindow(dpy, hwnd)
            x.XSync(dpy, False)
            # 反映を数回だけ待って _NET_ACTIVE_WINDOW を読む。読めない WM では検証不能なので
            # True（送れた＝ベストエフォート成功）にする。
            for _ in range(5):
                active = self._active_window(dpy, root)
                if active is None:
                    return True
                if active == hwnd:
                    return True
                time.sleep(0.03)
            return False
        finally:
            x.XCloseDisplay(dpy)

    def _active_window(self, dpy, root) -> Optional[int]:
        prop = self._lib.get_property(dpy, root, self._lib.intern(dpy, "_NET_ACTIVE_WINDOW"))
        if not prop:
            return None
        vals = parse_long_array(prop[3])
        return int(vals[0]) if vals else None
class WaylandWindowManager:
    """Wayland のウィンドウ列挙・前面化。コンポジタ固有 IPC（swaymsg / hyprctl）に委譲する。

    Wayland は他クライアントのウィンドウ操作の統一プロトコルを持たない（隔離設計）。実用上
    自動化フレンドリな sway / Hyprland は IPC を公開しているので、その CLI 経由で列挙・focus
    する。GNOME/KDE Wayland は該当 IPC が無いので未対応（construct 時に明示エラー）。
    """

    def __init__(self, runner=None):
        self._runner = runner or SubprocessRunner()
        self._comp = wayland_compositor()
        if self._comp is None:
            raise RuntimeError(
                "no supported Wayland window IPC (need sway via SWAYSOCK, or Hyprland)")

    def list_windows(self, visible_only: bool) -> List[Dict[str, Any]]:
        if self._comp == "sway":
            r = self._runner.run(["swaymsg", "-t", "get_tree", "-r"], None, 5.0, None)
            wins = self._parse(r, parse_sway_tree)
        else:
            r = self._runner.run(["hyprctl", "-j", "clients"], None, 5.0, None)
            wins = self._parse(r, parse_hyprland_clients)
        if visible_only:
            wins = [w for w in wins if w.get("title")]
        return wins

    @staticmethod
    def _parse(r, parser):
        if not (r.started and r.exit_code == 0):
            return []
        try:
            obj = json.loads((r.stdout or b"").decode("utf-8", "replace"))
        except ValueError:
            return []
        return parser(obj)

    def activate(self, hwnd: int) -> bool:
        if self._comp == "sway":
            r = self._runner.run(["swaymsg", f"[con_id={hwnd}] focus"], None, 5.0, None)
        else:
            r = self._runner.run(
                ["hyprctl", "dispatch", "focuswindow", f"address:0x{hwnd:x}"], None, 5.0, None)
        return bool(r.started and r.exit_code == 0)


def build_window(server, runner):
    if server == "x11":
        return try_build(X11WindowManager, "window control requires X11")
    if server == "wayland":
        return try_build(lambda: WaylandWindowManager(runner),
                         "window control requires sway/Hyprland IPC on Wayland")
    return UnsupportedBackend("window control requires a graphical session (no DISPLAY)")
