"""mouse.py — Linux のマウス操作 backend。

X11Mouse（XTEST: XTestFakeMotionEvent / XTestFakeButtonEvent）と WaylandMouse（ydotool）。
キー送出（keyboard.py）と同じく X11 は XTEST、Wayland は ydotool（uinput）に委ねる。
build_mouse がディスプレイ種別で選ぶ。座標は画面の絶対ピクセル。
"""

from __future__ import annotations

from common_backends import SubprocessRunner, UnsupportedBackend, try_build
from .x11lib import _lib
from .ydotool import not_installed_message, wrap_failure

# ホイールは X では擬似ボタン: 4=上 / 5=下 / 6=左 / 7=右。1 クリック = 押して離す。
_WHEEL_UP, _WHEEL_DOWN, _WHEEL_LEFT, _WHEEL_RIGHT = 4, 5, 6, 7


class X11Mouse:
    """XTEST でカーソル移動・ボタン・ホイールを送る。Linux/X11 専用。

    XTestFakeMotionEvent で絶対座標へ移動、XTestFakeButtonEvent でボタン（1=左/2=中/3=右）を
    押下/解放、ホイールは擬似ボタン 4/5/6/7 のクリックで表す。send_keys（X11Keyboard）と同じく
    libXtst を使い、操作のたびに Display を開閉してスレッド安全に保つ。
    """

    def __init__(self):
        self._lib = _lib()
        if self._lib.xtst is None:
            raise RuntimeError(
                "libXtst.so.6 not found — install the XTEST extension library "
                "(Debian/Ubuntu: libxtst6, Fedora: libXtst)")

    def move(self, x: int, y: int) -> None:
        lib = self._lib
        dpy = lib.open_display()
        try:
            lib.xtst.XTestFakeMotionEvent(dpy, -1, int(x), int(y), 0)
            lib.x.XSync(dpy, False)
        finally:
            lib.x.XCloseDisplay(dpy)

    def button(self, button: int, down: bool) -> None:
        lib = self._lib
        dpy = lib.open_display()
        try:
            lib.xtst.XTestFakeButtonEvent(dpy, int(button), bool(down), 0)
            lib.x.XSync(dpy, False)
        finally:
            lib.x.XCloseDisplay(dpy)

    def scroll(self, dx: int, dy: int) -> None:
        lib = self._lib
        dpy = lib.open_display()
        try:
            for btn, n in self._wheel_clicks(dx, dy):
                for _ in range(n):
                    lib.xtst.XTestFakeButtonEvent(dpy, btn, True, 0)
                    lib.xtst.XTestFakeButtonEvent(dpy, btn, False, 0)
            lib.x.XSync(dpy, False)
        finally:
            lib.x.XCloseDisplay(dpy)

    @staticmethod
    def _wheel_clicks(dx: int, dy: int):
        clicks = []
        if dy:
            clicks.append((_WHEEL_DOWN if dy > 0 else _WHEEL_UP, abs(dy)))
        if dx:
            clicks.append((_WHEEL_RIGHT if dx > 0 else _WHEEL_LEFT, abs(dx)))
        return clicks


class WaylandMouse:
    """Wayland 用: ydotool（/dev/uinput）でカーソル移動・ボタン・ホイールを送る。

    キー送出と同じく ydotool に委ねる。移動は絶対座標、ボタンは ydotool の click コード
    （0xC0=左/0xC1=右/0xC2=中、上位ニブル 0x40=押下 0x80=解放）。要 uinput 権限。
    """

    # ydotool click のボタンコード（番号 1/2/3 → ydotool コード）。0x40=down, 0x80=up を OR。
    _BTN = {1: 0x00, 3: 0x01, 2: 0x02}  # 左=0xC0 / 右=0xC1 / 中=0xC2 の下位
    _PRESS, _RELEASE = 0x40, 0x80

    def __init__(self, runner=None):
        self._runner = runner or SubprocessRunner()

    def _ydotool(self, argv):
        r = self._runner.run(["ydotool"] + argv, None, 5.0, None)
        if not r.started:
            raise RuntimeError(f"mouse: {not_installed_message()}")
        if r.exit_code != 0:
            raise RuntimeError("mouse: " + wrap_failure(self._runner, r.stderr or b""))

    def move(self, x: int, y: int) -> None:
        self._ydotool(["mousemove", "--absolute", "--", str(int(x)), str(int(y))])

    def button(self, button: int, down: bool) -> None:
        low = self._BTN.get(int(button))
        if low is None:
            raise RuntimeError(f"mouse: unsupported button {button}")
        code = (self._PRESS if down else self._RELEASE) | low
        self._ydotool(["click", f"0x{code:02X}"])

    def scroll(self, dx: int, dy: int) -> None:
        # ydotool mousemove --wheel で相対ホイール（y は下が正）。
        self._ydotool(["mousemove", "--wheel", "--", str(int(dx)), str(int(dy))])


def build_mouse(server, runner):
    if server == "x11":
        return try_build(X11Mouse, "mouse requires X11 + XTEST")
    if server == "wayland":
        return try_build(lambda: WaylandMouse(runner), "mouse requires ydotool on Wayland")
    return UnsupportedBackend("mouse requires a graphical session (no DISPLAY)")
