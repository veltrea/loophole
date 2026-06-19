"""linux_testlib.py — Linux backend テスト群の共有ヘルパ（フェイク・環境スワップ・集計）。

テストはストリーム別ファイル（test_linux_clipboard.py 等）に分かれているが、ランナーの
フェイクと env スワップ、PASS/FAIL 集計はここに集約して重複を避ける。各テストファイルは
このモジュールを read-only で使う（編集しない）ので、並行開発でも衝突しない。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))

from handlers import ProcessResult  # noqa: E402


class Checker:
    """PASS/FAIL を集計し、最後に done() で要約＋終了コードを出す。"""

    def __init__(self):
        self.failures = 0

    def ok(self, cond, label):
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
        if not cond:
            self.failures += 1

    def eq(self, actual, expected, label):
        if actual == expected:
            print(f"  [PASS] {label}")
        else:
            print(f"  [FAIL] {label}\n         expected={expected!r}\n         actual  ={actual!r}")
            self.failures += 1

    def done(self):
        print(f"\n{'ALL PASS' if self.failures == 0 else 'SOME FAILED'} ({self.failures} failure(s))")
        sys.exit(0 if self.failures == 0 else 1)


class FakeRunner:
    """argv[0]（ツール名）で応答を切り替えるフェイク。未登録ツールは未インストール扱い。"""

    def __init__(self, table=None):
        self.table = table or {}      # tool name -> ProcessResult
        self.calls = []               # (argv, stdin)

    def run(self, argv, cwd, timeout, stdin_text):
        self.calls.append((list(argv), stdin_text))
        res = self.table.get(argv[0])
        return res if res is not None else ProcessResult(-1, b"", b"", started=False)

    def feed_stdin(self, argv, stdin_text, timeout=5.0):
        # clipboard set はこちらを使う（出力 DEVNULL でデーモン化ツールでも固まらない経路）。
        self.calls.append((list(argv), stdin_text))
        res = self.table.get(argv[0])
        return res if res is not None else ProcessResult(-1, b"", b"", started=False)

    def spawn(self, argv, cwd):
        return 0

    def shell_argv(self, command):
        return ["/bin/sh", "-c", command]


class ResponderRunner:
    """argv 全体を見て応答を返すフェイク（gdbus の method / サブコマンドで分岐したいとき）。"""

    def __init__(self, responder):
        self.responder = responder
        self.calls = []

    def run(self, argv, cwd, timeout, stdin_text):
        self.calls.append(list(argv))
        return self.responder(list(argv))

    def feed_stdin(self, argv, stdin_text, timeout=5.0):
        self.calls.append(list(argv))
        return ProcessResult(0, b"", b"")

    def spawn(self, argv, cwd):
        return 0

    def shell_argv(self, command):
        return ["/bin/sh", "-c", command]


def with_env(env, fn):
    """ディスプレイ/コンポジタ関連の env だけ差し替えて fn() を呼び、元に戻す。"""
    keys = ["WAYLAND_DISPLAY", "DISPLAY", "XDG_SESSION_TYPE",
            "SWAYSOCK", "HYPRLAND_INSTANCE_SIGNATURE", "XDG_CURRENT_DESKTOP"]
    saved = {k: os.environ.get(k) for k in keys}
    try:
        for k in keys:
            os.environ.pop(k, None)
        for k, v in env.items():
            os.environ[k] = v
        return fn()
    finally:
        for k in keys:
            if saved[k] is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = saved[k]
