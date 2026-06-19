"""test_darwin_window.py — Mac の window backend のフェイク注入テスト。

osascript は呼ばず、FakeRunner で argv + stdout の契約を検証する:
- list_windows がタブ区切り出力をパースして dict のリストを返す
- visible_only がタイトル空を落とす
- activate(hwnd) が hwnd の上位 16bit を pid として osascript に渡す
- osascript エラー（exit!=0）が actionable に出る（Automation 権限のヒント含む）
- パーサの単体（破損行を落とす、合成 hwnd の生成）
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))
sys.path.insert(0, _HERE)

from darwin.parsers import parse_window_listing  # noqa: E402
from darwin.window import AppleScriptWindowManager, _split_hwnd, build_window  # noqa: E402
from handlers import ProcessResult  # noqa: E402
from linux_testlib import Checker, FakeRunner  # noqa: E402

c = Checker()


def _ok(stdout=b"", stderr=b"", code=0):
    return ProcessResult(code, stdout, stderr, started=True)


# --- parser unit tests ---------------------------------------------------
print("parse_window_listing():")
sample = (
    "100\tMain Window\t0\n"
    "100\t\t0\n"               # タイトル空（visible_only でフィルタ）
    "200\tNote\t1\n"
    "garbage line without tabs\n"
    "300\tonly two\tcols\n"    # 3 列ジャストはタブが 2 つあるので OK
    "abc\ttitle\t0\n"          # pid が数字でない → 落とす
)
got = parse_window_listing(sample, visible_only=True)
c.eq(len(got), 3, "visible_only drops title-empty and bad rows")
c.eq(got[0]["pid"], 100, "first entry pid=100")
c.eq(got[0]["title"], "Main Window", "title preserved")
c.eq(got[0]["hwnd"] >> 16, 100, "synthetic hwnd carries pid in upper 16 bits")
c.eq(got[0]["hwnd"] & 0xFFFF, 0, "first window of pid gets index 0")
c.eq(got[0]["minimized"], False, "minimized=0 parses as False")
c.eq(got[1]["pid"], 200, "second entry pid=200")
c.eq(got[1]["minimized"], True, "minimized=1 parses as True")
c.eq(got[1]["hwnd"] & 0xFFFF, 0, "first window of new pid gets index 0")
c.eq(got[2]["pid"], 300, "third entry pid=300, 3-col line accepted")

got_all = parse_window_listing(sample, visible_only=False)
c.eq(len(got_all), 4, "visible_only=False keeps title-empty (but still drops bad rows)")
c.eq(got_all[1]["title"], "", "title-empty entry kept")
c.eq(got_all[1]["hwnd"] & 0xFFFF, 1,
     "second window of pid 100 gets index 1 (cumulative even across visible filter)")

# 同じ pid に複数窓 → index が増える
sample2 = "1\tA\t0\n1\tB\t0\n1\tC\t0\n"
got = parse_window_listing(sample2, visible_only=False)
c.eq([w["hwnd"] & 0xFFFF for w in got], [0, 1, 2],
     "multiple windows per pid get sequential indices")

# --- _split_hwnd ---------------------------------------------------------
c.eq(_split_hwnd((1234 << 16) | 7), 1234, "_split_hwnd extracts pid from synthetic hwnd")


# --- AppleScriptWindowManager.list_windows ------------------------------
print("AppleScriptWindowManager.list_windows():")
runner = FakeRunner({"osascript": _ok(stdout=b"100\tEditor\t0\n200\tFinder\t0\n")})
wm = AppleScriptWindowManager(runner=runner)
got = wm.list_windows(visible_only=True)
c.eq(len(got), 2, "two windows from fake osascript output")
c.eq(got[0]["title"], "Editor", "title decoded utf-8")
c.eq(runner.calls[-1][0][0], "osascript", "calls osascript")
c.eq(runner.calls[-1][0][1], "-e", "passes the script via -e")

# osascript が無い PATH
runner = FakeRunner({})
wm = AppleScriptWindowManager(runner=runner)
raised = None
try:
    wm.list_windows(True)
except RuntimeError as exc:
    raised = str(exc)
c.ok(raised is not None and "osascript" in raised, "missing osascript raises actionable error")

# Automation 権限拒否（典型エラー -1743）
runner = FakeRunner({
    "osascript": _ok(stderr=b"... errAEEventNotPermitted (-1743) ...", code=1)
})
wm = AppleScriptWindowManager(runner=runner)
raised = None
try:
    wm.list_windows(True)
except RuntimeError as exc:
    raised = str(exc)
c.ok(raised is not None and "Automation" in raised,
     "permission error includes Automation hint")


# --- AppleScriptWindowManager.activate ----------------------------------
print("AppleScriptWindowManager.activate():")
runner = FakeRunner({"osascript": _ok(stdout=b"ok\n")})
wm = AppleScriptWindowManager(runner=runner)
ok = wm.activate((4242 << 16) | 0)
c.ok(ok, "activate returns True on 'ok' output")
argv = runner.calls[-1][0]
c.eq(argv[0], "osascript", "calls osascript for activate")
c.ok("4242" in argv, "passes pid as a string argument")

runner = FakeRunner({"osascript": _ok(stdout=b"not-found\n")})
wm = AppleScriptWindowManager(runner=runner)
c.ok(not wm.activate((9999 << 16) | 0), "activate returns False if pid not found")

runner = FakeRunner({"osascript": _ok(stderr=b"... not authorized ...", code=1)})
wm = AppleScriptWindowManager(runner=runner)
raised = None
try:
    wm.activate((1 << 16) | 0)
except RuntimeError as exc:
    raised = str(exc)
c.ok(raised is not None and "Automation" in raised,
     "activate permission error includes Automation hint")


# --- build_window factory ------------------------------------------------
print("build_window():")
wm = build_window(runner=FakeRunner({"osascript": _ok(stdout=b"")}))
c.ok(isinstance(wm, AppleScriptWindowManager), "factory returns AppleScriptWindowManager")

c.done()
