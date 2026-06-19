"""x11lib.py — Linux backend の共有 X11 低レベル層（libX11/libXtst を ctypes 直叩き）。

_X11Lib（関数プロトタイプ設定とエラーハンドラ登録）、各 X 構造体（XImage / ClientMessage /
Selection / Property イベント）、X プロトコル定数、プロセス共有の _lib() を提供する。
clipboard / screenshot / keyboard / window がここから _lib と構造体・定数を取る。
"""

from __future__ import annotations

import ctypes
import sys
from typing import Optional

IS_LINUX = sys.platform.startswith("linux")

# X11 定数
_ZPIXMAP = 2
_CLIENT_MESSAGE = 33
_SUBSTRUCTURE_NOTIFY = 1 << 19
_SUBSTRUCTURE_REDIRECT = 1 << 20
_ALL_PLANES = (1 << (8 * ctypes.sizeof(ctypes.c_ulong))) - 1


def _on_x_error(display, event):  # pragma: no cover - 実機 X11 でのみ通る
    """Xlib の既定エラーハンドラは protocol error で exit() する——それを無効化する。

    別プロセスのウィンドウを触る以上 BadWindow / BadValue は日常的に起きる。既定動作の
    ままだと不正な XID 一発で loophole 自身が落ちるので、何もしない（0 を返す）ハンドラに
    差し替える。win_backends が SendMessageTimeout(ABORTIFHUNG) で固まりを防ぐのと同じ精神。
    """
    return 0


_XERRORHANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)


