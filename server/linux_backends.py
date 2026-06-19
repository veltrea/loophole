"""linux_backends.py — 後方互換シム。実体は server/linux/ パッケージに分割済み。

能力ごとのモジュール（linux/clipboard.py, screenshot.py, keyboard.py, window.py, ime.py,
menu.py）と共有層（linux/x11lib.py, parsers.py）に分けてある。backends.py（ディスパッチャ）と
テスト・smoke は従来どおり `linux_backends` から名前を取れるよう、ここで re-export する。

新規の編集は各 linux/*.py モジュールで行うこと（並行開発のためのファイル所有制は
docs/linux-roadmap.md を参照）。
"""

from __future__ import annotations

from linux import build_handlers
from linux.parsers import (
    clipboard_commands,
    decode_text,
    desired_open_from,
    ibus_engine_is_active,
    parse_atspi_ref,
    parse_atspi_refs,
    parse_atspi_state,
    parse_atspi_string,
    parse_atspi_uint,
    parse_fcitx_state,
    parse_hyprland_clients,
    parse_long_array,
    parse_sway_tree,
    wayland_compositor,
    _IME_CONV_ON,
)
from linux.clipboard import ShellClipboard, X11Clipboard, build_clipboard
from linux.screenshot import (
    GnomeScreenshotter,
    GrimScreenshotter,
    SpectacleScreenshotter,
    WaylandScreenshotter,
    X11Screenshotter,
    build_screenshotter,
)
from linux.keyboard import WaylandKeyboard, X11Keyboard
from linux.window import WaylandWindowManager, X11WindowManager
from linux.ime import LinuxImeController
from linux.menu import LinuxMenuController
from linux.mouse import WaylandMouse, X11Mouse, build_mouse
from common_backends import SubprocessRunner, UnsupportedBackend

__all__ = [
    "build_handlers", "build_clipboard", "build_screenshotter",
    "clipboard_commands", "decode_text", "desired_open_from", "ibus_engine_is_active",
    "parse_atspi_ref", "parse_atspi_refs", "parse_atspi_state", "parse_atspi_string",
    "parse_atspi_uint", "parse_fcitx_state", "parse_hyprland_clients", "parse_long_array",
    "parse_sway_tree", "wayland_compositor", "_IME_CONV_ON",
    "ShellClipboard", "X11Clipboard", "GrimScreenshotter", "X11Screenshotter",
    "GnomeScreenshotter", "SpectacleScreenshotter", "WaylandScreenshotter",
    "WaylandKeyboard", "X11Keyboard", "WaylandWindowManager", "X11WindowManager",
    "LinuxImeController", "LinuxMenuController", "WaylandMouse", "X11Mouse", "build_mouse",
    "SubprocessRunner", "UnsupportedBackend",
]
