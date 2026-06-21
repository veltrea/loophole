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
            self._send_active(dpy, root, int(hwnd))
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

    def _send_active(self, dpy, root, win: int) -> None:
        """_NET_ACTIVE_WINDOW の ClientMessage を root へ送り窓を前面化する（activate と共有）。"""
        lib = self._lib
        x = lib.x
        active_atom = lib.intern(dpy, "_NET_ACTIVE_WINDOW")
        # ClientMessage を 24-long のバッファに重ねて組む（XEvent 全体幅の器が必要）。
        buf = (ctypes.c_long * 24)()
        ev = ctypes.cast(buf, ctypes.POINTER(_XClientMessageEvent)).contents
        ev.type = _CLIENT_MESSAGE
        ev.send_event = 1
        ev.display = dpy
        ev.window = win
        ev.message_type = active_atom
        ev.format = 32
        ev.data_l[0] = 2   # source indication: 2 = pager / 明示的なユーザー操作
        ev.data_l[1] = 0   # timestamp（CurrentTime）
        ev.data_l[2] = 0   # 現在のアクティブウィンドウ（不明）
        mask = _SUBSTRUCTURE_NOTIFY | _SUBSTRUCTURE_REDIRECT
        x.XSendEvent(dpy, root, False, mask, ctypes.cast(buf, ctypes.c_void_p))
        x.XRaiseWindow(dpy, win)
        x.XSync(dpy, False)

    def _active_window(self, dpy, root) -> Optional[int]:
        prop = self._lib.get_property(dpy, root, self._lib.intern(dpy, "_NET_ACTIVE_WINDOW"))
        if not prop:
            return None
        vals = parse_long_array(prop[3])
        return int(vals[0]) if vals else None

    def set_window(self, hwnd: int, position=None, size=None,
                   minimized: Optional[bool] = None, fullscreen: Optional[bool] = None,
                   maximized: Optional[bool] = None, raise_: Optional[bool] = None) -> Dict[str, Any]:
        """X11/EWMH で窓 1 枚の geometry/状態を設定し、適用後 state を返す。

        position=(x,y) / size=(w,h) は None=触らない。minimized/fullscreen/maximized/raise_ は
        bool or None。最大化/フルスクリーンは _NET_WM_STATE を WM へ ClientMessage で要求する
        EWMH の作法（add/remove）で、XChangeProperty 直書きでなく WM に通知する。適用順は macOS と
        揃える: raise → maximize → position → size → fullscreen → minimize。

        X11 は EWMH で本物の全画面があるので fullscreen は True/False とも扱える（macOS 同様）。
        maximized は protocol/macOS と揃え True のときだけ作用させる。窓が見つからなければ
        actionable な RuntimeError。EWMH を尊重しない軽量 WM では一部の軸が効かないことがある
        （best-effort）——readback は反映を数回待って実測を正直に返す。戻り値キーは Win/macOS と
        揃える（x, y, width, height, minimized, fullscreen）。
        """
        lib = self._lib
        x = lib.x
        dpy = lib.open_display()
        try:
            root = x.XDefaultRootWindow(dpy)
            win = int(hwnd)
            if self._geometry(dpy, root, win) is None:
                raise RuntimeError(
                    f"no window with id {hwnd} (it may have closed or been re-created; "
                    "re-run list_windows to get a current id)")
            if raise_:
                self._send_active(dpy, root, win)
            if maximized:  # macOS/protocol と同じく True のときだけ最大化する
                self._set_state(dpy, root, win,
                                ("_NET_WM_STATE_MAXIMIZED_VERT", "_NET_WM_STATE_MAXIMIZED_HORZ"),
                                add=True)
            if position is not None or size is not None:
                self._move_resize(dpy, win, position, size)
            if fullscreen is not None:  # X11 は本物の全画面: True=add / False=remove
                self._set_state(dpy, root, win, ("_NET_WM_STATE_FULLSCREEN",), add=bool(fullscreen))
            if minimized is not None:
                if minimized:
                    x.XIconifyWindow(dpy, win, x.XDefaultScreen(dpy))
                else:
                    x.XMapRaised(dpy, win)
                    self._send_active(dpy, root, win)
            x.XSync(dpy, False)
            return self._read_state_settled(dpy, root, win, minimized, fullscreen)
        finally:
            x.XCloseDisplay(dpy)

    def _set_state(self, dpy, root, win: int, atom_names, add: bool) -> None:
        """_NET_WM_STATE の atom を add/remove する ClientMessage を root へ送る（EWMH の作法）。

        atom_names は 1〜2 個（最大化は VERT+HORZ の 2 個を 1 メッセージで）。data.l =
        [action(1=add/0=remove), atom1, atom2, source=1(application), 0]。
        """
        lib = self._lib
        x = lib.x
        state_atom = lib.intern(dpy, "_NET_WM_STATE")
        a1 = lib.intern(dpy, atom_names[0])
        a2 = lib.intern(dpy, atom_names[1]) if len(atom_names) > 1 else 0
        buf = (ctypes.c_long * 24)()
        ev = ctypes.cast(buf, ctypes.POINTER(_XClientMessageEvent)).contents
        ev.type = _CLIENT_MESSAGE
        ev.send_event = 1
        ev.display = dpy
        ev.window = win
        ev.message_type = state_atom
        ev.format = 32
        ev.data_l[0] = 1 if add else 0   # _NET_WM_STATE_ADD(1) / _REMOVE(0)
        ev.data_l[1] = a1
        ev.data_l[2] = a2
        ev.data_l[3] = 1                 # source indication: 1 = application
        ev.data_l[4] = 0
        mask = _SUBSTRUCTURE_NOTIFY | _SUBSTRUCTURE_REDIRECT
        x.XSendEvent(dpy, root, False, mask, ctypes.cast(buf, ctypes.c_void_p))
        x.XSync(dpy, False)

    def _move_resize(self, dpy, win: int, position, size) -> None:
        """位置/サイズを設定する。両方なら XMoveResizeWindow、片方なら Move/Resize で触らない側を残す。"""
        x = self._lib.x
        if position is not None and size is not None:
            x.XMoveResizeWindow(dpy, win, int(position[0]), int(position[1]),
                                int(size[0]), int(size[1]))
        elif position is not None:
            x.XMoveWindow(dpy, win, int(position[0]), int(position[1]))
        else:
            x.XResizeWindow(dpy, win, int(size[0]), int(size[1]))

    def _geometry(self, dpy, root, win: int):
        """窓の root 絶対座標 (x, y, w, h) を返す。窓が無効（閉じた等）なら None。"""
        x = self._lib.x
        root_ret = ctypes.c_ulong()
        gx, gy = ctypes.c_int(), ctypes.c_int()
        gw, gh = ctypes.c_uint(), ctypes.c_uint()
        bw, depth = ctypes.c_uint(), ctypes.c_uint()
        ok = x.XGetGeometry(dpy, win, ctypes.byref(root_ret), ctypes.byref(gx), ctypes.byref(gy),
                            ctypes.byref(gw), ctypes.byref(gh), ctypes.byref(bw), ctypes.byref(depth))
        if not ok:
            return None
        # XGetGeometry の x,y は親(WM フレーム)相対。root への絶対座標へ変換する。
        ax, ay = ctypes.c_int(), ctypes.c_int()
        child = ctypes.c_ulong()
        x.XTranslateCoordinates(dpy, win, root, 0, 0,
                                ctypes.byref(ax), ctypes.byref(ay), ctypes.byref(child))
        return (int(ax.value), int(ay.value), int(gw.value), int(gh.value))

    def _state_atoms(self, dpy, win: int) -> List[int]:
        prop = self._lib.get_property(dpy, win, self._lib.intern(dpy, "_NET_WM_STATE"))
        return parse_long_array(prop[3]) if prop else []

    def _read_state(self, dpy, root, win: int) -> Dict[str, Any]:
        """適用後の実測 state（geometry + minimized/fullscreen）を Win/macOS と同じ形で返す。"""
        geo = self._geometry(dpy, root, win) or (0, 0, 0, 0)
        atoms = self._state_atoms(dpy, win)
        intern = self._lib.intern
        return {
            "x": geo[0], "y": geo[1], "width": geo[2], "height": geo[3],
            # list_windows と同じく _NET_WM_STATE_HIDDEN で最小化を判定する。
            "minimized": intern(dpy, "_NET_WM_STATE_HIDDEN") in atoms,
            "fullscreen": intern(dpy, "_NET_WM_STATE_FULLSCREEN") in atoms,
        }

    def _read_state_settled(self, dpy, root, win: int, want_min, want_fs) -> Dict[str, Any]:
        """非同期反映（最小化/フルスクリーン）を少し待って読み戻す。macOS の settle と同趣旨。"""
        for _ in range(10):
            st = self._read_state(dpy, root, win)
            if ((want_min is None or st["minimized"] == want_min)
                    and (want_fs is None or st["fullscreen"] == want_fs)):
                return st
            time.sleep(0.05)
        return self._read_state(dpy, root, win)
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
