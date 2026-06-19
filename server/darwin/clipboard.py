"""clipboard.py — macOS のクリップボード backend。

pbcopy / pbpaste（macOS に最初から入っている標準 CLI）に shell-out する。Linux の
ShellClipboard と同じ思想:
- pbcopy は stdin を読んでクリップボードに置く。pbpaste は stdout に現在のクリップボードを
  出す。どちらも UTF-8 で扱えるが、**LC_ALL を en_US.UTF-8 に明示**しないと、ターミナルで
  ロケールが空に近いとき（launchd 起動の agent 等）に **ASCII 範囲外の文字が "?" に化ける**
  ことがある（古典的な罠）。LC_ALL を子プロセスに渡す経路で確実に塞ぐ。
- 出力は生バイトで受け取って UTF-8 デコード（誤バイトは置換）。Windows backend が
  base64 経由でやっているような CP932 ダメ文字回避は macOS では不要（標準が UTF-8）。
- pbcopy はパイプを閉じれば即終了する（X11 の xclip のような「セレクション所有のため
  デーモン化」が無い）。run() でよく、feed_stdin の DEVNULL 回避は要らない。
"""

from __future__ import annotations

import os
from typing import Optional

from common_backends import SubprocessRunner


# pbcopy/pbpaste が UTF-8 で動作することを保証する環境変数の集合。
# LANG だけ立っていて LC_ALL が空のロケール（launchd 経由の起動など）で安全側に倒す。
_UTF8_ENV = {
    "LC_ALL": "en_US.UTF-8",
    "LANG": "en_US.UTF-8",
}


class _Utf8Runner:
    """与えられた runner をラップし、子プロセス環境に LC_ALL=en_US.UTF-8 を強制する。

    SubprocessRunner.run / feed_stdin は os.environ を引き継ぐ。クリップボード周りで
    ロケールの欠落が原因の文字化けを起こさないよう、ここで一段挟む。フェイク runner
    （テストの FakeRunner）は環境を見ないので、ラップしても無害（透過する）。
    """

    def __init__(self, inner):
        self._inner = inner

    def _enter(self):
        saved = {k: os.environ.get(k) for k in _UTF8_ENV}
        for k, v in _UTF8_ENV.items():
            os.environ[k] = v
        return saved

    def _exit(self, saved):
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def run(self, argv, cwd, timeout, stdin_text):
        saved = self._enter()
        try:
            return self._inner.run(argv, cwd, timeout, stdin_text)
        finally:
            self._exit(saved)

    def feed_stdin(self, argv, stdin_text, timeout=5.0):
        saved = self._enter()
        try:
            return self._inner.feed_stdin(argv, stdin_text, timeout)
        finally:
            self._exit(saved)


class PbcopyClipboard:
    """pbcopy / pbpaste 経由のクリップボード backend。

    - get(): `pbpaste` の stdout を UTF-8 でデコード。
    - set(): `pbcopy` に stdin で流す。pbcopy は出力を持たないので run() で良い
      （xclip と違い fork デーモン化しない）。
    """

    def __init__(self, runner: Optional[object] = None):
        self._runner = _Utf8Runner(runner or SubprocessRunner())

    def get(self) -> str:
        r = self._runner.run(["pbpaste"], None, 5.0, None)
        if not r.started:
            raise RuntimeError("clipboard get failed: pbpaste not found "
                               "(this should ship with macOS)")
        if r.exit_code != 0:
            err = (r.stderr or b"").decode("utf-8", "replace").strip()
            raise RuntimeError(f"clipboard get failed: pbpaste exit={r.exit_code} ({err})")
        return (r.stdout or b"").decode("utf-8", errors="replace")

    def set(self, text: str) -> None:
        r = self._runner.run(["pbcopy"], None, 5.0, text)
        if not r.started:
            raise RuntimeError("clipboard set failed: pbcopy not found "
                               "(this should ship with macOS)")
        if r.exit_code != 0:
            err = (r.stderr or b"").decode("utf-8", "replace").strip()
            raise RuntimeError(f"clipboard set failed: pbcopy exit={r.exit_code} ({err})")


def build_clipboard(runner=None):
    """darwin/__init__.py から呼ばれるファクトリ。"""
    return PbcopyClipboard(runner=runner)
