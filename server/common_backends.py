"""common_backends.py — OS 非依存の backend 実装（Windows でも Linux でも同じコードで動く）。

handlers.py のインターフェースのうち、プラットフォーム固有 API を一切使わずに実装できる
もの（プロセス起動・ファイル I/O・セッション情報）をここに集める。win_backends.py と
linux_backends.py の双方がここから import して使う（win↔linux の相互依存を作らない）。

  - SubprocessRunner  : subprocess（コマンドラインは UTF-8→そのまま、出力は生バイト捕捉）
  - LocalFileSystem   : 普通の open（バイナリ）
  - HostEnvironment   : platform / user / セッション・対話可否（Windows はセッション 0 判定、
                        Linux は X11/Wayland のディスプレイ到達可否）
  - UnsupportedBackend: そのプラットフォームに実装が無い能力の番人（呼ばれたら明示エラー）

ctypes.windll は Windows でのみ、X11 系の判定は env 参照のみなので、どのプラットフォームでも
安全に import できる。
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any, Dict, List, Optional

from handlers import ProcessResult

IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")
IS_DARWIN = sys.platform == "darwin"

# 子プロセスにコンソール窓を出さないフラグ（Windows のみ。他 OS では 0）
_CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0


class SubprocessRunner:
    def run(self, argv: List[str], cwd: Optional[str], timeout: Optional[float],
            stdin_text: Optional[str]) -> ProcessResult:
        try:
            proc = subprocess.Popen(
                argv,
                cwd=cwd,
                stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=_CREATE_NO_WINDOW,
            )
        except (FileNotFoundError, OSError):
            return ProcessResult(-1, b"", b"", started=False)
        try:
            stdin_bytes = stdin_text.encode("utf-8") if stdin_text is not None else None
            out, err = proc.communicate(input=stdin_bytes, timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            return ProcessResult(proc.returncode or 124, out or b"", err or b"")
        return ProcessResult(proc.returncode, out or b"", err or b"")

    def spawn(self, argv: List[str], cwd: Optional[str]) -> int:
        # GUI/常駐を起動して即返す。エージェントが対話セッションにいるので画面に出る。
        proc = subprocess.Popen(argv, cwd=cwd, close_fds=True)
        return proc.pid

    def feed_stdin(self, argv: List[str], stdin_text: str,
                   timeout: Optional[float] = 5.0) -> ProcessResult:
        """stdin にテキストを流し、出力は捨ててプロセス終了を待つ（出力捕捉はしない）。

        xclip / wl-copy のような「セレクション保持のため fork してデーモン化する」ツール用。
        run() のように stdout/stderr をパイプで捕捉すると、デーモン化した子が継承したパイプ
        を開いたままにするため communicate() が EOF を待って固まる（xclip の定番デッドロック）。
        ここでは stdout/stderr を DEVNULL に倒すので、親プロセスの終了だけを待てて固まらない。
        """
        try:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_CREATE_NO_WINDOW,
            )
        except (FileNotFoundError, OSError):
            return ProcessResult(-1, b"", b"", started=False)
        try:
            proc.communicate(input=(stdin_text or "").encode("utf-8"), timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return ProcessResult(proc.returncode or 124, b"", b"")
        return ProcessResult(proc.returncode, b"", b"")

    def shell_argv(self, command: str) -> List[str]:
        """シェルワンライナーを実行するための argv を、ホスト OS のシェルで組む。

        Windows: cmd.exe /S /C（/S /C は最外の引用を 1 組だけ外す Windows の定石）。
        POSIX  : /bin/sh -c。
        どのシェルで包むかは「実際にコマンドを起動する実行 backend」の責務なので、
        handlers 側はこれを呼ぶだけにして OS 分岐を持たない。
        """
        if IS_WINDOWS:
            return ["cmd.exe", "/S", "/C", command]
        return ["/bin/sh", "-c", command]


class LocalFileSystem:
    def read_bytes(self, path: str) -> bytes:
        with open(path, "rb") as f:
            return f.read()

    def write_bytes(self, path: str, data: bytes) -> None:
        with open(path, "wb") as f:
            f.write(data)

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def walk(self, root: str):
        # os.walk はトップダウン。呼び側が dirnames を破壊的に削れば枝刈りできる。
        return os.walk(root)

    def stat(self, path: str):
        st = os.stat(path)
        return st.st_size, st.st_mtime


def linux_display_server() -> Optional[str]:
    """Linux のディスプレイサーバー種別を env から判定する（純粋ロジック・テスト可能）。

    "wayland" / "x11" / None（GUI セッションに紐づいていない＝SSH の素のシェル等）。
    WAYLAND_DISPLAY が最優先（Wayland では XWayland 互換で DISPLAY も立つため）。
    XDG_SESSION_TYPE はヒントとして見るが、DISPLAY/WAYLAND_DISPLAY の実在を優先する。
    """
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    session = (os.environ.get("XDG_SESSION_TYPE") or "").strip().lower()
    if session in ("wayland", "x11"):
        return session
    return None


def _linux_session_info() -> Dict[str, Any]:
    """Linux で「GUI に届くか（= Windows のセッション 0 判定の等価物）」を返す。

    Windows のセッション分離は Linux に無いが、「SSH で入っただけでは GUI を操作できない」
    現実はディスプレイサーバー到達性として残る。display_server が取れていれば GUI 到達可能
    （interactive 相当）とみなす。
    """
    server = linux_display_server()
    display = os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")
    return {
        "display_server": server,
        "display": display,
        "interactive": server is not None,
    }


def _darwin_console_user() -> Optional[str]:
    """現在のコンソール（対面ログイン中）ユーザ名を返す（SCDynamicStore より軽い手段）。

    `stat -f '%Su' /dev/console` は POSIX で常に入っていて、対面ログイン中のユーザを返す。
    誰もログインしていない（ログインウィンドウ）なら "root" や "_" 系の名前になる。
    取れなければ None を返す（呼び元で `interactive` を判定する材料に使う）。
    """
    try:
        proc = subprocess.run(
            ["/usr/bin/stat", "-f", "%Su", "/dev/console"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=2.0, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    name = (proc.stdout or b"").decode("utf-8", "replace").strip()
    return name or None


def _darwin_tcc_probe() -> Dict[str, Optional[bool]]:
    """Mac の TCC（プライバシー許可）の現状を素朴に判定する。

    - accessibility: AXIsProcessTrusted() を ApplicationServices 経由で見る（prompt なし）。
    - screen_recording / automation: agent 起動時には判定しない（実呼び出し時の失敗で
      自然に表面化するため）。ここでは None を返し、`hello` の段階では false 確定を避ける。

    ApplicationServices が import できない/ld できない環境（極稀な配布）でも壊さない。
    """
    out: Dict[str, Optional[bool]] = {
        "accessibility": None,
        "screen_recording": None,
        "automation": None,
    }
    try:
        import ctypes
        from ctypes import util as _ctutil
        path = _ctutil.find_library("ApplicationServices")
        if not path:
            return out
        lib = ctypes.CDLL(path)
        lib.AXIsProcessTrusted.restype = ctypes.c_int
        lib.AXIsProcessTrusted.argtypes = []
        out["accessibility"] = bool(lib.AXIsProcessTrusted())
    except Exception:  # pragma: no cover - ld 不可など環境依存
        pass
    return out


def _darwin_session_info() -> Dict[str, Any]:
    """Mac で「Aqua セッションに届くか」を返す（Windows のセッション 0 判定の等価物）。

    対面ログインしているコンソールユーザと自プロセスのユーザが一致していれば、CGEvent /
    screencapture / osascript は基本的に通る（個別の TCC 許可は別途必要）。一致しなければ
    LaunchAgent でなく SSH ターミナルから直起動された agent が WindowServer に届かない可能性が
    高い（MAC-SSH-1 の罠）。

    返値:
      - console_user: /dev/console のオーナ（対面ログイン中のユーザ名）or None
      - interactive : console_user と自プロセスのユーザが一致していれば True
      - tcc         : {"accessibility": bool|None, "screen_recording": None, "automation": None}
    """
    console_user = _darwin_console_user()
    self_user = os.environ.get("USER") or os.environ.get("LOGNAME")
    interactive: Optional[bool]
    if console_user is None or self_user is None:
        interactive = None
    else:
        interactive = (console_user == self_user)
    return {
        "console_user": console_user,
        "interactive": interactive,
        "tcc": _darwin_tcc_probe(),
    }


def _windows_session_info() -> Dict[str, Any]:
    """現在のプロセスがどのセッションにいて、対話可能かを返す（Windows のみ呼ぶ）。"""
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        pid = kernel32.GetCurrentProcessId()
        session_id = ctypes.c_ulong()
        kernel32.ProcessIdToSessionId(pid, ctypes.byref(session_id))
        sid = int(session_id.value)
        # セッション 0 = サービス/非対話。1 以上 = 対話デスクトップ。
        return {"session_id": sid, "interactive": sid != 0}
    except Exception as exc:  # pragma: no cover - 実機 Windows でのみ通る
        return {"session_id": None, "interactive": None, "session_error": str(exc)}


class HostEnvironment:
    def describe(self) -> Dict[str, Any]:
        info: Dict[str, Any] = {
            "platform": sys.platform,
            "pid": os.getpid(),
            "cwd": os.getcwd(),
            "user": os.environ.get("USERNAME") or os.environ.get("USER"),
        }
        if IS_WINDOWS:
            info.update(_windows_session_info())
        elif IS_LINUX:
            info.update(_linux_session_info())
        elif IS_DARWIN:
            info.update(_darwin_session_info())
        return info


class UnsupportedBackend:
    """そのプラットフォームに実装が無い能力の番人。

    例: Linux ではまだ IME/メニュー操作を実装していない。Mac の結合テストでも GUI 系は
    未実装。construct は通すが、メソッドが呼ばれたときだけ明示的に RuntimeError にする
    （「黙って何もしない」より、何が未対応かを呼び元へ返す方が安全）。
    """

    def __init__(self, reason: str = "not supported on this platform"):
        self._reason = reason

    def __getattr__(self, name):
        reason = self.__dict__.get("_reason", "not supported on this platform")

        def _unavailable(*args, **kwargs):
            raise RuntimeError(f"{name}: {reason}")
        return _unavailable


def try_build(construct, reason: str):
    """backend を構築し、失敗したら UnsupportedBackend に倒す（汎用の組み立てヘルパ）。

    1 つの能力が組めなくても agent 全体は起動させ、他のコマンドは使えるようにする。
    各 OS backend のファクトリ（linux/* の build_*）が共有して使う。
    """
    try:
        return construct()
    except Exception as exc:  # ライブラリ欠如・接続不可など
        return UnsupportedBackend(f"{reason} ({exc})")
