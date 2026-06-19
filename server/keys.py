"""keys.py — キー仕様文字列を Windows 仮想キーコード列へ変換する純粋ロジック。

loophole は元々「クリップボードに値を入れて GUI にペースト」する経路で IME を避けて
きた（win_backends.Win32Clipboard 参照）。しかし GUI 自動化の大半は `Ctrl+S` /
`Alt+F4` / `Win+R` / `Enter` のような **ショートカット（キーの組み合わせ）** で進む。
それを送るために、人間が書きやすい `"ctrl+shift+s"` 形式を仮想キーコードに翻訳する。

ここは ctypes も win_backends も import しない純粋ロジックなので、Mac でも単体テスト
できる（tests/test_keys.py）。実際にキーを叩く部分は win_backends.SendInputKeyboard。

表記:
  - 1 ストローク = `"修飾+...+メイン"`。修飾キーは 0 個以上、メインキーはちょうど 1 個。
    例: "ctrl+s" / "alt+f4" / "win+r" / "ctrl+shift+esc" / 単独 "enter" "tab" "f5"
  - 複数ストローク = 空白区切りの 1 文字列（"win+r enter"）か、文字列のリスト
    (["win+r", "enter"])。どちらも左から順に送る想定。
  - 大文字小文字は無視する。"Ctrl+S" も "ctrl+s" も同じ。
"""

from __future__ import annotations

from typing import Dict, List, Set, Tuple, Union

# 修飾キーの仮想キーコード。
_VK_SHIFT = 0x10
_VK_CONTROL = 0x11
_VK_MENU = 0x12  # Alt
_VK_LWIN = 0x5B


def _build_vk_table() -> Dict[str, int]:
    """キー名（小文字）→ Windows 仮想キーコードの対応表を組む。"""
    vk: Dict[str, int] = {
        # --- 修飾キー（メインとしても単独で押せるよう表にも入れる） ---
        "shift": _VK_SHIFT,
        "ctrl": _VK_CONTROL, "control": _VK_CONTROL,
        "alt": _VK_MENU, "menu": _VK_MENU,
        "win": _VK_LWIN, "super": _VK_LWIN, "cmd": _VK_LWIN, "meta": _VK_LWIN,
        # --- 特殊キー ---
        "enter": 0x0D, "return": 0x0D,
        "tab": 0x09,
        "esc": 0x1B, "escape": 0x1B,
        "space": 0x20, "spacebar": 0x20,
        "backspace": 0x08, "bs": 0x08,
        "delete": 0x2E, "del": 0x2E,
        "insert": 0x2D, "ins": 0x2D,
        "home": 0x24, "end": 0x23,
        "pageup": 0x21, "pgup": 0x21, "prior": 0x21,
        "pagedown": 0x22, "pgdn": 0x22, "next": 0x22,
        "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
        "printscreen": 0x2C, "prtsc": 0x2C, "prtscn": 0x2C,
        "capslock": 0x14,
        "apps": 0x5D, "menukey": 0x5D,  # コンテキストメニュー（右クリック相当）キー
    }
    # 英字 a-z（VK は ASCII 大文字コードと同値: 'A'=0x41）。
    for c in range(ord("a"), ord("z") + 1):
        vk[chr(c)] = ord(chr(c).upper())
    # 数字 0-9（VK は ASCII 数字コードと同値: '0'=0x30）。
    for c in range(ord("0"), ord("9") + 1):
        vk[chr(c)] = ord(chr(c))
    # ファンクションキー f1-f24（VK_F1=0x70 から連番）。
    for i in range(1, 25):
        vk[f"f{i}"] = 0x70 + (i - 1)
    return vk


VK: Dict[str, int] = _build_vk_table()


