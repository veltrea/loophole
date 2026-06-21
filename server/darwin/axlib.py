"""axlib.py — Accessibility (AX) + CoreGraphics ウィンドウ列挙の ctypes 配線（共有層）。

window.py が「osascript + z-order index」の脆さ（-1719/-1728/argv の罠）から脱するための土台。
cglib.py（CGEvent 用）と同じ「薄い ctypes ヘルパ」方針で、CoreGraphics / CoreFoundation /
ApplicationServices(AX) を直接叩く。

要点:
  - **ウィンドウ識別子は CGWindowID**（WindowServer が振る window number）。生成〜破棄まで不変・
    システム全体で一意・z-order やタイトルに左右されない。これを loophole の hwnd に採用する
    ことで「z-order が動くと index が陳腐化」(-1719) を原理的に消す。
  - 列挙は `CGWindowListCopyWindowInfo` で候補 pid を拾い、各 pid の AX ウィンドウを辿って
    CGWindowID（`_AXUIElementGetWindow`）・タイトル・最小化・位置/サイズを得る。最小化窓も含む。
  - 操作は AX 属性の直接 set（AXPosition/AXSize/AXMinimized/AXFullScreen）と AXRaise アクション。

権限: AX の読み書きには **Accessibility（補助アクセス）** が要る（未許可で -25211 相当の失敗）。
`_AXUIElementGetWindow` は私的 API だが yabai/Hammerspoon 等が常用しており実務上安定。見つから
なければ list の CGWindowID 取得だけ諦め、pid+frame 突合にフォールバックする（_match_window）。

import 自体は副作用無し（Linux でも import できる）。framework のロードは _lib() 初回呼び出し時。
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import (
    POINTER, byref, c_bool, c_char, c_double, c_int32, c_long, c_uint32, c_void_p,
)
from ctypes import util as _ctutil
from typing import Any, Dict, List, Optional, Tuple

from .cglib import CGPoint


class CGSize(ctypes.Structure):
    _fields_ = [("width", c_double), ("height", c_double)]


class CGRect(ctypes.Structure):
    _fields_ = [("origin", CGPoint), ("size", CGSize)]


# ---- 定数 -------------------------------------------------------------------

_kCFStringEncodingUTF8 = 0x08000100

# CFNumberType（CFNumberGetValue 用）
_kCFNumberSInt32Type = 3
_kCFNumberSInt64Type = 4
_kCFNumberDoubleType = 13

# AXValueType（AXValueCreate/GetValue 用）
_kAXValueCGPointType = 1
_kAXValueCGSizeType = 2

# CGWindowListOption
_kCGWindowListOptionAll = 0
_kCGWindowListExcludeDesktopElements = 1 << 4
_kCGNullWindowID = 0

# AXError
_kAXErrorSuccess = 0

# AX 属性名 / アクション名は不変の文字列リテラル。kAX* 定数を dlsym せず CFString を直接作る。
_AX_WINDOWS = "AXWindows"
_AX_POSITION = "AXPosition"
_AX_SIZE = "AXSize"
_AX_MINIMIZED = "AXMinimized"
_AX_FULLSCREEN = "AXFullScreen"
_AX_TITLE = "AXTitle"
_AX_FRONTMOST = "AXFrontmost"
_AX_RAISE = "AXRaise"
# メニュー（menu_enumerate / menu_invoke 用）
_AX_MENU_BAR = "AXMenuBar"
_AX_CHILDREN = "AXChildren"
_AX_ENABLED = "AXEnabled"
_AX_MARKCHAR = "AXMenuItemMarkChar"
_AX_ROLE = "AXRole"
_AX_PRESS = "AXPress"
_AX_ROLE_MENU = "AXMenu"

# CGWindowList の辞書キーも定数の CFString 値＝キー名そのものなので CFString を直接作って引ける。
_CGW_NUMBER = "kCGWindowNumber"
_CGW_OWNER_PID = "kCGWindowOwnerPID"
_CGW_NAME = "kCGWindowName"
_CGW_BOUNDS = "kCGWindowBounds"
_CGW_LAYER = "kCGWindowLayer"


class _AXLib:
    """CoreGraphics + CoreFoundation + ApplicationServices(AX) を束ねる singleton。

    全関数の argtypes/restype を明示する（cglib と同じ理由：64bit でのポインタ切り詰め回避）。
    """

    def __init__(self):
        if sys.platform != "darwin":
            raise RuntimeError("axlib is only available on macOS (darwin)")

        cf_path = _ctutil.find_library("CoreFoundation")
        cg_path = _ctutil.find_library("CoreGraphics") or _ctutil.find_library(
            "ApplicationServices")
        ax_path = _ctutil.find_library("ApplicationServices")
        if not (cf_path and cg_path and ax_path):
            raise RuntimeError("CoreFoundation / CoreGraphics / ApplicationServices not found")

        self.cf = ctypes.CDLL(cf_path)
        self.cg = ctypes.CDLL(cg_path)
        self.ax = ctypes.CDLL(ax_path)

        cf, cg, ax = self.cf, self.cg, self.ax

        # ---- CoreFoundation --------------------------------------------------
        cf.CFRelease.restype = None
        cf.CFRelease.argtypes = [c_void_p]
        cf.CFArrayGetCount.restype = c_long
        cf.CFArrayGetCount.argtypes = [c_void_p]
        cf.CFArrayGetValueAtIndex.restype = c_void_p
        cf.CFArrayGetValueAtIndex.argtypes = [c_void_p, c_long]
        cf.CFDictionaryGetValue.restype = c_void_p
        cf.CFDictionaryGetValue.argtypes = [c_void_p, c_void_p]
        cf.CFNumberGetValue.restype = c_bool
        cf.CFNumberGetValue.argtypes = [c_void_p, c_long, c_void_p]
        cf.CFStringCreateWithCString.restype = c_void_p
        cf.CFStringCreateWithCString.argtypes = [c_void_p, c_void_p, c_uint32]
        cf.CFStringGetCString.restype = c_bool
        cf.CFStringGetCString.argtypes = [c_void_p, c_void_p, c_long, c_uint32]
        cf.CFStringGetLength.restype = c_long
        cf.CFStringGetLength.argtypes = [c_void_p]
        cf.CFBooleanGetValue.restype = c_bool
        cf.CFBooleanGetValue.argtypes = [c_void_p]
        cf.CFRetain.restype = c_void_p
        cf.CFRetain.argtypes = [c_void_p]
        # CFBoolean 定数（AXFullScreen/AXMinimized の set 用）
        self.kCFBooleanTrue = c_void_p.in_dll(cf, "kCFBooleanTrue")
        self.kCFBooleanFalse = c_void_p.in_dll(cf, "kCFBooleanFalse")

        # ---- CoreGraphics ----------------------------------------------------
        cg.CGWindowListCopyWindowInfo.restype = c_void_p
        cg.CGWindowListCopyWindowInfo.argtypes = [c_uint32, c_uint32]
        cg.CGRectMakeWithDictionaryRepresentation.restype = c_bool
        cg.CGRectMakeWithDictionaryRepresentation.argtypes = [c_void_p, POINTER(CGRect)]
        cg.CGMainDisplayID.restype = c_uint32
        cg.CGMainDisplayID.argtypes = []

        # ---- ApplicationServices (AX) ---------------------------------------
        ax.AXUIElementCreateApplication.restype = c_void_p
        ax.AXUIElementCreateApplication.argtypes = [c_int32]  # pid_t
        ax.AXUIElementCopyAttributeValue.restype = c_int32    # AXError
        ax.AXUIElementCopyAttributeValue.argtypes = [c_void_p, c_void_p, POINTER(c_void_p)]
        ax.AXUIElementSetAttributeValue.restype = c_int32
        ax.AXUIElementSetAttributeValue.argtypes = [c_void_p, c_void_p, c_void_p]
        ax.AXUIElementPerformAction.restype = c_int32
        ax.AXUIElementPerformAction.argtypes = [c_void_p, c_void_p]
        ax.AXValueCreate.restype = c_void_p
        ax.AXValueCreate.argtypes = [c_int32, c_void_p]
        ax.AXValueGetValue.restype = c_bool
        ax.AXValueGetValue.argtypes = [c_void_p, c_int32, c_void_p]
        ax.AXIsProcessTrusted.restype = c_bool
        ax.AXIsProcessTrusted.argtypes = []
        # 私的 API（CGWindowID 突合）。無い環境では None にして pid+frame 突合へ退避。
        try:
            self._get_window = ax._AXUIElementGetWindow
            self._get_window.restype = c_int32
            self._get_window.argtypes = [c_void_p, POINTER(c_uint32)]
        except AttributeError:  # pragma: no cover - 環境依存
            self._get_window = None

    # ---- CF 変換ヘルパ ------------------------------------------------------
    def cfstr(self, s: str) -> c_void_p:
        """py str -> CFStringRef（呼び元が CFRelease する）。"""
        ref = self.cf.CFStringCreateWithCString(
            None, s.encode("utf-8"), _kCFStringEncodingUTF8)
        return c_void_p(ref)

    def cfstr_to_py(self, ref) -> str:
        """CFStringRef -> py str（None/空は ""）。"""
        if not ref:
            return ""
        n = self.cf.CFStringGetLength(ref)
        buf = ctypes.create_string_buffer((n + 1) * 4)  # UTF-8 最悪 4byte/char
        ok = self.cf.CFStringGetCString(ref, buf, len(buf), _kCFStringEncodingUTF8)
        return buf.value.decode("utf-8", "replace") if ok else ""

    def cfnum_to_int(self, ref) -> Optional[int]:
        out = c_int32(0)
        if ref and self.cf.CFNumberGetValue(ref, _kCFNumberSInt32Type, byref(out)):
            return out.value
        out64 = ctypes.c_int64(0)
        if ref and self.cf.CFNumberGetValue(ref, _kCFNumberSInt64Type, byref(out64)):
            return out64.value
        return None


_LIB: "Optional[_AXLib]" = None


def _lib() -> "_AXLib":
    global _LIB
    if _LIB is None:
        _LIB = _AXLib()
    return _LIB


def is_process_trusted() -> bool:
    """自プロセスが Accessibility 信頼済みか（prompt なし）。"""
    return bool(_lib().ax.AXIsProcessTrusted())


# ---- CGWindowList で候補 pid を拾う ----------------------------------------

def _normal_window_pids() -> List[int]:
    """レイヤ0（通常ウィンドウ）を持つアプリの pid を重複なく返す。

    最小化のみのアプリも拾えるよう ExcludeDesktopElements だけ付けて全件取得し、layer==0 を残す。
    背景常駐（layer!=0 / ウィンドウ無し）は自然に落ちる。
    """
    lib = _lib()
    cf = lib.cf
    arr = lib.cg.CGWindowListCopyWindowInfo(
        _kCGWindowListOptionAll | _kCGWindowListExcludeDesktopElements, _kCGNullWindowID)
    if not arr:
        return []
    pids: List[int] = []
    seen = set()
    k_pid = lib.cfstr(_CGW_OWNER_PID)
    k_layer = lib.cfstr(_CGW_LAYER)
    try:
        n = cf.CFArrayGetCount(arr)
        for i in range(n):
            d = cf.CFArrayGetValueAtIndex(arr, i)
            layer = lib.cfnum_to_int(cf.CFDictionaryGetValue(d, k_layer))
            if layer != 0:
                continue
            pid = lib.cfnum_to_int(cf.CFDictionaryGetValue(d, k_pid))
            if pid and pid not in seen:
                seen.add(pid)
                pids.append(pid)
    finally:
        cf.CFRelease(k_pid)
        cf.CFRelease(k_layer)
        cf.CFRelease(arr)
    return pids


# ---- AX ウィンドウ操作 ------------------------------------------------------

def _copy_attr(el: c_void_p, attr_ref: c_void_p) -> Optional[c_void_p]:
    """AXUIElementCopyAttributeValue。成功時は CFTypeRef（呼び元が CFRelease）、失敗時 None。"""
    lib = _lib()
    out = c_void_p()
    err = lib.ax.AXUIElementCopyAttributeValue(el, attr_ref, byref(out))
    if err != _kAXErrorSuccess or not out:
        return None
    return out


def _window_id_of(el: c_void_p) -> Optional[int]:
    """AX ウィンドウ要素の CGWindowID を私的 API で得る。無理なら None。"""
    lib = _lib()
    if lib._get_window is None:
        return None
    wid = c_uint32(0)
    if lib._get_window(el, byref(wid)) == _kAXErrorSuccess and wid.value:
        return wid.value
    return None


def _ax_point(el: c_void_p, attr_ref: c_void_p) -> Optional[Tuple[int, int]]:
    lib = _lib()
    v = _copy_attr(el, attr_ref)
    if not v:
        return None
    try:
        p = CGPoint()
        if lib.ax.AXValueGetValue(v, _kAXValueCGPointType, byref(p)):
            return (int(round(p.x)), int(round(p.y)))
        return None
    finally:
        lib.cf.CFRelease(v)


def _ax_size(el: c_void_p, attr_ref: c_void_p) -> Optional[Tuple[int, int]]:
    lib = _lib()
    v = _copy_attr(el, attr_ref)
    if not v:
        return None
    try:
        s = CGSize()
        if lib.ax.AXValueGetValue(v, _kAXValueCGSizeType, byref(s)):
            return (int(round(s.width)), int(round(s.height)))
        return None
    finally:
        lib.cf.CFRelease(v)


def _ax_bool(el: c_void_p, attr_ref: c_void_p) -> Optional[bool]:
    lib = _lib()
    v = _copy_attr(el, attr_ref)
    if not v:
        return None
    try:
        return bool(lib.cf.CFBooleanGetValue(v))
    finally:
        lib.cf.CFRelease(v)


def _ax_title(el: c_void_p, attr_ref: c_void_p) -> str:
    lib = _lib()
    v = _copy_attr(el, attr_ref)
    if not v:
        return ""
    try:
        return lib.cfstr_to_py(v)
    finally:
        lib.cf.CFRelease(v)


def list_windows() -> List[Dict[str, Any]]:
    """全アプリのトップレベルウィンドウを CGWindowID 付きで列挙する。

    返り値の各要素: {"hwnd": CGWindowID(int), "title": str, "pid": int, "minimized": bool,
                     "x": int, "y": int, "width": int, "height": int}。
    最小化窓も含む（AX の AXWindows は最小化窓も返す）。CGWindowID が取れない窓は落とす
    （hwnd 無しでは操作できないため）。
    """
    lib = _lib()
    out: List[Dict[str, Any]] = []

    a_windows = lib.cfstr(_AX_WINDOWS)
    a_title = lib.cfstr(_AX_TITLE)
    a_min = lib.cfstr(_AX_MINIMIZED)
    a_pos = lib.cfstr(_AX_POSITION)
    a_size = lib.cfstr(_AX_SIZE)
    try:
        for pid in _normal_window_pids():
            app = lib.ax.AXUIElementCreateApplication(pid)
            if not app:
                continue
            app = c_void_p(app)
            try:
                wins = _copy_attr(app, a_windows)
                if not wins:
                    continue
                try:
                    n = lib.cf.CFArrayGetCount(wins)
                    for i in range(n):
                        w = lib.cf.CFArrayGetValueAtIndex(wins, i)
                        if not w:
                            continue
                        w = c_void_p(w)
                        wid = _window_id_of(w)
                        if wid is None:
                            continue
                        pos = _ax_point(w, a_pos) or (0, 0)
                        size = _ax_size(w, a_size) or (0, 0)
                        out.append({
                            "hwnd": wid,
                            "title": _ax_title(w, a_title),
                            "pid": pid,
                            "minimized": bool(_ax_bool(w, a_min)),
                            "x": pos[0], "y": pos[1],
                            "width": size[0], "height": size[1],
                        })
                finally:
                    lib.cf.CFRelease(wins)
            finally:
                lib.cf.CFRelease(app)
    finally:
        for r in (a_windows, a_title, a_min, a_pos, a_size):
            lib.cf.CFRelease(r)
    return out


def _find_ax_window(hwnd: int) -> "Optional[Tuple[c_void_p, c_void_p]]":
    """CGWindowID から (app要素, window要素) を解決する。呼び元が両方 CFRelease する。

    見つからなければ None。pid を絞らず全アプリを舐めるが、_normal_window_pids が小さいので可。
    """
    lib = _lib()
    a_windows = lib.cfstr(_AX_WINDOWS)
    try:
        for pid in _normal_window_pids():
            app = lib.ax.AXUIElementCreateApplication(pid)
            if not app:
                continue
            app = c_void_p(app)
            wins = _copy_attr(app, a_windows)
            if not wins:
                lib.cf.CFRelease(app)
                continue
            try:
                n = lib.cf.CFArrayGetCount(wins)
                for i in range(n):
                    w = lib.cf.CFArrayGetValueAtIndex(wins, i)
                    if w and _window_id_of(c_void_p(w)) == hwnd:
                        # w は wins の要素なので retain して返す（wins 解放後も生かす）
                        wkeep = c_void_p(lib.cf.CFRetain(w))
                        return (app, wkeep)
            finally:
                lib.cf.CFRelease(wins)
            lib.cf.CFRelease(app)
        return None
    finally:
        lib.cf.CFRelease(a_windows)


def _set_attr(el: c_void_p, attr_name: str, value_ref: c_void_p) -> bool:
    lib = _lib()
    a = lib.cfstr(attr_name)
    try:
        return lib.ax.AXUIElementSetAttributeValue(el, a, value_ref) == _kAXErrorSuccess
    finally:
        lib.cf.CFRelease(a)


def _set_point(el: c_void_p, attr_name: str, x: int, y: int) -> bool:
    lib = _lib()
    p = CGPoint(float(x), float(y))
    v = lib.ax.AXValueCreate(_kAXValueCGPointType, byref(p))
    if not v:
        return False
    try:
        return _set_attr(el, attr_name, c_void_p(v))
    finally:
        lib.cf.CFRelease(v)


def _set_size(el: c_void_p, attr_name: str, w: int, h: int) -> bool:
    lib = _lib()
    s = CGSize(float(w), float(h))
    v = lib.ax.AXValueCreate(_kAXValueCGSizeType, byref(s))
    if not v:
        return False
    try:
        return _set_attr(el, attr_name, c_void_p(v))
    finally:
        lib.cf.CFRelease(v)


def _set_bool(el: c_void_p, attr_name: str, on: bool) -> bool:
    lib = _lib()
    return _set_attr(el, attr_name, lib.kCFBooleanTrue if on else lib.kCFBooleanFalse)


def _set_bool_settle(el: c_void_p, attr_name: str, on: bool, tries: int = 8) -> None:
    """AXMinimized/AXFullScreen は**アニメーションで非同期**に反映される。set した直後に読むと
    旧状態が返るので、実測が要求値に追いつく（or タイムアウト）まで軽くポーリングして、戻り値の
    state を正直にする。窓が非対応で set が効かない場合はタイムアウトして実測（=効いていない）を残す。
    """
    lib = _lib()
    _set_bool(el, attr_name, on)
    a = lib.cfstr(attr_name)
    try:
        for _ in range(tries):
            if bool(_ax_bool(el, a)) == bool(on):
                return
            time.sleep(0.1)
    finally:
        lib.cf.CFRelease(a)


def visible_frame(hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    """maximize 用の「使用可能領域」(x,y,w,h)。当面はメイン display 全体を返す近似。

    本来は対象 display の visibleFrame（メニューバー/Dock を除く）を返したいが、Cocoa を使わず
    厳密に取るのは手間なので、まずメイン display 全体（CGDisplayBounds 相当）で maximize する。
    将来 NSScreen.visibleFrame の objc ブリッジに差し替える余地を残す。
    """
    lib = _lib()
    # CGDisplayBounds は struct 返しなので restype を都度設定して呼ぶ。
    lib.cg.CGDisplayBounds.restype = CGRect
    lib.cg.CGDisplayBounds.argtypes = [c_uint32]
    did = lib.cg.CGMainDisplayID()
    r = lib.cg.CGDisplayBounds(did)
    # メニューバーぶんだけ上を空ける素朴な近似（notch 無し既定 ≈ 25pt）。
    menubar = 25
    return (int(r.origin.x), int(r.origin.y) + menubar,
            int(r.size.width), int(r.size.height) - menubar)


def _read_state(w: c_void_p) -> Dict[str, Any]:
    """AX ウィンドウ要素の現在の位置/サイズ/最小化/フルスクリーンを読む。"""
    lib = _lib()
    a_pos = lib.cfstr(_AX_POSITION)
    a_size = lib.cfstr(_AX_SIZE)
    a_min = lib.cfstr(_AX_MINIMIZED)
    a_fs = lib.cfstr(_AX_FULLSCREEN)
    try:
        pos = _ax_point(w, a_pos) or (0, 0)
        sz = _ax_size(w, a_size) or (0, 0)
        return {"x": pos[0], "y": pos[1], "width": sz[0], "height": sz[1],
                "minimized": bool(_ax_bool(w, a_min)), "fullscreen": bool(_ax_bool(w, a_fs))}
    finally:
        for r in (a_pos, a_size, a_min, a_fs):
            lib.cf.CFRelease(r)


def raise_window(hwnd: int) -> bool:
    """CGWindowID の窓を「窓単位で」前面化する（AXRaise）＋アプリを frontmost に。

    activate（アプリ全体の前面化）と違い、特定の窓1枚を最前面へ持ち上げる。見つからなければ False。
    """
    found = _find_ax_window(hwnd)
    if found is None:
        return False
    app, w = found
    lib = _lib()
    try:
        a = lib.cfstr(_AX_RAISE)
        ok = lib.ax.AXUIElementPerformAction(w, a) == _kAXErrorSuccess
        lib.cf.CFRelease(a)
        _set_bool(app, _AX_FRONTMOST, True)  # アプリ自体も前面に
        return ok
    finally:
        lib.cf.CFRelease(w)
        lib.cf.CFRelease(app)


def set_window(hwnd: int, position=None, size=None, minimized=None,
               fullscreen=None, maximized: bool = False, do_raise: bool = False) -> Optional[Dict[str, Any]]:
    """CGWindowID の窓の位置/サイズ/最小化/フルスクリーン/最大化/前面化を設定し、適用後 state を返す。

    見つからなければ None。適用順は raise → maximize → position → size → fullscreen → minimize
    （最小化は隠すので最後）。maximized は visible_frame に position+size を当てる。
    """
    found = _find_ax_window(hwnd)
    if found is None:
        return None
    app, w = found
    lib = _lib()
    try:
        if do_raise:
            a = lib.cfstr(_AX_RAISE)
            lib.ax.AXUIElementPerformAction(w, a)
            lib.cf.CFRelease(a)
            _set_bool(app, _AX_FRONTMOST, True)
        if maximized:
            vf = visible_frame(hwnd)
            if vf:
                _set_point(w, _AX_POSITION, vf[0], vf[1])
                _set_size(w, _AX_SIZE, vf[2], vf[3])
        if position is not None:
            _set_point(w, _AX_POSITION, int(position[0]), int(position[1]))
        if size is not None:
            _set_size(w, _AX_SIZE, int(size[0]), int(size[1]))
        if fullscreen is not None:
            _set_bool_settle(w, _AX_FULLSCREEN, bool(fullscreen))
        if minimized is not None:
            _set_bool_settle(w, _AX_MINIMIZED, bool(minimized))
        return _read_state(w)
    finally:
        lib.cf.CFRelease(w)
        lib.cf.CFRelease(app)


# ---- メニュー（AXMenuBar 辿り + AXPress）------------------------------------

def cf_release(ref) -> None:
    """retained な AX/CF 参照を解放する（menu の id_map 後始末用に公開）。"""
    if ref:
        _lib().cf.CFRelease(ref)


def _children(el: c_void_p) -> List[c_void_p]:
    """el の AXChildren を AXUIElementRef のリストで返す（各 +1 retained。呼び元が release）。"""
    lib = _lib()
    a = lib.cfstr(_AX_CHILDREN)
    arr = _copy_attr(el, a)
    lib.cf.CFRelease(a)
    if not arr:
        return []
    out: List[c_void_p] = []
    try:
        n = lib.cf.CFArrayGetCount(arr)
        for i in range(n):
            ch = lib.cf.CFArrayGetValueAtIndex(arr, i)
            if ch:
                out.append(c_void_p(lib.cf.CFRetain(ch)))
    finally:
        lib.cf.CFRelease(arr)
    return out


def _str_attr(el: c_void_p, attr_name: str) -> str:
    lib = _lib()
    a = lib.cfstr(attr_name)
    try:
        return _ax_title(el, a)
    finally:
        lib.cf.CFRelease(a)


def _bool_attr(el: c_void_p, attr_name: str) -> bool:
    lib = _lib()
    a = lib.cfstr(attr_name)
    try:
        return bool(_ax_bool(el, a))
    finally:
        lib.cf.CFRelease(a)


def press_ref(ref: c_void_p) -> bool:
    """AXUIElement に AXPress を送る（メニュー項目の発火）。"""
    lib = _lib()
    a = lib.cfstr(_AX_PRESS)
    try:
        return lib.ax.AXUIElementPerformAction(ref, a) == _kAXErrorSuccess
    finally:
        lib.cf.CFRelease(a)


def _menu_bar_of(hwnd: int) -> "Optional[c_void_p]":
    """hwnd の所属アプリの AXMenuBar 要素を返す（+1 retained）or None。"""
    found = _find_ax_window(hwnd)
    if found is None:
        return None
    app, w = found
    lib = _lib()
    lib.cf.CFRelease(w)
    a = lib.cfstr(_AX_MENU_BAR)
    mb = _copy_attr(app, a)
    lib.cf.CFRelease(a)
    lib.cf.CFRelease(app)
    return mb


def _axmenu_child(el: c_void_p) -> "Optional[c_void_p]":
    """el の子のうち最初の AXMenu（ドロップダウン）を +1 で返す。他の子は release。無ければ None。"""
    kept = None
    for ch in _children(el):
        if kept is None and _str_attr(ch, _AX_ROLE) == _AX_ROLE_MENU:
            kept = ch
        else:
            cf_release(ch)
    return kept


def _walk_menu(menu_el: c_void_p, refs: Dict[int, Any], counter: List[int],
               depth: int, max_depth: int = 12) -> List[Dict[str, Any]]:
    """AXMenu / AXMenuBar の子（メニュー項目）を handlers 形式のノード列にする。

    各項目: サブメニューを持てば {"label","enabled","checked","command_id":None,"submenu":[...]}、
    実行可能なら command_id を採番し refs[command_id] に AXUIElement を +1 で保持（invoke 用）、
    タイトル空で子無しは {"separator": True}。
    """
    if depth > max_depth:
        return []
    out: List[Dict[str, Any]] = []
    for item in _children(menu_el):
        try:
            title = _str_attr(item, _AX_TITLE)
            sub_menu = _axmenu_child(item)
            if sub_menu is not None:
                sub_nodes = _walk_menu(sub_menu, refs, counter, depth + 1, max_depth)
                cf_release(sub_menu)
                out.append({"label": title, "enabled": _bool_attr(item, _AX_ENABLED),
                            "checked": bool(_str_attr(item, _AX_MARKCHAR)),
                            "command_id": None, "submenu": sub_nodes})
            elif not title.strip():
                out.append({"separator": True})
            else:
                cid = counter[0]
                counter[0] += 1
                refs[cid] = c_void_p(_lib().cf.CFRetain(item))
                out.append({"label": title, "enabled": _bool_attr(item, _AX_ENABLED),
                            "checked": bool(_str_attr(item, _AX_MARKCHAR)),
                            "command_id": cid, "submenu": None})
        finally:
            cf_release(item)
    return out


def enumerate_menu(hwnd: int) -> "tuple":
    """hwnd のアプリのメニューバーを列挙する。

    戻り値 (nodes, refs): nodes は handlers 形式の生ツリー（メニュー無し＝None）、refs は
    {command_id: 保持した AXUIElement}（invoke で press_ref する。呼び元が cf_release で後始末）。
    """
    mb = _menu_bar_of(hwnd)
    if mb is None:
        return None, {}
    refs: Dict[int, Any] = {}
    counter = [1]
    try:
        nodes = _walk_menu(mb, refs, counter, 0)
    finally:
        cf_release(mb)
    return nodes, refs


# ---- self-test（mini で `python3 -m darwin.axlib` 相当に直接実行して配線確認）-----------

if __name__ == "__main__":  # pragma: no cover
    print("AXIsProcessTrusted:", is_process_trusted())
    ws = list_windows()
    print(f"windows: {len(ws)}")
    for w in ws[:20]:
        print(f"  hwnd={w['hwnd']} pid={w['pid']} min={w['minimized']} "
              f"{w['x']},{w['y']} {w['width']}x{w['height']} {w['title']!r}")
