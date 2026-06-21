"""menu.py — macOS のメニュー backend（AX: AXMenuBar 辿り + AXPress）。

handlers.MenuController を満たす AXMenuController。Win/Linux と同じく enumerate/invoke の 2 段で、
列挙時に各実行可能項目へ合成 command_id を振り、invoke でその AX 要素に AXPress を送る。実体の
ctypes（AXMenuBar の再帰列挙・AXUIElementPerformAction）は axlib.py。

  - enumerate(hwnd) : hwnd のアプリのメニューバーを再帰列挙して生ツリーを返す（handler が整形）。
                      メニューを持たない/アクセシビリティ非公開なら None（handler は supported:false）。
  - invoke(hwnd, command_id) : 直近 enumerate が振った command_id の項目に AXPress。

hwnd は CGWindowID。menu はアプリ単位なので、axlib が hwnd→アプリ要素→AXMenuBar と辿る。
command_id→AX要素 の対応は本インスタンスに保持し、次の enumerate で前回ぶんを解放する（Linux の
LinuxMenuController と同じ「id_map を毎回作り直す」流儀）。

権限: AX なので **Accessibility** が要る（未許可は actionable エラー）。menu は System Events を
経由しないので Automation は不要。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import axlib as _axlib

_ACCESSIBILITY_HINT = (
    "macOS menu access needs Accessibility (assistive access). Grant it in "
    "System Settings > Privacy & Security > Accessibility for the agent's python, "
    "then restart the agent.")


class AXMenuController:
    """AX メニューバーを列挙・発火する。command_id→AX要素 の対応をインスタンスに保持。"""

    def __init__(self, runner: Optional[object] = None):
        self._refs: Dict[int, Any] = {}

    def _release(self) -> None:
        for ref in self._refs.values():
            _axlib.cf_release(ref)
        self._refs = {}

    def enumerate(self, hwnd: int) -> Optional[List[Dict[str, Any]]]:
        if not _axlib.is_process_trusted():
            raise RuntimeError(_ACCESSIBILITY_HINT)
        self._release()  # 前回の id_map を解放してから振り直す
        nodes, refs = _axlib.enumerate_menu(int(hwnd))
        self._refs = refs
        return nodes  # None = メニュー無し（handler が supported:false）

    def invoke(self, hwnd: int, command_id: int) -> bool:
        ref = self._refs.get(int(command_id))
        if ref is None:
            return False  # 直近 enumerate に無い id（要 re-enumerate）
        return bool(_axlib.press_ref(ref))


def build_menu(runner=None):
    """darwin/__init__.py から呼ばれるファクトリ。"""
    return AXMenuController()
