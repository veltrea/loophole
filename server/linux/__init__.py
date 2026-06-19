"""linux backend パッケージ — 能力ごとにモジュール分割。

build_handlers が各能力のファクトリ（build_clipboard / build_screenshotter / build_keyboard /
build_window / build_ime / build_menu）を合成して Handlers を組む。backends.py（ディスパッチャ）
は linux_backends シム経由でここの build_handlers / build_screenshotter を呼ぶ。
"""

from __future__ import annotations

from common_backends import (
    HostEnvironment, LocalFileSystem, SubprocessRunner, linux_display_server,
)
from .clipboard import build_clipboard
from .screenshot import build_screenshotter
from .keyboard import build_keyboard
from .window import build_window
from .ime import build_ime
from .menu import build_menu
from .mouse import build_mouse


def build_handlers():
    """Linux backend で Handlers を組み立てる（backends.build_handlers が linux で呼ぶ）。"""
    from handlers import Handlers
    server = linux_display_server()
    runner = SubprocessRunner()
    return Handlers(
        runner=runner,
        clipboard=build_clipboard(server, runner),
        screenshotter=build_screenshotter(),
        filesystem=LocalFileSystem(),
        environment=HostEnvironment(),
        keyboard=build_keyboard(server, runner),
        windows=build_window(server, runner),
        ime=build_ime(runner),
        menu=build_menu(runner),
        mouse=build_mouse(server, runner),
    )