class _X11Lib:
    """libX11 / libXtst をロードし、使う関数の argtypes/restype を設定して保持する。

    64bit では argtypes/restype を明示しないと、Window/Atom（ポインタ幅）が既定の
    c_int に切り詰められて壊れる（win_backends と同じ規律）。Display は各操作ごとに
    開閉してスレッド安全性を担保するので、ここはライブラリと関数の設定だけを持つ。
    """

    def __init__(self):
        if not IS_LINUX:
            raise RuntimeError("X11 backends are Linux-only")
        try:
            x = ctypes.CDLL("libX11.so.6")
        except OSError as exc:
            raise RuntimeError(
                "libX11.so.6 not found — install the X11 client library "
                "(Debian/Ubuntu: libx11-6, Fedora: libX11)") from exc
        try:
            xtst = ctypes.CDLL("libXtst.so.6")
        except OSError:
            xtst = None  # キーボードだけ未対応にして他は活かす

        ulong = ctypes.c_ulong
        void = ctypes.c_void_p
        cint = ctypes.c_int

        x.XOpenDisplay.argtypes = [ctypes.c_char_p]; x.XOpenDisplay.restype = void
        x.XCloseDisplay.argtypes = [void]; x.XCloseDisplay.restype = cint
        x.XDefaultRootWindow.argtypes = [void]; x.XDefaultRootWindow.restype = ulong
        x.XSync.argtypes = [void, cint]; x.XSync.restype = cint
        x.XFlush.argtypes = [void]; x.XFlush.restype = cint
        x.XFree.argtypes = [void]; x.XFree.restype = cint
        x.XInternAtom.argtypes = [void, ctypes.c_char_p, cint]; x.XInternAtom.restype = ulong
        x.XRaiseWindow.argtypes = [void, ulong]; x.XRaiseWindow.restype = cint
        x.XGetGeometry.argtypes = [
            void, ulong, ctypes.POINTER(ulong), ctypes.POINTER(cint), ctypes.POINTER(cint),
            ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
        x.XGetGeometry.restype = cint
        x.XGetImage.argtypes = [void, ulong, cint, cint, ctypes.c_uint, ctypes.c_uint,
                                ulong, cint]
        x.XGetImage.restype = ctypes.POINTER(_XImage)
        x.XGetWindowProperty.argtypes = [
            void, ulong, ulong, ctypes.c_long, ctypes.c_long, cint, ulong,
            ctypes.POINTER(ulong), ctypes.POINTER(cint), ctypes.POINTER(ulong),
            ctypes.POINTER(ulong), ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte))]
        x.XGetWindowProperty.restype = cint
        x.XSendEvent.argtypes = [void, ulong, cint, ctypes.c_long, void]
        x.XSendEvent.restype = cint
        x.XKeysymToKeycode.argtypes = [void, ulong]; x.XKeysymToKeycode.restype = ctypes.c_ubyte
        x.XSetErrorHandler.argtypes = [void]; x.XSetErrorHandler.restype = void
        # --- セレクション（クリップボード）所有・転送・イベントループ ---
        x.XCreateSimpleWindow.argtypes = [void, ulong, cint, cint, ctypes.c_uint,
                                          ctypes.c_uint, ctypes.c_uint, ulong, ulong]
        x.XCreateSimpleWindow.restype = ulong
        x.XSetSelectionOwner.argtypes = [void, ulong, ulong, ulong]
        x.XSetSelectionOwner.restype = cint
        x.XGetSelectionOwner.argtypes = [void, ulong]; x.XGetSelectionOwner.restype = ulong
        x.XConvertSelection.argtypes = [void, ulong, ulong, ulong, ulong, ulong]
        x.XConvertSelection.restype = cint
        x.XChangeProperty.argtypes = [void, ulong, ulong, ulong, cint, cint, void, cint]
        x.XChangeProperty.restype = cint
        x.XDeleteProperty.argtypes = [void, ulong, ulong]; x.XDeleteProperty.restype = cint
        x.XConnectionNumber.argtypes = [void]; x.XConnectionNumber.restype = cint
        x.XPending.argtypes = [void]; x.XPending.restype = cint
        x.XNextEvent.argtypes = [void, void]; x.XNextEvent.restype = cint
        x.XSelectInput.argtypes = [void, ulong, ctypes.c_long]; x.XSelectInput.restype = cint

        # Xlib の致命的 exit を抑止（参照を握って GC させない）。
        self._err_cb = _XERRORHANDLER(_on_x_error)
        x.XSetErrorHandler(ctypes.cast(self._err_cb, void))

        if xtst is not None:
            xtst.XTestFakeKeyEvent.argtypes = [void, ctypes.c_uint, cint, ulong]
            xtst.XTestFakeKeyEvent.restype = cint
            # マウス: 絶対移動（screen=-1 で現在のスクリーン）とボタン押下。
            xtst.XTestFakeMotionEvent.argtypes = [void, cint, cint, cint, ulong]
            xtst.XTestFakeMotionEvent.restype = cint
            xtst.XTestFakeButtonEvent.argtypes = [void, ctypes.c_uint, cint, ulong]
            xtst.XTestFakeButtonEvent.restype = cint

        self.x = x
        self.xtst = xtst

    def open_display(self):
        dpy = self.x.XOpenDisplay(None)
        if not dpy:
            raise RuntimeError("cannot open X display (is DISPLAY set and reachable?)")
        return dpy

    def intern(self, dpy, name: str) -> int:
        return int(self.x.XInternAtom(dpy, name.encode("ascii"), False))

    def get_property(self, dpy, win: int, prop: int, req_type: int = 0):
        """XGetWindowProperty の薄いラッパ。(type_atom, format, nitems, data_bytes) or None。"""
        x = self.x
        actual_type = ctypes.c_ulong()
        actual_format = ctypes.c_int()
        nitems = ctypes.c_ulong()
        bytes_after = ctypes.c_ulong()
        prop_ptr = ctypes.POINTER(ctypes.c_ubyte)()
        status = x.XGetWindowProperty(
            dpy, win, prop, 0, 1 << 20, False, req_type,
            ctypes.byref(actual_type), ctypes.byref(actual_format),
            ctypes.byref(nitems), ctypes.byref(bytes_after), ctypes.byref(prop_ptr))
        if status != 0:  # Success == 0
            return None
        if not prop_ptr:
            return None
        fmt = actual_format.value
        n = nitems.value
        if fmt == 8:
            nbytes = n
        elif fmt == 16:
            nbytes = n * 2
        elif fmt == 32:
            nbytes = n * ctypes.sizeof(ctypes.c_ulong)
        else:
            nbytes = 0
        data = ctypes.string_at(prop_ptr, nbytes) if nbytes else b""
        x.XFree(ctypes.cast(prop_ptr, ctypes.c_void_p))
        return int(actual_type.value), fmt, n, data


