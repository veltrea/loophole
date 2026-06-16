"""win_backends.py のうち Windows 非依存で検証できる契約をテストする。

PowerShell をやめて純 ctypes 化したので、テストの核は「撮影パイプラインの純ロジック」:
  - _bgra_to_rgb : BGRA(トップダウン) → RGB のチャンネル入れ替え（アルファ破棄）
  - _encode_png  : RGB → 妥当な PNG（署名・IHDR・チャンク CRC・zlib 復元で往復）
  - _grab_to_png : 上記の合成（撮った BGRA がそのまま正しい PNG になる）
  - DdagrabScreenshotter : ffmpeg 呼び出し（runner をフェイク注入）の成否変換

Win32 直叩き部分（Win32Clipboard / BitBltScreenshotter._grab / Win32MenuController）は
ctypes.windll を触るため Mac では回せない → 対象 Windows 実機スモークで確認する:
  - クリップボード日本語往復: set("検証_表予能ソ_①㈱") → get() が一致（CP932 が消えた証明）
  - capture(): PNG 署名（\\x89PNG）と妥当な寸法が出る
  - メニュー: メモ帳を起動 → Win32MenuController().enumerate(hwnd) で「ファイル/編集/書式/
    表示/ヘルプ」相当のツリー＋command_id が取れる。安全なトグル（書式→右端で折り返し）を
    invoke(hwnd, cid) → 再 enumerate で checked 反転を確認（発火が効いた証明・純テキスト観測）

    python3 tests/test_win_backends.py
"""

import binascii
import os
import struct
import sys
import tempfile
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))

import win_backends  # noqa: E402
from handlers import ProcessResult  # noqa: E402

PNG_SIG = b"\x89PNG\r\n\x1a\n"
failures = 0


def check(cond, label):
    global failures
    if cond:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label}")
        failures += 1


def parse_png(png):
    """最小 PNG パーサ: 署名検証 → チャンクを (type, data) で返しつつ CRC を検査する。"""
    assert png[:8] == PNG_SIG, "bad signature"
    chunks = []
    i = 8
    while i < len(png):
        (length,) = struct.unpack(">I", png[i:i + 4])
        typ = png[i + 4:i + 8]
        data = png[i + 8:i + 8 + length]
        (crc,) = struct.unpack(">I", png[i + 8 + length:i + 12 + length])
        assert crc == (binascii.crc32(typ + data) & 0xFFFFFFFF), f"bad CRC for {typ!r}"
        chunks.append((typ, data))
        i += 12 + length
    return chunks


print("_bgra_to_rgb — channel swap (drops alpha):")
# 2px トップダウン: (B,G,R,A)=(1,2,3,4),(5,6,7,8) → RGB=(3,2,1),(7,6,5)
bgra = bytes([1, 2, 3, 4, 5, 6, 7, 8])
rgb = win_backends._bgra_to_rgb(bgra, 2, 1)
check(rgb == bytes([3, 2, 1, 7, 6, 5]), "BGRA→RGB swaps B/R and drops the alpha byte")

print("_encode_png — produces a valid PNG that round-trips:")
W, H = 2, 2
src_rgb = bytes([
    10, 20, 30, 40, 50, 60,        # row 0: 2 px
    70, 80, 90, 100, 110, 120,     # row 1: 2 px
])
png = win_backends._encode_png(W, H, src_rgb)
check(png[:8] == PNG_SIG, "starts with the PNG signature")
chunks = parse_png(png)  # CRC 不一致なら assert で落ちる
types = [t for t, _ in chunks]
check(types[0] == b"IHDR" and types[-1] == b"IEND", "IHDR first, IEND last")
ihdr = dict(chunks)[b"IHDR"]
w, h, depth, ctype = struct.unpack(">IIBB", ihdr[:10])
check((w, h, depth, ctype) == (W, H, 8, 2), "IHDR carries width/height, 8-bit depth, RGB(=2)")
# IDAT を復元し、各行のフィルタバイトを剥がして元 RGB に一致するか
idat = b"".join(d for t, d in chunks if t == b"IDAT")
raw = zlib.decompress(idat)
stride = W * 3
check(raw[0] == 0, "scanline filter type is 0 (None)")
recovered = bytearray()
for y in range(H):
    off = y * (stride + 1)
    recovered += raw[off + 1:off + 1 + stride]
check(bytes(recovered) == src_rgb, "IDAT decompresses back to the exact RGB pixels")

