"""test_linux_mouse.py — マウス backend（mouse.py）の Mac で検証できる契約。

X11Mouse（XTEST）は実機 X11 でのみ動くため smoke 側で確認する。ここでは WaylandMouse の
ydotool argv 組み立て（runner フェイク）と build_mouse のディスパッチを検証する。

    python3 tests/test_linux_mouse.py
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))
sys.path.insert(0, _HERE)

import linux_backends as lb  # noqa: E402
from handlers import ProcessResult  # noqa: E402
from linux_testlib import Checker, FakeRunner, with_env  # noqa: E402

c = Checker()
_OK = {"ydotool": ProcessResult(0, b"", b"")}

print("WaylandMouse (ydotool argv for move / button / scroll):")
m = FakeRunner(dict(_OK))
lb.WaylandMouse(m).move(640, 360)
c.eq(m.calls[-1][0], ["ydotool", "mousemove", "--absolute", "--", "640", "360"],
     "move -> ydotool mousemove --absolute x y")
m = FakeRunner(dict(_OK))
lb.WaylandMouse(m).button(1, True)   # 左ボタン押下 = 0x40|0x00 = 0x40
c.eq(m.calls[-1][0], ["ydotool", "click", "0x40"], "left down -> click 0x40")
m = FakeRunner(dict(_OK))
lb.WaylandMouse(m).button(3, False)  # 右ボタン解放 = 0x80|0x01 = 0x81
c.eq(m.calls[-1][0], ["ydotool", "click", "0x81"], "right up -> click 0x81")
m = FakeRunner(dict(_OK))
lb.WaylandMouse(m).scroll(0, 3)
c.eq(m.calls[-1][0], ["ydotool", "mousemove", "--wheel", "--", "0", "3"],
     "scroll dy -> ydotool mousemove --wheel 0 3")
# ydotool 不在 -> actionable error
raised = None
try:
    lb.WaylandMouse(FakeRunner({})).move(1, 1)
except RuntimeError as exc:
    raised = str(exc)
c.ok(raised is not None and "ydotool" in raised, "missing ydotool -> actionable error")
c.ok(raised is not None and "apt install ydotool" in raised,
     "missing ydotool -> install-from-package hint (T2)")

print("WaylandMouse (ydotool fails at runtime -> diagnose() hints flow through):")
runner = FakeRunner({
    "ydotool": ProcessResult(1, b"", b"permission denied"),
    "pgrep":   ProcessResult(0, b"4321\n", b""),  # ydotoold running -> daemon hint suppressed
})
raised = None
try:
    lb.WaylandMouse(runner).move(0, 0)
except RuntimeError as exc:
    raised = str(exc)
c.ok(raised is not None and "ydotoold is not running" not in raised,
     "ydotoold up -> no spurious 'daemon not running' hint in mouse error")

print("build_mouse dispatch (no crash on Mac / missing display):")
c.ok(isinstance(with_env({}, lambda: lb.build_mouse(None, FakeRunner())), lb.UnsupportedBackend),
     "no display -> UnsupportedBackend")
# X11 を要求しても Mac では libXtst が無く _try が UnsupportedBackend に倒す（落ちない）。
c.ok(isinstance(lb.build_mouse("x11", FakeRunner()), lb.UnsupportedBackend),
     "x11 requested off-Linux -> degrades, not crash")
c.ok(isinstance(lb.build_mouse("wayland", FakeRunner()), lb.WaylandMouse),
     "wayland -> WaylandMouse")

c.done()
