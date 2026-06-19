"""window.py — macOS のウィンドウ列挙・前面化 backend（osascript / System Events 経由）。

handlers.WindowManager プロトコルを満たす AppleScriptWindowManager。

macOS には EWMH/Win32 のような「外部から渡せる安定したウィンドウ ID」が無い（AX のオブジェクト
参照は同プロセス内のみで意味を持つ）。loophole の API は OS 非依存に "hwnd: int" を返す契約な
ので、ここでは:

  - **hwnd = (pid << 16) | window_index** という合成 ID を作る（list の戻り値）
  - **activate(hwnd) は上位 16bit を pid として「そのアプリ全体を前面化」する**（best-effort）

実際に「特定の窓だけ raise」は AX の `AXRaise` が要るが、それは後続フェーズ（M12）。まずは
Win/Linux と同じ操作感の最小実装で push forward する。

データ取得は AppleScript（osascript -e）で行い、System Events に対して 1 行 1 ウィンドウの
タブ区切り行を吐かせる:
    pid<TAB>title<TAB>minimized<TAB>visible

これなら JSON 文字列のエスケープに悩まずに済む（タブをタイトルに含むウィンドウは現実的に
無く、含んだとしても 1 行ぶん壊れるだけで他のウィンドウ列挙は守られる）。

権限: System Events への AppleScript 送信には **Automation 権限**（System Settings >
Privacy & Security > Automation）が必要。未許可だと osascript が `errAEEventNotPermitted`
(-1743) で落ちる。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from common_backends import SubprocessRunner
from .parsers import parse_window_listing


# 1 行 1 ウィンドウのタブ区切り出力を吐かせる AppleScript。background only=true のプロセス
# （Dock アイコンを持たない常駐デーモン等）はユーザに見える窓を持たないので除外する。
_LIST_SCRIPT = '''
tell application "System Events"
  set out to ""
  repeat with proc in (every process whose background only is false)
    set pid to (unix id of proc)
    try
      set procWindows to (windows of proc)
    on error
      set procWindows to {}
    end try
    repeat with w in procWindows
      set wtitle to ""
      try
        set wtitle to (title of w)
      end try
      set wmin to "0"
      try
        if (value of attribute "AXMinimized" of w) then set wmin to "1"
      end try
      set out to out & (pid as text) & tab & wtitle & tab & wmin & linefeed
    end repeat
  end repeat
  return out
end tell
'''


_ACTIVATE_SCRIPT = '''
on run argv
  set targetPid to (item 1 of argv) as integer
  tell application "System Events"
    repeat with proc in (every process)
      if (unix id of proc) is targetPid then
        set frontmost of proc to true
        return "ok"
      end if
    end repeat
  end tell
  return "not-found"
end run
'''


def _split_hwnd(hwnd: int) -> int:
    """合成 hwnd（pid<<16 | index）から pid を取り出す。"""
    return int(hwnd) >> 16


class AppleScriptWindowManager:
    """osascript + System Events 経由で window list / activate を行う。"""

    def __init__(self, runner: Optional[object] = None):
        self._runner = runner or SubprocessRunner()

    def list_windows(self, visible_only: bool) -> List[Dict[str, Any]]:
        r = self._runner.run(
            ["osascript", "-e", _LIST_SCRIPT], None, 10.0, None)
        if not r.started:
            raise RuntimeError(
                "window list failed: osascript not found "
                "(this should ship with macOS)")
        if r.exit_code != 0:
            err = (r.stderr or b"").decode("utf-8", "replace").strip()
            # Automation 権限拒否の典型エラー文言を見たら hint を足す
            hint = ""
            if "-1743" in err or "not authorized" in err.lower():
                hint = (" — grant Automation permission to your terminal/agent in "
                        "System Settings > Privacy & Security > Automation")
            raise RuntimeError(f"window list failed: osascript exit={r.exit_code} ({err}){hint}")
        text = (r.stdout or b"").decode("utf-8", "replace")
        return parse_window_listing(text, visible_only)

    def activate(self, hwnd: int) -> bool:
        pid = _split_hwnd(hwnd)
        r = self._runner.run(
            ["osascript", "-e", _ACTIVATE_SCRIPT, "-", str(pid)],
            None, 10.0, None)
        if not r.started:
            raise RuntimeError("window activate failed: osascript not found")
        if r.exit_code != 0:
            err = (r.stderr or b"").decode("utf-8", "replace").strip()
            hint = ""
            if "-1743" in err or "not authorized" in err.lower():
                hint = (" — grant Automation permission in System Settings > "
                        "Privacy & Security > Automation")
            raise RuntimeError(f"window activate failed: osascript exit={r.exit_code} ({err}){hint}")
        out = (r.stdout or b"").decode("utf-8", "replace").strip()
        return out == "ok"


def build_window(runner=None):
    """darwin/__init__.py から呼ばれるファクトリ。"""
    return AppleScriptWindowManager(runner=runner)
