"""test_darwin_mouse.py — Mac の CGEventMouse のフェイク注入テスト。

実 CoreGraphics は触らず、cg/cf 関数群をフェイクで差し替えて契約を検証する:
- move(x, y) が CGEventCreateMouseEvent(MouseMoved, (x,y), Left) → Post を 1 回
- button(1, True) が LeftMouseDown を Post、button(3, False) が RightMouseUp を Post
- 未知のボタン番号は actionable RuntimeError
- scroll(dx, dy) は dy/dx 符号を反転して 2 軸 LineUnit で 1 イベント Post
- 全経路で source / event が CFRelease される
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))
sys.path.insert(0, _HERE)

from darwin.cglib import (  # noqa: E402
    ET_LEFT_MOUSE_DOWN, ET_LEFT_MOUSE_UP, ET_MOUSE_MOVED, ET_OTHER_MOUSE_DOWN,
    ET_RIGHT_MOUSE_DOWN, ET_RIGHT_MOUSE_UP, MOUSE_LEFT, MOUSE_RIGHT, SCROLL_UNIT_LINE,
)
from linux_testlib import Checker  # noqa: E402

c = Checker()


class _FakeCG:
    def __init__(self):
        self._next = 0x3000
        self.log = []

    def _hand(self):
        self._next += 1
        return self._next

    def CGEventSourceCreate(self, state):
        h = self._hand()
        self.log.append(("CGEventSourceCreate", state, h))
        return h

    def CGEventCreateMouseEvent(self, source, event_type, point, button):
        h = self._hand()
        # point は CGPoint structure。x, y を読んでログに残す。
        self.log.append(("CGEventCreateMouseEvent", source, event_type,
                         (point.x, point.y), button, h))
        return h

    def CGEventCreateScrollWheelEvent(self, source, unit, axes, w1, w2):
        h = self._hand()
        self.log.append(("CGEventCreateScrollWheelEvent", source, unit, axes, w1, w2, h))
        return h

    def CGEventPost(self, tap, event):
        self.log.append(("CGEventPost", tap, event))


class _FakeCF:
    def __init__(self):
        self.released = []

    def CFRelease(self, handle):
        self.released.append(handle)


class _FakeLib:
    def __init__(self, cg, cf):
        self.cg = cg
        self.cf = cf


def _make_mouse(cg, cf):
    from darwin.mouse import CGEventMouse
    m = CGEventMouse.__new__(CGEventMouse)
    m._lib = _FakeLib(cg, cf)
    return m


# --- move ----------------------------------------------------------------
print("CGEventMouse.move():")
cg = _FakeCG()
cf = _FakeCF()
m = _make_mouse(cg, cf)
m.move(123, 456)

names = [e[0] for e in cg.log]
c.eq(names, ["CGEventSourceCreate", "CGEventCreateMouseEvent", "CGEventPost"],
     "move: source + event + post")
src = cg.log[0][2]
ev = cg.log[1][5]
c.eq(cg.log[1][2], ET_MOUSE_MOVED, "event type = MouseMoved")
c.eq(cg.log[1][3], (123.0, 456.0), "point passed as (x, y) floats")
c.eq(cg.log[1][4], MOUSE_LEFT, "button arg defaults to Left (ignored for moves)")
c.eq(cg.log[2], ("CGEventPost", 0, ev), "posted to HID tap")
c.eq(cf.released, [ev, src], "CFRelease event then source")

# --- button down/up -----------------------------------------------------
print("CGEventMouse.button():")
cg = _FakeCG()
cf = _FakeCF()
m = _make_mouse(cg, cf)
m.button(1, True)
c.eq(cg.log[1][2], ET_LEFT_MOUSE_DOWN, "button(1, True) = LeftMouseDown")
m.button(1, False)
c.eq(cg.log[4][2], ET_LEFT_MOUSE_UP, "button(1, False) = LeftMouseUp")

cg = _FakeCG()
cf = _FakeCF()
m = _make_mouse(cg, cf)
m.button(3, True)
c.eq(cg.log[1][2], ET_RIGHT_MOUSE_DOWN, "button(3, True) = RightMouseDown")
c.eq(cg.log[1][4], MOUSE_RIGHT, "button(3) uses MOUSE_RIGHT")
m.button(3, False)
c.eq(cg.log[4][2], ET_RIGHT_MOUSE_UP, "button(3, False) = RightMouseUp")

cg = _FakeCG()
cf = _FakeCF()
m = _make_mouse(cg, cf)
m.button(2, True)
c.eq(cg.log[1][2], ET_OTHER_MOUSE_DOWN, "button(2, True) = OtherMouseDown (middle)")

# 不正なボタン
cg = _FakeCG()
cf = _FakeCF()
m = _make_mouse(cg, cf)
raised = None
try:
    m.button(7, True)
except RuntimeError as exc:
    raised = str(exc)
c.ok(raised is not None and "1/2/3" in raised, "unknown button raises actionable error")

# --- scroll --------------------------------------------------------------
print("CGEventMouse.scroll():")
cg = _FakeCG()
cf = _FakeCF()
m = _make_mouse(cg, cf)
m.scroll(0, 3)  # 下方向 3 クリック
c.eq(cg.log[1][0], "CGEventCreateScrollWheelEvent", "scroll uses scroll-wheel event")
c.eq(cg.log[1][2], SCROLL_UNIT_LINE, "unit = Line")
c.eq(cg.log[1][3], 2, "axes = 2")
# handlers の意味: dy>0 で下方向 → macOS の wheel1 は上正なので -3 が下方向
c.eq(cg.log[1][4], -3, "wheel1 = -dy (sign-flipped to match handlers' 'down is positive')")
c.eq(cg.log[1][5], 0, "wheel2 = -dx = 0 (no horizontal)")

cg = _FakeCG()
cf = _FakeCF()
m = _make_mouse(cg, cf)
m.scroll(2, -1)  # 右 2, 上 1
c.eq(cg.log[1][4], 1, "scroll(_, -1) → wheel1 = 1 (upward)")
c.eq(cg.log[1][5], -2, "scroll(2, _) → wheel2 = -2 (rightward, handlers says dx>0 means right)")

# --- NULL source -----------------------------------------------------------
print("NULL source paths:")


class _NullSourceCG(_FakeCG):
    def CGEventSourceCreate(self, state):
        self.log.append(("CGEventSourceCreate", state, 0))
        return 0


cg = _NullSourceCG()
cf = _FakeCF()
m = _make_mouse(cg, cf)
for label, fn in [
    ("move", lambda: m.move(1, 1)),
    ("button down", lambda: m.button(1, True)),
    ("scroll", lambda: m.scroll(0, 1)),
]:
    raised = None
    try:
        fn()
    except RuntimeError as exc:
        raised = str(exc)
    c.ok(raised is not None and "Accessibility" in raised,
         f"{label} raises with Accessibility hint when source is NULL")

c.done()
