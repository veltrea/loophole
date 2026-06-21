"""keyboard.py — macOS のキーボード入力 backend（CGEvent 経由）。

handlers.KeyboardSender プロトコルを満たす CGEventKeyboard を提供する。

設計:
- handlers から (modifier VK のリスト, main VK) を受け取る。VK は Windows 仮想キーコードの
  数値（共通のキー名解釈は keys.py が VK_TO_KVK で吸収する）。
- CGEventCreateKeyboardEvent で main の押下/離放イベントを 2 つ作り、CGEventSetFlags で
  修飾フラグを乗せ、CGEventPost(HIDEventTap) で流す。
- macOS の修飾は **個別のキーダウンイベントを別に送る**よりも、main の press/release に
  flags を載せる方が確実（pythonista の CGEvent サンプルや AppleScript の "key code" の
  振る舞いと一致）。修飾「キー単体」を送りたい時は main がそのまま修飾キーなので、
  flags=0 + 修飾の press/release で送る。
- TCC（Accessibility 権限）が必要。未許可だと CGEventPost が黙って無視するのが macOS の
  仕様 → ここでは投げない（呼び元の `hello.tcc.accessibility` で事前警告する）。
"""

from __future__ import annotations

import ctypes
from typing import List

import keys as keyspec
from .cglib import (
    FLAG_ALPHASHIFT, FLAG_ALTERNATE, FLAG_COMMAND, FLAG_CONTROL, FLAG_SHIFT,
    HID_SYSTEM_STATE, TAP_HID, _lib,
)


# Windows VK 値 → macOS の CGEventFlags のビット。「修飾として乗せる」ためのテーブル。
# keys.py の _VK_SHIFT/CONTROL/MENU/LWIN を流用する（不変の数値）。
_VK_SHIFT = 0x10
_VK_CONTROL = 0x11
_VK_ALT = 0x12     # menu/alt
_VK_CMD = 0x5B     # win/super/cmd/meta → macOS では Command

_VK_TO_CGFLAG = {
    _VK_SHIFT: FLAG_SHIFT,
    _VK_CONTROL: FLAG_CONTROL,
    _VK_ALT: FLAG_ALTERNATE,
    _VK_CMD: FLAG_COMMAND,
}


def _flags_for(modifiers: List[int]) -> int:
    """修飾 VK のリストを CGEventFlags ビットマスクに畳む。"""
    flags = 0
    for vk in modifiers:
        bit = _VK_TO_CGFLAG.get(vk)
        if bit is None:
            # 未知の修飾は無視するのではなく、明示的に弾く（呼び元のテストで気づける）。
            raise RuntimeError(
                f"keyboard: unsupported modifier VK 0x{vk:02x} on macOS "
                "(only shift/ctrl/alt/cmd are recognized)")
        flags |= bit
    return flags


def _main_kvk(main_vk: int) -> int:
    """main の Windows VK を macOS kVK_* にマップ（無ければ actionable）。"""
    kvk = keyspec.VK_TO_KVK.get(main_vk)
    if kvk is None:
        raise RuntimeError(
            f"keyboard: VK 0x{main_vk:02x} has no macOS key code equivalent")
    return kvk


class CGEventKeyboard:
    """CGEvent 経由でキーストロークを 1 打鍵分送る。

    send_chord(modifiers, main) で 1 ストローク:
      1. CGEventSource を作る（HIDSystemState）
      2. main の press + release を作り、それぞれに flags を乗せる
      3. CGEventPost(HIDEventTap) で流す
      4. event / source を CFRelease

    例外設計:
    - source 生成失敗（TCC 失敗時はこれが通る挙動もある）→ RuntimeError
    - キーコード変換不能 → RuntimeError（actionable）
    - CGEventPost 自体は戻り値が void なので「届いたか」は不明（macOS 仕様）。
      呼び元は `hello.tcc.accessibility` を事前に見て警告する。
    """

    def __init__(self):
        self._lib = _lib()

    def send_chord(self, modifiers: List[int], main: int) -> None:
        lib = self._lib
        cg = lib.cg
        cf = lib.cf

        # 修飾を flags に畳む（main 自身が修飾キーの場合は modifiers が空、flags=0）。
        flags = _flags_for(modifiers)
        kvk = _main_kvk(main)

        source = cg.CGEventSourceCreate(HID_SYSTEM_STATE)
        if not source:
            raise RuntimeError(
                "keyboard: CGEventSourceCreate failed "
                "(likely Accessibility permission denied)")

        try:
            press = cg.CGEventCreateKeyboardEvent(source, kvk, 1)
            if not press:
                raise RuntimeError("keyboard: CGEventCreateKeyboardEvent(press) failed")
            try:
                if flags:
                    cg.CGEventSetFlags(press, flags)
                cg.CGEventPost(TAP_HID, press)
            finally:
                cf.CFRelease(press)

            release = cg.CGEventCreateKeyboardEvent(source, kvk, 0)
            if not release:
                raise RuntimeError("keyboard: CGEventCreateKeyboardEvent(release) failed")
            try:
                if flags:
                    cg.CGEventSetFlags(release, flags)
                cg.CGEventPost(TAP_HID, release)
            finally:
                cf.CFRelease(release)
        finally:
            cf.CFRelease(source)

    def type_text(self, text: str) -> None:
        """文字列を 1 文字ずつ、その Unicode を press/release イベントに載せて打ち込む。

        CGEventKeyboardSetUnicodeString はキーコード（配列）も IME も通さず、その文字を
        前面アプリへ直接届ける。ゆえに日本語でも化けない（Windows の KEYEVENTF_UNICODE 相当）。
        BMP 外は UTF-16 サロゲートペアに分割して 1 イベントに載せる。TCC（Accessibility）が要る。
        """
        lib = self._lib
        cg, cf = lib.cg, lib.cf

        source = cg.CGEventSourceCreate(HID_SYSTEM_STATE)
        if not source:
            raise RuntimeError(
                "keyboard: CGEventSourceCreate failed "
                "(likely Accessibility permission denied)")
        try:
            for ch in text:
                units = self._utf16_units(ch)
                buf = (ctypes.c_uint16 * len(units))(*units)
                for down in (1, 0):
                    ev = cg.CGEventCreateKeyboardEvent(source, 0, down)
                    if not ev:
                        raise RuntimeError("keyboard: CGEventCreateKeyboardEvent failed")
                    try:
                        cg.CGEventKeyboardSetUnicodeString(
                            ev, len(units), ctypes.cast(buf, ctypes.c_void_p))
                        cg.CGEventPost(TAP_HID, ev)
                    finally:
                        cf.CFRelease(ev)
        finally:
            cf.CFRelease(source)

    @staticmethod
    def _utf16_units(text: str) -> List[int]:
        """文字列を UTF-16 コードユニット（16bit 整数）の並びにする。BMP 外はサロゲート対。"""
        raw = text.encode("utf-16-le")
        return [raw[i] | (raw[i + 1] << 8) for i in range(0, len(raw), 2)]


def build_keyboard(runner=None):
    """darwin/__init__.py から呼ばれるファクトリ（runner は不要だが API を揃える）。"""
    return CGEventKeyboard()
