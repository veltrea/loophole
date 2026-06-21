"""cglib.py — CoreGraphics / CoreFoundation の ctypes 配線（darwin パッケージの共有層）。

keyboard.py / mouse.py が CGEventCreate* / CGEventPost を呼ぶのに使う薄いラッパ。
Linux の x11lib.py（X11/libXtst を ctypes で叩く）と同じ位置づけ。

- CoreGraphics framework は macOS に最初から入っているのでロード失敗は基本起きない。
  もし失敗したら _lib() が RuntimeError を投げる（呼び元の try_build が UnsupportedBackend に
  倒す）。
- CGEvent / CGEventSource の生存管理は CFRelease で行う（ARC ではないので手動）。

import 自体は副作用無し（Linux でも import できる）。実際に framework をロードするのは
_lib() を最初に呼んだとき。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import c_double, c_int, c_int32, c_uint32, c_uint64, c_void_p
from ctypes import util as _ctutil

# ---- 公開 CG 定数 -----------------------------------------------------------

# CGEventSourceStateID。0=HIDSystemState, 1=CombinedSessionState, 2=PrivateState。
# キー/マウス注入には HIDSystemState（実機の HID キューに乗せる挙動が一番自然）を使う。
HID_SYSTEM_STATE = 0
COMBINED_SESSION_STATE = 1
PRIVATE_STATE = 2

# CGEventTapLocation。CGEventPost の引き先。0=HIDEventTap、1=SessionEventTap、2=AnnotatedSessionEventTap。
# HID に流せば WindowServer 以前の最低層から流れるので一番確実。
TAP_HID = 0
TAP_SESSION = 1
TAP_ANNOTATED_SESSION = 2

# CGEventFlags（修飾キー）。CGEventCreateKeyboardEvent では使わず CGEventSetFlags 経由で乗せる。
# 値は Carbon の kCGEventFlagMask*（HIToolbox/CGEventTypes.h）。
FLAG_ALPHASHIFT = 0x00010000  # CapsLock
FLAG_SHIFT      = 0x00020000
FLAG_CONTROL    = 0x00040000
FLAG_ALTERNATE  = 0x00080000  # Option
FLAG_COMMAND    = 0x00100000

# CGMouseButton。
MOUSE_LEFT = 0
MOUSE_RIGHT = 1
MOUSE_CENTER = 2

# CGEventType（マウス系）。
ET_LEFT_MOUSE_DOWN = 1
ET_LEFT_MOUSE_UP = 2
ET_RIGHT_MOUSE_DOWN = 3
ET_RIGHT_MOUSE_UP = 4
ET_MOUSE_MOVED = 5
ET_LEFT_MOUSE_DRAGGED = 6
ET_RIGHT_MOUSE_DRAGGED = 7
ET_OTHER_MOUSE_DOWN = 25
ET_OTHER_MOUSE_UP = 26
ET_OTHER_MOUSE_DRAGGED = 27

# CGScrollEventUnit。0=Pixel, 1=Line。Line でクリック数相当を渡す。
SCROLL_UNIT_PIXEL = 0
SCROLL_UNIT_LINE = 1


class CGPoint(ctypes.Structure):
    _fields_ = [("x", c_double), ("y", c_double)]


class _CGLib:
    """CoreGraphics + CoreFoundation の必要関数だけを束ねる singleton。

    全関数の argtypes/restype を明示することで 64bit Mac でのポインタ切り詰めを防ぐ
    （ctypes は restype 未指定だと c_int を返すため 64bit ポインタが壊れる）。
    """

    def __init__(self):
        if sys.platform != "darwin":
            raise RuntimeError("CoreGraphics is only available on macOS (darwin)")

        cg_path = _ctutil.find_library("CoreGraphics") or _ctutil.find_library(
            "ApplicationServices")
        cf_path = _ctutil.find_library("CoreFoundation")
        if not cg_path:
            raise RuntimeError(
                "CoreGraphics framework not found "
                "(neither CoreGraphics nor ApplicationServices in linker path)")
        if not cf_path:
            raise RuntimeError("CoreFoundation framework not found")

        self.cg = ctypes.CDLL(cg_path)
        self.cf = ctypes.CDLL(cf_path)

        # ---- イベントソース ---------------------------------------------------
        self.cg.CGEventSourceCreate.restype = c_void_p
        self.cg.CGEventSourceCreate.argtypes = [c_uint32]

        # ---- キーボードイベント ----------------------------------------------
        # CGEventRef CGEventCreateKeyboardEvent(CGEventSourceRef src, CGKeyCode keyCode, bool keyDown)
        self.cg.CGEventCreateKeyboardEvent.restype = c_void_p
        self.cg.CGEventCreateKeyboardEvent.argtypes = [c_void_p, c_uint32, c_int]

        # void CGEventSetFlags(CGEventRef event, CGEventFlags flags)
        self.cg.CGEventSetFlags.restype = None
        self.cg.CGEventSetFlags.argtypes = [c_void_p, c_uint64]
        self.cg.CGEventGetFlags.restype = c_uint64
        self.cg.CGEventGetFlags.argtypes = [c_void_p]

        # void CGEventKeyboardSetUnicodeString(CGEventRef, UniCharCount, const UniChar *)
        # UniChar=uint16(UTF-16) / UniCharCount=UInt32。文字を配列も IME も通さず本文入力する。
        self.cg.CGEventKeyboardSetUnicodeString.restype = None
        self.cg.CGEventKeyboardSetUnicodeString.argtypes = [c_void_p, c_uint32, c_void_p]

        # ---- マウスイベント ---------------------------------------------------
        # CGEventRef CGEventCreateMouseEvent(src, type, mouseCursorPosition, mouseButton)
        self.cg.CGEventCreateMouseEvent.restype = c_void_p
        self.cg.CGEventCreateMouseEvent.argtypes = [c_void_p, c_uint32, CGPoint, c_uint32]

        # CGEventRef CGEventCreateScrollWheelEvent(src, units, wheelCount, wheel1, ...)
        # 可変引数だが「軸 2 つ（縦・横）」固定で叩く。
        self.cg.CGEventCreateScrollWheelEvent.restype = c_void_p
        self.cg.CGEventCreateScrollWheelEvent.argtypes = [
            c_void_p, c_uint32, c_uint32, c_int32, c_int32]

        # ---- Post + Release ---------------------------------------------------
        # void CGEventPost(CGEventTapLocation tap, CGEventRef event)
        self.cg.CGEventPost.restype = None
        self.cg.CGEventPost.argtypes = [c_uint32, c_void_p]

        # CoreFoundation の CFRelease（CGEvent / CGEventSource を解放するのに使う）。
        self.cf.CFRelease.restype = None
        self.cf.CFRelease.argtypes = [c_void_p]


_LIB: "_CGLib | None" = None


def _lib() -> "_CGLib":
    """singleton として CoreGraphics ラッパを返す（初回呼び出しでロード）。"""
    global _LIB
    if _LIB is None:
        _LIB = _CGLib()
    return _LIB
