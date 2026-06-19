"""test_linux_clipboard.py — クリップボード backend（clipboard.py）の Mac で検証できる契約。

X11Clipboard（プロセス内セレクション所有・INCR）は実機 X11 でのみ動くため smoke 側で確認する。
ここでは clipboard_commands のツール選択、ShellClipboard の委譲、build_clipboard のフォールバックを検証。

    python3 tests/test_linux_clipboard.py
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))
sys.path.insert(0, _HERE)

import linux_backends as lb  # noqa: E402
from handlers import ProcessResult  # noqa: E402
from linux_testlib import Checker, FakeRunner  # noqa: E402

c = Checker()

print("clipboard_commands (tool choice by display server / mode):")
c.eq(lb.clipboard_commands("x11", "get"),
     [["xclip", "-selection", "clipboard", "-o"], ["xsel", "--clipboard", "--output"]],
     "x11 get -> xclip then xsel")
c.eq(lb.clipboard_commands("x11", "set"),
     [["xclip", "-selection", "clipboard", "-i"], ["xsel", "--clipboard", "--input"]],
     "x11 set -> xclip then xsel")
c.eq(lb.clipboard_commands("wayland", "get"), [["wl-paste", "--no-newline"]],
     "wayland get -> wl-paste")
c.eq(lb.clipboard_commands("wayland", "set"), [["wl-copy"]], "wayland set -> wl-copy")
none_get = lb.clipboard_commands(None, "get")
c.ok(none_get[0][0] == "xclip" and ["wl-paste", "--no-newline"] in none_get,
     "unknown server tries X11 first then Wayland")

print("ShellClipboard.get (delegates to xclip/xsel; empty + missing-tool handling):")
cb_ok = lb.ShellClipboard("x11", FakeRunner({"xclip": ProcessResult(0, b"hello", b"")}))
c.eq(cb_ok.get(), "hello", "xclip stdout returned as text")
fr = FakeRunner({"xsel": ProcessResult(0, b"world", b"")})
c.eq(lb.ShellClipboard("x11", fr).get(), "world", "falls back to xsel when xclip missing")
c.eq(fr.calls[0][0][0], "xclip", "tried xclip first")
c.eq(fr.calls[1][0][0], "xsel", "then xsel")
raised = None
try:
    lb.ShellClipboard("x11", FakeRunner({})).get()
except RuntimeError as exc:
    raised = str(exc)
c.ok(raised is not None and "xclip" in raised, "no clipboard tool -> actionable RuntimeError")

print("ShellClipboard.set (passes text on stdin, returns on exit 0):")
fr2 = FakeRunner({"xclip": ProcessResult(0, b"", b"")})
lb.ShellClipboard("x11", fr2).set("表予能 ソ")
c.eq(fr2.calls[-1][0], ["xclip", "-selection", "clipboard", "-i"], "set uses xclip -i")
c.eq(fr2.calls[-1][1], "表予能 ソ", "the text is handed to the tool via stdin")
raised2 = None
try:
    lb.ShellClipboard("wayland", FakeRunner({})).set("x")
except RuntimeError as exc:
    raised2 = str(exc)
c.ok(raised2 is not None and "wl-clipboard" in raised2, "no Wayland tool -> actionable error")

print("build_clipboard dispatch (X11 prefers in-process; degrades to xclip off-Linux):")
# Mac では X11Clipboard の構築が失敗する（libX11 無し）→ ShellClipboard に倒れる。
c.ok(isinstance(lb.build_clipboard("x11", FakeRunner()), lb.ShellClipboard),
     "x11 off-Linux -> falls back to ShellClipboard (xclip)")
c.ok(isinstance(lb.build_clipboard("wayland", FakeRunner()), lb.ShellClipboard),
     "wayland -> ShellClipboard (wl-clipboard)")

# X11Clipboard の PRIMARY 配線（実機 X が要る所有/配信は smoke 側で確認）。ここでは
# Mac でも確かめられる定数・構造体の契約だけを固定して退行を防ぐ。
print("X11Clipboard PRIMARY wiring (Mac-checkable constants/struct contracts):")
import ctypes  # noqa: E402
from linux.clipboard import _XA_PRIMARY, _XSelectionClearEvent  # noqa: E402

c.eq(_XA_PRIMARY, 1, "XA_PRIMARY is the predefined atom 1 (middle-click selection)")
# SelectionClear イベントから selection フィールドを読めること（どちらを失ったか判定する）。
fields = [n for n, _ in _XSelectionClearEvent._fields_]
c.ok("selection" in fields, "_XSelectionClearEvent exposes the selection field")
c.eq(fields[:6],
     ["type", "serial", "send_event", "display", "window", "selection"],
     "SelectionClear field order matches XSelectionClearEvent (selection at offset 5)")
c.ok(_XSelectionClearEvent._fields_[3][1] is ctypes.c_void_p,
     "display is pointer-width (64bit-safe layout)")

c.done()
