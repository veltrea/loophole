"""test_linux_window.py — Wayland のウィンドウ系純パーサと WaylandWindowManager（window.py）。

X11WindowManager（EWMH）は実機 X11 でのみ動くため smoke 側で確認する。ここでは sway/Hyprland の
JSON パースとコンポジタ判定、swaymsg/hyprctl 委譲（runner フェイク）を検証する。

    python3 tests/test_linux_window.py
"""

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))
sys.path.insert(0, _HERE)

import linux_backends as lb  # noqa: E402
from handlers import ProcessResult  # noqa: E402
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

c.done()
