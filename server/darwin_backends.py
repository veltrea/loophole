"""darwin_backends.py — 後方互換シム。実体は server/darwin/ パッケージに分割。

linux_backends.py と同じ位置づけ。backends.py（ディスパッチャ）とテスト・smoke は
`darwin_backends` から build_handlers / build_screenshotter を取れる。新規の編集は各
server/darwin/*.py モジュールで行うこと。
"""

from __future__ import annotations

from darwin import build_handlers, build_screenshotter
from common_backends import SubprocessRunner, UnsupportedBackend

__all__ = [
    "build_handlers",
    "build_screenshotter",
    "SubprocessRunner",
    "UnsupportedBackend",
]
