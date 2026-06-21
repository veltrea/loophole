"""test_darwin_keyboard.py — Mac の CGEventKeyboard のフェイク注入テスト。

実 CoreGraphics は触らず、cg/cf 関数群をフェイクで差し替えて契約を検証する:
- 修飾フラグ（shift/ctrl/alt/cmd）が CGEventFlags ビットに正しく畳まれる
- main の press → release を順に Post する
- CGEventSource / CGEvent が CFRelease で必ず解放される（リーク無し）
- 未知の修飾 / 変換不能な VK で actionable RuntimeError
- macOS kVK テーブルの代表値が Carbon の公式定数と一致する
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))
sys.path.insert(0, _HERE)

import keys as keyspec  # noqa: E402
from darwin.keyboard import CGEventKeyboard  # noqa: E402
from linux_testlib import Checker  # noqa: E402

c = Checker()


# --- Mac kVK 表の代表値検証 ----------------------------------------------
print("KVK table (sanity check vs Carbon HIToolbox/Events.h):")
c.eq(keyspec.KVK["a"], 0x00, "kVK_ANSI_A=0x00")
c.eq(keyspec.KVK["s"], 0x01, "kVK_ANSI_S=0x01")
c.eq(keyspec.KVK["return"], 0x24, "kVK_Return=0x24")
c.eq(keyspec.KVK["enter"], 0x24, "enter alias maps to Return")
c.eq(keyspec.KVK["tab"], 0x30, "kVK_Tab=0x30")
c.eq(keyspec.KVK["space"], 0x31, "kVK_Space=0x31")
c.eq(keyspec.KVK["escape"], 0x35, "kVK_Escape=0x35")
c.eq(keyspec.KVK["cmd"], 0x37, "kVK_Command=0x37 (cmd/win/super/meta)")
c.eq(keyspec.KVK["win"], 0x37, "win alias maps to Command")
c.eq(keyspec.KVK["shift"], 0x38, "kVK_Shift=0x38")
c.eq(keyspec.KVK["alt"], 0x3A, "kVK_Option=0x3A (alt/menu)")
c.eq(keyspec.KVK["ctrl"], 0x3B, "kVK_Control=0x3B")
c.eq(keyspec.KVK["backspace"], 0x33, "backspace → kVK_Delete=0x33 (Mac の Delete はバックスペース)")
c.eq(keyspec.KVK["delete"], 0x75, "delete → kVK_ForwardDelete=0x75")
c.eq(keyspec.KVK["up"], 0x7E, "kVK_UpArrow=0x7E")
c.eq(keyspec.KVK["f1"], 0x7A, "kVK_F1=0x7A")
c.eq(keyspec.KVK["f5"], 0x60, "kVK_F5=0x60")

# VK → KVK 機械生成の検証（修飾の cross-platform 紐付け）
c.eq(keyspec.VK_TO_KVK[0x10], 0x38, "VK_SHIFT (0x10) → kVK_Shift (0x38)")
c.eq(keyspec.VK_TO_KVK[0x11], 0x3B, "VK_CONTROL (0x11) → kVK_Control (0x3B)")
c.eq(keyspec.VK_TO_KVK[0x12], 0x3A, "VK_MENU/ALT (0x12) → kVK_Option (0x3A)")
c.eq(keyspec.VK_TO_KVK[0x5B], 0x37, "VK_LWIN/CMD (0x5B) → kVK_Command (0x37)")
c.eq(keyspec.VK_TO_KVK[ord('S')], 0x01, "VK 'S' → kVK_ANSI_S")


# --- CGEventKeyboard contract（フェイク CG/CF 注入） ----------------------
class _FakeCG:
    """CoreGraphics の必要関数だけを差し替える。呼び出しを log に記録する。"""

    def __init__(self, source_handle=0x1000, key_handles=None):
        self.source = source_handle
        self.keys = list(key_handles or [0x2001, 0x2002])  # press, release
        self.events_returned = []
        self.log = []  # ("name", ...args)

    def CGEventSourceCreate(self, state):
        self.log.append(("CGEventSourceCreate", state))
        return self.source

    def CGEventCreateKeyboardEvent(self, source, key_code, key_down):
        self.log.append(("CGEventCreateKeyboardEvent", source, key_code, int(key_down)))
        ev = self.keys.pop(0)
        self.events_returned.append(ev)
        return ev

    def CGEventSetFlags(self, ev, flags):
        self.log.append(("CGEventSetFlags", ev, flags))

    def CGEventKeyboardSetUnicodeString(self, ev, length, buf):
        # buf は ctypes の c_void_p（c_uint16 配列をキャストしたもの）。length と合わせて記録する。
        self.log.append(("CGEventKeyboardSetUnicodeString", ev, int(length)))

    def CGEventPost(self, tap, ev):
        self.log.append(("CGEventPost", tap, ev))


class _FakeCF:
    def __init__(self):
        self.released = []

    def CFRelease(self, handle):
        self.released.append(handle)


class _FakeLib:
    def __init__(self, cg, cf):
        self.cg = cg
        self.cf = cf


def _patched_keyboard(cg, cf):
    """CGEventKeyboard に差し替えた _FakeLib を持たせる。"""
    from darwin.keyboard import CGEventKeyboard
    kb = CGEventKeyboard.__new__(CGEventKeyboard)
    kb._lib = _FakeLib(cg, cf)
    return kb


print("CGEventKeyboard.send_chord():")

# 1) cmd+s — flags=COMMAND, main='s' を press/release
cg = _FakeCG(source_handle=0xAAAA, key_handles=[0xB001, 0xB002])
cf = _FakeCF()
kb = _patched_keyboard(cg, cf)
modifiers, main = keyspec.parse_chord("cmd+s")
kb.send_chord(modifiers, main)

# 期待される呼び順:
#   CGEventSourceCreate(0)
#   CGEventCreateKeyboardEvent(src, kVK_S=0x01, down=1)
#   CGEventSetFlags(press, COMMAND)
#   CGEventPost(HID, press)
#   CFRelease(press)
#   CGEventCreateKeyboardEvent(src, kVK_S=0x01, down=0)
#   CGEventSetFlags(release, COMMAND)
#   CGEventPost(HID, release)
#   CFRelease(release)
#   CFRelease(source)
COMMAND = 0x00100000
log_names = [entry[0] for entry in cg.log]
c.eq(log_names, [
    "CGEventSourceCreate",
    "CGEventCreateKeyboardEvent", "CGEventSetFlags", "CGEventPost",
    "CGEventCreateKeyboardEvent", "CGEventSetFlags", "CGEventPost",
], "cmd+s makes source + 2 events with flags + post each")
c.eq(cg.log[1], ("CGEventCreateKeyboardEvent", 0xAAAA, 0x01, 1), "press uses kVK_S, key_down=1")
c.eq(cg.log[2], ("CGEventSetFlags", 0xB001, COMMAND), "press flags include COMMAND")
c.eq(cg.log[3], ("CGEventPost", 0, 0xB001), "press posted to HID tap (0)")
c.eq(cg.log[4], ("CGEventCreateKeyboardEvent", 0xAAAA, 0x01, 0), "release uses kVK_S, key_down=0")
c.eq(cg.log[5], ("CGEventSetFlags", 0xB002, COMMAND), "release flags include COMMAND")
c.eq(cg.log[6], ("CGEventPost", 0, 0xB002), "release posted to HID tap")
c.eq(cf.released, [0xB001, 0xB002, 0xAAAA],
     "CFRelease called for press, release, source (in that order)")

# 2) ctrl+shift+a → flags は SHIFT|CONTROL
cg = _FakeCG(key_handles=[0xC001, 0xC002])
cf = _FakeCF()
kb = _patched_keyboard(cg, cf)
modifiers, main = keyspec.parse_chord("ctrl+shift+a")
kb.send_chord(modifiers, main)
SHIFT = 0x00020000
CONTROL = 0x00040000
c.eq(cg.log[2][2], SHIFT | CONTROL, "ctrl+shift composes both bits")
c.eq(cg.log[1][2], 0x00, "main key is kVK_ANSI_A")

# 3) 修飾なし（"enter"）→ flags ビットを乗せない（CGEventSetFlags が呼ばれない）
cg = _FakeCG(key_handles=[0xD001, 0xD002])
cf = _FakeCF()
kb = _patched_keyboard(cg, cf)
modifiers, main = keyspec.parse_chord("enter")
kb.send_chord(modifiers, main)
log_names = [entry[0] for entry in cg.log]
c.eq(log_names, [
    "CGEventSourceCreate",
    "CGEventCreateKeyboardEvent", "CGEventPost",
    "CGEventCreateKeyboardEvent", "CGEventPost",
], "modifier-free chord skips CGEventSetFlags entirely")

# 4) CGEventSourceCreate が NULL → TCC エラー風メッセージ
class _NullSourceCG(_FakeCG):
    def CGEventSourceCreate(self, state):
        self.log.append(("CGEventSourceCreate", state))
        return 0


cg = _NullSourceCG()
cf = _FakeCF()
kb = _patched_keyboard(cg, cf)
raised = None
try:
    kb.send_chord([], keyspec.VK["a"])
except RuntimeError as exc:
    raised = str(exc)
c.ok(raised is not None and "Accessibility" in raised,
     "NULL source raises with Accessibility hint")
c.eq(cf.released, [], "no CFRelease when source is NULL")

# 5) 変換できないキー（VK_F21=0x80）→ actionable
cg = _FakeCG()
cf = _FakeCF()
kb = _patched_keyboard(cg, cf)
raised = None
try:
    kb.send_chord([], 0xFE)  # F15 でも適当に存在しない VK
except RuntimeError as exc:
    raised = str(exc)
c.ok(raised is not None and "no macOS key code" in raised,
     "unmappable main VK raises actionable RuntimeError")


# --- CGEventKeyboard.type_text（Unicode 直接注入） -----------------------
print("CGEventKeyboard.type_text():")
cg = _FakeCG(source_handle=0xAAAA, key_handles=[0xE001, 0xE002, 0xE003, 0xE004])
cf = _FakeCF()
kb = _patched_keyboard(cg, cf)
kb.type_text("Aあ")  # 2 文字 × (press+release) = 4 イベント。各 1 UTF-16 ユニット。

log_names = [entry[0] for entry in cg.log]
c.eq(log_names, [
    "CGEventSourceCreate",
    "CGEventCreateKeyboardEvent", "CGEventKeyboardSetUnicodeString", "CGEventPost",
    "CGEventCreateKeyboardEvent", "CGEventKeyboardSetUnicodeString", "CGEventPost",
    "CGEventCreateKeyboardEvent", "CGEventKeyboardSetUnicodeString", "CGEventPost",
    "CGEventCreateKeyboardEvent", "CGEventKeyboardSetUnicodeString", "CGEventPost",
], "each char makes press+release, both carrying the unicode string, both posted")
c.eq(cg.log[1], ("CGEventCreateKeyboardEvent", 0xAAAA, 0, 1), "press uses keycode 0, key_down=1")
c.eq(cg.log[2], ("CGEventKeyboardSetUnicodeString", 0xE001, 1), "press carries 1 UTF-16 unit ('A')")
c.eq(cg.log[4], ("CGEventCreateKeyboardEvent", 0xAAAA, 0, 0), "release uses keycode 0, key_down=0")
c.eq(cf.released, [0xE001, 0xE002, 0xE003, 0xE004, 0xAAAA],
     "every event + the source are CFRelease'd (no leak)")

# BMP 外（絵文字 U+1F600）はサロゲートペア = 2 UTF-16 ユニットで 1 イベントに載る。
c.eq(CGEventKeyboard._utf16_units("😀"), [0xD83D, 0xDE00],
     "astral char split into a UTF-16 surrogate pair")

c.done()
