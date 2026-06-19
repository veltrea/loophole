"""keys.py の単体テスト（純粋ロジック・Mac で実行可）。

ctypes も Windows も要らない。キー仕様文字列 → 仮想キーコードの変換だけを検証する。

    python3 tests/test_keys.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import keys  # noqa: E402

failures = 0


def check(cond, label):
    global failures
    if cond:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label}")
        failures += 1


def check_eq(actual, expected, label):
    global failures
    if actual == expected:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label}\n         expected={expected!r}\n         actual  ={actual!r}")
        failures += 1


def expect_error(fn, label):
    global failures
    try:
        fn()
        print(f"  [FAIL] {label} (no error raised)")
        failures += 1
    except ValueError:
        print(f"  [PASS] {label}")


# よく使う仮想キーコード（参照値）。
VK_CTRL, VK_SHIFT, VK_ALT, VK_WIN = 0x11, 0x10, 0x12, 0x5B
VK_S, VK_R, VK_A, VK_C = 0x53, 0x52, 0x41, 0x43
VK_ENTER, VK_ESC, VK_TAB = 0x0D, 0x1B, 0x09

print("VK table sanity:")
check_eq(keys.VK["a"], 0x41, "a -> 0x41")
check_eq(keys.VK["z"], 0x5A, "z -> 0x5A")
check_eq(keys.VK["0"], 0x30, "0 -> 0x30")
check_eq(keys.VK["9"], 0x39, "9 -> 0x39")
check_eq(keys.VK["f1"], 0x70, "f1 -> 0x70")
check_eq(keys.VK["f5"], 0x74, "f5 -> 0x74")
check_eq(keys.VK["f24"], 0x87, "f24 -> 0x87")
check_eq(keys.VK["up"], 0x26, "up arrow -> 0x26")

print("parse_chord (single stroke):")
check_eq(keys.parse_chord("ctrl+s"), ([VK_CTRL], VK_S), "ctrl+s")
check_eq(keys.parse_chord("Ctrl+S"), ([VK_CTRL], VK_S), "case-insensitive")
check_eq(keys.parse_chord("ctrl+shift+s"), ([VK_CTRL, VK_SHIFT], VK_S), "two modifiers keep order")
check_eq(keys.parse_chord("win+r"), ([VK_WIN], VK_R), "win+r")
check_eq(keys.parse_chord("alt+f4"), ([VK_ALT], 0x73), "alt+f4 (F4=0x73)")
check_eq(keys.parse_chord("enter"), ([], VK_ENTER), "bare enter (no modifiers)")
check_eq(keys.parse_chord("F5"), ([], 0x74), "bare F5")
check_eq(keys.parse_chord(" ctrl + s "), ([VK_CTRL], VK_S), "whitespace around tokens trimmed")
check_eq(keys.parse_chord("control+escape"), ([VK_CTRL], VK_ESC), "alias control + escape")

print("parse_chord (errors):")
expect_error(lambda: keys.parse_chord("ctrl+nope"), "unknown key name -> ValueError")
expect_error(lambda: keys.parse_chord("ctrl+shift"), "modifier-only chord -> ValueError")
expect_error(lambda: keys.parse_chord(""), "empty spec -> ValueError")
expect_error(lambda: keys.parse_chord("+"), "only separators -> ValueError")
expect_error(lambda: keys.parse_chord("foo+s"), "non-modifier before '+' -> ValueError")

print("parse_sequence (multiple strokes):")
check_eq(keys.parse_sequence("win+r enter"),
         [([VK_WIN], VK_R), ([], VK_ENTER)], "space-separated string -> two strokes")
check_eq(keys.parse_sequence(["ctrl+a", "ctrl+c"]),
         [([VK_CTRL], VK_A), ([VK_CTRL], VK_C)], "list of strokes")
check_eq(keys.parse_sequence("ctrl+s"), [([VK_CTRL], VK_S)], "single stroke string -> one element")
expect_error(lambda: keys.parse_sequence(""), "empty string -> ValueError")
expect_error(lambda: keys.parse_sequence([]), "empty list -> ValueError")
expect_error(lambda: keys.parse_sequence([1, 2]), "non-string list -> ValueError")
expect_error(lambda: keys.parse_sequence("ctrl+a bad+x"), "one bad stroke fails the whole sequence")

print("normalize (display only, no parsing):")
check_eq(keys.normalize("win+r enter"), ["win+r", "enter"], "string split on whitespace")
check_eq(keys.normalize(["a", "b"]), ["a", "b"], "list passed through")

print("KEYSYM table (X11) sanity:")
check_eq(keys.KEYSYM["a"], 0x61, "a -> keysym 0x61 (ASCII lowercase)")
check_eq(keys.KEYSYM["z"], 0x7A, "z -> keysym 0x7A")
check_eq(keys.KEYSYM["0"], 0x30, "0 -> keysym 0x30")
check_eq(keys.KEYSYM["space"], 0x20, "space -> keysym 0x20")
check_eq(keys.KEYSYM["enter"], 0xFF0D, "enter -> XK_Return")
check_eq(keys.KEYSYM["esc"], 0xFF1B, "esc -> XK_Escape")
check_eq(keys.KEYSYM["f1"], 0xFFBE, "f1 -> XK_F1")
check_eq(keys.KEYSYM["f24"], 0xFFBE + 23, "f24 -> XK_F1+23")
check_eq(keys.KEYSYM["up"], 0xFF52, "up -> XK_Up")
check_eq(keys.KEYSYM["ctrl"], 0xFFE3, "ctrl -> XK_Control_L")
check_eq(keys.KEYSYM["win"], 0xFFEB, "win -> XK_Super_L")

print("VK_TO_KEYSYM (Windows VK -> X11 keysym) for the codes the keyboard backend sees:")
# parse_chord は VK を吐く。その VK が keysym に解決できることを確かめる（X11Keyboard が引く経路）。
check_eq(keys.VK_TO_KEYSYM[keys.VK["a"]], 0x61, "VK 'A'(0x41) -> keysym 'a'(0x61)")
check_eq(keys.VK_TO_KEYSYM[keys.VK["s"]], 0x73, "VK 'S' -> keysym 's'")
check_eq(keys.VK_TO_KEYSYM[VK_CTRL], 0xFFE3, "VK_CTRL -> XK_Control_L")
check_eq(keys.VK_TO_KEYSYM[VK_SHIFT], 0xFFE1, "VK_SHIFT -> XK_Shift_L")
check_eq(keys.VK_TO_KEYSYM[VK_WIN], 0xFFEB, "VK_WIN -> XK_Super_L")
check_eq(keys.VK_TO_KEYSYM[VK_ENTER], 0xFF0D, "VK_ENTER -> XK_Return")
# 別名（control/cmd 等）が同じ VK を指しても矛盾しない（同一 keysym に解決）。
check_eq(keys.VK["ctrl"], keys.VK["control"], "ctrl/control share one VK")
# parse_chord が返す全 VK が VK_TO_KEYSYM に存在する（未マッピングで送信時に落ちない保証）。
_seq = keys.parse_sequence("ctrl+shift+s win+r enter alt+f4 up tab")
_all_vks = {vk for mods, main in _seq for vk in (list(mods) + [main])}
check(all(vk in keys.VK_TO_KEYSYM for vk in _all_vks),
      "every VK a chord can produce has a keysym mapping")

print("VK_TO_EVDEV (Windows VK -> Linux evdev code, for Wayland ydotool):")
check_eq(keys.EVDEV["a"], 30, "a -> KEY_A (30)")
check_eq(keys.EVDEV["s"], 31, "s -> KEY_S (31)")
check_eq(keys.EVDEV["ctrl"], 29, "ctrl -> KEY_LEFTCTRL (29)")
check_eq(keys.EVDEV["enter"], 28, "enter -> KEY_ENTER (28)")
check_eq(keys.EVDEV["f1"], 59, "f1 -> KEY_F1 (59)")
check_eq(keys.EVDEV["f12"], 88, "f12 -> KEY_F12 (88)")
check_eq(keys.EVDEV["1"], 2, "digit 1 -> KEY_1 (2)")
check_eq(keys.EVDEV["0"], 11, "digit 0 -> KEY_0 (11)")
check_eq(keys.VK_TO_EVDEV[keys.VK["s"]], 31, "VK 'S' -> evdev KEY_S")
check_eq(keys.VK_TO_EVDEV[VK_CTRL], 29, "VK_CTRL -> KEY_LEFTCTRL")
check(all(vk in keys.VK_TO_EVDEV for vk in _all_vks),
      "every VK a chord can produce has an evdev mapping too")

print(f"\n{'ALL PASS' if failures == 0 else 'SOME FAILED'} ({failures} failure(s))")
sys.exit(0 if failures == 0 else 1)
