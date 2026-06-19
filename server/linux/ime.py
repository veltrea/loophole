"""ime.py — Linux の IME backend（fcitx5 を gdbus・ibus を CLI で制御）。"""

from __future__ import annotations

from typing import List, Optional

from common_backends import SubprocessRunner
from .parsers import _IME_CONV_ON, desired_open_from, ibus_engine_is_active, parse_fcitx_state


_FCITX5_DEST = "org.fcitx.Fcitx5"
_FCITX5_PATH = "/controller"
_FCITX5_IFACE = "org.fcitx.Fcitx.Controller1"


class LinuxImeController:
    """Linux の IME（fcitx5 優先・ibus フォールバック）の ON/OFF を読み書きする。

    Windows の IMM32（前面ウィンドウへ WM_IME_CONTROL）と違い、Linux の IME は入力メソッド
    フレームワークの D-Bus / CLI で制御する。loophole 本体は stdlib のみで動かす制約があるので
    python-dbus は使わず gdbus（fcitx5）/ ibus CLI に委譲する。X11/Wayland どちらでも効く。

    Windows の変換モード（ひらがな/カタカナ…）は Linux では一様に再現できないため、ここは
    「ON（日本語入力）か OFF（直接入力）か」の 1 軸に集約する——RDP/Wayland 越しに ASCII を
    送る前に IME を切る、という loophole の主目的はこれで満たせる。
    """

    def __init__(self, runner=None):
        self._runner = runner or SubprocessRunner()
        self._backend = "?"  # 未判定。"fcitx5" / "ibus" / None
        # ibus 専用: OFF にする直前にアクティブだった実エンジン名を覚えておき、次の ON で
        # それを復元する。覚えていなければ list-engine の先頭非 xkb にフォールバックする。
        self._ibus_last_engine: Optional[str] = None

    def _detect(self) -> Optional[str]:
        if self._backend != "?":
            return self._backend
        # fcitx5: gdbus で State を引けるか。
        r = self._runner.run(self._fcitx_argv("State"), None, 5.0, None)
        if r.started and r.exit_code == 0:
            self._backend = "fcitx5"
            return self._backend
        # ibus: `ibus engine` が現在エンジンを返すか。
        r = self._runner.run(["ibus", "engine"], None, 5.0, None)
        if r.started and r.exit_code == 0 and (r.stdout or b"").strip():
            self._backend = "ibus"
            return self._backend
        self._backend = None
        return None

    def _fcitx_argv(self, method: str) -> List[str]:
        return ["gdbus", "call", "--session", "--dest", _FCITX5_DEST,
                "--object-path", _FCITX5_PATH, "--method", f"{_FCITX5_IFACE}.{method}"]

    def get(self) -> Optional[tuple]:
        backend = self._detect()
        if backend == "fcitx5":
            r = self._runner.run(self._fcitx_argv("State"), None, 5.0, None)
            if not (r.started and r.exit_code == 0):
                return None
            open_ = parse_fcitx_state((r.stdout or b"").decode("utf-8", "replace"))
            if open_ is None:
                return None
            return (open_, _IME_CONV_ON if open_ else 0)
        if backend == "ibus":
            r = self._runner.run(["ibus", "engine"], None, 5.0, None)
            if not (r.started and r.exit_code == 0):
                return None
            open_ = ibus_engine_is_active((r.stdout or b"").decode("utf-8", "replace"))
            return (open_, _IME_CONV_ON if open_ else 0)
        return None

    def set(self, open: Optional[bool], conversion: Optional[int]) -> bool:
        target = desired_open_from(open, conversion)
        if target is None:
            return False
        backend = self._detect()
        if backend == "fcitx5":
            method = "Activate" if target else "Deactivate"
            r = self._runner.run(self._fcitx_argv(method), None, 5.0, None)
            return bool(r.started and r.exit_code == 0)
        if backend == "ibus":
            return self._set_ibus(target)
        return False

    def _set_ibus(self, target: bool) -> bool:
        """ibus の ON/OFF。OFF 直前の実エンジンを覚え、ON でそれを復元する（fcitx5 に近づける）。

        - OFF: いま実エンジン（非 xkb）が選ばれていれば名前を覚えてから xkb 配列に切り替える。
          既に xkb（＝もう OFF）なら覚えている分はそのままにし、冪等に成功扱いとする。
        - ON: 覚えたエンジンが今も利用可能ならそれへ、無ければ list-engine の先頭非 xkb へ。
          既にその実エンジンが選択中なら切替コマンドを省いて成功扱いにする（堅牢化）。
        """
        current = self._current_ibus_engine()
        if not target:
            # OFF へ。直前の実エンジンを記憶（xkb 以外のときだけ上書きする）。
            if current and not current.startswith("xkb:"):
                self._ibus_last_engine = current
            if current is not None and current.startswith("xkb:"):
                return True  # 既に直接入力。冪等に成功
            return self._switch_ibus_engine("xkb:us::eng")
        # ON へ。覚えたエンジン（利用可能なら）を優先し、無ければ先頭非 xkb。
        engine = self._pick_ibus_on_engine()
        if not engine:
            return False
        if current == engine:
            return True  # 既に目的のエンジン。切替不要で成功
        return self._switch_ibus_engine(engine)

    def _switch_ibus_engine(self, engine: str) -> bool:
        """`ibus engine NAME` を投げ、実際に切り替わったかを読み直しで検証する。

        ibus 1.5.x は環境次第で `ibus engine NAME` が成功時にも非ゼロを返すことがある
        （SetGlobalEngine の DBus 応答が UnknownMethod を返す経路 / 入力フォーカス無し等）。
        実機検証で再現したのを受け、exit code ではなく **ibus engine（読み）で再確認** する。
        """
        r = self._runner.run(["ibus", "engine", engine], None, 5.0, None)
        if not r.started:
            return False
        if r.exit_code == 0:
            return True
        return self._current_ibus_engine() == engine

    def _current_ibus_engine(self) -> Optional[str]:
        """`ibus engine` の現在エンジン名。読めなければ None（呼び出し側で記憶を更新しない）。"""
        r = self._runner.run(["ibus", "engine"], None, 5.0, None)
        if not (r.started and r.exit_code == 0):
            return None
        name = (r.stdout or b"").decode("utf-8", "replace").strip()
        return name or None

    def _pick_ibus_on_engine(self) -> Optional[str]:
        """ON にするエンジンを選ぶ。覚えたエンジンが今も list に在ればそれ、無ければ先頭非 xkb。"""
        available = self._ibus_ime_engines()
        if self._ibus_last_engine and self._ibus_last_engine in available:
            return self._ibus_last_engine
        return available[0] if available else None

    def _ibus_ime_engines(self) -> List[str]:
        """list-engine から非 xkb の実エンジン名を順序保持で返す（先頭が既定の ON 先）。"""
        r = self._runner.run(["ibus", "list-engine", "--name-only"], None, 5.0, None)
        if not (r.started and r.exit_code == 0):
            return []
        engines: List[str] = []
        for line in (r.stdout or b"").decode("utf-8", "replace").splitlines():
            name = line.strip()
            if name and not name.startswith("xkb:"):
                engines.append(name)
        return engines


def build_ime(runner):
    return LinuxImeController(runner)