def _build_keysym_table() -> Dict[str, int]:
    """キー名（小文字）→ X11 keysym の対応表を組む（Linux の XTEST 用）。

    VK と同じ名前集合をカバーする。これにより VK_TO_KEYSYM（後述）で「Windows VK →
    X11 keysym」を機械的に作れる。値は X11/keysymdef.h の定数:
      - 英字は小文字 keysym（'a'=0x61）。大文字化はショートカット指定では使わない
        （"shift+a" のように shift を明示する流儀なので、メインキーは小文字でよい）。
      - 数字・space は ASCII と同値。特殊キーは XK_* 定数（0xFF00 帯）。
      - F1..F24 は XK_F1=0xFFBE から連番。
    """
    ks: Dict[str, int] = {
        # --- 修飾キー（_L 側を使う） ---
        "shift": 0xFFE1,                                  # XK_Shift_L
        "ctrl": 0xFFE3, "control": 0xFFE3,               # XK_Control_L
        "alt": 0xFFE9, "menu": 0xFFE9,                   # XK_Alt_L
        "win": 0xFFEB, "super": 0xFFEB, "cmd": 0xFFEB, "meta": 0xFFEB,  # XK_Super_L
        # --- 特殊キー ---
        "enter": 0xFF0D, "return": 0xFF0D,               # XK_Return
        "tab": 0xFF09,                                   # XK_Tab
        "esc": 0xFF1B, "escape": 0xFF1B,                 # XK_Escape
        "space": 0x0020, "spacebar": 0x0020,             # XK_space
        "backspace": 0xFF08, "bs": 0xFF08,               # XK_BackSpace
        "delete": 0xFFFF, "del": 0xFFFF,                 # XK_Delete
        "insert": 0xFF63, "ins": 0xFF63,                 # XK_Insert
        "home": 0xFF50, "end": 0xFF57,                   # XK_Home / XK_End
        "pageup": 0xFF55, "pgup": 0xFF55, "prior": 0xFF55,   # XK_Prior
        "pagedown": 0xFF56, "pgdn": 0xFF56, "next": 0xFF56,  # XK_Next
        "up": 0xFF52, "down": 0xFF54, "left": 0xFF51, "right": 0xFF53,
        "printscreen": 0xFF61, "prtsc": 0xFF61, "prtscn": 0xFF61,  # XK_Print
        "capslock": 0xFFE5,                              # XK_Caps_Lock
        "apps": 0xFF67, "menukey": 0xFF67,               # XK_Menu（コンテキストメニュー）
    }
    # 英字 a-z（keysym は ASCII 小文字と同値: 'a'=0x61）。
    for c in range(ord("a"), ord("z") + 1):
        ks[chr(c)] = c
    # 数字 0-9（keysym は ASCII と同値: '0'=0x30）。
    for c in range(ord("0"), ord("9") + 1):
        ks[chr(c)] = c
    # ファンクションキー f1-f24（XK_F1=0xFFBE から連番）。
    for i in range(1, 25):
        ks[f"f{i}"] = 0xFFBE + (i - 1)
    return ks


KEYSYM: Dict[str, int] = _build_keysym_table()

# Windows 仮想キーコード → X11 keysym（同じ名前集合から機械的に作る）。
# linux_backends.X11Keyboard が、handlers から渡る VK 列をこの表で keysym に直す。
# 同じ VK を指す別名（ctrl/control 等）は同一 keysym に解決されるので衝突しない。
VK_TO_KEYSYM: Dict[int, int] = {VK[name]: KEYSYM[name] for name in VK}


def _build_evdev_table() -> Dict[str, int]:
    """キー名（小文字）→ Linux evdev キーコード（input-event-codes.h の KEY_*）を組む。

    Wayland の ydotool は keysym ではなく**カーネルの入力イベントコード**を取るので、
    XTEST(keysym) とは別の数値体系が要る。VK/KEYSYM と同じ名前集合をカバーし、
    VK_TO_EVDEV で「Windows VK → evdev」を機械的に作る。
    """
    ev: Dict[str, int] = {
        # --- 修飾キー（_LEFT 側） ---
        "shift": 42, "ctrl": 29, "control": 29, "alt": 56, "menu": 56,
        "win": 125, "super": 125, "cmd": 125, "meta": 125,  # KEY_LEFTMETA
        # --- 特殊キー ---
        "enter": 28, "return": 28, "tab": 15, "esc": 1, "escape": 1,
        "space": 57, "spacebar": 57, "backspace": 14, "bs": 14,
        "delete": 111, "del": 111, "insert": 110, "ins": 110,
        "home": 102, "end": 107,
        "pageup": 104, "pgup": 104, "prior": 104,
        "pagedown": 109, "pgdn": 109, "next": 109,
        "up": 103, "down": 108, "left": 105, "right": 106,
        "printscreen": 99, "prtsc": 99, "prtscn": 99,   # KEY_SYSRQ
        "capslock": 58, "apps": 139, "menukey": 139,    # KEY_MENU
    }
    # 英字 a-z（QWERTY 物理配列のコード）。
    letters = {
        "a": 30, "b": 48, "c": 46, "d": 32, "e": 18, "f": 33, "g": 34, "h": 35,
        "i": 23, "j": 36, "k": 37, "l": 38, "m": 50, "n": 49, "o": 24, "p": 25,
        "q": 16, "r": 19, "s": 31, "t": 20, "u": 22, "v": 47, "w": 17, "x": 45,
        "y": 21, "z": 44,
    }
    ev.update(letters)
    # 数字 1-9,0（KEY_1=2 .. KEY_9=10, KEY_0=11）。
    for n in range(1, 10):
        ev[str(n)] = n + 1
    ev["0"] = 11
    # ファンクションキー: F1-F10=59..68、F11=87、F12=88、F13-F24=183..194。
    f_codes = [59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 87, 88,
               183, 184, 185, 186, 187, 188, 189, 190, 191, 192, 193, 194]
    for i, code in enumerate(f_codes, start=1):
        ev[f"f{i}"] = code
    return ev


