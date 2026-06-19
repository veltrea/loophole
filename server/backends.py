"""backends.py — プラットフォームを見て実 OS backend を選ぶディスパッチャ。

agent.py はここから build_handlers / build_screenshotter を呼ぶ（どの OS かを意識しない）。

  - Windows : win_backends（Win32 を ctypes 直叩き）
  - Linux   : linux_backends（X11 を ctypes 直叩き／Wayland は一部 shell-out）
  - macOS   : darwin_backends（CoreGraphics/TIS/AX を ctypes、pbcopy/screencapture/osascript に shell-out）
  - その他POSIX  : OS 非依存の Runner/FileSystem/Environment だけ実装し、GUI 系は
                   UnsupportedBackend に倒す（agent は起動するが GUI 系は明示エラー）。

各 backend モジュールの import は関数内で遅延する。プラットフォームが違うモジュールを掴まない
ために遅延 import に統一する（読み込み自体は副作用無しだが、不要な ctypes 解決を避ける）。
"""

from __future__ import annotations

import sys

IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")
IS_DARWIN = sys.platform == "darwin"


def build_handlers():
    """このホストの OS に合った Handlers を組み立てて返す。"""
    if IS_WINDOWS:
        from win_backends import build_handlers as _build
        return _build()
    if IS_LINUX:
        from linux_backends import build_handlers as _build
        return _build()
    if IS_DARWIN:
        from darwin_backends import build_handlers as _build
        return _build()
    return _build_neutral_handlers()


def build_screenshotter():
    """このホストの OS に合ったスクリーンショッタ（viewer 用）を返す。"""
    if IS_WINDOWS:
        from win_backends import build_screenshotter as _build
        return _build()
    if IS_LINUX:
        from linux_backends import build_screenshotter as _build
        return _build()
    if IS_DARWIN:
        from darwin_backends import build_screenshotter as _build
        return _build()
    raise RuntimeError(f"no screenshot backend for platform {sys.platform!r}")


def _build_neutral_handlers():
    """Windows でも Linux でも Mac でもない POSIX 向け。GUI 系は未対応スタブ。

    プロセス起動・ファイル I/O・環境情報は common_backends で動くので、結合テスト
    （tests/test_e2e_loopback.py）はこの構成で run / read_file / write_file を検証できる。
    """
    from handlers import Handlers
    from common_backends import (
        HostEnvironment,
        LocalFileSystem,
        SubprocessRunner,
        UnsupportedBackend,
    )
    stub = UnsupportedBackend(f"no GUI backend on {sys.platform}")
    return Handlers(
        runner=SubprocessRunner(),
        clipboard=stub,
        screenshotter=stub,
        filesystem=LocalFileSystem(),
        environment=HostEnvironment(),
        keyboard=stub,
        windows=stub,
        ime=stub,
        menu=stub,
        mouse=stub,
    )
