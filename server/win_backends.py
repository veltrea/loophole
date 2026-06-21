"""win_backends.py — handlers.py のインターフェースの Windows 実装（Win32 を ctypes 直叩き）。

Windows 固有の能力だけをここに置く:
  - Clipboard    : Win32 CF_UNICODETEXT を ctypes 直叩き（対話セッションで動く＝IME を通らない）
  - Screenshotter: BitBlt + GDI を ctypes 直叩きで全画面 PNG。任意で FFmpeg ddagrab
                   （DXGI Desktop Duplication）に切替＝GPU 描画も撮れる
  - KeyboardSender / WindowManager / ImeController / MenuController : SendInput・EnumWindows・
    IMM32・GetMenu を ctypes 直叩き（MenuController はクラシック HMENU を担当。HMENU を
    持たないモダンアプリ＝WPF/WinForms/UWP 等は win_uia_menu の UIA フォールバックに回す）

OS 非依存の Runner / FileSystem / Environment は common_backends に、撮影の画素変換・PNG
符号化は imaging に切り出してある（双方を Linux backend と共有する）。非 Windows でも
import できるように、Win32 専用クラスの __init__ は呼ばれたときだけ windll を触る。
PowerShell には一切依存しない。
"""

from __future__ import annotations

import ctypes
import os
import sys
import tempfile
import time
import uuid
from ctypes import wintypes
from typing import Any, Dict, List, Optional

from common_backends import (
    HostEnvironment,
    LocalFileSystem,
    SubprocessRunner,
    UnsupportedBackend,
)
# 撮影パイプラインの純関数は imaging に集約。テスト（test_win_backends）が参照する
# 旧称（_bgra_to_rgb / _encode_png / _grab_to_png）を別名で温存する。
from imaging import bgra_to_rgb as _bgra_to_rgb
from imaging import encode_png as _encode_png
from imaging import grab_to_png as _grab_to_png

IS_WINDOWS = sys.platform == "win32"


# ---- クリップボード: Win32 CF_UNICODETEXT を ctypes 直叩き ---------------------
#
# PowerShell 経由をやめ、Win32 クリップボード API を直接呼ぶ。CF_UNICODETEXT は
# UTF-16 固定なので、PowerShell のコンソールパイプで踏んでいた CP932 文字化けが
# 原理的に発生しない（base64 ワークアラウンドが不要になる）。
#
# 地雷と対策（MSDN / pyperclip 由来のベストプラクティス）:
#   - OpenClipboard は他プロセスがクリップボードを開いていると失敗する
#     → デッドライン付きでリトライ（pyperclip と同じ ~0.5 秒 / 10ms 間隔）。
#   - SetClipboardData は hwnd=NULL だと EmptyClipboard で owner=NULL になり失敗しうる
#     → 隠しウィンドウ（message-only STATIC）を作って所有者として渡す。
#   - SetClipboardData 成功後は HGLOBAL の所有権が OS に移る → GlobalFree してはいけない。
#     失敗時のみ自分で GlobalFree する。GlobalUnlock してから CloseClipboard する。

_CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 0x0002
_HWND_MESSAGE = -3  # CreateWindowEx の親に渡すと message-only ウィンドウになる


class Win32Clipboard:
    """Win32 クリップボード（CF_UNICODETEXT）を ctypes で読み書きする。Windows 専用。

    対話デスクトップセッションに常駐するエージェントから呼ぶことで、RDP+IME に
    阻まれがちな「クリップボード経由の GUI アプリ操作」を無人で行える。テキストは
    UTF-16 で直接やり取りするので CP932 文字化けが起きない。
    """

    def __init__(self):
        # build_handlers 経由＝実機でのみ呼ばれる。Mac の import 時には評価されない。
        if not IS_WINDOWS:
            raise RuntimeError("Win32Clipboard is Windows-only")
        u = ctypes.windll.user32
        k = ctypes.windll.kernel32
        # 64bit ではハンドル＝ポインタ幅。restype/argtypes を明示しないと既定 c_int に
        # 切り詰められてハンドルが壊れる（このプロジェクトが過去に踏んだ罠）。
        u.OpenClipboard.argtypes = [wintypes.HWND]; u.OpenClipboard.restype = wintypes.BOOL
        u.CloseClipboard.argtypes = []; u.CloseClipboard.restype = wintypes.BOOL
        u.EmptyClipboard.argtypes = []; u.EmptyClipboard.restype = wintypes.BOOL
        u.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
        u.IsClipboardFormatAvailable.restype = wintypes.BOOL
        u.GetClipboardData.argtypes = [wintypes.UINT]; u.GetClipboardData.restype = ctypes.c_void_p
        u.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
        u.SetClipboardData.restype = ctypes.c_void_p
        u.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        u.CreateWindowExW.restype = wintypes.HWND
        k.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        k.GlobalAlloc.restype = ctypes.c_void_p
        k.GlobalLock.argtypes = [ctypes.c_void_p]; k.GlobalLock.restype = ctypes.c_void_p
        k.GlobalUnlock.argtypes = [ctypes.c_void_p]; k.GlobalUnlock.restype = wintypes.BOOL
        k.GlobalFree.argtypes = [ctypes.c_void_p]; k.GlobalFree.restype = ctypes.c_void_p
        self._u = u
        self._k = k
        # クリップボード所有者にする隠しウィンドウ（message-only）。失敗したら NULL で
        # 続行（get は NULL でも読めるが、set は NULL だと失敗しうる）。"STATIC" は
        # 組み込みウィンドウクラスなので RegisterClass 不要。
        self._hwnd = u.CreateWindowExW(
            0, "STATIC", "loophole_clipboard", 0, 0, 0, 0, 0,
            wintypes.HWND(_HWND_MESSAGE), None, None, None) or None

    def _open(self) -> None:
        """OpenClipboard をリトライ付きで開く（最大 ~0.5 秒）。失敗で RuntimeError。"""
        deadline = time.monotonic() + 0.5
        while True:
            if self._u.OpenClipboard(self._hwnd):
                return
            if time.monotonic() >= deadline:
                raise RuntimeError("OpenClipboard timed out (held by another process)")
            time.sleep(0.01)

    def get(self) -> str:
        # テキストが無ければ即空文字（OpenClipboard すら不要）。
        if not self._u.IsClipboardFormatAvailable(_CF_UNICODETEXT):
            return ""
        self._open()
        try:
            h = self._u.GetClipboardData(_CF_UNICODETEXT)
            if not h:
                return ""
            p = self._k.GlobalLock(h)
            if not p:
                return ""
            try:
                # CF_UNICODETEXT は NUL 終端の UTF-16。wstring_at が終端まで読む。
                return ctypes.wstring_at(p)
            finally:
                self._k.GlobalUnlock(h)  # Close の前に必ず Unlock する
        finally:
            self._u.CloseClipboard()  # h は OS 所有なので解放しない

    def set(self, text: str) -> None:
        # UTF-16LE + NUL 終端で HGLOBAL を確保して詰める（CP932 も base64 も不要）。
        buf = (text + "\0").encode("utf-16-le")
        h = self._k.GlobalAlloc(_GMEM_MOVEABLE, len(buf))
        if not h:
            raise RuntimeError("clipboard_set: GlobalAlloc failed")
        p = self._k.GlobalLock(h)
        if not p:
            self._k.GlobalFree(h)
            raise RuntimeError("clipboard_set: GlobalLock failed")
        ctypes.memmove(p, buf, len(buf))
        self._k.GlobalUnlock(h)
        self._open()
        try:
            self._u.EmptyClipboard()
            if not self._u.SetClipboardData(_CF_UNICODETEXT, h):
                # 失敗時は所有権が移っていないので自分で解放する。
                self._k.GlobalFree(h)
                raise RuntimeError("clipboard_set: SetClipboardData failed")
            # 成功時は OS が h を所有する → GlobalFree しない（二重解放になる）。
        finally:
            self._u.CloseClipboard()


