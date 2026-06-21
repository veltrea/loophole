"""test_darwin_window.py — Mac の AX/CGWindowID window backend のフェイク注入テスト。

実 ctypes（axlib）は呼ばず、フェイク axlib を注入して AXWindowManager のロジックを検証する:
- list_windows が CGWindowID ベースの dict（geometry 込み）を返す / visible_only でタイトル空を落とす
- Accessibility 未許可なら actionable エラー（黙って空を返さない）
- activate が raise_window に委譲する
- set_window が各軸を axlib に委譲し、適用後 state を返す / 窓が無ければ actionable エラー

実 AX/CGWindowID の挙動（-1719 が消えること等）は mini の実機 smoke 側で確認する。
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))
sys.path.insert(0, _HERE)

from darwin.window import AXWindowManager, build_window  # noqa: E402
from linux_testlib import Checker  # noqa: E402

c = Checker()


class FakeAx:
    """axlib モジュールの代役。AXWindowManager が呼ぶ関数だけ持つ。"""

    def __init__(self):
        self.trusted = True
        self.windows = []
        self.raised = []
        self.set_calls = []
        # set_window の戻り値。None にすると「窓が見つからない」を模せる。
        self.set_result = {"x": 0, "y": 0, "width": 0, "height": 0,
                           "minimized": False, "fullscreen": False}

    def is_process_trusted(self):
        return self.trusted

    def list_windows(self):
        return [dict(w) for w in self.windows]

    def raise_window(self, hwnd):
        self.raised.append(hwnd)
        return True

    def set_window(self, hwnd, position=None, size=None, minimized=None,
                   fullscreen=None, maximized=False, do_raise=False):
        self.set_calls.append({"hwnd": hwnd, "position": position, "size": size,
                               "minimized": minimized, "fullscreen": fullscreen,
                               "maximized": maximized, "do_raise": do_raise})
        return self.set_result


def _win(hwnd, title, pid=100, minimized=False, x=0, y=0, w=800, h=600):
    return {"hwnd": hwnd, "title": title, "pid": pid, "minimized": minimized,
            "x": x, "y": y, "width": w, "height": h}


# --- list_windows --------------------------------------------------------
print("AXWindowManager.list_windows():")
ax = FakeAx()
ax.windows = [
    _win(45, "Editor", x=10, y=20, w=900, h=640),
    _win(0, "", pid=200),            # タイトル空 → visible_only で落ちる
    _win(75, "Notes", pid=300, minimized=True),
]
wm = AXWindowManager(ax=ax)
got = wm.list_windows(visible_only=True)
c.eq(len(got), 2, "visible_only drops the title-empty window")
c.eq(got[0]["hwnd"], 45, "hwnd is the CGWindowID")
c.eq(got[0]["title"], "Editor", "title passed through")
c.eq((got[0]["x"], got[0]["y"], got[0]["width"], got[0]["height"]), (10, 20, 900, 640),
     "geometry is included in the listing")
c.eq(got[1]["minimized"], True, "minimized flag passed through")
c.eq(len(wm.list_windows(visible_only=False)), 3, "visible_only=False keeps the empty-title window")

# Accessibility 未許可 → actionable エラー（黙って空を返さない）
ax.trusted = False
raised = None
try:
    wm.list_windows(True)
except RuntimeError as exc:
    raised = str(exc)
c.ok(raised is not None and "Accessibility" in raised,
     "untrusted process raises an Accessibility hint")


# --- activate（窓単位 raise に委譲）-------------------------------------
print("AXWindowManager.activate():")
ax = FakeAx()
wm = AXWindowManager(ax=ax)
ok = wm.activate(75)
c.ok(ok, "activate returns True")
c.eq(ax.raised[-1], 75, "activate delegates to raise_window with the CGWindowID")


# --- set_window（各軸を axlib に委譲）----------------------------------
print("AXWindowManager.set_window():")
ax = FakeAx()
ax.set_result = {"x": 250, "y": 180, "width": 900, "height": 600,
                 "minimized": False, "fullscreen": False}
wm = AXWindowManager(ax=ax)
st = wm.set_window(75, position=(250, 180), size=(900, 600))
c.eq(st["x"], 250, "returns the applied state from axlib")
call = ax.set_calls[-1]
c.eq(call["hwnd"], 75, "passes the CGWindowID")
c.eq(call["position"], (250, 180), "passes position")
c.eq(call["size"], (900, 600), "passes size")

# 新軸（maximized / raise_）の委譲
wm.set_window(75, maximized=True, raise_=True)
call = ax.set_calls[-1]
c.eq(call["maximized"], True, "maximized passed through")
c.eq(call["do_raise"], True, "raise_ passed through as do_raise")

# 窓が見つからない（axlib が None）→ actionable エラー
ax.set_result = None
raised = None
try:
    wm.set_window(999, minimized=True)
except RuntimeError as exc:
    raised = str(exc)
c.ok(raised is not None and "999" in raised, "missing window raises an actionable error")

# 未許可 → set もエラー
ax.trusted = False
raised = None
try:
    wm.set_window(75, minimized=True)
except RuntimeError as exc:
    raised = str(exc)
c.ok(raised is not None and "Accessibility" in raised, "untrusted set_window raises a hint")


# --- build_window factory ------------------------------------------------
print("build_window():")
wm = build_window(runner=None)
c.ok(isinstance(wm, AXWindowManager), "factory returns AXWindowManager")

c.done()
