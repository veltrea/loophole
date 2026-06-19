"""keyboard.py — Linux のキー送出 backend。

X11Keyboard（XTEST）と WaylandKeyboard（ydotool）。build_keyboard がディスプレイ種別で選ぶ。
"""

from __future__ import annotations

from typing import List

import keys as keyspec
from common_backends import SubprocessRunner, UnsupportedBackend, try_build
from .x11lib import _lib
from .ydotool import not_installed_message, wrap_failure


class X11Keyboard:
    """XTEST（XTestFakeKeyEvent）で「修飾キー + メインキー」を物理キー入力として送る。Linux/X11 専用。

    handlers から渡る Windows 仮想キーコード列を keys.VK_TO_KEYSYM で X11 keysym に直し、
    現在のキーボード配列で keysym→keycode を引いて注入する。入力はフォーカスのある
    ウィンドウへ届く（win_backends.SendInputKeyboard と同じ「前面へ送る」流儀）。
    """

    def __init__(self):
        self._lib = _lib()
        if self._lib.xtst is None:
            raise RuntimeError(
                "libXtst.so.6 not found — install the XTEST extension library "
                "(Debian/Ubuntu: libxtst6, Fedora: libXtst)")

    def _keycode(self, dpy, vk: int) -> int:
        keysym = keyspec.VK_TO_KEYSYM.get(vk)
        if keysym is None:
            raise RuntimeError(f"no X11 keysym mapping for vk={vk:#04x}")
        code = self._lib.x.XKeysymToKeycode(dpy, keysym)
        if not code:
            raise RuntimeError(
                f"keysym {keysym:#06x} (vk={vk:#04x}) has no keycode on this layout")
        return int(code)

    def send_chord(self, modifiers: List[int], main: int) -> None:
        lib = self._lib
        x, xtst = lib.x, lib.xtst
        dpy = lib.open_display()
        try:
            mod_codes = [self._keycode(dpy, vk) for vk in modifiers]
            main_code = self._keycode(dpy, main)
            # 修飾を順に押す → メインを押して離す → 修飾を逆順で離す（Windows 版と同じ順序）。
            for code in mod_codes:
                xtst.XTestFakeKeyEvent(dpy, code, True, 0)
            xtst.XTestFakeKeyEvent(dpy, main_code, True, 0)
            xtst.XTestFakeKeyEvent(dpy, main_code, False, 0)
            for code in reversed(mod_codes):
                xtst.XTestFakeKeyEvent(dpy, code, False, 0)
            x.XSync(dpy, False)
        finally:
            x.XCloseDisplay(dpy)
class WaylandKeyboard:
    """Wayland のショートカット送出。ydotool（/dev/uinput）でカーネル入力層へ注入する。

    Wayland には X11 の XTEST に当たる統一入力注入が無い（virtual-keyboard プロトコルは
    wlroots 系専用）。ydotool はコンポジタの**下**（uinput）で動くので GNOME/KDE/sway を
    問わず効く。ただし ydotoold の常駐と /dev/uinput への権限が要る。ydotool はキー名でなく
    Linux evdev コードを取るので keys.VK_TO_EVDEV で変換する。
    """

    def __init__(self, runner=None):
        self._runner = runner or SubprocessRunner()

    def _evdev(self, vk: int) -> int:
        code = keyspec.VK_TO_EVDEV.get(vk)
        if code is None:
            raise RuntimeError(f"no evdev code for vk={vk:#04x}")
        return code

    def send_chord(self, modifiers: List[int], main: int) -> None:
        mod_codes = [self._evdev(vk) for vk in modifiers]
        main_code = self._evdev(main)
        seq: List[str] = [f"{c}:1" for c in mod_codes]
        seq += [f"{main_code}:1", f"{main_code}:0"]
        seq += [f"{c}:0" for c in reversed(mod_codes)]
        r = self._runner.run(["ydotool", "key"] + seq, None, 5.0, None)
        if not r.started:
            raise RuntimeError(f"send_keys: {not_installed_message()}")
        if r.exit_code != 0:
            raise RuntimeError("send_keys: " + wrap_failure(self._runner, r.stderr or b""))


def build_keyboard(server, runner):
    if server == "x11":
        return try_build(X11Keyboard, "send_keys requires X11 + XTEST")
    if server == "wayland":
        return try_build(lambda: WaylandKeyboard(runner), "send_keys requires ydotool on Wayland")
    return UnsupportedBackend("send_keys requires a graphical session (no DISPLAY)")
