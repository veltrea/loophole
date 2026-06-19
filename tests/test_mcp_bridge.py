"""test_mcp_bridge.py — MCP ツールが loophole 呼び出しへ正しくマップされるか検証する。

mcp パッケージが要るので uv 経由で実行する:
    uv run python tests/test_mcp_bridge.py
"""

import base64
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loophole import mcp_server  # noqa: E402

failures = 0


def check(cond, label):
    global failures
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond:
        failures += 1


class FakeClient:
    """mcp_server が使う Client を差し替えるフェイク。最後の呼び出しを記録する。"""

    last = None

    def __init__(self, *a, **k):
        pass

    def call(self, cmd, args=None, timeout=60.0):
        FakeClient.last = (cmd, args or {})
        canned = {
            "hello": {"ok": True, "result": {"platform": "win32", "user": "testuser",
                                             "session_id": 1, "interactive": True, "cwd": "C:/x",
                                             "agent_version": "0.1.0",
                                             "commands": ["hello", "mouse_move", "ping", "run"]}},
            "run": {"ok": True, "result": {"exit_code": 0, "stdout": "OUT", "stderr": ""}},
            "clipboard_get": {"ok": True, "result": {"text": "表予能"}},
            "clipboard_set": {"ok": True, "result": {"ok": True}},
            "screenshot": {"ok": True, "result": {"png_base64": base64.b64encode(b"PNGDATA").decode()}},
            "spawn": {"ok": True, "result": {"pid": 999}},
            "read_file": {"ok": True, "result": {"text": "file body"}},
            "write_file": {"ok": True, "result": {"ok": True}},
            "send_keys": {"ok": True, "result": {"sent": ["ctrl+s"], "count": 1}},
            "mouse_move": {"ok": True, "result": {"moved": True, "x": 640, "y": 360}},
            "mouse_click": {"ok": True, "result": {"clicked": 2, "button": "right"}},
            "mouse_scroll": {"ok": True, "result": {"scrolled": True, "dx": 0, "dy": 3}},
            "find_files": {"ok": True, "result": {
                "matches": [{"path": "C:/a.txt", "size": 5, "mtime": 0.0}],
                "truncated": False, "scanned": 3}},
            "ime_get": {"ok": True, "result": {"supported": True, "open": True,
                                               "conversion": 0x19, "mode": "hiragana", "roman": True}},
            "ime_set": {"ok": True, "result": {"supported": True, "open": False,
                                               "conversion": 0, "mode": "alphanumeric", "roman": False}},
            "menu_enumerate": {"ok": True, "result": {
                "supported": True, "hwnd": 4242, "title": "無題 - メモ帳",
                "items": [
                    {"label": "ファイル(&F)", "command_id": None, "enabled": True,
                     "checked": False, "separator": False, "path": "ファイル", "submenu": [
                        {"label": "終了(&X)", "command_id": 5, "enabled": True,
                         "checked": False, "separator": False, "path": "ファイル > 終了",
                         "destructive_guess": True},
                     ]},
                    {"label": "ワードラップ(&W)", "command_id": 20, "enabled": True,
                     "checked": True, "separator": False, "path": "ワードラップ"},
                ]}},
            "menu_invoke": {"ok": True, "result": {"posted": True, "hwnd": 4242, "command_id": 5}},
        }
        return canned[cmd]


mcp_server.Client = FakeClient  # _client() が FakeClient を返すよう差し替え

print("tool -> agent command mapping:")
out = mcp_server.loophole_hello()
check("session_id=1" in out and "interactive=True" in out, "loophole_hello formats session info")
check("agent_version=0.1.0" in out, "loophole_hello surfaces agent_version (client/server skew signal)")

out = mcp_server.loophole_shell("echo hi", encoding="cp932")
check(FakeClient.last == ("run", {"command": "echo hi", "encoding": "cp932"}), "loophole_shell -> run command")
check("[exit 0]" in out and "OUT" in out, "loophole_shell shows exit + stdout")

mcp_server.loophole_run(["cmd", "/c", "ver"])
check(FakeClient.last == ("run", {"argv": ["cmd", "/c", "ver"], "encoding": "auto"}), "loophole_run -> run argv")

check(mcp_server.loophole_clipboard_get() == "表予能", "loophole_clipboard_get returns text (dame-moji)")
mcp_server.loophole_clipboard_set("sample text")
check(FakeClient.last == ("clipboard_set", {"text": "sample text"}), "loophole_clipboard_set -> clipboard_set")

img = mcp_server.loophole_screenshot()
check(FakeClient.last[0] == "screenshot" and FakeClient.last[1] == {"data": True}, "loophole_screenshot requests data=True")
check(getattr(img, "data", None) == b"PNGDATA", "loophole_screenshot returns Image with decoded PNG bytes")

out = mcp_server.loophole_gui(["firefox.exe", "https://example.com"])
check(FakeClient.last == ("spawn", {"argv": ["firefox.exe", "https://example.com"]}) and "pid=999" in out, "loophole_gui -> spawn")

check(mcp_server.loophole_read_file("C:/r.txt") == "file body", "loophole_read_file -> read_file")
mcp_server.loophole_write_file("C:/w.txt", "body")
check(FakeClient.last == ("write_file", {"path": "C:/w.txt", "text": "body"}), "loophole_write_file -> write_file")

