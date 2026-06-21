"""keyboard.py — Linux のキー送出 backend。

X11Keyboard（XTEST）と WaylandKeyboard（ydotool）。build_keyboard がディスプレイ種別で選ぶ。
"""

from __future__ import annotations

import ctypes
from typing import List

import keys as keyspec
from common_backends import SubprocessRunner, UnsupportedBackend, try_build
from .x11lib import _lib
from .ydotool import not_installed_message, wrap_failure


def _char_to_keysym(ch: str) -> int:
    """1 文字を X11 keysym に直す（type_text の Unicode 注入用・純粋ロジック）。

    Latin-1（U+0000..U+00FF）は keysym がコードポイントと同値。それ以外は X11 の
    Unicode keysym 規約 `0x01000000 | codepoint` を使う（xdotool / 近年の X サーバが解する）。
    """
    cp = ord(ch)
    return cp if cp <= 0xFF else (cp | 0x01000000)


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

    def type_text(self, text: str) -> None:
        """文字列をそのまま打ち込む。各文字を**現在のレイアウトにある実キーコード**で XTEST 注入する。

        当初は xdotool 風に「空きキーコードへ文字ごとに再マップして叩く」方式にしたが、実機
        （Ubuntu/GNOME・X11）で全滅した: 再マップ→キー→即復元のレースで、注入側がマッピングを
        戻した後にターゲットが keysym を読む（XRefreshKeyboardMapping はサーバの現在値を引く）ため
        NoSymbol になる。xdotool 自身もこの環境では非 ASCII を取りこぼした。よって**グローバル
        キーマップを一切書き換えず**、レイアウトに存在する文字だけを実キーコード（必要なら shift）で
        送る方式にした。レイアウトに無い文字（日本語など）は actionable error で弾き、クリップボード
        貼り付けへ誘導する。これは「Linux の text は ASCII・直接入力向き／日本語は貼り付け」という
        設計上の割り切りと一致し、キーマップ破壊もレースも無い。

        注意: XTEST の合成キーは**有効な IME（XIM/fcitx/ibus）を通る**。日本語入力 ON のまま
        ASCII を打つと拾われて化けるので、必要なら ime_set(open=False) を先に。Windows の Unicode
        直接注入（KEYEVENTF_UNICODE）と違い、IME を迂回はしない。
        """
        lib = self._lib
        x, xtst = lib.x, lib.xtst
        dpy = lib.open_display()
        try:
            shift_code = self._keycode(dpy, 0x10)   # VK_SHIFT → Shift_L のキーコード
            for ch in text:
                code, need_shift = self._char_keycode(dpy, ch)
                if need_shift:
                    xtst.XTestFakeKeyEvent(dpy, shift_code, True, 0)
                xtst.XTestFakeKeyEvent(dpy, code, True, 0)
                xtst.XTestFakeKeyEvent(dpy, code, False, 0)
                if need_shift:
                    xtst.XTestFakeKeyEvent(dpy, shift_code, False, 0)
                x.XSync(dpy, False)
        finally:
            x.XCloseDisplay(dpy)

    def _char_keycode(self, dpy, ch: str) -> tuple[int, bool]:
        """1 文字を (keycode, need_shift) に解決する。レイアウトに無い文字は actionable error。"""
        x = self._lib.x
        keysym = _char_to_keysym(ch)
        code = int(x.XKeysymToKeycode(dpy, keysym))
        if not code:
            raise RuntimeError(
                f"character {ch!r} (keysym {keysym:#x}) is not on the current X11 keyboard "
                f"layout — type_text on Linux only sends layout characters (mostly ASCII). "
                f"For Japanese/other text use clipboard paste (clipboard_set + ctrl+v).")
        lvl0, lvl1 = self._keysyms_for(dpy, code)
        if keysym == lvl0:
            return code, False
        if keysym == lvl1:
            return code, True
        raise RuntimeError(
            f"character {ch!r} needs a key level this tool doesn't drive (AltGr / dead key) "
            f"— use clipboard paste for it.")

    def _keysyms_for(self, dpy, code: int) -> tuple[int, int]:
        """1 キーコードの level0 / level1 keysym を返す（shift 要否の判定用）。"""
        x = self._lib.x
        per_ref = ctypes.c_int()
        mapping = x.XGetKeyboardMapping(dpy, code, 1, ctypes.byref(per_ref))
        if not mapping:
            raise RuntimeError("XGetKeyboardMapping failed")
        try:
            n = int(per_ref.value)
            lvl0 = int(mapping[0]) if n >= 1 else 0
            lvl1 = int(mapping[1]) if n >= 2 else 0
            return lvl0, lvl1
        finally:
            x.XFree(ctypes.cast(mapping, ctypes.c_void_p))


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

    def type_text(self, text: str) -> None:
        """文字列をそのまま打ち込む（`ydotool type`）。`--` でオプション解釈を止め先頭ダッシュも安全に。

        ydotool type は文字を US 配列のキーコードへ写してカーネル入力層へ流すので、
        実用上 ASCII 向き（配列に無い日本語等は打てない）。さらに合成キーは**有効な IME を通る**
        ので、日本語入力 ON のままだと化ける。本文の日本語はクリップボード貼り付けを使うこと。
        """
        r = self._runner.run(["ydotool", "type", "--", text], None, 10.0, None)
        if not r.started:
            raise RuntimeError(f"type_text: {not_installed_message()}")
        if r.exit_code != 0:
            raise RuntimeError("type_text: " + wrap_failure(self._runner, r.stderr or b""))


def build_keyboard(server, runner):
    if server == "x11":
        return try_build(X11Keyboard, "send_keys requires X11 + XTEST")
    if server == "wayland":
        return try_build(lambda: WaylandKeyboard(runner), "send_keys requires ydotool on Wayland")
    return UnsupportedBackend("send_keys requires a graphical session (no DISPLAY)")