class _XImageFuncs(ctypes.Structure):
    # XImage 末尾の関数テーブル。destroy_image だけ呼ぶ（残りはポインタ幅の穴埋め）。
    _fields_ = [
        ("create_image", ctypes.c_void_p),
        ("destroy_image", ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p)),
        ("get_pixel", ctypes.c_void_p),
        ("put_pixel", ctypes.c_void_p),
        ("sub_image", ctypes.c_void_p),
        ("add_pixel", ctypes.c_void_p),
    ]


class _XImage(ctypes.Structure):
    _fields_ = [
        ("width", ctypes.c_int),
        ("height", ctypes.c_int),
        ("xoffset", ctypes.c_int),
        ("format", ctypes.c_int),
        ("data", ctypes.c_void_p),
        ("byte_order", ctypes.c_int),
        ("bitmap_unit", ctypes.c_int),
        ("bitmap_bit_order", ctypes.c_int),
        ("bitmap_pad", ctypes.c_int),
        ("depth", ctypes.c_int),
        ("bytes_per_line", ctypes.c_int),
        ("bits_per_pixel", ctypes.c_int),
        ("red_mask", ctypes.c_ulong),
        ("green_mask", ctypes.c_ulong),
        ("blue_mask", ctypes.c_ulong),
        ("obdata", ctypes.c_void_p),
        ("f", _XImageFuncs),
    ]


class _XClientMessageEvent(ctypes.Structure):
    # XSendEvent に渡す ClientMessage。実体は 24 個の long 幅バッファに重ねて使う
    # （Xlib は XEvent 全体を value コピーするので、構造体より大きい器に載せる必要がある）。
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("window", ctypes.c_ulong),
        ("message_type", ctypes.c_ulong),
        ("format", ctypes.c_int),
        ("data_l", ctypes.c_long * 5),
    ]


class _XSelectionRequestEvent(ctypes.Structure):
    # 別アプリが「あなたが所有する CLIPBOARD の中身をくれ」と要求してくるイベント（type 30）。
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("owner", ctypes.c_ulong),
        ("requestor", ctypes.c_ulong),
        ("selection", ctypes.c_ulong),
        ("target", ctypes.c_ulong),
        ("property", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
    ]


class _XSelectionEvent(ctypes.Structure):
    # こちらの XConvertSelection への返答（SelectionNotify, type 31）。送信時にも組み立てる。
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("requestor", ctypes.c_ulong),
        ("selection", ctypes.c_ulong),
        ("target", ctypes.c_ulong),
        ("property", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
    ]


class _XPropertyEvent(ctypes.Structure):
    # プロパティ変更通知（PropertyNotify, type 28）。INCR 転送のチャンク受け渡しに使う。
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("window", ctypes.c_ulong),
        ("atom", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("state", ctypes.c_int),   # 0=NewValue, 1=Delete
    ]


# X イベント種別（必要なものだけ）
_PROPERTY_NOTIFY = 28
_SELECTION_CLEAR = 29
_SELECTION_REQUEST = 30
_SELECTION_NOTIFY = 31
_XA_ATOM = 4        # 定義済みアトム XA_ATOM（TARGETS 応答の型）
_PROP_MODE_REPLACE = 0
_CURRENT_TIME = 0
_PROPERTY_NEW_VALUE = 0
_PROPERTY_DELETE = 1
_PROPERTY_CHANGE_MASK = 1 << 22


_LIB: Optional[_X11Lib] = None


def _lib() -> _X11Lib:
    """プロセス共有の _X11Lib を返す（プロトタイプ設定とエラーハンドラ登録は 1 回でよい）。"""
    global _LIB
    if _LIB is None:
        _LIB = _X11Lib()
    return _LIB
