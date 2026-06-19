"""test_linux_build.py — build_handlers のディスパッチ（linux/__init__.py の配線）。

ディスプレイ未検出・X11 ライブラリ不在（Mac）でも agent を壊さず UnsupportedBackend に倒し、
backend に触れないコマンド（ping/hello）は通り、GUI 系は actionable に失敗することを確認する。

    python3 tests/test_linux_build.py
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))
sys.path.insert(0, _HERE)

import linux_backends as lb  # noqa: E402
from linux_testlib import Checker, with_env  # noqa: E402

c = Checker()

print("build_handlers dispatch (Handlers built; GUI degrades gracefully on Mac):")
h = with_env({"DISPLAY": ":0"}, lb.build_handlers)
# ping は backend に触れない → 通る。clipboard は ShellClipboard が刺さる。
c.eq(h.dispatch("ping", {}), {"pong": True}, "handlers usable; ping works")
# キーボードは Mac では Unsupported に倒れている → send_keys が actionable に失敗する。
raised = None
try:
    h.dispatch("send_keys", {"keys": "ctrl+s"})
except Exception as exc:
    raised = str(exc)
c.ok(raised is not None, "send_keys raises (X11 unavailable here) instead of silently no-op")
# hello は環境情報を返す（全プラットフォームで通る）。
c.ok("platform" in h.dispatch("hello", {}), "hello returns environment info on any platform")

c.done()
