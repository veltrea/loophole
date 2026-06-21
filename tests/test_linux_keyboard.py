"""test_linux_keyboard.py — キー送出 backend（keyboard.py）の Mac 検証分。

X11Keyboard（XTEST）は実機 X11 でのみ動くため smoke 側で確認する。VK→evdev / VK→keysym の表は
test_keys.py が検証する。ここでは WaylandKeyboard（ydotool）の argv 組み立てを runner フェイクで検証。

    python3 tests/test_linux_keyboard.py
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))
sys.path.insert(0, _HERE)

import linux_backends as lb  # noqa: E402
from linux.keyboard import _char_to_keysym  # noqa: E402
from handlers import ProcessResult  # noqa: E402
from linux_testlib import Checker, FakeRunner  # noqa: E402

c = Checker()

print("WaylandKeyboard (ydotool evdev sequence):")
kr = FakeRunner({"ydotool": ProcessResult(0, b"", b"")})
lb.WaylandKeyboard(kr).send_chord([0x11], 0x53)  # ctrl+s -> KEY_LEFTCTRL(29), KEY_S(31)
c.eq(kr.calls[-1][0], ["ydotool", "key", "29:1", "31:1", "31:0", "29:0"],
     "ctrl+s -> press ctrl, press/release s, release ctrl (evdev codes)")
raised = None
try:
    lb.WaylandKeyboard(FakeRunner({})).send_chord([], 0x41)
except RuntimeError as exc:
    raised = str(exc)
c.ok(raised is not None and "ydotool" in raised, "missing ydotool -> actionable error")
c.ok(raised is not None and "apt install ydotool" in raised,
     "missing ydotool -> install-from-package hint (T2)")

print("WaylandKeyboard (ydotool fails at runtime -> diagnose() hints flow through):")
runner = FakeRunner({
    "ydotool": ProcessResult(1, b"", b"socket connect failed"),
    "pgrep":   ProcessResult(1, b"", b""),  # ydotoold not running
})
raised = None
try:
    lb.WaylandKeyboard(runner).send_chord([], 0x41)
except RuntimeError as exc:
    raised = str(exc)
c.ok(raised is not None and "ydotoold is not running" in raised,
     "ydotool exit!=0 + pgrep=miss -> daemon hint surfaces in send_keys error")

print("WaylandKeyboard.type_text (ydotool type):")
kr = FakeRunner({"ydotool": ProcessResult(0, b"", b"")})
lb.WaylandKeyboard(kr).type_text("hi -x")
c.eq(kr.calls[-1][0], ["ydotool", "type", "--", "hi -x"],
     "type_text -> `ydotool type -- <text>` (`--` keeps leading-dash text safe)")
raised = None
try:
    lb.WaylandKeyboard(FakeRunner({})).type_text("hi")
except RuntimeError as exc:
    raised = str(exc)
c.ok(raised is not None and "type_text" in raised and "ydotool" in raised,
     "missing ydotool -> actionable type_text error")

# X11 の type_text（空きキーコード再マップ）は実機 X11 でのみ動くので smoke 側で確認する。
# 文字→keysym の純変換だけここで押さえる（Latin-1 は同値、それ以外は 0x01000000|cp）。
print("_char_to_keysym (Unicode keysym rule):")
c.eq(_char_to_keysym("A"), 0x41, "ASCII 'A' -> keysym 0x41")
c.eq(_char_to_keysym("é"), 0xE9, "Latin-1 'é' (U+00E9) -> keysym 0x00E9")
c.eq(_char_to_keysym("あ"), 0x3042 | 0x01000000,
     "'あ' (U+3042) -> Unicode keysym 0x0100_3042")

c.done()
