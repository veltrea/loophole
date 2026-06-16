"""handlers.py — loophole のコマンドハンドラ（依存性注入で外部 I/O を切り離す）。

外部依存（プロセス起動・クリップボード・スクリーンショット・ファイル I/O）はすべて
インターフェース越しに受け取る。これにより:
  - tests/test_handlers.py では Windows もデスクトップも無しに、フェイクを注入して
    ハンドラのロジック（引数検証・戻り値整形・エラー番号）を Mac で検証できる
  - Windows 実装（Win32 API を ctypes で直叩き）は win_backends.py に隔離する

ハンドラは「リクエストの args（辞書）」を受け取り「result（JSON 化可能な値）」を返す。
不正な引数は HandlerError を投げ、server 層がエラー応答に変換する。
"""

from __future__ import annotations

import base64
import fnmatch
import re
from typing import Any, Callable, Dict, Iterator, List, Optional, Protocol, Tuple

import keys as keyspec
from protocol import decode_output


class HandlerError(Exception):
    """引数不正・実行失敗など、クライアントへエラー応答すべき状況。"""


# ---- 注入される外部 I/O のインターフェース ----------------------------------


class ProcessResult:
    def __init__(self, exit_code: int, stdout: bytes, stderr: bytes, started: bool = True):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.started = started


class Runner(Protocol):
    def run(self, argv: List[str], cwd: Optional[str], timeout: Optional[float],
            stdin_text: Optional[str]) -> ProcessResult:
        ...

    def spawn(self, argv: List[str], cwd: Optional[str]) -> int:
        """GUI/常駐プロセスを起動して即座に PID を返す（出力は捕捉しない）。"""
        ...


class Clipboard(Protocol):
    def get(self) -> str: ...
    def set(self, text: str) -> None: ...


class Screenshotter(Protocol):
    # 全画面を撮影し PNG の生バイトを返す（保存先は呼び側が決める）
    def capture(self) -> bytes: ...


class FileSystem(Protocol):
    def read_bytes(self, path: str) -> bytes: ...
    def write_bytes(self, path: str, data: bytes) -> None: ...
    def exists(self, path: str) -> bool: ...
    def walk(self, root: str) -> Iterator[Tuple[str, List[str], List[str]]]:
        """os.walk 互換: (dirpath, dirnames, filenames) を上から順に yield する。"""
        ...
    def stat(self, path: str) -> Tuple[int, float]:
        """(サイズ[bytes], 更新時刻[epoch 秒]) を返す。"""
        ...


class Environment(Protocol):
    def describe(self) -> Dict[str, Any]:
        """セッション情報（user / session id / interactive かどうか / platform）。"""
        ...


class KeyboardSender(Protocol):
    def send_chord(self, modifiers: List[int], main: int) -> None:
        """修飾キー（押しっぱなし）+ メインキーを 1 打鍵分送る。"""
        ...


class WindowManager(Protocol):
    def list_windows(self, visible_only: bool) -> List[Dict[str, Any]]:
        """トップレベルウィンドウを列挙する。各要素は
        {"hwnd": int, "title": str, "pid": int, "minimized": bool}。
        visible_only=True なら可視かつタイトルのあるウィンドウだけ返す。"""
        ...

    def activate(self, hwnd: int) -> bool:
        """指定ハンドルのウィンドウを（最小化なら復元して）最前面へ。
        実際に前面化できたら True を返す。"""
        ...


class ImeController(Protocol):
    def get(self) -> Optional[Tuple[bool, int]]:
        """前面ウィンドウの IME の (open, conversion) を返す。
        open=IME が ON（日本語入力モード）か、conversion=変換ビットフィールド。
        IME を持たない/応答しないウィンドウでは None。"""
        ...

    def set(self, open: Optional[bool], conversion: Optional[int]) -> bool:
        """open / conversion を設定する。None を渡した軸は変更しない。
        成功したら True。"""
        ...