print("_grab_to_png — full pipeline (BGRA grab → PNG decodes to expected RGB):")
# 2px トップダウン BGRA → 期待 RGB=(50,100,200),(7,8,9)
grab_bgra = bytes([200, 100, 50, 255, 9, 8, 7, 0])
png2 = win_backends._grab_to_png(2, 1, grab_bgra)
ch2 = parse_png(png2)
raw2 = zlib.decompress(b"".join(d for t, d in ch2 if t == b"IDAT"))
check(raw2 == bytes([0, 50, 100, 200, 7, 8, 9]),
      "pipeline yields filter0 + R/G/B per pixel (no alpha)")

print("DdagrabScreenshotter — success path (faked ffmpeg writes the PNG):")
expected = PNG_SIG + b"dda image bytes"


class FakeRunnerOK:
    def run(self, argv, cwd, timeout, stdin_text):
        # argv の末尾が出力 PNG パス（-y の次）。そこへ実際に書く。
        with open(argv[-1], "wb") as f:
            f.write(expected)
        return ProcessResult(0, b"", b"")


got = win_backends.DdagrabScreenshotter(ffmpeg="ffmpeg", runner=FakeRunnerOK()).capture()
check(got == expected, "capture() returns the PNG the (faked) ffmpeg wrote")
leftover = [f for f in os.listdir(tempfile.gettempdir()) if f.startswith("loophole_dda_")]
check(leftover == [], "capture() removes its temp PNG afterwards")

print("DdagrabScreenshotter — failure path (nonzero exit → RuntimeError with stderr):")


class FakeRunnerFail:
    def run(self, argv, cwd, timeout, stdin_text):
        return ProcessResult(1, b"", "ddagrab boom".encode("utf-8"))


raised = None
try:
    win_backends.DdagrabScreenshotter(runner=FakeRunnerFail()).capture()
except RuntimeError as exc:
    raised = str(exc)
check(raised is not None and "boom" in raised, "nonzero exit raises RuntimeError carrying stderr")

print("DdagrabScreenshotter — ffmpeg missing (not started → clear RuntimeError):")


class FakeRunnerMissing:
    def run(self, argv, cwd, timeout, stdin_text):
        return ProcessResult(-1, b"", b"", started=False)


raised2 = None
try:
    win_backends.DdagrabScreenshotter(runner=FakeRunnerMissing()).capture()
except RuntimeError as exc:
    raised2 = str(exc)
check(raised2 is not None and "ffmpeg" in raised2.lower(), "missing ffmpeg raises a clear RuntimeError")

print("_select_screenshotter — env LOOPHOLE_SCREENSHOT_BACKEND picks the class:")
# Win32 クラスは Mac で __init__ できないので、選択ロジックだけをダミーに差し替えて検証。
_orig_bitblt = win_backends.BitBltScreenshotter
_orig_dda = win_backends.DdagrabScreenshotter
win_backends.BitBltScreenshotter = lambda: "BITBLT"
win_backends.DdagrabScreenshotter = lambda: "DDA"
_orig_env = os.environ.get("LOOPHOLE_SCREENSHOT_BACKEND")
try:
    os.environ.pop("LOOPHOLE_SCREENSHOT_BACKEND", None)
    check(win_backends._select_screenshotter() == "BITBLT", "default → BitBlt")
    os.environ["LOOPHOLE_SCREENSHOT_BACKEND"] = "ddagrab"
    check(win_backends._select_screenshotter() == "DDA", "ddagrab → Ddagrab")
    os.environ["LOOPHOLE_SCREENSHOT_BACKEND"] = "bitblt"
    check(win_backends._select_screenshotter() == "BITBLT", "bitblt → BitBlt")
    os.environ["LOOPHOLE_SCREENSHOT_BACKEND"] = "nonsense"
    check(win_backends._select_screenshotter() == "BITBLT", "unknown value falls back to BitBlt")
finally:
    win_backends.BitBltScreenshotter = _orig_bitblt
    win_backends.DdagrabScreenshotter = _orig_dda
    if _orig_env is None:
        os.environ.pop("LOOPHOLE_SCREENSHOT_BACKEND", None)
    else:
        os.environ["LOOPHOLE_SCREENSHOT_BACKEND"] = _orig_env

print("Win32MenuController — constants & off-Windows guard (Mac-safe contract):")
check(win_backends._WM_COMMAND == 0x0111, "WM_COMMAND constant is 0x0111")
check(len(win_backends._MENUITEMINFOW._fields_) == 12, "_MENUITEMINFOW declares all 12 fields")
raised_menu = None
try:
    win_backends.Win32MenuController()
except RuntimeError as exc:
    raised_menu = str(exc)
check(raised_menu is not None and "Windows-only" in raised_menu,
      "Win32MenuController refuses to construct off-Windows (windll guard)")

print(f"\n{'ALL PASS' if failures == 0 else 'SOME FAILED'} ({failures} failure(s))")
sys.exit(0 if failures == 0 else 1)
