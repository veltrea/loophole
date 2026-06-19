"""test_darwin_screenshot.py — Mac の screencapture backend のフェイク注入テスト。

実 screencapture は呼ばず、FakeRunner で argv と PNG 戻り値の契約を検証する。
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))
sys.path.insert(0, _HERE)

from darwin.screenshot import ScreencaptureScreenshotter, build_screenshotter  # noqa: E402
from handlers import ProcessResult  # noqa: E402
from linux_testlib import Checker, FakeRunner  # noqa: E402

c = Checker()

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_PNG_BODY = _PNG_MAGIC + b"\x00" * 32  # ヘッダだけのダミー PNG


def _ok(stdout=b"", stderr=b"", code=0):
    return ProcessResult(code, stdout, stderr, started=True)


def _fail():
    return ProcessResult(-1, b"", b"", started=False)


print("ScreencaptureScreenshotter.capture():")

# 正常系
runner = FakeRunner({"screencapture": _ok(stdout=_PNG_BODY)})
ss = ScreencaptureScreenshotter(runner=runner)
out = ss.capture()
c.eq(out, _PNG_BODY, "returns the PNG bytes from screencapture stdout")
c.eq(runner.calls[-1][0], ["screencapture", "-x", "-t", "png", "-"],
     "calls screencapture with -x -t png - (silent / PNG / stdout)")

# screencapture が無い PATH
runner = FakeRunner({})
ss = ScreencaptureScreenshotter(runner=runner)
raised = None
try:
    ss.capture()
except RuntimeError as exc:
    raised = str(exc)
c.ok(raised is not None and "screencapture" in raised, "missing tool raises actionable error")

# 非 0 exit
runner = FakeRunner({"screencapture": _ok(stderr=b"perm denied", code=1)})
ss = ScreencaptureScreenshotter(runner=runner)
raised = None
try:
    ss.capture()
except RuntimeError as exc:
    raised = str(exc)
c.ok(raised is not None and "exit=1" in raised and "Screen Recording" in raised,
     "non-zero exit raises with TCC hint")

# stdout 空（権限が無い時の典型）
runner = FakeRunner({"screencapture": _ok(stdout=b"")})
ss = ScreencaptureScreenshotter(runner=runner)
raised = None
try:
    ss.capture()
except RuntimeError as exc:
    raised = str(exc)
c.ok(raised is not None and "Screen Recording" in raised, "empty output raises with TCC hint")

# PNG ではない出力
runner = FakeRunner({"screencapture": _ok(stdout=b"not a png at all")})
ss = ScreencaptureScreenshotter(runner=runner)
raised = None
try:
    ss.capture()
except RuntimeError as exc:
    raised = str(exc)
c.ok(raised is not None and "not a PNG" in raised, "non-PNG output raises explicit error")

# build_screenshotter ファクトリ
print("build_screenshotter():")
ss = build_screenshotter()
c.ok(isinstance(ss, ScreencaptureScreenshotter), "factory returns ScreencaptureScreenshotter")

c.done()
