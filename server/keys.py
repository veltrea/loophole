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
