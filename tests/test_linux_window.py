"""test_linux_window.py — Wayland のウィンドウ系純パーサと WaylandWindowManager（window.py）。

X11WindowManager（EWMH）は実機 X11 でのみ動くため smoke 側で確認する。ここでは sway/Hyprland の
JSON パースとコンポジタ判定、swaymsg/hyprctl 委譲（runner フェイク）を検証する。

    python3 tests/test_linux_window.py
"""

import ctypes
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))
sys.path.insert(0, _HERE)

import linux_backends as lb  # noqa: E402
from handlers import ProcessResult  # noqa: E402
from linux.x11lib import _XClientMessageEvent  # noqa: E402
from linux_testlib import Checker, FakeRunner, with_env  # noqa: E402

c = Checker()

print("Wayland pure parsers (sway tree / hyprland clients / compositor detect):")
sway_tree = {
    "type": "root", "nodes": [
        {"type": "output", "nodes": [
            {"type": "workspace", "nodes": [
                {"type": "con", "id": 7, "name": "term", "pid": 111, "app_id": "foot"},
                {"type": "con", "id": 8, "name": None, "pid": None},  # 構造コンテナ（pidなし）
            ], "floating_nodes": [
                {"type": "floating_con", "id": 9, "name": "popup", "pid": 222,
                 "window_properties": {"class": "Dialog"}},
            ]},
        ]},
    ],
}
sw = lb.parse_sway_tree(sway_tree)
c.eq(sorted(w["hwnd"] for w in sw), [7, 9], "sway tree -> only real windows (pid leaves)")
c.eq([w for w in sw if w["hwnd"] == 7][0]["title"], "term", "sway con title from name")
c.eq([w for w in sw if w["hwnd"] == 9][0]["pid"], 222, "sway floating con pid")
hypr = lb.parse_hyprland_clients([
    {"address": "0x55aa", "title": "Editor", "pid": 333, "hidden": False},
    {"address": "0x4001", "title": "Chat", "pid": 444, "hidden": True},
    {"address": "bogus", "title": "x", "pid": 1},  # 不正アドレスは落とす
])
c.eq([w["hwnd"] for w in hypr], [0x55aa, 0x4001], "hyprland clients -> address parsed to int")
c.eq([w for w in hypr if w["hwnd"] == 0x4001][0]["minimized"], True, "hyprland hidden -> minimized")
c.eq(with_env({"SWAYSOCK": "/run/sway.sock"}, lb.wayland_compositor), "sway", "SWAYSOCK -> sway")
c.eq(with_env({"HYPRLAND_INSTANCE_SIGNATURE": "abc"}, lb.wayland_compositor), "hyprland",
     "HYPRLAND_INSTANCE_SIGNATURE -> hyprland")
c.eq(with_env({}, lb.wayland_compositor), None, "no wayland compositor env -> None")

print("WaylandWindowManager (sway/hyprland IPC via fake runner):")
swr = FakeRunner({"swaymsg": ProcessResult(0, json.dumps(sway_tree).encode(), b"")})
wm = lb.WaylandWindowManager.__new__(lb.WaylandWindowManager)
wm._runner = swr
wm._comp = "sway"
c.eq(sorted(w["hwnd"] for w in wm.list_windows(True)), [7, 9], "sway list_windows parses get_tree")
c.ok(wm.activate(7) is True, "sway activate issues a focus command")
c.eq(swr.calls[-1][0], ["swaymsg", "[con_id=7] focus"], "sway activate -> [con_id=N] focus")
hypr_json = json.dumps([{"address": "0x55aa", "title": "Editor", "pid": 333}]).encode()
hr = FakeRunner({"hyprctl": ProcessResult(0, hypr_json, b"")})
wm2 = lb.WaylandWindowManager.__new__(lb.WaylandWindowManager)
wm2._runner = hr
wm2._comp = "hyprland"
c.eq(wm2.list_windows(True)[0]["hwnd"], 0x55aa, "hyprland list_windows parses clients")
c.ok(wm2.activate(0x55aa) is True, "hyprland activate dispatches focuswindow")
c.eq(hr.calls[-1][0], ["hyprctl", "dispatch", "focuswindow", "address:0x55aa"],
     "hyprland activate -> focuswindow address:0x..")

# --- X11WindowManager.set_window（EWMH ClientMessage を fake x11lib で検証）----------
# 実 X11 の geometry 読み戻し（XGetGeometry の byref 充填）は smoke 側で確認するので、ここでは
# _geometry / _state_atoms をスタブして「各軸が正しい EWMH メッセージ/API を出すか」を固める。
print("X11WindowManager.set_window (EWMH ClientMessage via fake x11lib):")

_ATOMS = {
    "_NET_ACTIVE_WINDOW": 101, "_NET_WM_STATE": 102,
    "_NET_WM_STATE_MAXIMIZED_VERT": 103, "_NET_WM_STATE_MAXIMIZED_HORZ": 104,
    "_NET_WM_STATE_FULLSCREEN": 105, "_NET_WM_STATE_HIDDEN": 106,
}


