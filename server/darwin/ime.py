"""ime.py — macOS の IME backend（TIS 経由）。

handlers.ImeController プロトコルを満たす TISImeController。

設計:
- get(): 現在の入力ソース ID を見て、日本語エンジンなら open=True、それ以外（ABC など）
  なら open=False を返す。conversion は macOS の TIS で表現できないため 0 固定（Linux 側
  と同じ「OS が ON/OFF だけ持つ」モデル）。
- set(open=True): キャッシュした「直前まで使っていた日本語入力ソース」に切り替え。
  記憶が無ければ all_source_ids() から最初の日本語ソースを選ぶ。1 つも無ければ False。
- set(open=False): com.apple.keylayout.ABC に切り替え。
- set(conversion=...): TIS は対応しない → 無視（True を返す軸として扱わない）。

注意（MAC-IME-1）: TIS は「フォーカス先の入力ソース」を切り替える。非フォーカス時の切替は
すぐ反映されない/別ウィンドウで戻る等の挙動がある。これはユーザの環境依存。
"""

from __future__ import annotations

from typing import Optional, Tuple

from .tislib import DEFAULT_ABC_ID, _lib, is_japanese_id


class TISImeController:
    """TIS 経由で IME の ON/OFF を読み書き。"""

    def __init__(self):
        self._lib = _lib()
        # 直近の「日本語 ON だったときの ID」を覚えておき、ON 復帰で再選択する。
        self._last_japanese_id: Optional[str] = None

    def get(self) -> Optional[Tuple[bool, int]]:
        sid = self._lib.current_source_id()
        if sid is None:
            return None
        if is_japanese_id(sid):
            # 学習: 次に OFF→ON する時のため記憶する
            self._last_japanese_id = sid
            return (True, 0)
        return (False, 0)

    def set(self, open: Optional[bool], conversion: Optional[int]) -> bool:
        # conversion は macOS では表現できないので無視（軸として None 同様扱い）。
        if open is None:
            # open を変えない指定。conversion も実質無視するので何もせず True を返す。
            # Linux 側と挙動を揃える。
            return True

        if open:
            # ON にする: 記憶があればそれ、無ければ全列挙から最初の日本語ソースを選ぶ
            target = self._last_japanese_id or self._first_japanese_id()
            if target is None:
                return False
            ok = self._lib.select_by_id(target)
            if ok:
                self._last_japanese_id = target
            return ok

        # OFF にする: ABC キーボードに切り替え。直前の日本語 ID を覚えておく（次の ON で復元）。
        cur = self._lib.current_source_id()
        if cur and is_japanese_id(cur):
            self._last_japanese_id = cur
        return self._lib.select_by_id(DEFAULT_ABC_ID)

    def _first_japanese_id(self) -> Optional[str]:
        for sid in self._lib.all_source_ids():
            if is_japanese_id(sid):
                return sid
        return None


def build_ime(runner=None):
    """darwin/__init__.py から呼ばれるファクトリ（runner は不要だが API を揃える）。"""
    return TISImeController()