# ---- スクリーンショット: BitBlt + GDI を ctypes 直叩き + 自作 PNG ---------------

_SM_XVIRTUALSCREEN = 76
_SM_YVIRTUALSCREEN = 77
_SM_CXVIRTUALSCREEN = 78
_SM_CYVIRTUALSCREEN = 79
_SRCCOPY = 0x00CC0020
_BI_RGB = 0
_DIB_RGB_COLORS = 0

_dpi_aware_done = False


def _ensure_dpi_aware() -> None:
    """プロセスを per-monitor DPI aware にする（物理ピクセルで撮るため）。1 回だけ。

    DPI 非対応のままだと VirtualScreen が論理ピクセルを返し、高 DPI で縮小・ぼけた
    撮影になる。DPI 依存 API より前＝起動時に呼ぶのが理想。既に設定済み（manifest や
    前回呼び出し）なら各 API は失敗するが、その場合は既にアウェアなので無視してよい。
    """
    global _dpi_aware_done
    if _dpi_aware_done or not IS_WINDOWS:
        return
    _dpi_aware_done = True
    # PER_MONITOR_AWARE_V2 = -4（Win10 1703+）。擬似ハンドルを c_void_p で渡す。
    try:
        f = ctypes.windll.user32.SetProcessDpiAwarenessContext
        f.argtypes = [ctypes.c_void_p]; f.restype = wintypes.BOOL
        if f(ctypes.c_void_p(-4)):
            return
    except Exception:
        pass
    try:  # PROCESS_PER_MONITOR_DPI_AWARE = 2（Win8.1+）
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:  # 最後の砦: system-DPI aware（Vista+）
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 1)]


# 撮影パイプラインの純関数（_bgra_to_rgb / _encode_png / _grab_to_png）は imaging に移設し、
# モジュール冒頭で同名の別名として import 済み（BitBltScreenshotter.capture が _grab_to_png を使う）。