class FakeX:
    def __init__(self):
        self.sent = []    # XSendEvent で送られた ClientMessage（デコード済み）
        self.calls = []   # その他 X 呼び出し（move/resize/iconify/map/raise）

    def XDefaultRootWindow(self, dpy):
        return 0x1

    def XDefaultScreen(self, dpy):
        return 0

    def XSync(self, dpy, flag):
        pass

    def XCloseDisplay(self, dpy):
        pass

    def XRaiseWindow(self, dpy, win):
        self.calls.append(("XRaiseWindow", int(win)))

    def XSendEvent(self, dpy, root, propagate, mask, event_ptr):
        # buf がまだ生きているこの場でデコードして取り込む（戻り後は void_p がダングルしうる）。
        ev = ctypes.cast(event_ptr, ctypes.POINTER(_XClientMessageEvent)).contents
        self.sent.append({"window": int(ev.window), "message_type": int(ev.message_type),
                          "data": [int(ev.data_l[i]) for i in range(5)]})
        return 1

    def XMoveResizeWindow(self, dpy, win, x, y, w, h):
        self.calls.append(("XMoveResizeWindow", int(win), x, y, w, h))

    def XMoveWindow(self, dpy, win, x, y):
        self.calls.append(("XMoveWindow", int(win), x, y))

    def XResizeWindow(self, dpy, win, w, h):
        self.calls.append(("XResizeWindow", int(win), w, h))

    def XIconifyWindow(self, dpy, win, screen):
        self.calls.append(("XIconifyWindow", int(win), screen))

    def XMapRaised(self, dpy, win):
        self.calls.append(("XMapRaised", int(win)))


class FakeX11Lib:
    def __init__(self):
        self.x = FakeX()

    def open_display(self):
        return 1

    def intern(self, dpy, name):
        return _ATOMS[name]

    def get_property(self, dpy, win, prop, req_type=0):
        return None  # readback は _state_atoms をスタブするので未使用


def _mk_wm(geometry=(10, 20, 300, 200), atoms=None):
    wm = lb.X11WindowManager.__new__(lb.X11WindowManager)
    wm._lib = FakeX11Lib()
    wm._geometry = lambda dpy, root, win: geometry
    wm._state_atoms = lambda dpy, win: list(atoms or [])
    return wm


# move+resize + maximize(True) + raise(True): 各軸が対応する EWMH/API を出すか
wm = _mk_wm()
st = wm.set_window(0xABC, position=(100, 150), size=(640, 480), maximized=True, raise_=True)
fx = wm._lib.x
c.eq([m for m in fx.sent if m["message_type"] == 101][0]["data"][0], 2,
     "raise -> _NET_ACTIVE_WINDOW ClientMessage with source=2 (pager)")
mx = [m for m in fx.sent if m["message_type"] == 102][0]
c.eq((mx["data"][0], mx["data"][1], mx["data"][2], mx["data"][3]), (1, 103, 104, 1),
     "maximize -> _NET_WM_STATE add(1) VERT+HORZ, source=1 (application)")
mr = [c2 for c2 in fx.calls if c2[0] == "XMoveResizeWindow"][-1]
c.eq(mr[1:], (0xABC, 100, 150, 640, 480), "position+size -> XMoveResizeWindow(win, x,y,w,h)")
c.eq(st, {"x": 10, "y": 20, "width": 300, "height": 200, "minimized": False, "fullscreen": False},
     "readback returns geometry + minimized/fullscreen (keys aligned with Win/macOS)")

# position-only / size-only -> Move / Resize（触らない側を残す）
wm = _mk_wm()
wm.set_window(0xABC, position=(5, 6))
c.eq([c2 for c2 in wm._lib.x.calls if c2[0] == "XMoveWindow"][-1][1:], (0xABC, 5, 6),
     "position-only -> XMoveWindow")
wm = _mk_wm()
wm.set_window(0xABC, size=(800, 600))
c.eq([c2 for c2 in wm._lib.x.calls if c2[0] == "XResizeWindow"][-1][1:], (0xABC, 800, 600),
     "size-only -> XResizeWindow")

# fullscreen True=add / False=remove（X11 は本物の全画面なので両方扱える）
wm = _mk_wm(geometry=(0, 0, 0, 0), atoms=[105])  # FULLSCREEN 在 -> settle 即時
wm.set_window(0xABC, fullscreen=True)
fsm = [m for m in wm._lib.x.sent if m["message_type"] == 102][-1]
c.eq((fsm["data"][0], fsm["data"][1]), (1, 105), "fullscreen=True -> _NET_WM_STATE add FULLSCREEN")
wm = _mk_wm(geometry=(0, 0, 0, 0), atoms=[])    # FULLSCREEN 無し -> want False で settle 即時
wm.set_window(0xABC, fullscreen=False)
fsm = [m for m in wm._lib.x.sent if m["message_type"] == 102][-1]
c.eq((fsm["data"][0], fsm["data"][1]), (0, 105), "fullscreen=False -> _NET_WM_STATE remove FULLSCREEN")

# minimize -> XIconifyWindow / restore -> XMapRaised + 再 activate
wm = _mk_wm(atoms=[106])   # HIDDEN 在 -> minimized True で settle 即時
wm.set_window(0xABC, minimized=True)
c.ok(any(c2[0] == "XIconifyWindow" and c2[1] == 0xABC for c2 in wm._lib.x.calls),
     "minimized=True -> XIconifyWindow")
wm = _mk_wm(atoms=[])      # HIDDEN 無し -> minimized False で settle 即時
wm.set_window(0xABC, minimized=False)
c.ok(any(c2[0] == "XMapRaised" and c2[1] == 0xABC for c2 in wm._lib.x.calls),
     "minimized=False -> XMapRaised (restore)")
c.ok(any(m["message_type"] == 101 for m in wm._lib.x.sent),
     "restore also re-activates (_NET_ACTIVE_WINDOW)")

# 窓が見つからない（_geometry が None）-> actionable な RuntimeError
wm = lb.X11WindowManager.__new__(lb.X11WindowManager)
wm._lib = FakeX11Lib()
wm._geometry = lambda dpy, root, win: None
raised_x = None
try:
    wm.set_window(54321, minimized=True)
except RuntimeError as exc:
    raised_x = str(exc)
c.ok(raised_x is not None and "54321" in raised_x,
     "missing window raises an actionable RuntimeError naming the id")

c.done()