EVDEV: Dict[str, int] = _build_evdev_table()

# Windows 仮想キーコード → Linux evdev コード（linux_backends.WaylandKeyboard が ydotool 用に使う）。
VK_TO_EVDEV: Dict[int, int] = {VK[name]: EVDEV[name] for name in VK}


def _build_kvk_table() -> Dict[str, int]:
    """キー名（小文字）→ macOS 仮想キーコード（Carbon HIToolbox の kVK_*）を組む。

    darwin_backends.CGEventKeyboard が、handlers から渡る VK 列をこの表で kVK_* に直して
    CGEventCreateKeyboardEvent に渡す。VK / KEYSYM / EVDEV と同じ名前集合をカバーし、
    VK_TO_KVK で「Windows VK → macOS kVK_*」を機械的に作る。

    値は Carbon/HIToolbox/Events.h の kVK_* 定数（公開ヘッダ）。**ANSI 配列**の物理キー
    コード（JIS など他配列でも識別子としてはこの値を使う）。

    注意: cmd/win/super/meta は **kVK_Command（0x37）** にマップ。alt/menu は
    **kVK_Option（0x3A）**。これは Win/Linux と修飾キー名を共通化するための紐付け。
    """
    kvk: Dict[str, int] = {
        # --- 修飾キー（_L 側） ---
        "shift": 0x38,                                  # kVK_Shift
        "ctrl": 0x3B, "control": 0x3B,                  # kVK_Control
        "alt": 0x3A, "menu": 0x3A,                      # kVK_Option
        "win": 0x37, "super": 0x37,                     # kVK_Command（Win 側 win キー相当）
        "cmd": 0x37, "meta": 0x37,                      # kVK_Command（Mac での自然名）
        # --- 特殊キー ---
        "enter": 0x24, "return": 0x24,                  # kVK_Return
        "tab": 0x30,                                    # kVK_Tab
        "esc": 0x35, "escape": 0x35,                    # kVK_Escape
        "space": 0x31, "spacebar": 0x31,                # kVK_Space
        "backspace": 0x33, "bs": 0x33,                  # kVK_Delete（macOS の Delete はバックスペース）
        "delete": 0x75, "del": 0x75,                    # kVK_ForwardDelete（前方削除）
        "insert": 0x72, "ins": 0x72,                    # kVK_Help（macOS には Insert が無く Help と同位置）
        "home": 0x73, "end": 0x77,                      # kVK_Home / kVK_End
        "pageup": 0x74, "pgup": 0x74, "prior": 0x74,    # kVK_PageUp
        "pagedown": 0x79, "pgdn": 0x79, "next": 0x79,   # kVK_PageDown
        "up": 0x7E, "down": 0x7D, "left": 0x7B, "right": 0x7C,
        # macOS にはネイティブの PrintScreen キーがない（shift+cmd+3/4 が機能相当）。
        # シンボルとしては 0x69 (kVK_F13) を割り当てる慣習がある。
        "printscreen": 0x69, "prtsc": 0x69, "prtscn": 0x69,
        "capslock": 0x39,                               # kVK_CapsLock
        "apps": 0x6E, "menukey": 0x6E,                  # kVK_PC_ContextMenu 風（外部キーボード用）
    }
    # 英字 a-z（kVK_ANSI_A..Z は連続していない。Events.h の定義を反映）。
    letters = {
        "a": 0x00, "b": 0x0B, "c": 0x08, "d": 0x02, "e": 0x0E, "f": 0x03,
        "g": 0x05, "h": 0x04, "i": 0x22, "j": 0x26, "k": 0x28, "l": 0x25,
        "m": 0x2E, "n": 0x2D, "o": 0x1F, "p": 0x23, "q": 0x0C, "r": 0x0F,
        "s": 0x01, "t": 0x11, "u": 0x20, "v": 0x09, "w": 0x0D, "x": 0x07,
        "y": 0x10, "z": 0x06,
    }
    kvk.update(letters)
    # 数字 0-9（kVK_ANSI_0..9。0 だけ離れている）。
    digits = {
        "1": 0x12, "2": 0x13, "3": 0x14, "4": 0x15, "5": 0x17,
        "6": 0x16, "7": 0x1A, "8": 0x1C, "9": 0x19, "0": 0x1D,
    }
    kvk.update(digits)
    # ファンクションキー F1-F20（macOS は F20 まで定義あり）。
    # 値は Events.h の kVK_F1..kVK_F20:
    #   F1=0x7A, F2=0x78, F3=0x63, F4=0x76, F5=0x60, F6=0x61, F7=0x62, F8=0x64,
    #   F9=0x65, F10=0x6D, F11=0x67, F12=0x6F, F13=0x69, F14=0x6B, F15=0x71,
    #   F16=0x6A, F17=0x40, F18=0x4F, F19=0x50, F20=0x5A
    f_codes = [0x7A, 0x78, 0x63, 0x76, 0x60, 0x61, 0x62, 0x64, 0x65, 0x6D,
               0x67, 0x6F, 0x69, 0x6B, 0x71, 0x6A, 0x40, 0x4F, 0x50, 0x5A]
    for i, code in enumerate(f_codes, start=1):
        kvk[f"f{i}"] = code
    # F21-F24 は macOS で対応が無い（要求があれば後日割り当て）。
    return kvk