class BitBltScreenshotter:
    """BitBlt + GDI で全仮想画面を撮り PNG を返す。Windows 専用。

    loophole が実際に動く全環境（RDP セッション含む）で動く既定の撮影方式。GPU
    アクセラレーション描画（ブラウザ等）は BitBlt の原理上「黒画面」になる——その
    対策はローカルコンソール限定で DdagrabScreenshotter（DXGI）に切り替える。
    プロセス内完結なので PowerShell 版のような毎フレームのプロセス起動コストは無い。
    """

    def __init__(self):
        if not IS_WINDOWS:
            raise RuntimeError("BitBltScreenshotter is Windows-only")
        _ensure_dpi_aware()
        u = ctypes.windll.user32
        g = ctypes.windll.gdi32
        # 64bit ではハンドルがポインタ幅。restype/argtypes を必ず明示する。
        u.GetDC.argtypes = [wintypes.HWND]; u.GetDC.restype = ctypes.c_void_p
        u.ReleaseDC.argtypes = [wintypes.HWND, ctypes.c_void_p]; u.ReleaseDC.restype = ctypes.c_int
        u.GetSystemMetrics.argtypes = [ctypes.c_int]; u.GetSystemMetrics.restype = ctypes.c_int
        g.CreateCompatibleDC.argtypes = [ctypes.c_void_p]; g.CreateCompatibleDC.restype = ctypes.c_void_p
        g.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        g.CreateCompatibleBitmap.restype = ctypes.c_void_p
        g.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]; g.SelectObject.restype = ctypes.c_void_p
        g.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
        g.BitBlt.restype = wintypes.BOOL
        g.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT, wintypes.UINT,
                                ctypes.c_void_p, ctypes.POINTER(_BITMAPINFO), wintypes.UINT]
        g.GetDIBits.restype = ctypes.c_int
        g.DeleteObject.argtypes = [ctypes.c_void_p]; g.DeleteObject.restype = wintypes.BOOL
        g.DeleteDC.argtypes = [ctypes.c_void_p]; g.DeleteDC.restype = wintypes.BOOL
        self._u = u
        self._g = g

    def _grab(self):
        """全仮想画面を撮って (width, height, bgra_bytes) を返す（Win32 部分）。

        GDI リソースは必ず try/finally で対に解放する（HGLOBAL の所有権規律と同じ作法）。
        """
        u, g = self._u, self._g
        x = u.GetSystemMetrics(_SM_XVIRTUALSCREEN)
        y = u.GetSystemMetrics(_SM_YVIRTUALSCREEN)
        w = u.GetSystemMetrics(_SM_CXVIRTUALSCREEN)
        h = u.GetSystemMetrics(_SM_CYVIRTUALSCREEN)
        if w <= 0 or h <= 0:
            raise RuntimeError(f"screenshot: bad virtual screen size {w}x{h}")
        hdc = u.GetDC(None)
        mem = bmp = old = None
        try:
            mem = g.CreateCompatibleDC(hdc)
            bmp = g.CreateCompatibleBitmap(hdc, w, h)
            old = g.SelectObject(mem, bmp)
            if not g.BitBlt(mem, 0, 0, w, h, hdc, x, y, _SRCCOPY):
                raise RuntimeError("screenshot: BitBlt failed")
            bmi = _BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = w
            bmi.bmiHeader.biHeight = -h   # 負＝トップダウン（行が自然順で反転不要）
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32  # 32bpp＝ストライドが常に width*4（パディング無し）
            bmi.bmiHeader.biCompression = _BI_RGB
            buf = (ctypes.c_char * (w * h * 4))()
            got = g.GetDIBits(mem, bmp, 0, h, ctypes.cast(buf, ctypes.c_void_p),
                              ctypes.byref(bmi), _DIB_RGB_COLORS)
            if got != h:
                raise RuntimeError(f"screenshot: GetDIBits copied {got}/{h} lines")
            return w, h, bytes(buf)
        finally:
            if old is not None:
                g.SelectObject(mem, old)
            if bmp:
                g.DeleteObject(bmp)
            if mem:
                g.DeleteDC(mem)
            u.ReleaseDC(None, hdc)

    def capture(self) -> bytes:
        w, h, bgra = self._grab()
        return _grab_to_png(w, h, bgra)


class DdagrabScreenshotter:
    """FFmpeg ddagrab（DXGI Desktop Duplication）で撮る能力拡張版。Windows 専用。

    BitBlt が黒画面になる GPU アクセラレーション描画も撮れる。ただし Desktop
    Duplication は **RDP セッションでは動かない**ので、ローカルコンソールで動かす
    ときの任意機能。FFmpeg が要る（LOOPHOLE_FFMPEG で実体パスを渡せる）。
    """

    def __init__(self, ffmpeg: Optional[str] = None, runner=None):
        self._ffmpeg = ffmpeg or os.environ.get("LOOPHOLE_FFMPEG") or "ffmpeg"
        self._runner = runner or SubprocessRunner()

    def capture(self) -> bytes:
        # ddagrab は D3D11 フレームを返すので hwdownload,format=bgra で CPU 側へ落として
        # PNG 出力する。単一フィルタグラフ出力なので ffmpeg が自動マップする。
        tmp = os.path.join(tempfile.gettempdir(), "loophole_dda_" + uuid.uuid4().hex + ".png")
        argv = [self._ffmpeg, "-hide_banner", "-loglevel", "error",
                "-filter_complex", "ddagrab=0,hwdownload,format=bgra",
                "-frames:v", "1", "-y", tmp]
        result = self._runner.run(argv, cwd=None, timeout=30, stdin_text=None)
        if not result.started:
            raise RuntimeError(
                "ddagrab: ffmpeg not found (install FFmpeg or set LOOPHOLE_FFMPEG)")
        if result.exit_code != 0:
            raise RuntimeError(
                "ddagrab capture failed: " + (result.stderr or b"").decode("utf-8", "replace"))
        try:
            with open(tmp, "rb") as f:
                return f.read()
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass


# ---- Win32 SendInput でショートカットキーを送る（ctypes） --------------------

# ULONG_PTR は wintypes に無いのでポインタ幅から自前定義する。
_ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32

_INPUT_KEYBOARD = 1
_KEYEVENTF_EXTENDEDKEY = 0x0001
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004  # wScan を Unicode コードユニットとして注入（配列も IME も通さない）

# 拡張キー（E0 プレフィクス）。EXTENDEDKEY を立てないと一部アプリで効かない/
# テンキー側と混同される（矢印・編集キー・Win・Apps・PrintScreen）。
_EXTENDED_VKS = {
    0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28,  # PageUp/Down End Home 矢印
    0x2C, 0x2D, 0x2E,                                  # PrintScreen Insert Delete
    0x5B, 0x5C, 0x5D,                                  # LWin RWin Apps
}


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _MOUSEINPUT(ctypes.Structure):
    # union のサイズを正しくするためだけに定義する（最大メンバ）。
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


