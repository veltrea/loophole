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

print("Win32WindowManager.set_window — axes map to the right Win32 calls (fake user32):")
# Win32 直叩きは windll を触るので、__new__ で構築して self._u にフェイクを差す（raise 経路は
# activate→windll.kernel32 を触るため Mac では検証しない＝Windows 実機スモークで確認する）。


class FakeUser32:
    def __init__(self, rect=(0, 0, 0, 0), iconic=False, is_window=True):
        self.calls = []
        self._rect = rect
        self._iconic = iconic
        self._is_window = is_window

    def IsWindow(self, h):
        return 1 if self._is_window else 0

    def ShowWindow(self, h, cmd):
        self.calls.append(("ShowWindow", cmd)); return 1

    def SetWindowPos(self, h, after, x, y, w, ht, flags):
        self.calls.append(("SetWindowPos", x, y, w, ht, flags)); return 1

    def GetWindowRect(self, h, p):
        p.contents.left, p.contents.top, p.contents.right, p.contents.bottom = self._rect
        return 1

    def IsIconic(self, h):
        return 1 if self._iconic else 0


wm = win_backends.Win32WindowManager.__new__(win_backends.Win32WindowManager)
fake_u = FakeUser32(rect=(100, 150, 740, 630), iconic=False)  # → w=640, h=480
wm._u = fake_u

# move + resize → 1 回の SetWindowPos（NOZORDER|NOACTIVATE のみ。NOMOVE/NOSIZE は立てない）
st = wm.set_window(123, position=(100, 150), size=(640, 480))
swp = [c for c in fake_u.calls if c[0] == "SetWindowPos"][-1]
check(swp[1:5] == (100, 150, 640, 480), "SetWindowPos receives x,y,w,h")
check(swp[5] == (win_backends._SWP_NOZORDER | win_backends._SWP_NOACTIVATE),
      "move+resize sets neither NOMOVE nor NOSIZE")
check(st == {"x": 100, "y": 150, "width": 640, "height": 480,
             "minimized": False, "fullscreen": False},
      "readback derives w/h from GetWindowRect; fullscreen is always False")

# move only → NOSIZE を立てる（サイズを残す）
fake_u.calls.clear()
wm.set_window(123, position=(10, 20))
swp = [c for c in fake_u.calls if c[0] == "SetWindowPos"][-1]
check(swp[5] & win_backends._SWP_NOSIZE and not swp[5] & win_backends._SWP_NOMOVE,
      "position-only sets SWP_NOSIZE (and not NOMOVE)")

# resize only → NOMOVE を立てる（位置を残す）
fake_u.calls.clear()
wm.set_window(123, size=(800, 600))
swp = [c for c in fake_u.calls if c[0] == "SetWindowPos"][-1]
check(swp[5] & win_backends._SWP_NOMOVE and not swp[5] & win_backends._SWP_NOSIZE,
      "size-only sets SWP_NOMOVE (and not NOSIZE)")

# 最小化/復元 → ShowWindow(SW_MINIMIZE / SW_RESTORE)
fake_u.calls.clear()
wm.set_window(123, minimized=True)
check(("ShowWindow", win_backends._SW_MINIMIZE) in fake_u.calls,
      "minimized=True → ShowWindow(SW_MINIMIZE)")
fake_u.calls.clear()
wm.set_window(123, minimized=False)
check(("ShowWindow", win_backends._SW_RESTORE) in fake_u.calls,
      "minimized=False → ShowWindow(SW_RESTORE)")

# 最大化は True のときだけ作用（False は no-op = macOS/protocol と揃える）
fake_u.calls.clear()
wm.set_window(123, maximized=True)
check(("ShowWindow", win_backends._SW_MAXIMIZE) in fake_u.calls,
      "maximized=True → ShowWindow(SW_MAXIMIZE)")
fake_u.calls.clear()
wm.set_window(123, maximized=False)
check(all(c[0] != "ShowWindow" for c in fake_u.calls),
      "maximized=False is a no-op (no ShowWindow)")

# fullscreen は捏造しない: maximize に化けさせず、readback も常に False
fake_u.calls.clear()
st = wm.set_window(123, fullscreen=True)
check(all(c[0] != "ShowWindow" for c in fake_u.calls) and st["fullscreen"] is False,
      "fullscreen=True is not faked via maximize; readback stays False (no OS fullscreen on Windows)")

# 窓が無い → actionable な RuntimeError
wm._u = FakeUser32(is_window=False)
raised_sw = None
try:
    wm.set_window(999, minimized=True)
except RuntimeError as exc:
    raised_sw = str(exc)
check(raised_sw is not None and "999" in raised_sw,
      "missing window raises an actionable RuntimeError naming the handle")

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