out = mcp_server.loophole_send_keys("ctrl+s")
check(FakeClient.last == ("send_keys", {"keys": "ctrl+s"}), "loophole_send_keys -> send_keys")
check("ctrl+s" in out, "loophole_send_keys echoes the strokes sent")

mcp_server.loophole_mouse("move", x=640, y=360)
check(FakeClient.last == ("mouse_move", {"x": 640, "y": 360}), "loophole_mouse move -> mouse_move")
mcp_server.loophole_mouse("click", x=10, y=20, button="right", count=2)
check(FakeClient.last == ("mouse_click", {"button": "right", "count": 2, "x": 10, "y": 20}),
      "loophole_mouse click -> mouse_click with button/count/coords")
out = mcp_server.loophole_mouse("scroll", dy=3)
check(FakeClient.last == ("mouse_scroll", {"dx": 0, "dy": 3}) and "dy=3" in out,
      "loophole_mouse scroll -> mouse_scroll")
err = None
try:
    mcp_server.loophole_mouse("teleport")
except Exception as exc:
    err = str(exc)
check(err is not None and "move" in err, "loophole_mouse rejects unknown action")

out = mcp_server.loophole_find_files("C:/proj", "*.txt")
check(FakeClient.last[0] == "find_files"
      and FakeClient.last[1]["root"] == "C:/proj"
      and FakeClient.last[1]["pattern"] == "*.txt"
      and FakeClient.last[1]["match"] == "glob",
      "loophole_find_files -> find_files with root/pattern/match")
check("C:/a.txt" in out, "loophole_find_files lists matched paths")

out = mcp_server.loophole_ime_get()
check(FakeClient.last == ("ime_get", {}), "loophole_ime_get -> ime_get")
check("on (Japanese input)" in out and "hiragana" in out and "roman" in out,
      "loophole_ime_get summarizes open/mode/roman")

out = mcp_server.loophole_ime_set(open=False)
check(FakeClient.last == ("ime_set", {"open": False}), "loophole_ime_set(open=False) -> ime_set open only")
check("off (direct input)" in out, "loophole_ime_set echoes the resulting state")
mcp_server.loophole_ime_set(mode="hiragana", roman=True)
check(FakeClient.last == ("ime_set", {"mode": "hiragana", "roman": True}),
      "loophole_ime_set forwards only the provided axes")
try:
    mcp_server.loophole_ime_set()
    check(False, "ime_set with no axis should raise")
except mcp_server._AgentError:
    check(True, "loophole_ime_set with no axis raises before calling the agent")

print("menu tool:")
out = mcp_server.loophole_menu("list", title="メモ帳")
check(FakeClient.last == ("menu_enumerate", {"title": "メモ帳"}), "loophole_menu list -> menu_enumerate")
check("[id=5]" in out and "[id=20]" in out, "menu list shows invokable command ids")
check("⚠destructive?" in out, "menu list flags destructive items")
check("checked" in out, "menu list shows checked flag")

out = mcp_server.loophole_menu("invoke", hwnd=4242, command_id=5)
check(FakeClient.last == ("menu_invoke", {"hwnd": 4242, "command_id": 5}),
      "loophole_menu invoke -> menu_invoke")
check("command_id=5" in out and "hwnd=4242" in out, "menu invoke confirms what was posted")

try:
    mcp_server.loophole_menu("invoke", title="メモ帳")  # command_id 欠落
    check(False, "invoke without command_id should raise")
except mcp_server._AgentError:
    check(True, "loophole_menu invoke without command_id raises before calling agent")

try:
    mcp_server.loophole_menu("frobnicate", title="x")
    check(False, "bad action should raise")
except mcp_server._AgentError:
    check(True, "loophole_menu rejects unknown action")

print("error handling:")
class ErrClient(FakeClient):
    def call(self, cmd, args=None, timeout=60.0):
        return {"ok": False, "error": "boom"}
mcp_server.Client = ErrClient
try:
    mcp_server.loophole_shell("x")
    check(False, "agent error should raise")
except mcp_server._AgentError as e:
    check("boom" in str(e), "agent error surfaces message")

class DownClient:
    def __init__(self, *a, **k): pass
    def call(self, *a, **k): raise OSError("refused")
mcp_server.Client = DownClient

# 未設定（LOOPHOLE_SSH 無し）なら、ターミナルでなく loophole_configure へ誘導する
mcp_server.os.environ.pop("LOOPHOLE_SSH", None)
try:
    mcp_server.loophole_hello()
    check(False, "unreachable should raise")
except mcp_server._AgentError as e:
    check("loophole_configure" in str(e), "unconfigured -> points to loophole_configure")

# 設定済みで届かないなら、トンネル/サーバー確認の actionable なヒントを出す
mcp_server.os.environ["LOOPHOLE_SSH"] = "me@192.0.2.1"
try:
    mcp_server.loophole_hello()
    check(False, "unreachable should raise")
except mcp_server._AgentError as e:
    check("LOOPHOLE_SSH" in str(e) or "サーバー" in str(e), "configured-but-down gives actionable hint")
mcp_server.os.environ.pop("LOOPHOLE_SSH", None)

print(f"\n{'ALL PASS' if failures == 0 else 'SOME FAILED'} ({failures} failure(s))")
sys.exit(0 if failures == 0 else 1)
