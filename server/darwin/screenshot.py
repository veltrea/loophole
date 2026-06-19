"""screenshot.py — macOS のスクリーンショット backend。

`screencapture -x -t png -` で PNG を stdout に吐かせて受け取る（Linux の Grim と同じ流儀）。

- `-x` : シャッター音を鳴らさない
- `-t png` : PNG 形式
- `-` : 出力先を stdout にする（一時ファイル不要）

マルチディスプレイは既定で結合された 1 枚の PNG にはならず、`-D <index>` を指定しないと
**メインディスプレイのみ**が撮られる。複数 display を 1 枚にしたいケースは将来 M12 で
明示パラメータを足す（現状はメイン画面のみで十分。Windows / Linux も「ルートウィンドウ
1 枚」を返す挙動と揃う）。

screencapture は Screen Recording 権限（TCC）未許可だと **空の PNG 相当**を吐く（黒一色）。
ここでは「成功した PNG バイト列」とだけ約束し、TCC 警告は `hello` の tcc ブロックで別途
表面化する（MAC-TCC-1）。
"""

from __future__ import annotations

from typing import Optional

from common_backends import SubprocessRunner


class ScreencaptureScreenshotter:
    """`screencapture -x -t png -` の stdout を返すだけのシンプル backend。

    Linux の GrimScreenshotter と同じく:
    - started=False → ツール不在の actionable エラー
    - exit!=0 → stderr 付きの actionable エラー
    - stdout 空 → 空 PNG エラー
    """

    def __init__(self, runner: Optional[object] = None):
        self._runner = runner or SubprocessRunner()

    def capture(self) -> bytes:
        r = self._runner.run(["screencapture", "-x", "-t", "png", "-"], None, 30.0, None)
        if not r.started:
            raise RuntimeError(
                "screenshot failed: screencapture not found "
                "(this should ship with macOS — check PATH)")
        if r.exit_code != 0:
            err = (r.stderr or b"").decode("utf-8", "replace").strip()
            raise RuntimeError(
                f"screenshot failed: screencapture exit={r.exit_code} ({err}). "
                "Grant Screen Recording permission in System Settings > Privacy & Security.")
        data = r.stdout or b""
        if not data:
            raise RuntimeError(
                "screenshot failed: screencapture produced no output. "
                "This typically means Screen Recording permission is denied "
                "(System Settings > Privacy & Security > Screen Recording).")
        # 軽い PNG マジック検査（先頭 8 バイト）。screencapture は通常 PNG を吐くが、
        # 万が一壊れた出力に倒れたとき早期に気づけるように。
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError(
                "screenshot failed: screencapture stdout is not a PNG "
                f"(first bytes: {data[:8]!r})")
        return data


def build_screenshotter():
    """darwin/__init__.py から呼ばれるファクトリ。"""
    return ScreencaptureScreenshotter()
