"""test_darwin_clipboard.py — Mac の pbcopy/pbpaste backend のフェイク注入テスト。

実 pbcopy/pbpaste は呼ばず、FakeRunner で argv と stdin の契約を検証する:
- get() が pbpaste を呼び、stdout を UTF-8 でデコードして返す
- set() が pbcopy を呼び、stdin にテキストを流す
- 起動失敗（pbcopy/pbpaste が無い PATH）は actionable RuntimeError
- 非 0 終了は exit/stderr を含む RuntimeError
- 日本語（マルチバイト UTF-8）が往復する
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))
sys.path.insert(0, _HERE)

from darwin.clipboard import PbcopyClipboard, build_clipboard  # noqa: E402
from handlers import ProcessResult  # noqa: E402
from linux_testlib import Checker, FakeRunner  # noqa: E402

c = Checker()


def _ok(stdout=b"", stderr=b"", code=0):
    return ProcessResult(code, stdout, stderr, started=True)


def _fail_to_start():
    return ProcessResult(-1, b"", b"", started=False)


# --- get -----------------------------------------------------------------
print("PbcopyClipboard.get():")
runner = FakeRunner({"pbpaste": _ok(stdout="hello 日本語".encode("utf-8"))})
cb = PbcopyClipboard(runner=runner)
c.eq(cb.get(), "hello 日本語", "decodes pbpaste stdout as utf-8")
c.eq(runner.calls[-1][0], ["pbpaste"], "calls pbpaste with no args")

# 起動失敗（pbpaste が無い）→ actionable
runner = FakeRunner({})  # 何も登録しない
cb = PbcopyClipboard(runner=runner)
raised = None
try:
    cb.get()
except RuntimeError as exc:
    raised = str(exc)
c.ok(raised is not None and "pbpaste" in raised, "get raises with pbpaste hint when missing")

# 非 0 終了
runner = FakeRunner({"pbpaste": _ok(stderr=b"boom", code=2)})
cb = PbcopyClipboard(runner=runner)
raised = None
try:
    cb.get()
except RuntimeError as exc:
    raised = str(exc)
c.ok(raised is not None and "exit=2" in raised and "boom" in raised,
     "get raises with exit code and stderr on non-zero")

# --- set -----------------------------------------------------------------
print("PbcopyClipboard.set():")
runner = FakeRunner({"pbcopy": _ok()})
cb = PbcopyClipboard(runner=runner)
cb.set("こんにちは")
c.eq(runner.calls[-1][0], ["pbcopy"], "calls pbcopy with no args")
c.eq(runner.calls[-1][1], "こんにちは", "feeds the text on stdin")

# 起動失敗 / 非 0 も同形にエラーする
runner = FakeRunner({})
cb = PbcopyClipboard(runner=runner)
raised = None
try:
    cb.set("x")
except RuntimeError as exc:
    raised = str(exc)
c.ok(raised is not None and "pbcopy" in raised, "set raises with pbcopy hint when missing")

runner = FakeRunner({"pbcopy": _ok(stderr=b"nope", code=1)})
cb = PbcopyClipboard(runner=runner)
raised = None
try:
    cb.set("x")
except RuntimeError as exc:
    raised = str(exc)
c.ok(raised is not None and "exit=1" in raised and "nope" in raised,
     "set raises with exit code and stderr on non-zero")

# --- build_clipboard returns PbcopyClipboard ----------------------------
print("build_clipboard():")
cb = build_clipboard(runner=FakeRunner({"pbpaste": _ok(stdout=b"x")}))
c.ok(isinstance(cb, PbcopyClipboard), "factory returns PbcopyClipboard")
c.eq(cb.get(), "x", "factory-built backend round-trips through fake")

c.done()