class SendInputKeyboard:
    """Win32 SendInput で「修飾キー + メインキー」を物理キー入力として送る。

    対話デスクトップセッションに常駐するエージェントから呼ぶと、前面ウィンドウへ
    Ctrl+S 等のショートカットを無人で送れる。SendKeys（PowerShell）と違い低レベルの
    SendInput を使うので、修飾キーの押し下げ/解放順序を正確に制御できる。Windows 専用。
    """

    def send_chord(self, modifiers: List[int], main: int) -> None:
        if not IS_WINDOWS:
            raise RuntimeError("key send is Windows-only")
        # 修飾を順に押す → メインを押して離す → 修飾を逆順で離す。
        for vk in modifiers:
            self._emit(vk, down=True)
        self._emit(main, down=True)
        self._emit(main, down=False)
        for vk in reversed(modifiers):
            self._emit(vk, down=False)

    def type_text(self, text: str) -> None:
        """文字列を 1 文字ずつ Unicode キーイベントとして打ち込む（KEYEVENTF_UNICODE）。

        wVk=0・wScan=UTF-16 コードユニットで送ると、Windows はキーボード配列も IME も
        通さずにその文字を前面ウィンドウへ届ける（WM_CHAR 相当）。ゆえに日本語でも
        化けずに打てる——send_keys（和音）が「文字入力には使うな」なのと対照的に、
        こちらが loophole の正規の文字タイプ経路。BMP 外は UTF-16 サロゲートペアに分割。
        """
        if not IS_WINDOWS:
            raise RuntimeError("key send is Windows-only")
        for u in self._utf16_units(text):
            self._emit_unicode(u, down=True)
            self._emit_unicode(u, down=False)

    @staticmethod
    def _utf16_units(text: str) -> List[int]:
        """文字列を UTF-16 コードユニット（16bit 整数）の並びにする。BMP 外はサロゲート対。"""
        raw = text.encode("utf-16-le")
        return [raw[i] | (raw[i + 1] << 8) for i in range(0, len(raw), 2)]

    def _emit_unicode(self, unit: int, down: bool) -> None:
        flags = _KEYEVENTF_UNICODE | (0 if down else _KEYEVENTF_KEYUP)
        ki = _KEYBDINPUT(wVk=0, wScan=unit, dwFlags=flags, time=0, dwExtraInfo=0)
        inp = _INPUT(type=_INPUT_KEYBOARD, u=_INPUTUNION(ki=ki))
        sent = ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
        if sent != 1:
            err = ctypes.windll.kernel32.GetLastError()
            raise RuntimeError(f"SendInput failed for unicode={unit:#06x} (GetLastError={err})")

    def _emit(self, vk: int, down: bool) -> None:
        flags = 0 if down else _KEYEVENTF_KEYUP
        if vk in _EXTENDED_VKS:
            flags |= _KEYEVENTF_EXTENDEDKEY
        ki = _KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0)
        inp = _INPUT(type=_INPUT_KEYBOARD, u=_INPUTUNION(ki=ki))
        sent = ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
        if sent != 1:
            err = ctypes.windll.kernel32.GetLastError()
            raise RuntimeError(f"SendInput failed for vk={vk:#04x} (GetLastError={err})")


# ---- Win32 でウィンドウを列挙・前面化する（ctypes） --------------------------

_VK_MENU = 0x12  # Alt（フォアグラウンドロック解除トリック用）
_SW_SHOW = 5
_SW_RESTORE = 9
_SW_MAXIMIZE = 3
_SW_MINIMIZE = 6

# SetWindowPos フラグ（位置のみ/サイズのみ指定や z-order・アクティブ化抑止）。
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010


def _configure_user32():
    """user32 の関数プロトタイプ（argtypes/restype）を設定して (user32, WNDENUMPROC) を返す。

    64bit で必須: 設定しないと HWND（ポインタ幅）が既定の c_int(32bit) に切り詰められ、
    GetForegroundWindow の戻り値やハンドル比較が壊れる。実機（Windows）でのみ呼ぶこと
    （ctypes.WINFUNCTYPE / windll は Windows 専用）。
    """
    u = ctypes.windll.user32
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    u.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    u.EnumWindows.restype = wintypes.BOOL
    u.IsWindow.argtypes = [wintypes.HWND]; u.IsWindow.restype = wintypes.BOOL
    u.IsWindowVisible.argtypes = [wintypes.HWND]; u.IsWindowVisible.restype = wintypes.BOOL
    u.IsIconic.argtypes = [wintypes.HWND]; u.IsIconic.restype = wintypes.BOOL
    u.GetWindowTextLengthW.argtypes = [wintypes.HWND]; u.GetWindowTextLengthW.restype = ctypes.c_int
    u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.GetWindowTextW.restype = ctypes.c_int
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    u.GetWindowThreadProcessId.restype = wintypes.DWORD
    u.GetForegroundWindow.argtypes = []; u.GetForegroundWindow.restype = wintypes.HWND
    u.SetForegroundWindow.argtypes = [wintypes.HWND]; u.SetForegroundWindow.restype = wintypes.BOOL
    u.BringWindowToTop.argtypes = [wintypes.HWND]; u.BringWindowToTop.restype = wintypes.BOOL
    u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]; u.ShowWindow.restype = wintypes.BOOL
    u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, wintypes.UINT]
    u.SetWindowPos.restype = wintypes.BOOL
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    u.GetWindowRect.restype = wintypes.BOOL
    u.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    u.AttachThreadInput.restype = wintypes.BOOL
    u.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, _ULONG_PTR]
    u.keybd_event.restype = None
    return u, WNDENUMPROC


