"""mouse.py — macOS のマウス操作 backend（CGEvent 経由）。

handlers.MouseController プロトコルを満たす CGEventMouse。

設計:
- move(x, y): CGEventCreateMouseEvent(MouseMoved, (x, y), 0) → Post。
- button(b, down): 押下/解放を CGEventCreateMouseEvent で作る。ボタン番号は handlers の
  既定（1=左 / 2=中 / 3=右）。
- scroll(dx, dy): CGEventCreateScrollWheelEvent(LineUnit, axes=2, dy, dx)。
  macOS は単位を Line（クリック数相当）/Pixel で選べる。computer use 等の挙動と揃え
  「クリック数で渡したい」用途を一級にして Line を使う。

座標は画面の絶対ピクセル（左上原点）。マルチディスプレイ環境はメインディスプレイの
論理座標で渡せば WindowServer が解釈する。
"""

from __future__ import annotations

from .cglib import (
    CGPoint, ET_LEFT_MOUSE_DOWN, ET_LEFT_MOUSE_DRAGGED, ET_LEFT_MOUSE_UP,
    ET_MOUSE_MOVED, ET_OTHER_MOUSE_DOWN, ET_OTHER_MOUSE_DRAGGED, ET_OTHER_MOUSE_UP,
    ET_RIGHT_MOUSE_DOWN, ET_RIGHT_MOUSE_DRAGGED, ET_RIGHT_MOUSE_UP,
    HID_SYSTEM_STATE, MOUSE_CENTER, MOUSE_LEFT, MOUSE_RIGHT,
    SCROLL_UNIT_LINE, TAP_HID, _lib,
)


# handlers の button 番号（1=左 / 2=中 / 3=右）→ (CGMouseButton, down/up/dragged event types)
_BUTTON_MAP = {
    1: (MOUSE_LEFT, ET_LEFT_MOUSE_DOWN, ET_LEFT_MOUSE_UP, ET_LEFT_MOUSE_DRAGGED),
    2: (MOUSE_CENTER, ET_OTHER_MOUSE_DOWN, ET_OTHER_MOUSE_UP, ET_OTHER_MOUSE_DRAGGED),
    3: (MOUSE_RIGHT, ET_RIGHT_MOUSE_DOWN, ET_RIGHT_MOUSE_UP, ET_RIGHT_MOUSE_DRAGGED),
}


class CGEventMouse:
    """CGEvent 経由でマウス操作を送る。"""

    def __init__(self):
        self._lib = _lib()

    def _make_source(self):
        source = self._lib.cg.CGEventSourceCreate(HID_SYSTEM_STATE)
        if not source:
            raise RuntimeError(
                "mouse: CGEventSourceCreate failed "
                "(likely Accessibility permission denied)")
        return source

    def move(self, x: int, y: int) -> None:
        lib = self._lib
        cg = lib.cg
        cf = lib.cf
        source = self._make_source()
        try:
            ev = cg.CGEventCreateMouseEvent(
                source, ET_MOUSE_MOVED, CGPoint(float(x), float(y)), MOUSE_LEFT)
            if not ev:
                raise RuntimeError("mouse: CGEventCreateMouseEvent(move) failed")
            try:
                cg.CGEventPost(TAP_HID, ev)
            finally:
                cf.CFRelease(ev)
        finally:
            cf.CFRelease(source)

    def button(self, button: int, down: bool) -> None:
        spec = _BUTTON_MAP.get(int(button))
        if spec is None:
            raise RuntimeError(f"mouse: unsupported button {button} (use 1/2/3)")
        cg_button, down_type, up_type, _drag_type = spec
        event_type = down_type if down else up_type
        lib = self._lib
        cg = lib.cg
        cf = lib.cf
        source = self._make_source()
        try:
            # 現在のカーソル位置は事前に判らないので (0, 0) を渡す
            # （CGEvent のセマンティクス上、event_type と button があれば WindowServer は
            # 現在位置を保持してイベントを解釈する。それでも mouse_click 直前は
            # mouse_move を打つのが推奨運用 — handlers の mouse_click が両方を順に呼ぶ）。
            ev = cg.CGEventCreateMouseEvent(
                source, event_type, CGPoint(0.0, 0.0), cg_button)
            if not ev:
                raise RuntimeError(f"mouse: CGEventCreateMouseEvent(button {button}) failed")
            try:
                cg.CGEventPost(TAP_HID, ev)
            finally:
                cf.CFRelease(ev)
        finally:
            cf.CFRelease(source)

    def scroll(self, dx: int, dy: int) -> None:
        """dy > 0 で下方向、dx > 0 で右方向（handlers のコメントに合わせる）。

        macOS の CGEventCreateScrollWheelEvent は **wheel1 が「上方向」が正**なので
        handlers の意味（下が正）と逆。符号を反転して渡す。
        """
        lib = self._lib
        cg = lib.cg
        cf = lib.cf
        source = self._make_source()
        try:
            # 軸数 2、wheel1 = -dy（上正→下正に反転）、wheel2 = -dx（右正→左正に反転）
            ev = cg.CGEventCreateScrollWheelEvent(
                source, SCROLL_UNIT_LINE, 2, int(-dy), int(-dx))
            if not ev:
                raise RuntimeError("mouse: CGEventCreateScrollWheelEvent failed")
            try:
                cg.CGEventPost(TAP_HID, ev)
            finally:
                cf.CFRelease(ev)
        finally:
            cf.CFRelease(source)

    def drag(self, x1: int, y1: int, x2: int, y2: int, button: int = 1, steps: int = 24) -> None:
        """(x1,y1) で押し、補間しながら (x2,y2) まで動かして離す（本物のドラッグ）。

        単なる「down → move → up」だと macOS は MOUSE_MOVED を流すだけでドラッグと見なさない
        アプリがある。途中を **LeftMouseDragged**（ボタン別の DRAGGED イベント）で送ることで、
        テキスト範囲選択・スライダー・D&D が正しく成立する。steps で中間点の数を決める。
        """
        spec = _BUTTON_MAP.get(int(button))
        if spec is None:
            raise RuntimeError(f"mouse: unsupported button {button} (use 1/2/3)")
        cg_button, down_type, up_type, drag_type = spec
        n = max(1, int(steps))
        lib = self._lib
        cg, cf = lib.cg, lib.cf
        source = self._make_source()

        def _post(event_type, px, py):
            ev = cg.CGEventCreateMouseEvent(source, event_type, CGPoint(float(px), float(py)), cg_button)
            if not ev:
                raise RuntimeError("mouse: CGEventCreateMouseEvent(drag) failed")
            try:
                cg.CGEventPost(TAP_HID, ev)
            finally:
                cf.CFRelease(ev)

        try:
            _post(ET_MOUSE_MOVED, x1, y1)   # 開始点へ
            _post(down_type, x1, y1)        # 押下
            for i in range(1, n + 1):       # 補間ドラッグ
                t = i / n
                _post(drag_type, x1 + (x2 - x1) * t, y1 + (y2 - y1) * t)
            _post(up_type, x2, y2)          # 終点で解放
        finally:
            cf.CFRelease(source)


def build_mouse(runner=None):
    """darwin/__init__.py から呼ばれるファクトリ（runner は不要だが API を揃える）。"""
    return CGEventMouse()