class MenuController(Protocol):
    def enumerate(self, hwnd: int) -> Optional[List[Dict[str, Any]]]:
        """hwnd のメニューバーを再帰列挙して生ツリーを返す。

        各ノードは次のいずれか:
          - 区切り線      : {"separator": True}
          - 通常/ポップアップ: {"label": str, "command_id": int, "enabled": bool,
                              "checked": bool, "submenu": [...] or None}
        メニューを持たないウィンドウ（リボン/Electron/UWP 等）は None を返す。"""
        ...

    def invoke(self, hwnd: int, command_id: int) -> bool:
        """command_id を WM_COMMAND として hwnd に Post する。送れたら True。"""
        ...


# ---- IME 変換モードのビットフラグ（IMM32 の IME_CMODE_*）---------------------
#
# Open status（IME の ON/OFF）とは別の軸。これは IME が ON のときに「何で入力するか」。
# ユーザーの言う「ローマ字入力か」は ROMAN ビット、「ダイレクト入力」は open=False。
_CMODE_NATIVE = 0x0001     # 日本語入力（ひらがな/カタカナ）。0 なら英数
_CMODE_KATAKANA = 0x0002   # カタカナ（NATIVE と併用）。0 ならひらがな
_CMODE_FULLSHAPE = 0x0008  # 全角。0 なら半角
_CMODE_ROMAN = 0x0010      # ローマ字入力。0 ならかな入力

# 人間可読なモード名 → 変換ビット（ROMAN は別軸なので含めない）。
_IME_MODE_TO_BITS = {
    "hiragana":          _CMODE_NATIVE | _CMODE_FULLSHAPE,                    # ひらがな
    "katakana":          _CMODE_NATIVE | _CMODE_KATAKANA | _CMODE_FULLSHAPE,  # 全角カタカナ
    "katakana-half":     _CMODE_NATIVE | _CMODE_KATAKANA,                     # 半角カタカナ
    "alphanumeric-full": _CMODE_FULLSHAPE,                                    # 全角英数
    "alphanumeric":      0,                                                   # 半角英数
}
_IME_BITS_TO_MODE = {v: k for k, v in _IME_MODE_TO_BITS.items()}
_IME_MODE_MASK = _CMODE_NATIVE | _CMODE_KATAKANA | _CMODE_FULLSHAPE


def _decode_conversion(conv: int) -> Tuple[Optional[str], bool]:
    """変換ビットフィールドを (モード名 or None, ローマ字入力か) に分解する。

    モード名は既知の組み合わせのみ（未知のビット構成なら None）。生の int は
    呼び側が conversion としてそのまま返すので、ここでは読みやすさ用の要約だけ。
    """
    roman = bool(conv & _CMODE_ROMAN)
    mode = _IME_BITS_TO_MODE.get(conv & _IME_MODE_MASK)
    return mode, roman


# ---- メニュー列挙の純粋ロジック ---------------------------------------------
#
# Win32 直叩き（GetMenu / GetMenuItemInfoW / PostMessage）は win_backends に隔離する。
# ここはバックエンドが返した生ツリーを公開形へ整形するだけなので Mac でテストできる。

# 破壊的操作と推測されるラベルの語（大小無視・英日）。一致したノードに助言フラグを付ける。
# これは「止める」ためではなく「呼び側（CLAUDE.md ルール）が除外を判断する」ための目印。
_DESTRUCTIVE = re.compile(
    r"exit|quit|close|delete|remove|format|erase|overwrite|send|"
    r"削除|終了|閉じ|消去|初期化|上書き|送信",
    re.IGNORECASE,
)


def _clean_label(label: str) -> str:
    """メニューラベルを表示・経路用に正規化する。

    - 末尾の `\t<ショートカット>`（例 "新規(N)\tCtrl+N"）はアクセラレータ表示なので落とす
    - 末尾の日本語ニーモニック括弧 `(&X)`（例 "ファイル(&F)"）を落とす → "ファイル"
    - 残った単独の `&`（英語式アクセラレータ "&File"）は除去。`&&` はリテラル `&` に畳む
    """
    label = label.split("\t", 1)[0]
    # 日本語メニュー慣習の末尾ニーモニック "(&F)" / "(&N)" を丸ごと落とす。
    label = re.sub(r"\s*\(&[^)]\)\s*$", "", label)
    out: List[str] = []
    i = 0
    while i < len(label):
        if label[i] == "&":
            if i + 1 < len(label) and label[i + 1] == "&":
                out.append("&")
                i += 2
                continue
            i += 1
            continue
        out.append(label[i])
        i += 1
    return "".join(out).strip()


