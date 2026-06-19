"""darwin backend パッケージ — 能力ごとにモジュール分割。

build_handlers が各能力のファクトリを合成して Handlers を組む。backends.py（ディスパッチャ）
は darwin_backends シム経由でここの build_handlers / build_screenshotter を呼ぶ。

Linux 側（server/linux/）と同じ構成方針:
  - 共有層: cglib（CoreGraphics/CoreFoundation ctypes）, tislib（Text Input Sources）,
            axlib（Accessibility）。
  - 能力別: clipboard / screenshot / keyboard / mouse / window / ime / menu。
  - try_build で各能力を組み、失敗時は UnsupportedBackend に倒す（agent 全体は起動させる）。

スキャフォルディング段階では各能力モジュールが build_* を export していなくても agent が
起動するよう、空ファクトリ（=未実装スタブ）を内部で持ち、モジュール追加に従って差し替える。
"""

from __future__ import annotations

from common_backends import (
    HostEnvironment, LocalFileSystem, SubprocessRunner, UnsupportedBackend, try_build as _try,
)


def _stub(label: str):
    """まだ実装されていない能力用の遅延ファクトリ。"""
    return UnsupportedBackend(f"{label}: not yet implemented on darwin")


def _build_clipboard(runner):
    try:
        from .clipboard import build_clipboard
    except ImportError:
        return _stub("clipboard")
    return _try(lambda: build_clipboard(runner), "clipboard backend init failed")


def _build_screenshotter():
    try:
        from .screenshot import build_screenshotter
    except ImportError:
        return _stub("screenshot")
    return _try(build_screenshotter, "screenshot backend init failed")


def _build_keyboard(runner):
    try:
        from .keyboard import build_keyboard
    except ImportError:
        return _stub("keyboard")
    return _try(lambda: build_keyboard(runner), "keyboard backend init failed")


def _build_mouse(runner):
    try:
        from .mouse import build_mouse
    except ImportError:
        return _stub("mouse")
    return _try(lambda: build_mouse(runner), "mouse backend init failed")


def _build_window(runner):
    try:
        from .window import build_window
    except ImportError:
        return _stub("window")
    return _try(lambda: build_window(runner), "window backend init failed")


def _build_ime(runner):
    try:
        from .ime import build_ime
    except ImportError:
        return _stub("ime")
    return _try(lambda: build_ime(runner), "ime backend init failed")


def _build_menu(runner):
    try:
        from .menu import build_menu
    except ImportError:
        return _stub("menu")
    return _try(lambda: build_menu(runner), "menu backend init failed")


def build_handlers():
    """darwin backend で Handlers を組み立てる（backends.build_handlers が darwin で呼ぶ）。"""
    from handlers import Handlers
    runner = SubprocessRunner()
    return Handlers(
        runner=runner,
        clipboard=_build_clipboard(runner),
        screenshotter=_build_screenshotter(),
        filesystem=LocalFileSystem(),
        environment=HostEnvironment(),
        keyboard=_build_keyboard(runner),
        windows=_build_window(runner),
        ime=_build_ime(runner),
        menu=_build_menu(runner),
        mouse=_build_mouse(runner),
    )


def build_screenshotter():
    """darwin のスクリーンショッタ（viewer 用）を返す。"""
    return _build_screenshotter()
