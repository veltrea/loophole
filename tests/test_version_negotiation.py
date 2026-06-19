"""test_version_negotiation.py — 接続時バージョンネゴシエーション。

docs/version-negotiation.md の実装を検証する。mcp パッケージが要るので uv 経由:
    uv run python tests/test_version_negotiation.py
"""

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


# 全ツールが要求するコマンドの和集合（＋メタ系コマンド）。
ALL_CMDS = {"hello", "ping"}
for _needs in mcp_server.TOOL_REQUIREMENTS.values():
    ALL_CMDS |= set(_needs)


def tool_names():
    return {t.name for t in mcp_server.mcp._tool_manager.list_tools()}


def make_fake(hello_result=None, raises=False):
    class FC:
        def __init__(self, *a, **k):
            pass

        def call(self, cmd, args=None, timeout=60.0):
            if raises:
                raise OSError("connection refused (test)")
            if cmd == "hello":
                return {"ok": True, "result": hello_result}
            return {"ok": True, "result": {}}
    return FC


# ---- 純関数: _compat_verdict（protocol 版判定）----
print("_compat_verdict:")
check(mcp_server._compat_verdict(None)[0] == "outdated", "protocol_version 無し -> outdated")
check(mcp_server._compat_verdict("x")[0] == "unknown", "不正値 -> unknown")
check(mcp_server._compat_verdict(mcp_server.MIN_COMPATIBLE_PROTOCOL - 1)[0] == "too_old",
      "下限未満 -> too_old")
check(mcp_server._compat_verdict(mcp_server.EXPECTED_PROTOCOL + 1)[0] == "client_old",
      "前提より新しい -> client_old")
check(mcp_server._compat_verdict(mcp_server.EXPECTED_PROTOCOL)[0] == "ok", "一致 -> ok")

# ---- 純関数: _tools_to_gate（能力ゲート判定）----
print("_tools_to_gate:")
check(mcp_server._tools_to_gate(sorted(ALL_CMDS)) == [], "全コマンドあり -> ゲート無し")
check(mcp_server._tools_to_gate(None) == [], "commands None -> ゲート無し（材料なし）")
check(mcp_server._tools_to_gate([]) == [], "commands 空 -> ゲート無し")
check(mcp_server._tools_to_gate(sorted(ALL_CMDS - {"screenshot"})) == ["loophole_screenshot"],
      "screenshot 欠落 -> loophole_screenshot のみゲート")
check("loophole_mouse" in mcp_server._tools_to_gate(sorted(ALL_CMDS - {"mouse_scroll"})),
      "mouse_scroll 欠落 -> loophole_mouse ゲート（必要コマンドの一部欠落でも）")

# ---- 統合: _negotiate ----
os.environ["LOOPHOLE_SSH"] = "tester@host"  # _handshake が早期 None で抜けないように

# (1) 到達・全コマンド -> ゲート無し・互換OK
mcp_server.Client = make_fake({"platform": "linux", "agent_version": "0.2.0",
                               "protocol_version": mcp_server.EXPECTED_PROTOCOL,
                               "commands": sorted(ALL_CMDS)})
mcp_server._negotiate()
print("_negotiate (到達・全コマンド):")
check("loophole_screenshot" in tool_names(), "全コマンドあり -> ツール温存")
check("互換OK" in mcp_server._compat_summary and "全ツール利用可能" in mcp_server._compat_summary,
      "summary が互換OK・全ツール利用可能")

# (2) 設定あり・不達 -> フォールバック（ゲート無し）
mcp_server.Client = make_fake(raises=True)
mcp_server._compat_summary = ""
mcp_server._negotiate()
print("_negotiate (不達フォールバック):")
check("loophole_screenshot" in tool_names(), "不達 -> ゲートせず全公開")
check("確認はできませんでした" in mcp_server._compat_summary, "summary がフォールバック告知")

# (3) 到達・screenshot/mouse_scroll 欠落 -> 該当ツールを登録解除（最後に実行：破壊的）
mcp_server.Client = make_fake({"platform": "linux", "agent_version": "0.2.0",
                               "protocol_version": mcp_server.EXPECTED_PROTOCOL,
                               "commands": sorted(ALL_CMDS - {"screenshot", "mouse_scroll"})})
mcp_server._negotiate()
print("_negotiate (欠落 -> ゲート):")
names = tool_names()
check("loophole_screenshot" not in names, "screenshot 欠落 -> loophole_screenshot を登録解除")
check("loophole_mouse" not in names, "mouse_scroll 欠落 -> loophole_mouse を登録解除")
check("loophole_run" in names, "run は在る -> loophole_run/shell は温存")
check("loophole_status" in names, "メタ系 loophole_status は常に温存")
check("loophole_screenshot" in mcp_server._compat_summary, "summary に無効化ツールを列挙")

# (4) instructions への best-effort 注入（接続時に AI が読める版差サマリ）
instr = getattr(getattr(mcp_server.mcp, "_mcp_server", None), "instructions", "") or ""
check("loophole_screenshot" in instr, "版差サマリが server instructions にも載る（接続時告知）")

print()
if failures:
    print(f"FAILED: {failures} failure(s)")
    sys.exit(1)
print("ALL PASS (0 failure(s))")