class Win32WindowManager:
    """EnumWindows でトップレベル窓を列挙し、SetForegroundWindow 系で前面化する。Windows 専用。

    対話デスクトップセッションに常駐するエージェントから呼ぶ前提。Windows は
    バックグラウンドからの SetForegroundWindow を制限する（フォアグラウンドロック）ので、
    最小化なら ShowWindow で復元し、現フォアグラウンドスレッドへ AttachThreadInput して
    から前面化する。仕上げに Alt を一瞬叩いてロックを解く（入力を送れる立場を使う）。

    フィルタ（タイトル部分一致・曖昧判定）は handlers 側の純粋ロジックが担うので、
    ここは「全列挙」と「HWND 指定の前面化」だけを提供する。
    """

    def __init__(self):
        # __init__ は build_handlers 経由＝実機でのみ呼ばれる。Mac の import 時には
        # 評価されない（WINFUNCTYPE/windll を触らない）。
        if not IS_WINDOWS:
            raise RuntimeError("Win32WindowManager is Windows-only")
        self._u, self._WNDENUMPROC = _configure_user32()

    def list_windows(self, visible_only: bool) -> List[Dict[str, Any]]:
        u = self._u
        windows: List[Dict[str, Any]] = []

        def _cb(hwnd, _lparam):
            if visible_only and not u.IsWindowVisible(hwnd):
                return True
            length = u.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                u.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value
            else:
                title = ""
            if visible_only and not title:
                return True
            pid = wintypes.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            windows.append({
                "hwnd": int(hwnd),
                "title": title,
                "pid": int(pid.value),
                "minimized": bool(u.IsIconic(hwnd)),
            })
            return True

        # コールバックは EnumWindows 呼び出し中だけ生きていればよい（同期呼び出し）。
        u.EnumWindows(self._WNDENUMPROC(_cb), 0)
        return windows

    def activate(self, hwnd: int) -> bool:
        u = self._u
        kernel32 = ctypes.windll.kernel32
        h = wintypes.HWND(hwnd)
        if not u.IsWindow(h):
            return False
        # 最小化されていれば復元、そうでなければ可視化のみ（位置・サイズは触らない）。
        u.ShowWindow(h, _SW_RESTORE if u.IsIconic(h) else _SW_SHOW)
        # フォアグラウンドロック回避: 現フォアグラウンドスレッドの入力状態へアタッチする。
        fg = u.GetForegroundWindow()
        cur_tid = kernel32.GetCurrentThreadId()
        fg_tid = u.GetWindowThreadProcessId(wintypes.HWND(fg), None) if fg else 0
        attached = bool(fg_tid) and fg_tid != cur_tid and bool(
            u.AttachThreadInput(cur_tid, fg_tid, True))
        try:
            # Alt を一瞬叩いてフォアグラウンドロックを解く。
            u.keybd_event(_VK_MENU, 0, 0, 0)
            u.keybd_event(_VK_MENU, 0, _KEYEVENTF_KEYUP, 0)
            u.BringWindowToTop(h)
            u.SetForegroundWindow(h)
        finally:
            if attached:
                u.AttachThreadInput(cur_tid, fg_tid, False)
        # 実際に前面へ来たかを最終確認する（点滅だけして失敗するケースを弾く）。
        return bool(u.GetForegroundWindow() == hwnd)

    def set_window(self, hwnd: int, position=None, size=None,
                   minimized: Optional[bool] = None, fullscreen: Optional[bool] = None,
                   maximized: Optional[bool] = None, raise_: Optional[bool] = None) -> Dict[str, Any]:
        """HWND の窓の位置/サイズ/最小化/最大化/前面化を設定し、適用後 state を返す。

        position=(x,y) / size=(w,h) は None=触らない。minimized/maximized/raise_ は bool or None。
        座標は**物理ピクセル**（_ensure_dpi_aware 済み・screenshot/mouse と同系）。適用順は macOS と
        揃える: raise → maximize → position → size → (fullscreen) → minimize。

        **Windows に OS レベルの全画面は無い**（F11 はアプリ固有）。maximize で代用して
        「fullscreen 成功」と返すのは偽陽性なので捏造しない——fullscreen 引数は受けるが反映せず、
        readback の "fullscreen" は常に False。窓が見つからなければ actionable な RuntimeError。
        """
        u = self._u
        h = wintypes.HWND(hwnd)
        if not u.IsWindow(h):
            raise RuntimeError(
                f"no window with handle {hwnd} (it may have closed; "
                "re-run list_windows to get a current handle)")
        if raise_:
            self.activate(hwnd)  # AttachThreadInput 込みの前面化を再利用
        if maximized:
            u.ShowWindow(h, _SW_MAXIMIZE)  # macOS/protocol と同じく True のときだけ作用
        if position is not None or size is not None:
            x = int(position[0]) if position is not None else 0
            y = int(position[1]) if position is not None else 0
            w = int(size[0]) if size is not None else 0
            ht = int(size[1]) if size is not None else 0
            flags = _SWP_NOZORDER | _SWP_NOACTIVATE
            if position is None:
                flags |= _SWP_NOMOVE  # 移動せずリサイズだけ
            if size is None:
                flags |= _SWP_NOSIZE  # サイズを変えず移動だけ
            u.SetWindowPos(h, wintypes.HWND(0), x, y, w, ht, flags)
        # fullscreen は捏造しない（Windows に OS 全画面は無い）。引数は受けるが反映しない。
        if minimized is not None:
            u.ShowWindow(h, _SW_MINIMIZE if minimized else _SW_RESTORE)
        return self._read_state(h)

    def _read_state(self, h) -> Dict[str, Any]:
        """GetWindowRect で実測 geometry、IsIconic で最小化を読む。fullscreen は常に False。"""
        u = self._u
        rect = wintypes.RECT()
        u.GetWindowRect(h, ctypes.pointer(rect))
        return {
            "x": int(rect.left), "y": int(rect.top),
            "width": int(rect.right - rect.left), "height": int(rect.bottom - rect.top),
            "minimized": bool(u.IsIconic(h)),
            "fullscreen": False,  # Windows に OS レベルの全画面は無い
        }


# ---- Win32 IMM32 で前面ウィンドウの IME 状態を読み書きする（ctypes）---------
#
# 別プロセスの窓の IME を触るので ImmGetOpenStatus（同一プロセス専用）は使えない。
# 代わりに「その窓のデフォルト IME ウィンドウ」へ WM_IME_CONTROL を送る定番手法を使う。
# これは SendMessage 経由なのでクロスプロセスでも効く。固まったアプリで loophole 自体が
# 止まらないよう SendMessageTimeout（SMTO_ABORTIFHUNG, 1 秒）で送る。
_WM_IME_CONTROL = 0x0283
_IMC_GETCONVERSIONMODE = 0x0001
_IMC_SETCONVERSIONMODE = 0x0002
_IMC_GETOPENSTATUS = 0x0005
_IMC_SETOPENSTATUS = 0x0006
_SMTO_ABORTIFHUNG = 0x0002

# LRESULT はポインタ幅の符号付き整数。SendMessageTimeout の戻り値と out 引数に使う。
_LRESULT = ctypes.c_ssize_t


