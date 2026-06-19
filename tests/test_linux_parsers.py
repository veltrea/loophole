"""test_linux_parsers.py — モジュール横断で使う純パーサ（parsers.py）と画素変換（imaging）。

複数モジュールから参照される共有の純関数だけをここで検証する（各能力固有のパーサは
その能力のテストファイルに置く: AT-SPI→menu、fcitx/ibus→ime、sway/hyprland→window 等）。

    python3 tests/test_linux_parsers.py
"""

import ctypes
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))
sys.path.insert(0, _HERE)

import imaging  # noqa: E402
import linux_backends as lb  # noqa: E402
from linux_testlib import Checker  # noqa: E402

c = Checker()

print("parse_long_array (format-32 property = C long array, LP64):")
width = ctypes.sizeof(ctypes.c_ulong)
packed = struct.pack("@" + "L" * 3, 0x12345678, 0xFF, 0x1)
c.eq(lb.parse_long_array(packed), [0x12345678, 0xFF, 0x1], "round-trips 3 longs")
c.eq(lb.parse_long_array(b""), [], "empty data -> empty list")
c.eq(len(packed), 3 * width, "each item is sizeof(c_ulong) wide")

print("decode_text (UTF8_STRING vs legacy WM_NAME, trailing NUL):")
c.eq(lb.decode_text(b"hello\x00", True), "hello", "utf-8 with trailing NUL")
c.eq(lb.decode_text("café".encode("utf-8"), True), "café", "utf-8 multibyte")
c.eq(lb.decode_text(b"caf\xe9", False), "café", "latin-1 legacy name")
c.eq(lb.decode_text(b"a\x00b", True), "a", "NUL-separated -> first string")

print("imaging.ximage_to_rgb (ZPixmap BGRX/BGR -> RGB):")
# 32bpp, no padding: 2px [B,G,R,X]*2 -> RGB
c.eq(imaging.ximage_to_rgb(bytes([1, 2, 3, 4, 5, 6, 7, 8]), 2, 1, 8, 32),
     bytes([3, 2, 1, 7, 6, 5]), "32bpp no padding swaps B/R, drops X")
# 32bpp with row padding: 1px/row, bytes_per_line=8 (4 used + 4 pad), 2 rows
padded = bytes([10, 20, 30, 40, 99, 99, 99, 99, 50, 60, 70, 80, 99, 99, 99, 99])
c.eq(imaging.ximage_to_rgb(padded, 1, 2, 8, 32), bytes([30, 20, 10, 70, 60, 50]),
     "32bpp honors bytes_per_line padding")
# 24bpp packed: 2px [B,G,R]*2
c.eq(imaging.ximage_to_rgb(bytes([1, 2, 3, 4, 5, 6]), 2, 1, 6, 24),
     bytes([3, 2, 1, 6, 5, 4]), "24bpp packed -> RGB")

c.done()