KVK: Dict[str, int] = _build_kvk_table()

# Windows 仮想キーコード → macOS 仮想キーコード（darwin_backends.CGEventKeyboard が使う）。
# F21-F24 のように Mac に存在しないキーは表から欠ける（その VK が来たら KeyError → actionable）。
VK_TO_KVK: Dict[int, int] = {VK[name]: KVK[name] for name in VK if name in KVK}

# メインキーになりうるかに関わらず「修飾キー」として扱える名前の集合。
MODIFIER_NAMES: Set[str] = {
    "shift", "ctrl", "control", "alt", "menu", "win", "super", "cmd", "meta",
}


def _vk_of(name: str) -> int:
    code = VK.get(name)
    if code is None:
        raise ValueError(f"unknown key name: {name!r}")
    return code


def parse_chord(spec: str) -> Tuple[List[int], int]:
    """1 ストローク "ctrl+shift+s" を (修飾キー VK のリスト, メインキー VK) にする。

    末尾の要素をメインキー、それ以外を修飾キーとして扱う。修飾キーだけで
    メインキーが無い指定や、未知のキー名は ValueError。
    """
    if not isinstance(spec, str):
        raise ValueError(f"key spec must be a string, got {type(spec).__name__}")
    parts = [p.strip().lower() for p in spec.split("+")]
    parts = [p for p in parts if p]
    if not parts:
        raise ValueError(f"empty key spec: {spec!r}")

    *mod_names, main_name = parts
    # メインキーが修飾キー名だと「修飾だけ」の指定になる（例: "ctrl+shift"）。
    if main_name in MODIFIER_NAMES and mod_names:
        raise ValueError(
            f"key spec {spec!r} has no non-modifier key (a chord needs one main key)")

    modifiers: List[int] = []
    for name in mod_names:
        if name not in MODIFIER_NAMES:
            raise ValueError(
                f"{name!r} is not a modifier (only ctrl/alt/shift/win may precede '+')")
        modifiers.append(_vk_of(name))
    main = _vk_of(main_name)
    return modifiers, main


def parse_sequence(keys: Union[str, List[str]]) -> List[Tuple[List[int], int]]:
    """複数ストロークをまとめてパースする。

    文字列なら空白で分割して各ストローク、リストなら各要素をストロークとして扱う。
    1 つでも不正があれば ValueError（部分的に送らせない）。
    """
    if isinstance(keys, str):
        specs = keys.split()
    elif isinstance(keys, list):
        if not all(isinstance(k, str) for k in keys):
            raise ValueError("key list must contain only strings")
        specs = keys
    else:
        raise ValueError(f"keys must be a string or list of strings, got {type(keys).__name__}")

    if not specs:
        raise ValueError("no keys to send")
    return [parse_chord(s) for s in specs]


def normalize(keys: Union[str, List[str]]) -> List[str]:
    """履歴・戻り値表示用に、入力をストローク文字列のリストへ正規化する（パースはしない）。"""
    if isinstance(keys, str):
        return keys.split()
    return [str(k) for k in keys]
