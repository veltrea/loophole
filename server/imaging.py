"""imaging.py — スクリーンショットの画素変換と PNG 符号化（純粋ロジック・stdlib のみ）。

各 OS のスクリーンショット backend（win_backends.BitBltScreenshotter /
linux_backends.X11Screenshotter）は、撮影した生バッファを「トップダウンの BGRA/BGRX」
として取り出す。そこから先（チャンネル入れ替え・PNG 符号化）はプラットフォーム非依存
なので、ここに純関数として切り出して両 backend で共有する。

ctypes も OS API も import しないので Mac でも単体テストできる（tests/test_win_backends.py
が _bgra_to_rgb / _encode_png / _grab_to_png の別名経由で、tests/test_linux_backends.py が
ximage_to_rgb 経由で検証する）。
"""

from __future__ import annotations

import binascii
import struct
import zlib


def bgra_to_rgb(bgra, width: int, height: int) -> bytes:
    """トップダウン BGRA/BGRX バッファを RGB バイト列へ（拡張スライス代入＝C 速度）。

    フル画面は数千万バイト。ピクセル単位の Python ループは厳禁。アルファ/パディング
    バイトは捨てる（PNG は color type 2 = RGB）。ストライドが width*4 ちょうど（パディング
    無し）であることを前提にする——撮影 backend 側で 32bpp パック済みを渡す約束。
    """
    src = bytes(bgra)
    rgb = bytearray(width * height * 3)
    rgb[0::3] = src[2::4]  # R ← BGRA の R
    rgb[1::3] = src[1::4]  # G
    rgb[2::3] = src[0::4]  # B
    return bytes(rgb)


def encode_png(width: int, height: int, rgb: bytes) -> bytes:
    """RGB バイト列（行優先・トップダウン）を PNG にする。stdlib(zlib) のみ。純関数。"""
    def chunk(typ: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + typ + data +
                struct.pack(">I", binascii.crc32(typ + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8bit, RGB(=2)
    stride = width * 3
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # 各スキャンラインの先頭にフィルタ種別 0（None）
        raw += rgb[y * stride:(y + 1) * stride]
    idat = zlib.compress(bytes(raw), 6)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", idat) + chunk(b"IEND", b""))


def grab_to_png(width: int, height: int, bgra) -> bytes:
    """撮影した BGRA（トップダウン・ストライド = width*4）を PNG バイト列にする純パイプライン。"""
    return encode_png(width, height, bgra_to_rgb(bgra, width, height))


def ximage_to_rgb(data: bytes, width: int, height: int, bytes_per_line: int,
                  bits_per_pixel: int) -> bytes:
    """X11 XGetImage の ZPixmap データ（リトルエンディアン）を RGB バイト列へ。

    X の TrueColor 24/32bit visual は、リトルエンディアン機では各ピクセルが
    [B, G, R, X] の並び（0x00RRGGBB のメモリ表現）で来るので Windows の BGRA と同じ。
    ただし XImage は行ごとに bytes_per_line のパディングが入りうるので、ストライドが
    width*4 ちょうどなら高速パスで bgra_to_rgb に流し、そうでなければ行単位で詰め直す。

    bits_per_pixel は 32（パック 32bpp）と 24（パック 24bpp）に対応する。
    """
    if bits_per_pixel == 32:
        if bytes_per_line == width * 4:
            return bgra_to_rgb(data, width, height)
        # パディング有り: 各行から width*4 バイトだけ取り出して連結し直す。
        packed = bytearray(width * 4 * height)
        row = width * 4
        for y in range(height):
            off = y * bytes_per_line
            packed[y * row:(y + 1) * row] = data[off:off + row]
        return bgra_to_rgb(bytes(packed), width, height)
    if bits_per_pixel == 24:
        # パック 24bpp: メモリ並びは [B, G, R]（リトルエンディアン）。行パディングを考慮。
        rgb = bytearray(width * height * 3)
        for y in range(height):
            off = y * bytes_per_line
            for x in range(width):
                p = off + x * 3
                q = (y * width + x) * 3
                rgb[q] = data[p + 2]      # R
                rgb[q + 1] = data[p + 1]  # G
                rgb[q + 2] = data[p]      # B
        return bytes(rgb)
    raise ValueError(f"unsupported bits_per_pixel for screenshot: {bits_per_pixel}")