class Win32ImeController:
    """前面ウィンドウの IME の ON/OFF と変換モードを読み書きする。Windows 専用。

    RDP/VNC 越しの computer-use では、IME が ON（日本語入力モード）だと送った英字が
    読みに吸われて入力が化ける。open=False（直接入力）にしてから type すれば化けない。
    read(get)・write(set) とも、前面ウィンドウのデフォルト IME ウィンドウへ
    WM_IME_CONTROL を投げる（別プロセスでも効く経路）。

    変換モード ↔ 人間可読なモード名の対応付けは handlers 側の純粋ロジックが担うので、
    ここは生の (open, conversion) を読み書きするだけ。
    """

    def __init__(self):
        # build_handlers 経由＝実機でのみ呼ばれる。Mac の import 時には評価されない。
        if not IS_WINDOWS:
            raise RuntimeError("Win32ImeController is Windows-only")
        u = ctypes.windll.user32
        imm = ctypes.windll.imm32
        # 64bit では argtypes/restype 必須（HWND がポインタ幅。既定 c_int だと壊れる）。
        u.GetForegroundWindow.argtypes = []
        u.GetForegroundWindow.restype = wintypes.HWND
        imm.ImmGetDefaultIMEWnd.argtypes = [wintypes.HWND]
        imm.ImmGetDefaultIMEWnd.restype = wintypes.HWND
        u.SendMessageTimeoutW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
            wintypes.UINT, wintypes.UINT, ctypes.POINTER(_LRESULT)]
        u.SendMessageTimeoutW.restype = _LRESULT
        self._u = u
        self._imm = imm

    def _ime_hwnd(self) -> Optional[int]:
        fg = self._u.GetForegroundWindow()
        if not fg:
            return None
        # その窓に紐づくデフォルト IME ウィンドウ。IME を持たない窓なら NULL。
        ime = self._imm.ImmGetDefaultIMEWnd(fg)
        return ime or None

    def _send(self, ime_hwnd: int, command: int, value: int) -> Optional[int]:
        """WM_IME_CONTROL を送り、窓プロシージャの結果（LRESULT）を返す。

        SendMessageTimeout の戻り値が 0 = 送信自体が失敗（タイムアウト/ハング）。その
        ときは None。GET 系では out（result.value）が読み取った値。SET 系は窓側の戻り値が
        実装依存なので値は見ず、「送信できたか（None でないか）」だけを成否判定に使う。
        """
        out = _LRESULT()
        ok = self._u.SendMessageTimeoutW(
            ime_hwnd, _WM_IME_CONTROL, command, value,
            _SMTO_ABORTIFHUNG, 1000, ctypes.byref(out))
        if not ok:
            return None
        return int(out.value)

    def get(self) -> Optional[tuple]:
        ime = self._ime_hwnd()
        if not ime:
            return None
        open_val = self._send(ime, _IMC_GETOPENSTATUS, 0)
        if open_val is None:
            return None
        conv = self._send(ime, _IMC_GETCONVERSIONMODE, 0)
        return (bool(open_val), int(conv or 0))

    def set(self, open: Optional[bool], conversion: Optional[int]) -> bool:
        ime = self._ime_hwnd()
        if not ime:
            return False
        ok = True
        if open is not None:
            ok = (self._send(ime, _IMC_SETOPENSTATUS, 1 if open else 0) is not None) and ok
        if conversion is not None:
            ok = (self._send(ime, _IMC_SETCONVERSIONMODE, int(conversion)) is not None) and ok
        return ok


# ---- メニュー: GetMenu / GetMenuItemInfoW で列挙、WM_COMMAND で発火 -----------

_WM_COMMAND = 0x0111

_MIIM_STATE = 0x0001
_MIIM_ID = 0x0002
_MIIM_SUBMENU = 0x0004
_MIIM_STRING = 0x0040
_MIIM_FTYPE = 0x0100

_MFT_SEPARATOR = 0x0800
_MFS_GRAYED = 0x0003   # GRAYED と DISABLED は同値（無効項目の検知に使う）
_MFS_CHECKED = 0x0008

_MENU_MAX_DEPTH = 8    # 循環・異常に深いメニューに対する安全弁


class _MENUITEMINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("fMask", wintypes.UINT),
        ("fType", wintypes.UINT),
        ("fState", wintypes.UINT),
        ("wID", wintypes.UINT),
        ("hSubMenu", wintypes.HMENU),
        ("hbmpChecked", wintypes.HBITMAP),
        ("hbmpUnchecked", wintypes.HBITMAP),
        ("dwItemData", ctypes.c_size_t),   # ULONG_PTR（ポインタ幅）
        ("dwTypeData", wintypes.LPWSTR),
        ("cch", wintypes.UINT),
        ("hbmpItem", wintypes.HBITMAP),
    ]


