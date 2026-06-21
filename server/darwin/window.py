"""window.py — macOS のウィンドウ列挙・操作 backend（AX + CGWindowID）。

handlers.WindowManager を満たす AXWindowManager。**ウィンドウ識別子は CGWindowID**（WindowServer の
window number）で、z-order やタイトルに左右されない安定 ID。これにより従来の osascript 実装が抱えて
いた「z-order が動くと index が陳腐化」(-1719) / フルスクリーン窓の再アドレス不可 (-1728) /
osascript argv の罠を解消する。実体の ctypes 配線は axlib.py。

  - list_windows : 全アプリのトップレベル窓を {hwnd=CGWindowID, title, pid, minimized, x,y,width,height}
                   で返す（最小化窓も含む。geometry も同時に返せるようになった）。
  - activate     : 指定窓を**窓単位で**前面化（AXRaise）＋アプリを frontmost に。
  - set_window   : 位置/サイズ/最小化/フルスクリーン/最大化/前面化を AX 属性直叩きで設定し、
                   適用後の実測 state を読み戻す。

権限: AX の読み書きには **Accessibility（補助アクセス）** が必須。未許可なら actionable な
RuntimeError を投げる（黙って空を返さない）。`hello.tcc.accessibility` がこのゲートを手前で示す。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import axlib as _axlib


_ACCESSIBILITY_HINT = (
    "macOS window access needs Accessibility (assistive access). Grant it in "
    "System Settings > Privacy & Security > Accessibility for the agent's python, "
    "then restart the agent.")


class AXWindowManager:
    """AX + CGWindowID でウィンドウを列挙・操作する。

    実 ctypes は axlib モジュールに委譲する。テストでは ax にフェイクモジュールを注入できる
    （ctypes/実 OS を呼ばずに list/activate/set のロジックを検証する）。
    """

    def __init__(self, ax=None, runner: Optional[object] = None):
        # runner は API 互換のため受けるだけ（AX backend は subprocess を使わない）。
        self._ax = ax if ax is not None else _axlib

    def _require_trusted(self) -> None:
        if not self._ax.is_process_trusted():
            raise RuntimeError(_ACCESSIBILITY_HINT)

    def list_windows(self, visible_only: bool) -> List[Dict[str, Any]]:
        self._require_trusted()
        wins = self._ax.list_windows()
        if visible_only:
            wins = [w for w in wins if str(w.get("title", "")).strip()]
        return [{
            "hwnd": w["hwnd"], "title": w["title"], "pid": w["pid"],
            "minimized": w["minimized"],
            "x": w["x"], "y": w["y"], "width": w["width"], "height": w["height"],
        } for w in wins]

    def activate(self, hwnd: int) -> bool:
        self._require_trusted()
        return bool(self._ax.raise_window(int(hwnd)))

    def set_window(self, hwnd: int, position=None, size=None,
                   minimized: Optional[bool] = None, fullscreen: Optional[bool] = None,
                   maximized: Optional[bool] = None, raise_: Optional[bool] = None) -> Dict[str, Any]:
        """CGWindowID の窓の geometry/状態を設定し、適用後 state を返す。

        position=(x,y) / size=(w,h) / minimized / fullscreen は省略可（None=触らない）。
        maximized=True で使用可能領域に最大化、raise_=True で窓単位の前面化。
        窓が見つからない（閉じた等）ときは actionable な RuntimeError。
        """
        self._require_trusted()
        state = self._ax.set_window(
            int(hwnd), position=position, size=size,
            minimized=minimized, fullscreen=fullscreen,
            maximized=bool(maximized), do_raise=bool(raise_))
        if state is None:
            raise RuntimeError(
                f"no window with id {hwnd} (it may have closed or been re-created; "
                "re-run list_windows to get a current id)")
        return state


def build_window(runner=None):
    """darwin/__init__.py から呼ばれるファクトリ。"""
    return AXWindowManager(runner=runner)