def _format_menu_items(raw: List[Dict[str, Any]], parent_path: str) -> List[Dict[str, Any]]:
    """バックエンドの生ツリーを公開形へ整形する（再帰）。

    付与するもの: path（ルートからのラベル経路）, destructive_guess（助言フラグ）。
    サブメニューを束ねる項目は command_id を None にする（コマンドとして発火できないため）。
    """
    items: List[Dict[str, Any]] = []
    for node in raw:
        if node.get("separator"):
            items.append({"separator": True})
            continue
        clean = _clean_label(str(node.get("label", "")))
        path = (parent_path + " > " + clean) if parent_path else clean
        submenu_raw = node.get("submenu")
        has_submenu = bool(submenu_raw)
        out: Dict[str, Any] = {
            "label": node.get("label", ""),
            "command_id": None if has_submenu else node.get("command_id"),
            "enabled": bool(node.get("enabled", True)),
            "checked": bool(node.get("checked", False)),
            "separator": False,
            "path": path,
        }
        if _DESTRUCTIVE.search(clean):
            out["destructive_guess"] = True
        if has_submenu:
            out["submenu"] = _format_menu_items(submenu_raw, path)
        items.append(out)
    return items


# ---- ハンドラ群 -------------------------------------------------------------


class Handlers:
    def __init__(self, runner: Runner, clipboard: Clipboard,
                 screenshotter: Screenshotter, filesystem: FileSystem,
                 environment: Environment, keyboard: KeyboardSender,
                 windows: WindowManager, ime: ImeController,
                 menu: MenuController):
        self._runner = runner
        self._clipboard = clipboard
        self._screenshot = screenshotter
        self._fs = filesystem
        self._env = environment
        self._keyboard = keyboard
        self._windows = windows
        self._ime = ime
        self._menu = menu

    def dispatch(self, cmd: str, args: Dict[str, Any]) -> Any:
        handler: Optional[Callable[[Dict[str, Any]], Any]] = self._table().get(cmd)
        if handler is None:
            raise HandlerError(f"unknown command: {cmd}")
        return handler(args)

    def commands(self) -> List[str]:
        return sorted(self._table().keys())

    def _table(self) -> Dict[str, Callable[[Dict[str, Any]], Any]]:
        return {
            "ping": self._ping,
            "hello": self._hello,
            "run": self._run,
            "spawn": self._spawn,
            "clipboard_get": self._clipboard_get,
            "clipboard_set": self._clipboard_set,
            "screenshot": self._screenshot_cmd,
            "read_file": self._read_file,
            "write_file": self._write_file,
            "send_keys": self._send_keys,
            "find_files": self._find_files,
            "list_windows": self._list_windows,
            "activate_window": self._activate_window,
            "ime_get": self._ime_get,
            "ime_set": self._ime_set,
            "menu_enumerate": self._menu_enumerate,
            "menu_invoke": self._menu_invoke,
        }

    # ping / hello は疎通とセッション情報の確認用
    def _ping(self, args: Dict[str, Any]) -> Any:
        return {"pong": True}

    def _hello(self, args: Dict[str, Any]) -> Any:
        return self._env.describe()

    def _run(self, args: Dict[str, Any]) -> Any:
        """コマンドを実行し stdout/stderr/exit_code を返す。

        args:
          argv     : 文字列配列（必須）。シェルを介さず execvp 相当で起動する。
          command  : 文字列（argv の代わり）。cmd.exe /S /C 経由でシェル実行。
          cwd      : 作業ディレクトリ（省略可）
          timeout  : 秒（省略可）
          encoding : "auto"(既定) / "utf-8" / "cp932"
          stdin    : 標準入力に流すテキスト（省略可）
        """
        encoding = args.get("encoding", "auto")
        cwd = args.get("cwd")
        timeout = args.get("timeout")
        stdin_text = args.get("stdin")

        if "argv" in args:
            argv = args["argv"]
            if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
                raise HandlerError("'argv' must be an array of strings")
            if not argv:
                raise HandlerError("'argv' must not be empty")
        elif "command" in args:
            command = args["command"]
            if not isinstance(command, str):
                raise HandlerError("'command' must be a string")
            # Windows のシェルワンライナー。/S /C は最外の引用を 1 組だけ外す。
            argv = ["cmd.exe", "/S", "/C", command]
        else:
            raise HandlerError("run requires 'argv' or 'command'")

        result = self._runner.run(argv, cwd, timeout, stdin_text)
        if not result.started:
            raise HandlerError(f"failed to start process: {argv[0]}")
        return {
            "exit_code": result.exit_code,
            "stdout": decode_output(result.stdout, encoding),
            "stderr": decode_output(result.stderr, encoding),
        }

    def _spawn(self, args: Dict[str, Any]) -> Any:
        """GUI / 常駐プロセスを起動して PID を返す（出力は捕捉しない）。

        エージェントが対話デスクトップセッションにいるので、ここで起動した
        GUI アプリは実際の画面に出る（= セッション 0 の壁の回避）。
        """
        argv = args.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            raise HandlerError("spawn requires non-empty 'argv' array of strings")
        pid = self._runner.spawn(argv, args.get("cwd"))
        return {"pid": pid}

    def _clipboard_get(self, args: Dict[str, Any]) -> Any:
        return {"text": self._clipboard.get()}

    def _clipboard_set(self, args: Dict[str, Any]) -> Any:
        if "text" not in args or not isinstance(args["text"], str):
            raise HandlerError("clipboard_set requires string 'text'")
        self._clipboard.set(args["text"])
        return {"ok": True}

    def _screenshot_cmd(self, args: Dict[str, Any]) -> Any:
        """全画面を撮影する。

        path（省略可）: 指定すればエージェント側のそのパスにも PNG を保存する。
        data（既定 True）: True なら PNG を base64 で返す（MCP が Image にして返す用）。
        """
        path = args.get("path")
        if path is not None and not isinstance(path, str):
            raise HandlerError("screenshot 'path' must be a string")
        include_data = args.get("data", True)
        png = self._screenshot.capture()
        result: Dict[str, Any] = {"bytes": len(png)}
        if path:
            self._fs.write_bytes(path, png)
            result["path"] = path
        if include_data:
            result["png_base64"] = base64.b64encode(png).decode("ascii")
        return result

    def _read_file(self, args: Dict[str, Any]) -> Any:
        path = args.get("path")
        if not isinstance(path, str) or not path:
            raise HandlerError("read_file requires string 'path'")
        if not self._fs.exists(path):
            raise HandlerError(f"file not found: {path}")
        data = self._fs.read_bytes(path)
        return {"text": decode_output(data, args.get("encoding", "auto"))}

    def _write_file(self, args: Dict[str, Any]) -> Any:
        path = args.get("path")
        text = args.get("text")
        if not isinstance(path, str) or not path:
            raise HandlerError("write_file requires string 'path'")
        if not isinstance(text, str):
            raise HandlerError("write_file requires string 'text'")
        self._fs.write_bytes(path, text.encode("utf-8"))
        return {"ok": True}

    def _send_keys(self, args: Dict[str, Any]) -> Any:
        """ショートカット（キーの組み合わせ）を送る。

        args:
          keys : "ctrl+s" のような 1 ストローク、空白区切りの複数（"win+r enter"）、
                 または文字列のリスト（["win+r", "enter"]）。

        これは **文字入力ではなくショートカット用**。日本語などの文字入力は IME を
        通って化けるので、clipboard_set → ペーストを使うこと（loophole の元々の流儀）。
        """
        if "keys" not in args:
            raise HandlerError("send_keys requires 'keys' (a string or array of strings)")
        spec = args["keys"]
        try:
            chords = keyspec.parse_sequence(spec)
        except ValueError as exc:
            raise HandlerError(str(exc)) from exc
        for modifiers, main in chords:
            self._keyboard.send_chord(modifiers, main)
        return {"sent": keyspec.normalize(spec), "count": len(chords)}

    def _find_files(self, args: Dict[str, Any]) -> Any:
        """ルート以下をファイル名で検索する（GUI を開かずに目的のファイルを探す）。

        args:
          root        : 検索開始ディレクトリ（必須）
          pattern     : マッチ対象（必須）。match="glob" なら "*.txt" 等、
                        match="substring" なら大小無視の部分一致文字列。
          match       : "glob"(既定) / "substring"
          max_results : 返す最大件数（既定 200）。超えたら truncated=True
          max_depth   : root を 0 とした探索深さ上限（省略可）
          include_dirs: True ならディレクトリ名もマッチ対象に含める（既定 False）
        """
        root = args.get("root")
        if not isinstance(root, str) or not root:
            raise HandlerError("find_files requires string 'root'")
        pattern = args.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            raise HandlerError("find_files requires string 'pattern'")
        match = args.get("match", "glob")
        if match not in ("glob", "substring"):
            raise HandlerError("find_files 'match' must be 'glob' or 'substring'")
        max_results = args.get("max_results", 200)
        if not isinstance(max_results, int) or isinstance(max_results, bool) or max_results <= 0:
            raise HandlerError("find_files 'max_results' must be a positive integer")
        max_depth = args.get("max_depth")
        if max_depth is not None and (not isinstance(max_depth, int) or isinstance(max_depth, bool) or max_depth < 0):
            raise HandlerError("find_files 'max_depth' must be a non-negative integer")
        include_dirs = bool(args.get("include_dirs", False))

        if not self._fs.exists(root):
            raise HandlerError(f"root not found: {root}")

        needle = pattern.lower()

        def is_match(name: str) -> bool:
            if match == "glob":
                return fnmatch.fnmatch(name.lower(), needle)
            return needle in name.lower()

        matches: List[Dict[str, Any]] = []
        scanned = 0
        truncated = False
        root_depth = root.replace("\\", "/").rstrip("/").count("/")

        for dirpath, dirnames, filenames in self._fs.walk(root):
            if max_depth is not None:
                depth = dirpath.replace("\\", "/").rstrip("/").count("/") - root_depth
                if depth > max_depth:
                    # このレベルは深すぎる。実 os.walk なら下にも潜らせない。
                    dirnames[:] = []
                    continue
                if depth >= max_depth:
                    dirnames[:] = []  # 次レベルが上限超えになるので枝刈り

            names = list(filenames) + (list(dirnames) if include_dirs else [])
            for name in names:
                scanned += 1
                if not is_match(name):
                    continue
                full = _join(dirpath, name)
                try:
                    size, mtime = self._fs.stat(full)
                except OSError:
                    size, mtime = -1, 0.0
                matches.append({"path": full, "size": size, "mtime": mtime})
                if len(matches) >= max_results:
                    truncated = True
                    break
            if truncated:
                break

        return {"matches": matches, "truncated": truncated, "scanned": scanned}

    def _list_windows(self, args: Dict[str, Any]) -> Any:
        """開いているトップレベルウィンドウのタイトル一覧を返す。

        args:
          pattern      : タイトルの部分一致（大小無視）でフィルタ（省略可）
          visible_only : True(既定) なら可視かつタイトルのあるウィンドウだけ

        戻り値: {"windows": [{"hwnd","title","pid","minimized"}, ...], "count": N}
        フィルタ（部分一致）はここで純粋に行うので Mac でテストできる。Win32 は
        win_backends.Win32WindowManager が「全列挙」だけを担う。
        """
        pattern = args.get("pattern")
        if pattern is not None and not isinstance(pattern, str):
            raise HandlerError("list_windows 'pattern' must be a string")
        visible_only = bool(args.get("visible_only", True))
        windows = self._windows.list_windows(visible_only)
        if pattern:
            needle = pattern.lower()
            windows = [w for w in windows if needle in str(w.get("title", "")).lower()]
        return {"windows": windows, "count": len(windows)}

    def _activate_window(self, args: Dict[str, Any]) -> Any:
        """タイトルの部分一致または HWND 指定でウィンドウを最前面化する。

        args:
          title : タイトルの部分一致（大小無視）。複数該当時は前面化せず候補を返す
          hwnd  : ウィンドウハンドル直指定（list_windows の結果から）。title より優先
        title か hwnd のどちらか一方は必須。

        戻り値:
          前面化成功     : {"activated": True, "hwnd": H, "title": T}
          曖昧（複数該当）: {"activated": False, "ambiguous": True, "candidates": [...]}
        誤爆で無関係なウィンドウを前面に出さないため、曖昧なときは何も動かさない。
        """
        hwnd = args.get("hwnd")
        if hwnd is not None:
            if not isinstance(hwnd, int) or isinstance(hwnd, bool):
                raise HandlerError("activate_window 'hwnd' must be an integer")
            if not self._windows.activate(hwnd):
                raise HandlerError(
                    f"could not activate window hwnd={hwnd} (no such window or focus refused)")
            return {"activated": True, "hwnd": hwnd}

        title = args.get("title")
        if not isinstance(title, str) or not title:
            raise HandlerError("activate_window requires 'title' (substring) or 'hwnd' (integer)")
        needle = title.lower()
        candidates = [w for w in self._windows.list_windows(True)
                      if needle in str(w.get("title", "")).lower()]
        if not candidates:
            raise HandlerError(f"no visible window's title contains {title!r}")
        if len(candidates) > 1:
            return {"activated": False, "ambiguous": True, "candidates": candidates}
        target = candidates[0]
        if not self._windows.activate(int(target["hwnd"])):
            raise HandlerError(
                f"could not activate window {target.get('title')!r} (focus refused)")
        return {"activated": True, "hwnd": target["hwnd"], "title": target.get("title")}

    def _ime_get(self, args: Dict[str, Any]) -> Any:
        """前面ウィンドウの IME 状態を読む（変更はしない）。

        戻り値:
          IME を持つ窓 : {"supported": True, "open": bool, "conversion": int,
                          "mode": str|None, "roman": bool}
          持たない窓   : {"supported": False}

        open=True が日本語入力モード（computer-use の type が読みに吸われる状態）、
        open=False が直接入力（type が化けない）。mode/roman は open=True のときの
        入力方式で、open=False のときは参考値（その IME が次に ON になったときの設定）。
        """
        state = self._ime.get()
        if state is None:
            return {"supported": False}
        open_, conv = state
        mode, roman = _decode_conversion(int(conv))
        return {"supported": True, "open": bool(open_), "conversion": int(conv),
                "mode": mode, "roman": roman}

    def _ime_set(self, args: Dict[str, Any]) -> Any:
        """前面ウィンドウの IME 状態を変える。指定しなかった軸は変更しない。

        args（いずれか 1 つ以上が必須）:
          open       : bool  IME の ON/OFF。False=直接入力（type が化けない）
          mode       : str   "hiragana" / "katakana" / "katakana-half" /
                             "alphanumeric" / "alphanumeric-full"
          roman      : bool  ローマ字入力(True) か かな入力(False) か
          conversion : int   変換ビットフィールドを直接指定（mode/roman より優先）

        戻り値は変更後の _ime_get と同じ形（確認用）。
        """
        target_open: Optional[bool] = None
        if "open" in args:
            if not isinstance(args["open"], bool):
                raise HandlerError("ime_set 'open' must be a boolean")
            target_open = args["open"]

        target_conv: Optional[int] = None
        if "conversion" in args:
            conv = args["conversion"]
            if not isinstance(conv, int) or isinstance(conv, bool):
                raise HandlerError("ime_set 'conversion' must be an integer")
            target_conv = conv
        elif "mode" in args or "roman" in args:
            mode = args.get("mode")
            if mode is not None and mode not in _IME_MODE_TO_BITS:
                raise HandlerError(
                    "ime_set 'mode' must be one of " + ", ".join(sorted(_IME_MODE_TO_BITS)))
            roman = args.get("roman")
            if roman is not None and not isinstance(roman, bool):
                raise HandlerError("ime_set 'roman' must be a boolean")
            # 指定しなかった軸は現状を保つため、いま設定されている変換モードを読む。
            current = self._ime.get()
            cur_conv = current[1] if current else 0
            base = _IME_MODE_TO_BITS[mode] if mode is not None else (cur_conv & ~_CMODE_ROMAN)
            if roman is not None:
                roman_bit = _CMODE_ROMAN if roman else 0
            else:
                roman_bit = cur_conv & _CMODE_ROMAN
            target_conv = base | roman_bit

        if target_open is None and target_conv is None:
            raise HandlerError(
                "ime_set requires at least one of 'open', 'mode', 'roman', 'conversion'")

        if not self._ime.set(target_open, target_conv):
            raise HandlerError(
                "ime_set failed: the foreground window has no IME or refused the change")
        return self._ime_get({})

    def _resolve_target(self, args: Dict[str, Any]) -> Tuple[Optional[int], Optional[str], Optional[List[Dict[str, Any]]]]:
        """args の hwnd / title から対象ウィンドウを解決する。

        戻り値 (hwnd, title, ambiguous):
          - hwnd 直指定 or title が一意 : (hwnd, title|None, None)
          - title が複数該当          : (None, None, candidates) ← 呼び側は候補を返し何も操作しない
        title 該当なし・引数不足は HandlerError。activate_window と同じ「曖昧なら動かさない」流儀。
        """
        hwnd = args.get("hwnd")
        if hwnd is not None:
            if not isinstance(hwnd, int) or isinstance(hwnd, bool):
                raise HandlerError("'hwnd' must be an integer")
            return hwnd, None, None
        title = args.get("title")
        if not isinstance(title, str) or not title:
            raise HandlerError("menu command requires 'title' (substring) or 'hwnd' (integer)")
        needle = title.lower()
        candidates = [w for w in self._windows.list_windows(True)
                      if needle in str(w.get("title", "")).lower()]
        if not candidates:
            raise HandlerError(f"no visible window's title contains {title!r}")
        if len(candidates) > 1:
            return None, None, candidates
        return int(candidates[0]["hwnd"]), candidates[0].get("title"), None

    def _menu_enumerate(self, args: Dict[str, Any]) -> Any:
        """対象ウィンドウのメニューバーを列挙する（読み取り専用・発火しない）。

        args: title（部分一致）か hwnd のどちらか。hwnd 優先。
        戻り値:
          メニューあり : {"supported": True, "hwnd", "title", "items": [...]}
          メニュー無し : {"supported": False, "hwnd": H}（リボン/Electron/UWP 等）
          title 曖昧   : {"ambiguous": True, "candidates": [...]}
        """
        hwnd, title, ambiguous = self._resolve_target(args)
        if ambiguous is not None:
            return {"ambiguous": True, "candidates": ambiguous}
        raw = self._menu.enumerate(int(hwnd))
        if raw is None:
            return {"supported": False, "hwnd": hwnd}
        return {"supported": True, "hwnd": hwnd, "title": title,
                "items": _format_menu_items(raw, "")}

    def _menu_invoke(self, args: Dict[str, Any]) -> Any:
        """メニューコマンドを command_id で発火する（WM_COMMAND を Post）。

        args:
          title / hwnd : 対象ウィンドウ（menu_enumerate が返した hwnd を渡すのが安全）
          command_id   : 発火するコマンド ID（正の整数。0 以下や非 int は拒否）

        戻り値: {"posted": True, "hwnd", "command_id"}（title 曖昧時は ambiguous 候補）
        PostMessage は非同期なので posted=True は「送れた」であり「完了」ではない。
        """
        hwnd, _title, ambiguous = self._resolve_target(args)
        if ambiguous is not None:
            return {"ambiguous": True, "candidates": ambiguous}
        command_id = args.get("command_id")
        if not isinstance(command_id, int) or isinstance(command_id, bool) or command_id <= 0:
            raise HandlerError("menu_invoke requires a positive integer 'command_id'")
        if not self._menu.invoke(int(hwnd), command_id):
            raise HandlerError(
                f"could not post command_id={command_id} to hwnd={hwnd} "
                f"(no such window, or it has no menu)")
        return {"posted": True, "hwnd": hwnd, "command_id": command_id}


def _join(dirpath: str, name: str) -> str:
    """dirpath と name を、dirpath が使っている区切り文字で連結する（OS 非依存）。

    os.path.join は実行 OS の区切りになるため使わない。検索対象はエージェント
    ホスト（Windows）のパスで、テストは Mac で走るので、区切りは入力に従う。
    """
    if not dirpath:
        return name
    if dirpath.endswith(("/", "\\")):
        return dirpath + name
    sep = "\\" if "\\" in dirpath else "/"
    return dirpath + sep + name