class _ClassicMenuController:
    """クラシック Win32 メニューバーを列挙し、コマンドを WM_COMMAND で発火する。Windows 専用。

    GetMenu(hwnd) でメニューバーを取り、GetMenuItemInfoW で各項目（ラベル・wID・状態・
    サブメニュー）を読む。発火はメニューを開かず PostMessage(WM_COMMAND, wID) を投げるだけで、
    キー操作不要・ブラインド・決定的。リボン/Electron/UWP/WPF/WinForms は GetMenu が NULL を
    返すので enumerate は None（→ 上位の Win32MenuController が UIA フォールバックに回す）。

    Win32MenuController（公開クラス）の「1 段目＝高速・ブラインド・副作用なし」の経路。
    ツリー整形・破壊的ラベル判定は handlers 側の純粋ロジックが担うので、ここは生ツリーを
    返すだけ。W 系 API なのでラベルは UTF-16 のまま取れ、CP932 のダメ文字問題と無縁。
    """

    def __init__(self):
        # Win32MenuController（合成クラス）経由＝実機でのみ生成される。
        if not IS_WINDOWS:
            raise RuntimeError("_ClassicMenuController is Windows-only")
        u = ctypes.windll.user32
        # 64bit では argtypes/restype 必須（HMENU/HWND がポインタ幅。既定 c_int だと壊れる）。
        u.GetMenu.argtypes = [wintypes.HWND]; u.GetMenu.restype = wintypes.HMENU
        u.GetMenuItemCount.argtypes = [wintypes.HMENU]; u.GetMenuItemCount.restype = ctypes.c_int
        u.GetMenuItemInfoW.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.BOOL,
                                       ctypes.POINTER(_MENUITEMINFOW)]
        u.GetMenuItemInfoW.restype = wintypes.BOOL
        u.IsWindow.argtypes = [wintypes.HWND]; u.IsWindow.restype = wintypes.BOOL
        u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u.PostMessageW.restype = wintypes.BOOL
        self._u = u

    def enumerate(self, hwnd: int) -> Optional[List[Dict[str, Any]]]:
        u = self._u
        h = wintypes.HWND(hwnd)
        if not u.IsWindow(h):
            return None
        hmenu = u.GetMenu(h)
        if not hmenu:
            return None  # メニューバーを持たない（リボン/Electron/UWP 等）
        return self._read_menu(hmenu, 0)

    def _read_menu(self, hmenu: int, depth: int) -> List[Dict[str, Any]]:
        u = self._u
        items: List[Dict[str, Any]] = []
        if depth >= _MENU_MAX_DEPTH:
            return items
        count = u.GetMenuItemCount(hmenu)
        if count < 0:
            return items
        for i in range(count):
            # 1 回目: 状態・ID・サブメニュー・種別 と「文字列長」を測る（dwTypeData=NULL）。
            mii = _MENUITEMINFOW()
            mii.cbSize = ctypes.sizeof(_MENUITEMINFOW)
            mii.fMask = _MIIM_STATE | _MIIM_ID | _MIIM_SUBMENU | _MIIM_STRING | _MIIM_FTYPE
            mii.dwTypeData = None
            mii.cch = 0
            if not u.GetMenuItemInfoW(hmenu, i, True, ctypes.byref(mii)):
                continue
            if mii.fType & _MFT_SEPARATOR:
                items.append({"separator": True})
                continue
            # 2 回目: cch+1 のバッファを与えてラベルを取得（可変長文字列の定石）。
            label = ""
            if mii.cch > 0:
                length = mii.cch
                buf = ctypes.create_unicode_buffer(length + 1)
                mii2 = _MENUITEMINFOW()
                mii2.cbSize = ctypes.sizeof(_MENUITEMINFOW)
                mii2.fMask = _MIIM_STRING
                mii2.dwTypeData = ctypes.cast(buf, wintypes.LPWSTR)
                mii2.cch = length + 1
                if u.GetMenuItemInfoW(hmenu, i, True, ctypes.byref(mii2)):
                    label = buf.value
            node: Dict[str, Any] = {
                "label": label,
                "command_id": int(mii.wID),
                "enabled": not bool(mii.fState & _MFS_GRAYED),
                "checked": bool(mii.fState & _MFS_CHECKED),
            }
            if mii.hSubMenu:
                node["submenu"] = self._read_menu(mii.hSubMenu, depth + 1)
            items.append(node)
        return items

    def invoke(self, hwnd: int, command_id: int) -> bool:
        u = self._u
        h = wintypes.HWND(hwnd)
        if not u.IsWindow(h):
            return False
        # メニュー由来の WM_COMMAND: HIWORD(wParam)=0, LOWORD=command_id, lParam=0。
        return bool(u.PostMessageW(h, _WM_COMMAND, command_id & 0xFFFF, 0))


class Win32MenuController:
    """メニュー列挙・発火の Windows 実装（クラシック → UIA の 2 段フォールバック）。Windows 専用。

    1 段目（高速・ブラインド・副作用なし）: クラシック Win32 メニューバー（HMENU）を
    GetMenu/GetMenuItemInfoW で読み、WM_COMMAND を Post して発火する（_ClassicMenuController）。
    メモ帳・多くのネイティブ Win32 アプリ・FileMaker 等が該当。

    2 段目（フォールバック）: GetMenu が NULL（HMENU を持たない＝WPF/WinForms/UWP/WinUI 等の
    モダンアプリ）のとき、UI Automation（アクセシビリティ）でメニューバーを辿る
    （win_uia_menu.UiaMenuController）。これは Linux の AT-SPI フォールバックと対称の設計。
    comtypes が要る——無ければ UIA 段は黙って無効化され、従来どおり supported:false を返す
    （回帰なし）。リボン(Office)/Electron は UIA でも安定しないので best-effort（取れなければ
    supported:false → スクショ＋マウスに退避）。

    invoke は「直近の enumerate がどちらの段で当たったか」を hwnd 単位で覚えて振り分ける。
    enumerate せず既知の wID を直接 invoke する従来の使い方はクラシック経路に倒す（UIA 段は
    enumerate で合成 ID を作らないと invoke できないため、Linux backend と同じ前提）。
    """

    def __init__(self):
        # build_handlers 経由＝実機でのみ呼ばれる。Mac の import 時には評価されない。
        if not IS_WINDOWS:
            raise RuntimeError("Win32MenuController is Windows-only")
        self._classic = _ClassicMenuController()
        self._uia = None          # 遅延生成: None=未試行 / False=利用不可 / インスタンス
        self._uia_hwnds = set()    # 直近 enumerate が UIA 段で当たった hwnd の集合

    def _ensure_uia(self):
        """UIA backend を遅延生成する。comtypes 不在や COM 初期化失敗なら False を覚えて諦める。

        win_uia_menu の import は Windows 実機のここでだけ起きる（comtypes 依存をモジュール
        トップに置かないことで、Mac での import と Mac テストを汚さない）。
        """
        if self._uia is None:
            try:
                from win_uia_menu import UiaMenuController
                self._uia = UiaMenuController()
            except Exception:
                self._uia = False  # 二度と試さない（毎回 import を試す無駄を避ける）
        return self._uia or None

    def enumerate(self, hwnd: int) -> Optional[List[Dict[str, Any]]]:
        # 1 段目: クラシック HMENU。取れたら即返す（最速・副作用なし）。
        tree = self._classic.enumerate(hwnd)
        if tree is not None:
            self._uia_hwnds.discard(hwnd)
            return tree
        # 2 段目: HMENU が無い→ UIA フォールバック（comtypes があれば）。
        uia = self._ensure_uia()
        if uia is None:
            return None
        tree = uia.enumerate(hwnd)
        if tree is None:
            self._uia_hwnds.discard(hwnd)
            return None
        self._uia_hwnds.add(hwnd)
        return tree

    def invoke(self, hwnd: int, command_id: int) -> bool:
        # 直近 enumerate が UIA で当たった hwnd は UIA で発火、それ以外はクラシック。
        if hwnd in self._uia_hwnds and self._uia:
            return self._uia.invoke(hwnd, command_id)
        return self._classic.invoke(hwnd, command_id)


