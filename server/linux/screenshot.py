"""screenshot.py — Linux のスクリーンショット backend。

X11Screenshotter（XGetImage）と Wayland 系（grim / gnome-screenshot / spectacle）。
build_screenshotter がディスプレイ種別とデスクトップ環境で選ぶ。

Wayland はコンポジタごとに撮り方が違う:
- wlroots 系（sway / Hyprland）: grim が PNG を stdout に吐ける（最優先・zero-file）。
- GNOME: grim は動かないので gnome-screenshot に委譲（一時 PNG に書かせて読み戻す）。
- KDE/Plasma: 同様に spectacle に委譲。
WaylandScreenshotter は「grim → デスクトップ別ツール」の順で試し、どれも無ければ明示エラー。
将来的には xdg-desktop-portal（org.freedesktop.portal.Screenshot）で統一する余地がある。
"""

from __future__ import annotations

import ctypes
import os
import tempfile
import uuid

import imaging
from common_backends import SubprocessRunner, UnsupportedBackend, linux_display_server, try_build as _try
from .x11lib import _lib, _ALL_PLANES, _ZPIXMAP


class X11Screenshotter:
    """XGetImage でルートウィンドウ（= 全モニタを覆う仮想画面）を撮り PNG を返す。Linux/X11 専用。

    BitBlt と同じく GPU 直描画は黒くなりうるが、loophole が動く環境（X11 デスクトップ）で
    確実に動く既定方式。撮った ZPixmap を imaging の純関数で RGB→PNG にする。
    """

    def __init__(self):
        self._lib = _lib()

    def capture(self) -> bytes:
        lib = self._lib
        x = lib.x
        dpy = lib.open_display()
        try:
            root = x.XDefaultRootWindow(dpy)
            root_ret = ctypes.c_ulong()
            rx = ctypes.c_int(); ry = ctypes.c_int()
            w = ctypes.c_uint(); h = ctypes.c_uint()
            bw = ctypes.c_uint(); depth = ctypes.c_uint()
            if not x.XGetGeometry(dpy, root, ctypes.byref(root_ret), ctypes.byref(rx),
                                  ctypes.byref(ry), ctypes.byref(w), ctypes.byref(h),
                                  ctypes.byref(bw), ctypes.byref(depth)):
                raise RuntimeError("screenshot: XGetGeometry failed")
            ximg = x.XGetImage(dpy, root, 0, 0, w.value, h.value, _ALL_PLANES, _ZPIXMAP)
            if not ximg:
                raise RuntimeError("screenshot: XGetImage returned NULL")
            try:
                img = ximg.contents
                if img.byte_order != 0:  # 0 = LSBFirst（ほぼ全ての Linux デスクトップ）
                    raise RuntimeError(
                        f"screenshot: unsupported MSBFirst byte order ({img.byte_order})")
                width, height = img.width, img.height
                bpl, bpp = img.bytes_per_line, img.bits_per_pixel
                data = ctypes.string_at(img.data, height * bpl)
                rgb = imaging.ximage_to_rgb(data, width, height, bpl, bpp)
                return imaging.encode_png(width, height, rgb)
            finally:
                img.f.destroy_image(ctypes.cast(ximg, ctypes.c_void_p))
        finally:
            x.XCloseDisplay(dpy)


class GrimScreenshotter:
    """Wayland 用: grim に全画面 PNG を stdout へ吐かせて受け取る。wlroots 系コンポジタ向け。"""

    def __init__(self, runner=None):
        self._runner = runner or SubprocessRunner()

    def capture(self) -> bytes:
        r = self._runner.run(["grim", "-"], None, 30.0, None)
        if not r.started:
            raise RuntimeError("screenshot: grim not found (install grim for Wayland capture)")
        if r.exit_code != 0:
            raise RuntimeError("screenshot: grim failed: "
                               + (r.stderr or b"").decode("utf-8", "replace"))
        return r.stdout or b""


