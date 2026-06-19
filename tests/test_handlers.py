"""handlers.py の単体テスト（Mac / Windows どちらでも実行可）。

外部 I/O はすべてフェイクを注入するので、実プロセス起動も Windows も不要。

    python3 tests/test_handlers.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import handlers  # noqa: E402
from handlers import HandlerError, ProcessResult  # noqa: E402
from fakes import (FakeRunner, FakeClipboard, FakeScreenshotter, FakeFS, FakeKeyboard,  # noqa: E402
                   FakeEnv, FakeWindowManager, FakeIme, FakeMenuController)

failures = 0


def check(cond, label):
    global failures
    if cond:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label}")
        failures += 1


def check_eq(actual, expected, label):
    global failures
    if actual == expected:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label}\n         expected={expected!r}\n         actual  ={actual!r}")
        failures += 1


def expect_error(fn, label):
    global failures
    try:
        fn()
        print(f"  [FAIL] {label} (no error raised)")
        failures += 1
    except HandlerError:
        print(f"  [PASS] {label}")


def make_handlers():
    runner = FakeRunner()
    clip = FakeClipboard()
    shot = FakeScreenshotter()
    fs = FakeFS()
    kbd = FakeKeyboard()
    win = FakeWindowManager()
    ime = FakeIme()
    menu = FakeMenuController()
    h = handlers.Handlers(runner, clip, shot, fs, FakeEnv(), kbd, win, ime, menu)
    return h, runner, clip, shot, fs, kbd, win, ime, menu


print("dispatch / commands:")
h, runner, clip, shot, fs, kbd, win, ime, menu = make_handlers()
check("run" in h.commands() and "screenshot" in h.commands(), "commands() lists handlers")
expect_error(lambda: h.dispatch("nonsense", {}), "unknown command raises HandlerError")
check_eq(h.dispatch("ping", {}), {"pong": True}, "ping returns pong")
hello = h.dispatch("hello", {})
check(hello["user"] == "testuser" and hello["platform"] == "win32" and hello["interactive"] is True,
      "hello returns environment info")
check_eq(hello["agent_version"], handlers.AGENT_VERSION,
         "hello includes agent_version (client/server skew signal)")
check("mouse_move" in hello["commands"] and "hello" in hello["commands"],
      "hello includes commands list (server capabilities)")

print("run (argv):")
runner.next_result = ProcessResult(0, "出力".encode("cp932"), b"")
out = h.dispatch("run", {"argv": ["cmd", "/c", "echo", "x"], "cwd": "C:/tmp"})
check_eq(out["exit_code"], 0, "exit code passed through")
check_eq(out["stdout"], "出力", "stdout decoded via auto (CP932 fallback)")
check_eq(runner.calls[-1]["cwd"], "C:/tmp", "cwd forwarded to runner")
expect_error(lambda: h.dispatch("run", {}), "run without argv/command raises")
expect_error(lambda: h.dispatch("run", {"argv": []}), "empty argv raises")
expect_error(lambda: h.dispatch("run", {"argv": [1, 2]}), "non-string argv raises")

print("run (command -> runner.shell_argv):")
runner.next_result = ProcessResult(0, b"ok", b"")
h.dispatch("run", {"command": "echo a & echo b"})
# ハンドラは OS 分岐を持たず runner.shell_argv に委ねる（FakeRunner は /bin/sh -c を返す）。
check_eq(runner.calls[-1]["argv"], ["/bin/sh", "-c", "echo a & echo b"],
         "command wrapped by runner.shell_argv")

print("run (encoding override + failure):")
runner.next_result = ProcessResult(0, "日本語".encode("utf-8"), b"")
out = h.dispatch("run", {"argv": ["x"], "encoding": "utf-8"})
check_eq(out["stdout"], "日本語", "explicit utf-8 encoding honored")
runner.next_result = ProcessResult(-1, b"", b"", started=False)
expect_error(lambda: h.dispatch("run", {"argv": ["missing.exe"]}), "failed start raises HandlerError")

print("spawn (GUI):")
out = h.dispatch("spawn", {"argv": ["firefox.exe", "https://example.com"]})
check_eq(out, {"pid": 4321}, "spawn returns pid")
check_eq(runner.spawned[-1]["argv"], ["firefox.exe", "https://example.com"], "spawn argv recorded")
expect_error(lambda: h.dispatch("spawn", {}), "spawn without argv raises")

print("clipboard:")
check_eq(h.dispatch("clipboard_get", {}), {"text": "initial"}, "clipboard_get reads value")
h.dispatch("clipboard_set", {"text": "sample clipboard value"})
check_eq(clip.value, "sample clipboard value", "clipboard_set writes value")
expect_error(lambda: h.dispatch("clipboard_set", {}), "clipboard_set without text raises")

print("screenshot:")
out = h.dispatch("screenshot", {})  # path 省略でも撮れる（MCP 用に base64 を返す）
check(shot.captured, "screenshotter invoked even without a path")
check(out["bytes"] == len(shot.png), "returns byte count")
check("png_base64" in out and "path" not in out, "no path -> base64 only, no path key")
import base64 as _b64
check(_b64.b64decode(out["png_base64"]) == shot.png, "base64 decodes back to the PNG bytes")
out2 = h.dispatch("screenshot", {"path": "C:/tmp/shot.png"})
check_eq(out2.get("path"), "C:/tmp/shot.png", "path echoed when given")
check_eq(fs.store.get("C:/tmp/shot.png"), shot.png, "PNG written to path on the agent host")
out3 = h.dispatch("screenshot", {"path": "C:/tmp/s2.png", "data": False})
check("png_base64" not in out3, "data=False omits base64")
expect_error(lambda: h.dispatch("screenshot", {"path": 123}), "non-string path raises")

print("read_file / write_file:")
h.dispatch("write_file", {"path": "C:/tmp/r.txt", "text": "表予能"})
check_eq(fs.store["C:/tmp/r.txt"], "表予能".encode("utf-8"), "write_file stores UTF-8 bytes")
out = h.dispatch("read_file", {"path": "C:/tmp/r.txt"})
check_eq(out["text"], "表予能", "read_file round-trips dame-moji")
expect_error(lambda: h.dispatch("read_file", {"path": "C:/missing"}), "read missing file raises")
expect_error(lambda: h.dispatch("write_file", {"path": "x"}), "write without text raises")

print("send_keys (shortcuts):")
out = h.dispatch("send_keys", {"keys": "ctrl+s"})
check_eq(out["count"], 1, "single chord counted")
check_eq(kbd.sent[-1], ([0x11], 0x53), "ctrl+s -> ([VK_CONTROL], VK_S)")
kbd.sent.clear()
out = h.dispatch("send_keys", {"keys": ["win+r", "enter"]})
check_eq(out["count"], 2, "list of strokes counted")
check_eq(kbd.sent, [([0x5B], 0x52), ([], 0x0D)], "win+r then enter sent in order")
check_eq(out["sent"], ["win+r", "enter"], "sent echoes the normalized input")
kbd.sent.clear()
h.dispatch("send_keys", {"keys": "ctrl+shift+esc"})
check_eq(kbd.sent[-1], ([0x11, 0x10], 0x1B), "ctrl+shift+esc -> two modifiers + escape")
expect_error(lambda: h.dispatch("send_keys", {}), "send_keys without keys raises")
expect_error(lambda: h.dispatch("send_keys", {"keys": "ctrl+nope"}), "unknown key name raises")
expect_error(lambda: h.dispatch("send_keys", {"keys": "ctrl+shift"}), "modifier-only chord raises")

print("find_files (search by name):")
h, runner, clip, shot, fs, kbd, win, ime, menu = make_handlers()
fs.tree = [
    ("C:/proj", ["src", "docs"], ["readme.txt", "notes.md"]),
    ("C:/proj/src", [], ["main.py", "util.py", "data.TXT"]),
    ("C:/proj/docs", [], ["guide.txt"]),
]
fs.stats = {
    "C:/proj/readme.txt": (10, 100.0),
    "C:/proj/src/data.TXT": (20, 200.0),
    "C:/proj/docs/guide.txt": (30, 300.0),
}
out = h.dispatch("find_files", {"root": "C:/proj", "pattern": "*.txt"})
check_eq(sorted(m["path"] for m in out["matches"]),
         ["C:/proj/docs/guide.txt", "C:/proj/readme.txt", "C:/proj/src/data.TXT"],
         "glob *.txt is case-insensitive and recursive")
check_eq(out["truncated"], False, "not truncated under the cap")
check(any(m["size"] == 10 for m in out["matches"]), "stat size attached to matches")

out = h.dispatch("find_files", {"root": "C:/proj", "pattern": "util", "match": "substring"})
check_eq([m["path"] for m in out["matches"]], ["C:/proj/src/util.py"], "substring match finds util.py")

out = h.dispatch("find_files", {"root": "C:/proj", "pattern": "*", "max_results": 2})
check_eq(len(out["matches"]), 2, "max_results caps the matches")
check_eq(out["truncated"], True, "truncated flagged when the cap is hit")

out = h.dispatch("find_files", {"root": "C:/proj", "pattern": "*.txt", "max_depth": 0})
check_eq([m["path"] for m in out["matches"]], ["C:/proj/readme.txt"], "max_depth=0 = root only")

out = h.dispatch("find_files", {"root": "C:/proj", "pattern": "src", "include_dirs": True})
check_eq([m["path"] for m in out["matches"]], ["C:/proj/src"], "include_dirs matches directory names")
out = h.dispatch("find_files", {"root": "C:/proj", "pattern": "src"})
check_eq(out["matches"], [], "without include_dirs, directory names are not matched")

expect_error(lambda: h.dispatch("find_files", {"root": "C:/nope", "pattern": "*"}), "missing root raises")
expect_error(lambda: h.dispatch("find_files", {"pattern": "*"}), "find_files without root raises")
expect_error(lambda: h.dispatch("find_files", {"root": "C:/proj"}), "find_files without pattern raises")
expect_error(lambda: h.dispatch("find_files", {"root": "C:/proj", "pattern": "*", "match": "weird"}),
             "bad match mode raises")

print("list_windows / activate_window:")
h, runner, clip, shot, fs, kbd, win, ime, menu = make_handlers()
win.windows = [
    {"hwnd": 11, "title": "Untitled - Notepad", "pid": 100, "minimized": False},
    {"hwnd": 22, "title": "Document1 - Word", "pid": 200, "minimized": False},
    {"hwnd": 33, "title": "notes - Notepad", "pid": 300, "minimized": True},
]
out = h.dispatch("list_windows", {})
check_eq(out["count"], 3, "list_windows returns every window without a filter")
out = h.dispatch("list_windows", {"pattern": "notepad"})
check_eq(sorted(w["hwnd"] for w in out["windows"]), [11, 33], "pattern filters titles (case-insensitive)")
expect_error(lambda: h.dispatch("list_windows", {"pattern": 5}), "non-string pattern raises")

out = h.dispatch("activate_window", {"hwnd": 22})
check_eq(out, {"activated": True, "hwnd": 22}, "activate by hwnd returns activated")
check_eq(win.activated[-1], 22, "manager.activate called with the hwnd")
out = h.dispatch("activate_window", {"title": "Word"})
check_eq(out["activated"], True, "unique title substring activates")
check_eq(out["hwnd"], 22, "resolved to the single matching hwnd")
out = h.dispatch("activate_window", {"title": "Notepad"})
check_eq(out.get("ambiguous"), True, "ambiguous title returns candidates without acting")
check_eq(len(out["candidates"]), 2, "both Notepad windows offered as candidates")
expect_error(lambda: h.dispatch("activate_window", {"title": "Nonexistent"}), "no match raises")
expect_error(lambda: h.dispatch("activate_window", {}), "activate_window without title/hwnd raises")
win.activate_result = False
expect_error(lambda: h.dispatch("activate_window", {"hwnd": 11}), "focus refused raises")

print("ime_get / ime_set:")
h, runner, clip, shot, fs, kbd, win, ime, menu = make_handlers()
# 直接入力（OFF）の初期状態を読む。
ime.state = (False, 0)
out = h.dispatch("ime_get", {})
check_eq(out, {"supported": True, "open": False, "conversion": 0,
               "mode": "alphanumeric", "roman": False}, "ime_get reports direct input")
# ひらがな・ローマ字入力（NATIVE|FULLSHAPE|ROMAN = 0x19）を読む。
ime.state = (True, 0x19)
out = h.dispatch("ime_get", {})
check_eq(out, {"supported": True, "open": True, "conversion": 0x19,
               "mode": "hiragana", "roman": True}, "ime_get decodes hiragana + roman")
# IME を持たない窓は supported=False。
ime.state = None
check_eq(h.dispatch("ime_get", {}), {"supported": False}, "no-IME window reports unsupported")

# open=False（直接入力）にする — type が化けないための主操作。
ime.state = (True, 0x19)
out = h.dispatch("ime_set", {"open": False})
check_eq(ime.sets[-1], (False, None), "ime_set open=False only touches open status")
check_eq(out["open"], False, "ime_set returns the resulting state")
check_eq(out["mode"], "hiragana", "conversion left intact when only open is set")

# mode 変更は ROMAN ビットを現状から引き継ぐ（roman 未指定なので 0x10 を保つ）。
ime.state = (True, 0x19)  # hiragana + roman
out = h.dispatch("ime_set", {"mode": "katakana"})
check_eq(ime.sets[-1], (None, 0x1B), "mode=katakana keeps the roman bit (0x0B|0x10)")
check_eq(out["mode"], "katakana", "resulting mode reflects the change")
check_eq(out["roman"], True, "roman preserved across a mode change")

# roman だけ変更すると表示モードは現状維持。
ime.state = (True, 0x19)  # hiragana + roman
h.dispatch("ime_set", {"roman": False})
check_eq(ime.sets[-1], (None, 0x09), "roman=False clears only the roman bit, keeps hiragana")

# open と mode を同時指定。
ime.state = (False, 0)
out = h.dispatch("ime_set", {"open": True, "mode": "alphanumeric-full"})
check_eq(ime.sets[-1], (True, 0x08), "open=True + alphanumeric-full sets both axes")
check_eq(out, {"supported": True, "open": True, "conversion": 0x08,
               "mode": "alphanumeric-full", "roman": False}, "combined set returns full state")

# conversion 直接指定は mode/roman を無視して raw を書く。
ime.state = (True, 0)
h.dispatch("ime_set", {"conversion": 0x1B})
check_eq(ime.sets[-1], (None, 0x1B), "raw conversion written verbatim")

expect_error(lambda: h.dispatch("ime_set", {}), "ime_set with no axis raises")
expect_error(lambda: h.dispatch("ime_set", {"open": "yes"}), "non-bool open raises")
expect_error(lambda: h.dispatch("ime_set", {"mode": "klingon"}), "unknown mode raises")
expect_error(lambda: h.dispatch("ime_set", {"roman": 1}), "non-bool roman raises")
expect_error(lambda: h.dispatch("ime_set", {"conversion": "0x10"}), "non-int conversion raises")
ime.state = None
expect_error(lambda: h.dispatch("ime_set", {"open": False}), "set on a no-IME window raises")

print("menu_enumerate (formatting / flags / errors):")
h, runner, clip, shot, fs, kbd, win, ime, menu = make_handlers()
# バックエンドが返す生ツリー（path / destructive_guess は handler が付ける）。
menu.tree = [
    {"label": "ファイル(&F)", "command_id": 0, "enabled": True, "checked": False, "submenu": [
        {"label": "新規(&N)\tCtrl+N", "command_id": 1, "enabled": True, "checked": False},
        {"separator": True},
        {"label": "終了(&X)", "command_id": 5, "enabled": True, "checked": False},
    ]},
    {"label": "&Edit", "command_id": 0, "enabled": False, "checked": False, "submenu": [
        {"label": "&Undo", "command_id": 10, "enabled": False, "checked": False},
        {"label": "Delete Line", "command_id": 11, "enabled": True, "checked": False},
    ]},
    {"label": "ワードラップ(&W)", "command_id": 20, "enabled": True, "checked": True},
]
out = h.dispatch("menu_enumerate", {"hwnd": 12345})
check_eq(out["supported"], True, "enumerate supported=True when menu present")
check_eq(out["hwnd"], 12345, "hwnd echoed")
top = out["items"]
check_eq(top[0]["path"], "ファイル", "top label cleaned of & for path")
check_eq(top[0]["command_id"], None, "submenu header has command_id None")
check_eq(top[0]["submenu"][0]["path"], "ファイル > 新規", "child path joins parent; \\t accel stripped")
check_eq(top[0]["submenu"][0]["command_id"], 1, "leaf command_id preserved")
check_eq(top[0]["submenu"][1], {"separator": True}, "separator preserved as-is")
check(top[0]["submenu"][2].get("destructive_guess") is True, "JP '終了' flagged destructive")
check_eq(top[1]["enabled"], False, "grayed top item enabled=False")
check_eq(top[1]["submenu"][0]["enabled"], False, "grayed child enabled=False")
check(top[1]["submenu"][1].get("destructive_guess") is True, "EN 'Delete' flagged destructive")
check("destructive_guess" not in top[0]["submenu"][0], "benign item has no destructive flag")
check_eq(top[2]["checked"], True, "checked flag preserved")
check_eq(top[2]["command_id"], 20, "toggle leaf command_id preserved")

# メニューを持たない窓（リボン/Electron/UWP）。
menu.supported = False
out = h.dispatch("menu_enumerate", {"hwnd": 999})
check_eq(out, {"supported": False, "hwnd": 999}, "no-menu window -> supported:false")
menu.supported = True

# title 解決（一意 / 曖昧 / 該当なし）。
win.windows = [{"hwnd": 77, "title": "無題 - メモ帳", "pid": 1, "minimized": False}]
out = h.dispatch("menu_enumerate", {"title": "メモ帳"})
check_eq((out["supported"], out["hwnd"], out["title"]), (True, 77, "無題 - メモ帳"),
         "unique title resolves to its hwnd and echoes title")
win.windows = [{"hwnd": 1, "title": "メモ帳 A", "pid": 1, "minimized": False},
               {"hwnd": 2, "title": "メモ帳 B", "pid": 2, "minimized": False}]
out = h.dispatch("menu_enumerate", {"title": "メモ帳"})
check(out.get("ambiguous") is True and len(out["candidates"]) == 2,
      "ambiguous title returns candidates, does not enumerate")
expect_error(lambda: h.dispatch("menu_enumerate", {"title": "存在しない窓"}),
             "title with no match raises")
expect_error(lambda: h.dispatch("menu_enumerate", {"hwnd": "x"}), "non-int hwnd raises")
expect_error(lambda: h.dispatch("menu_enumerate", {}), "missing title/hwnd raises")

print("menu_invoke (posting / validation):")
h, runner, clip, shot, fs, kbd, win, ime, menu = make_handlers()
out = h.dispatch("menu_invoke", {"hwnd": 12345, "command_id": 5})
check_eq(out, {"posted": True, "hwnd": 12345, "command_id": 5}, "invoke returns posted envelope")
check_eq(menu.invoked, [(12345, 5)], "backend.invoke called with (hwnd, command_id)")
expect_error(lambda: h.dispatch("menu_invoke", {"hwnd": 1, "command_id": 0}),
             "command_id=0 rejected")
expect_error(lambda: h.dispatch("menu_invoke", {"hwnd": 1, "command_id": -3}),
             "negative command_id rejected")
expect_error(lambda: h.dispatch("menu_invoke", {"hwnd": 1, "command_id": "5"}),
             "non-int command_id rejected")
expect_error(lambda: h.dispatch("menu_invoke", {"hwnd": 1}), "missing command_id rejected")
check_eq(menu.invoked, [(12345, 5)], "no invalid call reached the backend")
# backend が False を返したら HandlerError。
menu.invoke_result = False
expect_error(lambda: h.dispatch("menu_invoke", {"hwnd": 1, "command_id": 5}),
             "backend invoke False -> HandlerError")
menu.invoke_result = True
# title 経由で hwnd を解決して発火。
win.windows = [{"hwnd": 88, "title": "無題 - メモ帳", "pid": 1, "minimized": False}]
h.dispatch("menu_invoke", {"title": "メモ帳", "command_id": 3})
check_eq(menu.invoked[-1], (88, 3), "title resolves to hwnd before posting")
# 曖昧な title では発火しない。
win.windows = [{"hwnd": 1, "title": "メモ帳 A", "pid": 1, "minimized": False},
               {"hwnd": 2, "title": "メモ帳 B", "pid": 2, "minimized": False}]
before = list(menu.invoked)
out = h.dispatch("menu_invoke", {"title": "メモ帳", "command_id": 9})
check(out.get("ambiguous") is True, "ambiguous title on invoke returns candidates")
check_eq(menu.invoked, before, "ambiguous title does not post anything")

print("mouse (move / click / scroll via MouseController):")
from fakes import FakeMouse  # noqa: E402
fm = FakeMouse()
hm = handlers.Handlers(FakeRunner(), FakeClipboard(), FakeScreenshotter(), FakeFS(),
                       FakeEnv(), FakeKeyboard(), FakeWindowManager(), FakeIme(),
                       FakeMenuController(), mouse=fm)
hm.dispatch("mouse_move", {"x": 100, "y": 200})
check_eq(fm.events[-1], ("move", 100, 200), "mouse_move -> backend.move(x,y)")
fm.events.clear()
hm.dispatch("mouse_click", {"button": "right", "x": 10, "y": 20, "count": 2})
check_eq(fm.events,
         [("move", 10, 20), ("button", 3, True), ("button", 3, False),
          ("button", 3, True), ("button", 3, False)],
         "click right at (10,20) x2 -> move + two down/up of button 3")
fm.events.clear()
hm.dispatch("mouse_click", {})
check_eq(fm.events, [("button", 1, True), ("button", 1, False)],
         "default click = left, no move, single")
fm.events.clear()
hm.dispatch("mouse_scroll", {"dy": 3})
check_eq(fm.events[-1], ("scroll", 0, 3), "mouse_scroll dy -> backend.scroll(0,3)")
expect_error(lambda: hm.dispatch("mouse_move", {"x": 1}), "mouse_move missing y -> error")
expect_error(lambda: hm.dispatch("mouse_click", {"button": "nope"}), "bad button name -> error")
expect_error(lambda: hm.dispatch("mouse_scroll", {}), "scroll with no dx/dy -> error")
# mouse 未注入（mouse=None）→ 明示エラー
hn = handlers.Handlers(FakeRunner(), FakeClipboard(), FakeScreenshotter(), FakeFS(),
                       FakeEnv(), FakeKeyboard(), FakeWindowManager(), FakeIme(),
                       FakeMenuController())
expect_error(lambda: hn.dispatch("mouse_move", {"x": 1, "y": 2}),
             "mouse unavailable (mouse=None) -> HandlerError")

print(f"\n{'ALL PASS' if failures == 0 else 'SOME FAILED'} ({failures} failure(s))")
sys.exit(0 if failures == 0 else 1)