# ---- スクリーンショット backend の選択 --------------------------------------


def _select_screenshotter():
    """env LOOPHOLE_SCREENSHOT_BACKEND で撮影方式を選ぶ。

    既定 "bitblt" は loophole が動く全環境（RDP 含む）で確実に動く。"ddagrab" は GPU
    描画対策の能力拡張だが Desktop Duplication が RDP で動かないためローカルコンソール
    専用（FFmpeg のインストールが前提）。
    """
    backend = os.environ.get("LOOPHOLE_SCREENSHOT_BACKEND", "bitblt").strip().lower()
    if backend == "ddagrab":
        return DdagrabScreenshotter()
    return BitBltScreenshotter()


def build_screenshotter():
    """Windows のスクリーンショッタを単体で組む（viewer.py のライブビューア用）。

    env の LOOPHOLE_SCREENSHOT_BACKEND に従う。撮影方式は状態を持たないので、
    Handlers の内部とは別インスタンスでも問題ない。viewer は capture() しか使わない。
    backends.build_screenshotter がプラットフォームを見てこれ（or Linux 版）を選ぶ。
    """
    return _select_screenshotter()


class Win32Mouse:
    """SetCursorPos と mouse_event でカーソル移動・ボタン・ホイールを送る。Windows 専用。

    対話デスクトップセッションに常駐するエージェントから呼ぶ前提。座標は物理ピクセル
    （screenshot と同じ仮想スクリーン座標系）。mouse_event は古い API だが自動化には十分。
    """

    _DOWN = {1: 0x0002, 2: 0x0020, 3: 0x0008}  # LEFT/MIDDLE/RIGHT DOWN
    _UP = {1: 0x0004, 2: 0x0040, 3: 0x0010}    # LEFT/MIDDLE/RIGHT UP
    _WHEEL = 0x0800
    _HWHEEL = 0x1000
    _WHEEL_DELTA = 120

    def __init__(self):
        if not IS_WINDOWS:
            raise RuntimeError("Win32Mouse is Windows-only")
        _ensure_dpi_aware()  # 物理ピクセルで動かす（screenshot と座標系を揃える）
        u = ctypes.windll.user32
        u.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
        u.SetCursorPos.restype = wintypes.BOOL
        u.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.DWORD, _ULONG_PTR]
        u.mouse_event.restype = None
        self._u = u

    def move(self, x: int, y: int) -> None:
        self._u.SetCursorPos(int(x), int(y))

    def button(self, button: int, down: bool) -> None:
        flags = (self._DOWN if down else self._UP).get(int(button))
        if flags is None:
            raise RuntimeError(f"Win32Mouse: unsupported button {button}")
        self._u.mouse_event(flags, 0, 0, 0, 0)

    def scroll(self, dx: int, dy: int) -> None:
        # dy>0 = 下方向（WHEEL は上が正なので符号反転）。dx>0 = 右。signed を DWORD に詰める。
        if dy:
            self._u.mouse_event(self._WHEEL, 0, 0, (-self._WHEEL_DELTA * dy) & 0xFFFFFFFF, 0)
        if dx:
            self._u.mouse_event(self._HWHEEL, 0, 0, (self._WHEEL_DELTA * dx) & 0xFFFFFFFF, 0)

    def drag(self, x1: int, y1: int, x2: int, y2: int, button: int = 1, steps: int = 24) -> None:
        """(x1,y1) で押し、補間しながら (x2,y2) まで動かして離す。Windows はボタン押下中の
        SetCursorPos が WM_MOUSEMOVE（ボタン down）として届くのでドラッグが成立する。"""
        down = self._DOWN.get(int(button))
        up = self._UP.get(int(button))
        if down is None:
            raise RuntimeError(f"Win32Mouse: unsupported button {button}")
        n = max(1, int(steps))
        self._u.SetCursorPos(int(x1), int(y1))
        self._u.mouse_event(down, 0, 0, 0, 0)
        for i in range(1, n + 1):
            t = i / n
            self._u.SetCursorPos(int(x1 + (x2 - x1) * t), int(y1 + (y2 - y1) * t))
        self._u.mouse_event(up, 0, 0, 0, 0)


def build_handlers():
    """Windows backend で Handlers を組み立てる（backends.build_handlers が win32 で呼ぶ）。

    OS 非依存の Runner / FileSystem / Environment は common_backends から、Win32 専用の
    clipboard/screenshot/keyboard/windows/ime/menu/mouse はこのモジュールから組む。
    """
    from handlers import Handlers
    return Handlers(
        runner=SubprocessRunner(),
        clipboard=Win32Clipboard(),
        screenshotter=_select_screenshotter(),
        filesystem=LocalFileSystem(),
        environment=HostEnvironment(),
        keyboard=SendInputKeyboard(),
        windows=Win32WindowManager(),
        ime=Win32ImeController(),
        menu=Win32MenuController(),
        mouse=Win32Mouse(),
    )
