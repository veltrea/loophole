"""tislib.py — Text Input Sources（TIS）の ctypes 配線。

macOS の IME（日本語/中国語/韓国語入力ソース）を切り替える唯一の公開 API。
HIToolbox（Carbon サブフレームワーク）に入っている。

提供する操作:
  - current_source_id() → 現在の入力ソース ID（"com.apple.keylayout.ABC" 等）
  - all_source_ids()    → 有効な全入力ソース ID のリスト
  - select_by_id(id)    → 指定 ID の入力ソースを選択
  - is_japanese_id(id)  → ID が日本語入力ソースか（純粋ロジック・テスト可）

参考: developer.apple.com/documentation/coreservices/tis_input_sources (TextInputSources.h)
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import c_int, c_uint8, c_uint32, c_void_p
from ctypes import util as _ctutil
from typing import List, Optional


# CFString エンコーディング（kCFStringEncodingUTF8）。
_CFSTR_UTF8 = 0x08000100

# OSStatus（CGError 同様の int32）。0 が noErr。


class _TISLib:
    """HIToolbox（TIS）+ CoreFoundation の必要関数を束ねる singleton。"""

    def __init__(self):
        if sys.platform != "darwin":
            raise RuntimeError("TIS is only available on macOS (darwin)")

        cf_path = _ctutil.find_library("CoreFoundation")
        if not cf_path:
            raise RuntimeError("CoreFoundation framework not found")
        self.cf = ctypes.CDLL(cf_path)

        # HIToolbox は Carbon の一部。framework フルパスでロードする
        # （find_library が "HIToolbox" を解決できないことがある）。
        hi_paths = [
            _ctutil.find_library("HIToolbox"),
            "/System/Library/Frameworks/Carbon.framework/Frameworks/HIToolbox.framework/HIToolbox",
        ]
        hi = None
        for path in hi_paths:
            if not path:
                continue
            try:
                hi = ctypes.CDLL(path)
                break
            except OSError:
                continue
        if hi is None:
            raise RuntimeError("HIToolbox framework not found (Carbon subframework)")
        self.hi = hi

        # ---- CoreFoundation: CFString と CFArray の最小ヘルパ -----------------
        self.cf.CFStringCreateWithCString.restype = c_void_p
        self.cf.CFStringCreateWithCString.argtypes = [c_void_p, ctypes.c_char_p, c_uint32]

        self.cf.CFStringGetLength.restype = ctypes.c_long
        self.cf.CFStringGetLength.argtypes = [c_void_p]

        self.cf.CFStringGetCString.restype = c_int
        self.cf.CFStringGetCString.argtypes = [c_void_p, ctypes.c_char_p, ctypes.c_long, c_uint32]

        self.cf.CFStringGetMaximumSizeForEncoding.restype = ctypes.c_long
        self.cf.CFStringGetMaximumSizeForEncoding.argtypes = [ctypes.c_long, c_uint32]

        self.cf.CFArrayGetCount.restype = ctypes.c_long
        self.cf.CFArrayGetCount.argtypes = [c_void_p]

        self.cf.CFArrayGetValueAtIndex.restype = c_void_p
        self.cf.CFArrayGetValueAtIndex.argtypes = [c_void_p, ctypes.c_long]

        self.cf.CFRelease.restype = None
        self.cf.CFRelease.argtypes = [c_void_p]

        self.cf.CFDictionaryCreate.restype = c_void_p
        self.cf.CFDictionaryCreate.argtypes = [
            c_void_p,                           # allocator
            ctypes.POINTER(c_void_p),           # keys
            ctypes.POINTER(c_void_p),           # values
            ctypes.c_long,                      # numValues
            c_void_p,                           # keyCallBacks
            c_void_p,                           # valueCallBacks
        ]

        # CFBoolean の grobal シンボルを引く（kCFBooleanTrue / kCFBooleanFalse）
        self._cf_true = ctypes.c_void_p.in_dll(self.cf, "kCFBooleanTrue")
        self._cf_false = ctypes.c_void_p.in_dll(self.cf, "kCFBooleanFalse")
        self._cf_type_dict_keys = ctypes.c_void_p.in_dll(self.cf, "kCFTypeDictionaryKeyCallBacks")
        self._cf_type_dict_vals = ctypes.c_void_p.in_dll(self.cf, "kCFTypeDictionaryValueCallBacks")

        # ---- HIToolbox: TIS API ----------------------------------------------
        self.hi.TISCopyCurrentKeyboardInputSource.restype = c_void_p
        self.hi.TISCopyCurrentKeyboardInputSource.argtypes = []

        self.hi.TISCreateInputSourceList.restype = c_void_p
        self.hi.TISCreateInputSourceList.argtypes = [c_void_p, c_uint8]

        self.hi.TISGetInputSourceProperty.restype = c_void_p
        self.hi.TISGetInputSourceProperty.argtypes = [c_void_p, c_void_p]

        self.hi.TISSelectInputSource.restype = c_int
        self.hi.TISSelectInputSource.argtypes = [c_void_p]

        # 既知の property key（CFString グローバル）。HIToolbox の TextInputSources.h より:
        #   kTISPropertyInputSourceID         "TISPropertyInputSourceID"
        #   kTISPropertyInputSourceIsSelectCapable
        #   kTISPropertyInputSourceCategory
        # これらは framework グローバル変数なので in_dll で引く。
        self._kProp_InputSourceID = ctypes.c_void_p.in_dll(
            self.hi, "kTISPropertyInputSourceID")
        self._kProp_IsSelectCapable = ctypes.c_void_p.in_dll(
            self.hi, "kTISPropertyInputSourceIsSelectCapable")

    # ---- 高レベル ヘルパ -------------------------------------------------------

    def cfstring_to_str(self, cfstr: c_void_p) -> Optional[str]:
        """CFString → Python str（UTF-8 デコード）。NULL は None。"""
        if not cfstr:
            return None
        length = self.cf.CFStringGetLength(cfstr)
        max_size = self.cf.CFStringGetMaximumSizeForEncoding(length, _CFSTR_UTF8) + 1
        buf = ctypes.create_string_buffer(int(max_size))
        if not self.cf.CFStringGetCString(cfstr, buf, max_size, _CFSTR_UTF8):
            return None
        return buf.value.decode("utf-8", errors="replace")

    def str_to_cfstring(self, s: str) -> c_void_p:
        """Python str → CFString。所有権あり（呼び出し側で CFRelease）。"""
        return self.cf.CFStringCreateWithCString(None, s.encode("utf-8"), _CFSTR_UTF8)

    def make_filter_dict(self, select_capable: bool) -> c_void_p:
        """{kTISPropertyInputSourceIsSelectCapable: kCFBooleanTrue} の CFDictionary を作る。

        TISCreateInputSourceList のフィルタとして使う。所有権あり（呼び出し側で CFRelease）。
        """
        keys = (c_void_p * 1)(self._kProp_IsSelectCapable.value)
        vals = (c_void_p * 1)(self._cf_true.value if select_capable else self._cf_false.value)
        return self.cf.CFDictionaryCreate(
            None, keys, vals, 1,
            ctypes.byref(self._cf_type_dict_keys),
            ctypes.byref(self._cf_type_dict_vals),
        )

    def source_id(self, source: c_void_p) -> Optional[str]:
        """TISInputSourceRef から kTISPropertyInputSourceID 文字列を取り出す。"""
        cfstr = self.hi.TISGetInputSourceProperty(source, self._kProp_InputSourceID)
        return self.cfstring_to_str(cfstr)

    def current_source_id(self) -> Optional[str]:
        cur = self.hi.TISCopyCurrentKeyboardInputSource()
        if not cur:
            return None
        try:
            return self.source_id(cur)
        finally:
            self.cf.CFRelease(cur)

    def all_source_ids(self) -> List[str]:
        """選択可能（select capable）な全入力ソースの ID をリストで返す。"""
        filter_dict = self.make_filter_dict(select_capable=True)
        try:
            arr = self.hi.TISCreateInputSourceList(filter_dict, 0)
        finally:
            self.cf.CFRelease(filter_dict)
        if not arr:
            return []
        ids: List[str] = []
        try:
            n = self.cf.CFArrayGetCount(arr)
            for i in range(n):
                src = self.cf.CFArrayGetValueAtIndex(arr, i)
                if not src:
                    continue
                sid = self.source_id(src)
                if sid:
                    ids.append(sid)
        finally:
            self.cf.CFRelease(arr)
        return ids

    def select_by_id(self, target_id: str) -> bool:
        """ID 一致の入力ソースを選択する。見つからなければ False、選択失敗も False。"""
        filter_dict = self.make_filter_dict(select_capable=True)
        try:
            arr = self.hi.TISCreateInputSourceList(filter_dict, 0)
        finally:
            self.cf.CFRelease(filter_dict)
        if not arr:
            return False
        try:
            n = self.cf.CFArrayGetCount(arr)
            for i in range(n):
                src = self.cf.CFArrayGetValueAtIndex(arr, i)
                if not src:
                    continue
                sid = self.source_id(src)
                if sid == target_id:
                    status = self.hi.TISSelectInputSource(src)
                    return status == 0
            return False
        finally:
            self.cf.CFRelease(arr)


_LIB: Optional[_TISLib] = None


def _lib() -> _TISLib:
    global _LIB
    if _LIB is None:
        _LIB = _TISLib()
    return _LIB


# 純粋ロジック（テスト可）------------------------------------------------------


def is_japanese_id(source_id: str) -> bool:
    """入力ソース ID が日本語入力エンジンか判定する。

    主要ベンダ:
      - Apple Kotoeri: com.apple.inputmethod.Kotoeri.*  /  Japanese / Hiragana / Katakana 等
      - Google 日本語入力: com.google.inputmethod.Japanese.*
      - ATOK: com.justsystem.inputmethod.atok* / com.justsystems.inputmethod.atok*
      - 旧 Apple JapaneseIM: com.apple.inputmethod.JapaneseIM
    判定:
      1. 既知の prefix（速い）
      2. それ以外でも "Japan" / "Japanese" / "Hiragana" / "Kotoeri" を ID に含めば日本語扱い

    ABC キーボードレイアウト等は False（com.apple.keylayout.* は IME ではない）。
    """
    if not source_id:
        return False
    sid = source_id
    # ABC や RomanLocale 等のキーボードレイアウトは除外
    if sid.startswith("com.apple.keylayout."):
        return False
    known_prefixes = (
        "com.apple.inputmethod.Kotoeri",
        "com.apple.inputmethod.Japanese",
        "com.apple.inputmethod.JapaneseIM",
        "com.google.inputmethod.Japanese",
        "com.justsystem.inputmethod.atok",
        "com.justsystems.inputmethod.atok",
    )
    if sid.startswith(known_prefixes):
        return True
    # 緩めの文字列マッチ（未知ベンダの拾い）
    lower = sid.lower()
    keywords = ("japanese", "kotoeri", "hiragana", "katakana", "atok")
    return any(k in lower for k in keywords)


# 既定で使う英字キーレイアウト（OFF 時の選択先）。
DEFAULT_ABC_ID = "com.apple.keylayout.ABC"
