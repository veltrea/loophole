"""test_darwin_ime.py — Mac の TIS / IME backend のテスト。

is_japanese_id（純粋ロジック）と TISImeController（_lib をフェイクで差し替え）を検証する。
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))
sys.path.insert(0, _HERE)

from darwin.tislib import DEFAULT_ABC_ID, is_japanese_id  # noqa: E402
from linux_testlib import Checker  # noqa: E402

c = Checker()


# --- is_japanese_id (pure logic) ----------------------------------------
print("is_japanese_id():")
positive = [
    "com.apple.inputmethod.Kotoeri.Japanese",
    "com.apple.inputmethod.Kotoeri.Japanese.Hiragana",
    "com.apple.inputmethod.Kotoeri.Japanese.Katakana",
    "com.apple.inputmethod.JapaneseIM",
    "com.google.inputmethod.Japanese.base",
    "com.google.inputmethod.Japanese.Roman",
    "com.justsystem.inputmethod.atok33.Japanese",
    "com.example.JapaneseIME.hiragana",          # 緩いキーワード一致
    "com.unknown.vendor.something-katakana",     # 同上
]
for sid in positive:
    c.ok(is_japanese_id(sid), f"japanese: {sid!r}")

negative = [
    "com.apple.keylayout.ABC",
    "com.apple.keylayout.US",
    "com.apple.keylayout.Dvorak",
    "com.apple.inputmethod.SCIM.ITABC",          # Simplified Chinese
    "com.apple.inputmethod.Korean.2SetKorean",
    "com.apple.inputmethod.TYIM.Cangjie",        # Traditional Chinese
    "",
]
for sid in negative:
    c.ok(not is_japanese_id(sid), f"non-japanese: {sid!r}")


# --- TISImeController（フェイク _lib） -----------------------------------
class _FakeLib:
    """tislib._TISLib の最小代用。状態を Python オブジェクトで持つ。"""

    def __init__(self, current="com.apple.keylayout.ABC", sources=None):
        self.current = current
        self.sources = list(sources or [
            "com.apple.keylayout.ABC",
            "com.apple.inputmethod.Kotoeri.Japanese.Hiragana",
            "com.apple.inputmethod.Kotoeri.Japanese.Katakana",
        ])
        self.calls = []

    def current_source_id(self):
        self.calls.append(("current",))
        return self.current

    def all_source_ids(self):
        self.calls.append(("all",))
        return list(self.sources)

    def select_by_id(self, target):
        self.calls.append(("select", target))
        if target in self.sources:
            self.current = target
            return True
        return False


def _make_ime(lib):
    from darwin.ime import TISImeController
    ime = TISImeController.__new__(TISImeController)
    ime._lib = lib
    ime._last_japanese_id = None
    return ime


print("TISImeController.get():")
lib = _FakeLib(current="com.apple.keylayout.ABC")
ime = _make_ime(lib)
c.eq(ime.get(), (False, 0), "ABC layout → (False, 0)")
c.eq(ime._last_japanese_id, None, "get(ABC) does not learn a japanese id")

lib = _FakeLib(current="com.apple.inputmethod.Kotoeri.Japanese.Hiragana")
ime = _make_ime(lib)
c.eq(ime.get(), (True, 0), "Kotoeri Hiragana → (True, 0)")
c.eq(ime._last_japanese_id, "com.apple.inputmethod.Kotoeri.Japanese.Hiragana",
     "get(japanese) learns the current id for later restore")


print("TISImeController.set(open=False):")
lib = _FakeLib(current="com.apple.inputmethod.Kotoeri.Japanese.Hiragana")
ime = _make_ime(lib)
ok = ime.set(open=False, conversion=None)
c.ok(ok, "set(False) returns True")
c.eq(lib.current, DEFAULT_ABC_ID, "current became ABC")
c.eq(ime._last_japanese_id, "com.apple.inputmethod.Kotoeri.Japanese.Hiragana",
     "set(False) memorizes the previous japanese id")


print("TISImeController.set(open=True):")
# 直近の日本語 ID 記憶があればそれを選ぶ
lib = _FakeLib(current=DEFAULT_ABC_ID)
ime = _make_ime(lib)
ime._last_japanese_id = "com.apple.inputmethod.Kotoeri.Japanese.Katakana"
ok = ime.set(open=True, conversion=None)
c.ok(ok, "set(True) returns True when remembered id exists")
c.eq(lib.current, "com.apple.inputmethod.Kotoeri.Japanese.Katakana",
     "set(True) restores the remembered japanese id")

# 記憶なし → all_source_ids から最初の日本語を選ぶ
lib = _FakeLib(current=DEFAULT_ABC_ID)
ime = _make_ime(lib)
ok = ime.set(open=True, conversion=None)
c.ok(ok, "set(True) without memory falls back to first japanese source")
c.ok(lib.current.startswith("com.apple.inputmethod.Kotoeri.Japanese"),
     "fell back to first japanese in source list")

# 日本語ソースが 1 つも無い → False
lib = _FakeLib(current=DEFAULT_ABC_ID, sources=[DEFAULT_ABC_ID, "com.apple.keylayout.US"])
ime = _make_ime(lib)
c.ok(not ime.set(open=True, conversion=None),
     "set(True) returns False when no japanese source exists")


print("TISImeController.set(open=None):")
lib = _FakeLib(current=DEFAULT_ABC_ID)
ime = _make_ime(lib)
ok = ime.set(open=None, conversion=42)
c.ok(ok, "set(open=None) is a no-op that returns True")
c.eq(len(lib.calls), 0, "set(open=None) doesn't touch TIS")
c.eq(lib.current, DEFAULT_ABC_ID, "current unchanged")


# --- build_ime ファクトリ -------------------------------------------------
# 実 _lib を作ろうとすると HIToolbox をロードするので Mac でのみ呼べる。
# ファクトリの構造だけ最低限確認する（return が TISImeController 型）。
print("build_ime():")
if sys.platform == "darwin":
    from darwin.ime import build_ime, TISImeController
    ime = build_ime()
    c.ok(isinstance(ime, TISImeController), "factory returns TISImeController on darwin")
else:
    print("  [SKIP] build_ime() requires darwin to load HIToolbox")

c.done()
