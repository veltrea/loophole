"""smoke_linux_x11.py — 実 X11（Xvfb + openbox）で linux_backends を端から端まで叩く。

Mac では回せない ctypes 直叩き部分（XGetImage / XTEST / EWMH / XSendEvent）を、仮想 X
サーバー上で実際に動かして検証する。リポジトリ常設の単体テストではなく、Linux 実機/VM で
手動またはオペレータ手順から走らせるスモーク（test_win_backends が Windows 実機スモークを
ドキュメントしているのと同じ位置づけ）。

実行（linux-vm 等で）:
    xvfb-run -a -s "-screen 0 1280x800x24" bash -c 'openbox & sleep 1.5; python3 tests/smoke_linux_x11.py'
"""

import base64
import os
import struct
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))

import backends  # noqa: E402

fail = 0


def check(cond, label):
    global fail
    print(("  [PASS] " if cond else "  [FAIL] ") + label)
    if not cond:
        fail += 1


h = backends.build_handlers()

print("hello (Linux session info):")
env = h.dispatch("hello", {})
print("  ->", env)
check(env.get("platform", "").startswith("linux"), "platform is linux")
check(env.get("display_server") == "x11", "display_server detected as x11")
check(env.get("interactive") is True, "interactive=True (GUI reachable)")

print("screenshot (XGetImage -> imaging PNG):")
r = h.dispatch("screenshot", {"data": True})
png = base64.b64decode(r["png_base64"])
check(png[:8] == b"\x89PNG\r\n\x1a\n", "PNG signature present")
check(r["bytes"] > 1000, f"plausible size ({r['bytes']} bytes)")
w, hh = struct.unpack(">II", png[16:24])
print("  -> dims", w, "x", hh)
check(w >= 100 and hh >= 100, "IHDR dimensions look like the virtual screen")

print("clipboard (xclip round-trip, dame-moji):")
h.dispatch("clipboard_set", {"text": "検証_表予能ソ_①㈱"})
got = h.dispatch("clipboard_get", {})["text"]
check(got == "検証_表予能ソ_①㈱", "clipboard round-trips UTF-8 exactly")

print("send_keys (XTEST: keysym->keycode + fake input, must not raise):")
try:
    res = h.dispatch("send_keys", {"keys": "ctrl+s"})
    check(res.get("count") == 1, "ctrl+s sent as 1 stroke via XTEST")
    res2 = h.dispatch("send_keys", {"keys": "win+r enter alt+f4 up tab"})
    check(res2.get("count") == 5, "multi-stroke sequence all resolve to keycodes")
except Exception as exc:
    check(False, f"send_keys raised: {exc!r}")

print("window manager (EWMH: spawn xterm -> list -> activate):")
h.dispatch("spawn", {"argv": ["xterm", "-T", "loophole_smoke_win", "-e", "sleep 60"]})
time.sleep(2.5)
lw = h.dispatch("list_windows", {"pattern": "loophole_smoke_win"})
print("  ->", lw)
check(lw["count"] >= 1, "list_windows finds the spawned xterm via _NET_CLIENT_LIST")
if lw["count"] >= 1:
    win = lw["windows"][0]
    check(win.get("pid", 0) > 0, "window reports a pid (_NET_WM_PID)")
    aw = h.dispatch("activate_window", {"hwnd": win["hwnd"]})
    print("  ->", aw)
    check(aw.get("activated") is True, "activate_window raises it to the front")

print()
print("RESULT:", "ALL PASS" if fail == 0 else f"{fail} FAILURE(S)")
sys.exit(0 if fail == 0 else 1)