def _capture_to_tmp(runner, argv_for_tmp, tool, hint):
    """一時 PNG パスを作って argv_for_tmp(tmp) を実行し、書かれた PNG を読み戻して返す。

    grim と違い gnome-screenshot / spectacle は stdout に吐けないので、tempfile 経由で撮らせて
    読み戻す（DdagrabScreenshotter と同じ方式）。tmp は必ず後始末する。argv_for_tmp は
    「tmp パスを受けて argv リストを返す」呼び出し可能（ツールごとにフラグ並びが違うため）。
    """
    tmp = os.path.join(tempfile.gettempdir(), "loophole_shot_" + uuid.uuid4().hex + ".png")
    try:
        r = runner.run(argv_for_tmp(tmp), None, 30.0, None)
        if not r.started:
            raise RuntimeError(f"screenshot: {tool} not found ({hint})")
        if r.exit_code != 0:
            raise RuntimeError(f"screenshot: {tool} failed: "
                               + (r.stderr or b"").decode("utf-8", "replace"))
        try:
            with open(tmp, "rb") as f:
                data = f.read()
        except OSError as exc:
            raise RuntimeError(f"screenshot: {tool} produced no readable file ({exc})")
        if not data:
            raise RuntimeError(f"screenshot: {tool} produced an empty file")
        return data
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


class GnomeScreenshotter:
    """GNOME(Wayland) 用: gnome-screenshot に一時 PNG を撮らせて読み戻す。

    GNOME の Mutter は wlroots プロトコルを話さないので grim が使えない。gnome-screenshot は
    GNOME 標準ツールで `-f <file>` に全画面 PNG を保存する。
    """

    def __init__(self, runner=None):
        self._runner = runner or SubprocessRunner()

    def capture(self) -> bytes:
        return _capture_to_tmp(
            self._runner,
            lambda tmp: ["gnome-screenshot", "-f", tmp],
            "gnome-screenshot",
            "install gnome-screenshot for GNOME Wayland capture")


class SpectacleScreenshotter:
    """KDE/Plasma(Wayland) 用: spectacle に一時 PNG を撮らせて読み戻す。

    KDE の KWin も wlroots プロトコルを話さない。spectacle は KDE 標準ツールで、
    `-b`(バックグラウンド・GUI 無し) `-n`(通知無し) `-o <file>` で全画面 PNG を保存する。
    """

    def __init__(self, runner=None):
        self._runner = runner or SubprocessRunner()

    def capture(self) -> bytes:
        return _capture_to_tmp(
            self._runner,
            lambda tmp: ["spectacle", "-b", "-n", "-o", tmp],
            "spectacle",
            "install spectacle for KDE Wayland capture")


def _desktop_fallback(runner):
    """XDG_CURRENT_DESKTOP からデスクトップ別の Wayland フォールバック撮影器を選ぶ。

    複数値はコロン区切り（例 "ubuntu:GNOME"）なので小文字で部分一致を見る。該当が無ければ None。
    """
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or "").lower()
    if "gnome" in desktop:
        return GnomeScreenshotter(runner=runner)
    if "kde" in desktop or "plasma" in desktop:
        return SpectacleScreenshotter(runner=runner)
    return None


class WaylandScreenshotter:
    """Wayland 撮影の束ね役: grim（wlroots）を最優先、駄目ならデスクトップ別ツールに委譲。

    capture() は grim → デスクトップ別（GNOME=gnome-screenshot / KDE=spectacle）の順で試し、
    最初に成功したものを返す。grim が未インストール/失敗で、かつフォールバックも無い/失敗なら、
    どの方法が試され何故落ちたかを含む明示的な RuntimeError を投げる。
    """

    def __init__(self, runner=None):
        self._runner = runner or SubprocessRunner()
        self._primary = GrimScreenshotter(runner=self._runner)
        self._fallback = _desktop_fallback(self._runner)

    def capture(self) -> bytes:
        errors = []
        try:
            return self._primary.capture()
        except RuntimeError as exc:
            errors.append(str(exc))
        if self._fallback is not None:
            try:
                return self._fallback.capture()
            except RuntimeError as exc:
                errors.append(str(exc))
        else:
            desktop = os.environ.get("XDG_CURRENT_DESKTOP") or "?"
            errors.append("no per-desktop Wayland fallback for "
                          f"XDG_CURRENT_DESKTOP={desktop!r} "
                          "(supported: GNOME via gnome-screenshot, KDE via spectacle)")
        raise RuntimeError("screenshot: no working Wayland method; tried: " + " | ".join(errors))


def build_screenshotter():
    """Linux のスクリーンショッタ（viewer 用）を、ディスプレイ種別に応じて返す。"""
    server = linux_display_server()
    if server == "wayland":
        return WaylandScreenshotter()
    if server == "x11":
        return _try(X11Screenshotter, "screenshot requires X11")
    return UnsupportedBackend("screenshot requires a display (no DISPLAY/WAYLAND_DISPLAY)")
