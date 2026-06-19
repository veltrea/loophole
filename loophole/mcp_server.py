#!/usr/bin/env python3
"""mcp_server.py — loophole を MCP サーバーとして公開する薄いブリッジ。

Claude Code（や他の MCP クライアント）から、対象 PC（Windows / Linux）のデスクトップ
セッションに常駐する loophole をネイティブツールとして使えるようにする。Serena のような
「専用 MCP を足して作業の解像度を上げる」立ち位置。

構成:
    MCP クライアント ──stdio(JSONL)──▶ mcp_server.py（Mac ローカル）
                                          └─ TCP ──▶ loophole（対象 PC・ssh -L 越し）

設計方針: loophole はインストール可能なパッケージ。entry point `loophole` で起動する。
設定は ~/.loophole/config（KEY=value）に 1 回書く——`loophole --setup` が対話で埋める。
登録はこの 1 行（clone もパスも要らない）:
    claude mcp add loophole -- loophole

- このサーバー自身は Mac ローカルで stdio 起動する（Claude Code が spawn）。
- 設定の出どころは環境変数 または ~/.loophole/config（env が優先）。主なキー:
      LOOPHOLE_SSH（"user@host"。自動トンネルの宛先・実質これだけ埋めればよい）
      LOOPHOLE_SSH_KEY（鍵パス・任意） / LOOPHOLE_SSH_PORT（SSH ポート・既定 22）
      LOOPHOLE_SSH_OPTS（追加 ssh オプション・任意） / LOOPHOLE_PORT（既定 9999） / LOOPHOLE_TOKEN（任意）
- 対象 PC への到達は SSH ポートフォワード（ssh -L 9999:127.0.0.1:9999）。認証は SSH に丸投げ。
  LOOPHOLE_SSH があれば起動時に ssh -L を内部で spawn し（終了時に畳む）、手動トンネルを不要にする。
  未設定なら従来どおり外側のトンネルに繋ぐだけ。ポートが既に開いていれば spawn せず再利用する。
- loophole との通信は protocol.Client を再利用（cli.Client）。
"""

from __future__ import annotations

import atexit
import base64
import datetime
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time

from mcp.server.fastmcp import FastMCP, Image

from .cli import Client  # 薄いクライアント（Client クラス・protocol を使う）


def _log(msg: str) -> None:
    # ログは必ず stderr。stdout は MCP の JSONL 専用で、1 行でも混ざると無応答になる。
    print(f"[loophole] {msg}", file=sys.stderr, flush=True)


# --- 設定ファイル ~/.loophole/config -----------------------------------------
# ユーザーが触る設定はここ 1 か所。KEY=value 形式で、環境変数が優先（既存の登録を壊さない）。
# 初回起動でファイルが無ければテンプレートを書き出し、何を埋めればいいか案内する。

CONFIG_PATH = os.path.expanduser(os.environ.get("LOOPHOLE_CONFIG", "~/.loophole/config"))

_CONFIG_TEMPLATE = """\
# loophole の設定 — 対象 PC のアドレスを書いて保存するだけ。1 回でいい。
# 形式は KEY=value。行頭 '#' はコメント。基本は LOOPHOLE_SSH の 1 行を有効にすれば動く。

# 【ほぼ必須】自動 SSH トンネルの宛先（対象 PC への SSH ログイン先・Windows / Linux 可）
#LOOPHOLE_SSH=user@192.168.1.x

# SSH 秘密鍵（任意。省くと ssh-agent / 既定の鍵を使う）
#LOOPHOLE_SSH_KEY=~/.ssh/id_ed25519

# 以下は必要な人だけ
#LOOPHOLE_SSH_PORT=22
#LOOPHOLE_SSH_OPTS=-o ProxyJump=none
#LOOPHOLE_PORT=9999
#LOOPHOLE_TOKEN=
"""


def _load_config_file(create_template: bool = True) -> None:
    """~/.loophole/config を読み、未設定の環境変数だけ埋める（env が優先）。

    ファイルが無ければテンプレートを書いて案内する（存在自体は失敗扱いにしない——
    自分でトンネルを張る運用もあり得るので、設定ゼロでもサーバーは起動できる）。
    create_template=False のときは雛形を書かない（--setup が直後に本物を書くため）。
    """
    if not os.path.exists(CONFIG_PATH):
        if create_template:
            try:
                os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
                with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                    f.write(_CONFIG_TEMPLATE)
                _log(f"設定ファイルの雛形を作りました: {CONFIG_PATH} — "
                     f"'loophole --setup' で対話的に埋められます")
            except OSError as exc:
                _log(f"設定ファイルを作成できませんでした: {CONFIG_PATH}（{exc}）")
        return
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip()
                if key and key not in os.environ:   # env が優先
                    os.environ[key] = val
    except OSError as exc:
        _log(f"設定ファイルを読めませんでした: {CONFIG_PATH}（{exc}）")


_load_config_file(create_template="--setup" not in sys.argv)

HOST = os.environ.get("LOOPHOLE_HOST", "127.0.0.1")
PORT = int(os.environ.get("LOOPHOLE_PORT", "9999"))
TOKEN = os.environ.get("LOOPHOLE_TOKEN") or None

mcp = FastMCP(
    "loophole",
    instructions=(
        "loophole は対象 PC（Windows / Linux）を SSH 越しに操作する MCP。初回だけ接続設定が要る。\n"
        "セットアップがまだのとき（loophole_status が configured=false、または各ツールが"
        "『未設定』/接続不可を返す）は、ターミナルを開かせず、チャットでこう進めること:\n"
        "  1) ユーザーに『操作したい PC の IP アドレス』を聞く（例 192.168.1.x）\n"
        "  2) ユーザーに『その PC でのユーザー名（サインイン名）』を聞く\n"
        "  3) loophole_configure(host_ip, username) を呼ぶ\n"
        "loophole_configure が接続テスト・設定保存・トンネル開通・疎通確認まで行い、結果を返す。"
        "SSH 鍵や踏み台などの専門設定はユーザーに聞かなくてよい（空のままでよい・裏で自動対応）。"
    ),
)


# --- SSH トンネル自動化 -----------------------------------------------------
# LOOPHOLE_SSH があれば、手元側のこのプロセスが ssh -L を子プロセスとして抱え、
# ユーザーが別ターミナルで手動トンネルを張る工程を不要にする。ログは全て stderr
# （stdout は MCP の JSONL。1 行でも混ざると MCP クライアントが無応答になる）。

_tunnel: subprocess.Popen | None = None


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _close_tunnel() -> None:
    """子プロセスの ssh を終了させる（idempotent。atexit と finally の両方から呼ばれる）。"""
    global _tunnel
    proc, _tunnel = _tunnel, None
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()


def _open_tunnel() -> bool:
    """LOOPHOLE_SSH があれば ssh -L を spawn し、ローカルポートが開くまで待つ。

    戻り値: トンネルが使える状態なら True、未設定/張れなかったら False。
    **失敗してもプロセスは落とさない**——未設定のまま起動して、あとからチャットで
    loophole_configure を呼んで設定し直せるようにするため。ポートが既に開いていれば
    spawn せず再利用する（手動トンネルや残骸と衝突しない）。
    """
    global _tunnel
    target = os.environ.get("LOOPHOLE_SSH")
    if not target:
        return False  # 未設定 — チャットから loophole_configure で設定する

    if _port_open("127.0.0.1", PORT):
        _log(f"ポート {PORT} は既に開いています。既存のトンネルを再利用します（ssh は起動しません）")
        return True

    argv = ["ssh"]
    ssh_port = os.environ.get("LOOPHOLE_SSH_PORT")
    if ssh_port:
        argv += ["-p", ssh_port]
    key = os.environ.get("LOOPHOLE_SSH_KEY")
    if key:
        argv += ["-i", os.path.expanduser(key)]
    argv += [
        "-N",  # コマンドを実行しない（ポート転送だけ）
        "-o", "ExitOnForwardFailure=yes",   # 転送に失敗したら即終了（黙って繋がらないを防ぐ）
        "-o", "ServerAliveInterval=30",     # 無通信でも生存確認を送り、寝落ち回線を検知
        "-o", "ServerAliveCountMax=3",
        "-o", "BatchMode=yes",              # パスフレーズ/パスワードを尋ねず即失敗（TTY が無い）
        "-o", "StrictHostKeyChecking=accept-new",  # 初回の未知ホストは TOFU で受理、変更時は拒否
        "-L", f"{PORT}:127.0.0.1:{PORT}",
    ]
    extra = os.environ.get("LOOPHOLE_SSH_OPTS")
    if extra:
        argv += shlex.split(extra)  # 例: "-o ProxyJump=none"
    argv.append(target)

    # ssh の stderr は一時ファイルへ（PIPE を読まず放置するとバッファ詰まりで固まりうる）。
    # stdin/stdout は DEVNULL に倒す — このプロセスの stdin/stdout は MCP の JSONL 用。
    errlog = tempfile.NamedTemporaryFile(
        prefix="loophole-ssh-", suffix=".log", mode="w+", delete=False)
    proc = subprocess.Popen(
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errlog)

    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break  # ssh が即死（鍵・ホスト名・転送失敗など）
        if _port_open("127.0.0.1", PORT):
            _tunnel = proc
            atexit.register(_close_tunnel)
            _log(f"SSH トンネルを張りました: {PORT}->127.0.0.1:{PORT} 経由 {target}（pid {proc.pid}）")
            return True
        time.sleep(0.3)

    # ここに来たら失敗 — ssh を畳んで、何を言っていたかを添えて終了する。
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
    errlog.flush()
    errlog.seek(0)
    detail = errlog.read().strip() or "（ssh からの出力なし）"
    _log(f"SSH トンネルを張れませんでした: 宛先 {target}（転送 {PORT}->127.0.0.1:{PORT}）")
    _log(f"  ssh の出力: {detail}")
    _log("  確認: LOOPHOLE_SSH / LOOPHOLE_SSH_KEY が正しいか、鍵にパスフレーズが無いか "
         "（または ssh-agent に載っているか）。VPN・踏み台越しなら "
         "LOOPHOLE_SSH_OPTS=\"-o ProxyJump=none\" が要ることがあります。")
    return False


def _probe_ssh(target: str, base_opts: str = "") -> tuple[bool, str, str]:
    """target へ実際に SSH して 1 行返るか試す（print しない・MCP ツールからも使える）。

    一度で繋がらなければ VPN・踏み台回避（ProxyJump=none）で 1 回だけ試し直す——専門用語を
    ユーザーに聞かずに、よくある詰まりを裏で吸収するため。
    戻り値: (成功か, 実際に効いた追加 ssh オプション, 失敗時の ssh 出力)。
    """
    def attempt(extra: str) -> tuple[bool, str]:
        argv = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                "-o", "StrictHostKeyChecking=accept-new"]
        if extra:
            argv += shlex.split(extra)
        argv += [target, "echo loophole-ok"]
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=25)
        except (subprocess.TimeoutExpired, OSError) as exc:
            return False, str(exc)
        return (r.returncode == 0 and "loophole-ok" in r.stdout), (r.stderr or "").strip()

    ok, _ = attempt(base_opts)
    if ok:
        return True, base_opts, ""
    retry = (base_opts + " " if base_opts else "") + "-o ProxyJump=none -o IdentitiesOnly=yes"
    ok2, err2 = attempt(retry)
    if ok2:
        return True, retry, ""
    return False, base_opts, err2


def _client(via: str | None = None) -> Client:
    return Client(HOST, PORT, TOKEN, via=via)


class _AgentError(Exception):
    pass


def _call(cmd: str, args: dict | None = None, timeout: float = 60.0,
          via: str | None = None) -> dict:
    """loophole を呼び、result を返す。エラーは actionable なメッセージで上げる。

    via: 呼び元ツール名。エージェントの実行履歴（/log）に「どの MCP ツールが
    叩いたか」として残る。Client コンストラクタに渡るので .call() の引数は不変
    （MCP ブリッジテストのフェイクに手を入れずに済む）。
    """
    try:
        resp = _client(via).call(cmd, args or {}, timeout=timeout)
    except OSError as exc:
        if not os.environ.get("LOOPHOLE_SSH"):
            raise _AgentError(
                "loophole はまだ設定されていません。ターミナルに戻らせず、ユーザーに対象 "
                "PC の IP アドレスとユーザー名を聞いて loophole_configure を呼んでください。"
            ) from exc
        raise _AgentError(
            f"loophole に接続できません（{HOST}:{PORT}、{exc}）。"
            f"~/.loophole/config の LOOPHOLE_SSH を確認、対象 PC 側で loophole サーバーが "
            f"動いているか確認を。loophole_configure で設定し直すこともできます。"
        ) from exc
    if not resp.get("ok"):
        raise _AgentError(f"loophole error: {resp.get('error')}")
    return resp.get("result")


@mcp.tool()
def loophole_hello() -> str:
    """Report the agent host's session info (platform, user, session_id, interactive).

    Use this first to confirm the agent is reachable and which desktop session it
    runs in. interactive=true / session_id>=1 means clipboard, screenshot and GUI
    spawning will actually affect a visible desktop.
    """
    r = _call("hello")
    return (
        f"platform={r.get('platform')} user={r.get('user')} "
        f"session_id={r.get('session_id')} interactive={r.get('interactive')} "
        f"cwd={r.get('cwd')}"
    )


@mcp.tool()
def loophole_status() -> str:
    """Report whether loophole is configured and reachable. Call this if unsure.

    If it returns configured=false, do NOT send the user to a terminal: ask them for
    their target machine's IP address and their username on it, then call
    loophole_configure with those.
    """
    configured = bool(os.environ.get("LOOPHOLE_SSH"))
    if not configured:
        return ("configured=false — loophole はまだセットアップされていません。"
                "ユーザーに対象 PC の IP アドレスとユーザー名を聞いて "
                "loophole_configure を呼んでください。")
    target = os.environ.get("LOOPHOLE_SSH")
    reachable = _port_open("127.0.0.1", PORT)
    tail = "" if reachable else ("（ポートに届いていません。対象 PC・loophole サーバーの起動を確認、"
                                 "または loophole_configure で設定し直し）")
    return f"configured=true target={target} reachable={reachable}{tail}"


@mcp.tool()
def loophole_configure(host_ip: str, username: str,
                       ssh_key: str = "", ssh_opts: str = "") -> str:
    """Set up loophole's connection to the target machine (Windows or Linux), in chat.

    Call this when loophole isn't configured yet. First ask the user, in plain words,
    for just two things: (1) the target machine's IP address, (2) their username on
    that machine. Do NOT ask about SSH keys or proxy/jump-host settings — leave ssh_key
    and ssh_opts empty; loophole figures those out by itself.

    It tests the SSH connection (auto-retrying with a jump-host bypass if the first try
    fails), writes ~/.loophole/config, opens the SSH tunnel, and checks that the
    loophole agent on the target answers. Returns a plain-language status to relay.

    Args:
        host_ip: the target machine's IP address, e.g. "192.168.1.x"
        username: the user's account name on that machine (the SSH login user)
        ssh_key: optional SSH private key path (blank = default key / ssh-agent)
        ssh_opts: optional extra ssh options (blank = none; auto-handled)
    """
    host_ip = (host_ip or "").strip()
    username = (username or "").strip()
    if not host_ip or not username:
        raise _AgentError("loophole_configure needs both host_ip and username "
                          "(ask the user for the machine's IP and their username)")
    target = f"{username}@{host_ip}"

    # 1) 実際に SSH して試す（踏み台回避の自動リトライ込み・専門設定は裏で）
    ok, working_opts, detail = _probe_ssh(target, (ssh_opts or "").strip())

    # 2) 設定ファイルを書く（次回起動でも効く）
    lines = ["# loophole 設定 — loophole_configure が自動生成", f"LOOPHOLE_SSH={target}"]
    if ssh_key.strip():
        lines.append(f"LOOPHOLE_SSH_KEY={ssh_key.strip()}")
    if working_opts:
        lines.append(f"LOOPHOLE_SSH_OPTS={working_opts}")
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as exc:
        raise _AgentError(f"設定ファイルを書けませんでした（{CONFIG_PATH}）: {exc}")

    # 3) 動作中プロセスにも反映（env 更新）
    os.environ["LOOPHOLE_SSH"] = target
    if ssh_key.strip():
        os.environ["LOOPHOLE_SSH_KEY"] = os.path.expanduser(ssh_key.strip())
    if working_opts:
        os.environ["LOOPHOLE_SSH_OPTS"] = working_opts
    else:
        os.environ.pop("LOOPHOLE_SSH_OPTS", None)

    if not ok:
        return (f"設定を {CONFIG_PATH} に保存しました（接続先 {target}）。ただし今はまだ SSH で"
                f"届きません。よくある原因: 対象 PC の電源が入っていない/スリープ、IP かユーザー名"
                f"が違う、その PC にまだ SSH 鍵が登録されていない。"
                + (f" 技術的な詳細: {detail}" if detail else ""))

    # 4) トンネルを張り直す（古いものがあれば畳んでから）
    _close_tunnel()
    if not _open_tunnel():
        return (f"設定を保存し SSH も通りましたが、トンネルを張れませんでした（{target}）。"
                f"少し待って再試行するか、ローカルのポート {PORT} が他で使われていないか確認を。")

    # 5) agent が応答するか
    try:
        r = _call("hello", timeout=15.0)
    except _AgentError:
        return (f"設定とトンネルは OK（接続先 {target}）。ただし loophole agent が応答しません。"
                f"対象 PC 側で loophole サーバーが対話セッションで動いているか確認してください。")
    return (f"設定完了（接続先 {target}）。接続 OK: user={r.get('user')} "
            f"session_id={r.get('session_id')} interactive={r.get('interactive')}。"
            f"これで loophole の各ツールが使えます。")


@mcp.tool()
def loophole_shell(command: str, encoding: str = "auto") -> str:
    """Run a one-line shell command on the agent host's shell and return its output.

    Uses the host shell: cmd.exe /S /C on Windows, /bin/sh -c on Linux.

    Args:
        command: the command line, e.g. "echo %USERNAME% & ver" (Windows) or "echo $USER; uname -a" (Linux)
        encoding: how to decode output bytes - "auto" (UTF-8 then CP932), "utf-8", or "cp932"
    """
    r = _call("run", {"command": command, "encoding": encoding}, via="loophole_shell")
    out = r.get("stdout", "")
    err = r.get("stderr", "")
    tail = f"\n[stderr]\n{err}" if err else ""
    return f"[exit {r.get('exit_code')}]\n{out}{tail}"


@mcp.tool()
def loophole_run(argv: list[str], encoding: str = "auto") -> str:
    """Run a program on the agent host WITHOUT a shell (argv list, no quoting pitfalls).

    Args:
        argv: argument vector, e.g. ["cmd", "/c", "dir"] - argv[0] is the program
        encoding: output decode strategy - "auto" / "utf-8" / "cp932"
    """
    r = _call("run", {"argv": argv, "encoding": encoding}, via="loophole_run")
    out = r.get("stdout", "")
    err = r.get("stderr", "")
    tail = f"\n[stderr]\n{err}" if err else ""
    return f"[exit {r.get('exit_code')}]\n{out}{tail}"


@mcp.tool()
def loophole_clipboard_get() -> str:
    """Read the agent host's clipboard (the desktop session's clipboard).

    Useful to retrieve a value a GUI app placed on the clipboard - e.g. a result
    the app copied - without keyboard input.
    """
    return _call("clipboard_get", via="loophole_clipboard_get").get("text", "")


@mcp.tool()
def loophole_clipboard_set(text: str) -> str:
    """Set the agent host's clipboard. The value can then be pasted into a GUI app.

    This bypasses keyboard/IME entirely (the original reason for the agent): set a
    value here, then paste it in the target app with right-click or Ctrl+V.

    Args:
        text: the text to place on the clipboard (UTF-8, may contain Japanese)
    """
    _call("clipboard_set", {"text": text}, via="loophole_clipboard_set")
    return "clipboard set"


@mcp.tool()
def loophole_screenshot() -> Image:
    """Capture the agent host's full desktop and return it as a PNG image.

    Lets you SEE the remote desktop - something a plain SSH shell (which has no
    desktop) cannot do. Use to verify GUI state (e.g. a dialog or a rendered window).
    """
    r = _call("screenshot", {"data": True}, timeout=60.0, via="loophole_screenshot")
    png = base64.b64decode(r["png_base64"])
    return Image(data=png, format="png")


@mcp.tool()
def loophole_gui(argv: list[str]) -> str:
    """Launch a GUI / long-running program on the agent host's desktop and return its PID.

    Because the agent lives in an interactive desktop session, the launched GUI is
    visible on screen (impossible from a plain non-interactive SSH shell). Returns
    immediately.

    Args:
        argv: program and arguments, e.g. on Windows
              ["C:/Program Files/Mozilla Firefox/firefox.exe", "https://example.com"]
              or on Linux ["firefox", "https://example.com"]
    """
    r = _call("spawn", {"argv": argv}, via="loophole_gui")
    return f"started pid={r.get('pid')}"


@mcp.tool()
def loophole_read_file(path: str, encoding: str = "auto") -> str:
    """Read a text file from the agent host and return its contents.

    Args:
        path: absolute path on the agent host, e.g. "C:/path/to/report.txt"
        encoding: decode strategy - "auto" / "utf-8" / "cp932"
    """
    return _call("read_file", {"path": path, "encoding": encoding}, via="loophole_read_file").get("text", "")


@mcp.tool()
def loophole_write_file(path: str, text: str) -> str:
    """Write a UTF-8 text file on the agent host (overwrites).

    Args:
        path: absolute path on the agent host
        text: file contents (UTF-8)
    """
    _call("write_file", {"path": path, "text": text}, via="loophole_write_file")
    return f"wrote {path}"


@mcp.tool()
def loophole_send_keys(keys: str | list[str]) -> str:
    """Send keyboard SHORTCUTS (key chords) to the agent host's foreground window.

    This is for shortcuts like Ctrl+S, Alt+F4, Win+R, Enter, Tab - NOT for typing
    text. To enter text (especially Japanese), use loophole_clipboard_set then paste
    it with Ctrl+V via this tool, because typed characters go through the IME and garble.

    Each stroke is "mod+...+key": modifiers are ctrl/alt/shift/win and the last token
    is the main key (a-z, 0-9, f1-f24, enter, tab, esc, space, up/down/left/right,
    home/end, pageup/pagedown, delete, insert, ...). Case-insensitive.

    Args:
        keys: one stroke ("ctrl+s"), several space-separated ("win+r enter"), or a
              list of strokes (["win+r", "enter"]). Sent left to right.
    """
    r = _call("send_keys", {"keys": keys}, via="loophole_send_keys")
    sent = r.get("sent", [])
    return f"sent {r.get('count', 0)} stroke(s): {' '.join(sent)}"


@mcp.tool()
def loophole_mouse(action: str, x: int | None = None, y: int | None = None,
                   button: str = "left", count: int = 1, dx: int = 0, dy: int = 0) -> str:
    """Move, click, or scroll the mouse on the agent host's desktop (absolute coords).

    Coordinates are absolute screen pixels in the same coordinate system as
    loophole_screenshot. Use this for the simple pointer actions loophole can do
    without computer use; for complex drag/visual targeting, prefer computer use.

    Args:
        action: "move" (to x,y), "click" (button at x,y if given; count=2 for double),
                or "scroll" (by dx/dy wheel clicks)
        x, y: target coordinates (required for "move"; optional for "click")
        button: "left" (default), "middle", or "right" (for "click")
        count: number of clicks (1 default; 2 = double-click)
        dx, dy: wheel clicks for "scroll" — dy>0 scrolls down, dx>0 scrolls right
    """
    if action == "move":
        if x is None or y is None:
            raise _AgentError("loophole_mouse(action='move') requires x and y")
        r = _call("mouse_move", {"x": x, "y": y}, via="loophole_mouse")
        return f"moved to ({r.get('x')}, {r.get('y')})"
    if action == "click":
        args: dict = {"button": button, "count": count}
        if x is not None and y is not None:
            args["x"], args["y"] = x, y
        r = _call("mouse_click", args, via="loophole_mouse")
        where = f" at ({x}, {y})" if x is not None and y is not None else ""
        return f"{r.get('button')} click x{r.get('clicked')}{where}"
    if action == "scroll":
        r = _call("mouse_scroll", {"dx": dx, "dy": dy}, via="loophole_mouse")
        return f"scrolled dx={r.get('dx')} dy={r.get('dy')}"
    raise _AgentError("loophole_mouse 'action' must be 'move', 'click', or 'scroll'")


@mcp.tool()
def loophole_find_files(root: str, pattern: str, match: str = "glob",
                        max_results: int = 200, max_depth: int | None = None,
                        include_dirs: bool = False) -> str:
    """Search for files by name under a directory on the agent host (no GUI needed).

    More structured than `dir /s`: returns path + size + mtime, handles Unicode paths,
    and caps the result count.

    Args:
        root: directory to search from, e.g. "C:/Users/me/Documents"
        pattern: a glob like "*.txt" (match="glob"), or a case-insensitive substring
                 (match="substring")
        match: "glob" (default) or "substring"
        max_results: cap on returned matches (default 200); if reached, output says truncated
        max_depth: how deep below root to descend (0 = root only); None = unlimited
        include_dirs: also match directory names (default False = files only)
    """
    args: dict = {"root": root, "pattern": pattern, "match": match,
                  "max_results": max_results, "include_dirs": include_dirs}
    if max_depth is not None:
        args["max_depth"] = max_depth
    r = _call("find_files", args, via="loophole_find_files")
    matches = r.get("matches", [])
    if not matches:
        return f"no matches for {pattern!r} under {root} (scanned {r.get('scanned', 0)} names)"
    lines = []
    for m in matches:
        size = m.get("size", -1)
        try:
            when = datetime.datetime.fromtimestamp(m.get("mtime", 0.0)).strftime("%Y-%m-%d %H:%M")
        except (OverflowError, OSError, ValueError):
            when = "?"
        lines.append(f"{m.get('path')}\t{size} bytes\t{when}")
    head = f"{len(matches)} match(es)" + (" (truncated)" if r.get("truncated") else "")
    return head + "\n" + "\n".join(lines)


def _format_ime(r: dict) -> str:
    """ime_get / ime_set の result を 1 行に整形する（両ツール共通）。"""
    if not r.get("supported"):
        return ("no IME available here (Windows: classic Win32 windows expose one, some "
                "UWP/Electron don't; Linux: needs fcitx5 or ibus running)")
    state = "on (Japanese input)" if r.get("open") else "off (direct input)"
    mode = r.get("mode") or f"raw conversion {r.get('conversion')}"
    roman = "roman" if r.get("roman") else "kana"
    return f"IME {state}; mode={mode}; input={roman}"


@mcp.tool()
def loophole_ime_get() -> str:
    """Read the IME (Input Method Editor) state of the agent host's foreground window.

    Check this before sending keystrokes over a remote session (RDP/VNC, or X11/Wayland):
    if the IME is ON (Japanese input mode), ASCII you type gets swallowed as phonetic
    reading and garbles. Turn it off with loophole_ime_set(open=False) first, then type.

    Reports whether an IME is available, whether it's open (on/off), the conversion mode
    (hiragana/katakana/alphanumeric...), and roman vs kana input. On Windows this reads
    the foreground window's IME; on Linux it reads fcitx5/ibus (only open is meaningful
    there - mode/roman are Windows-only).
    """
    return _format_ime(_call("ime_get", via="loophole_ime_get"))


@mcp.tool()
def loophole_ime_set(open: bool | None = None, mode: str | None = None,
                     roman: bool | None = None, conversion: int | None = None) -> str:
    """Change the foreground window's IME state. Axes left unset are not touched.

    Primary use: loophole_ime_set(open=False) forces DIRECT INPUT before you send
    keystrokes over a remote session, so typed ASCII isn't eaten by the Japanese IME.
    (For entering Japanese TEXT, prefer loophole_clipboard_set + paste; this tool sets
    the input MODE, not the text.)

    Args:
        open: True = IME on (Japanese input), False = off (direct input). Most useful.
        mode: conversion mode when on - "hiragana", "katakana", "katakana-half",
              "alphanumeric", "alphanumeric-full".
        roman: True = roman (romaji) input, False = kana input.
        conversion: raw conversion bitfield (power users; overrides mode/roman).

    Windows: classic Win32 windows (Notepad, browser address bars, most editors) honor
    this; some UWP / Electron apps ignore WM_IME_CONTROL and the call reports an error.
    Linux: drives fcitx5/ibus, where only `open` (on/off) applies - mode/roman/conversion
    are Windows-only and a mode-only call there will report it can't be set.
    """
    args: dict = {}
    if open is not None:
        args["open"] = open
    if mode is not None:
        args["mode"] = mode
    if roman is not None:
        args["roman"] = roman
    if conversion is not None:
        args["conversion"] = conversion
    if not args:
        raise _AgentError(
            "loophole_ime_set needs at least one of open, mode, roman, conversion")
    return _format_ime(_call("ime_set", args, via="loophole_ime_set"))


def _clean_menu_label(node: dict) -> str:
    """handler が計算した path の末尾（= 正規化済みラベル）を表示用に取り出す。"""
    path = node.get("path", "")
    return path.split(" > ")[-1] if path else node.get("label", "")


def _render_menu(items: list, depth: int = 0) -> list:
    """メニューツリーをインデント付きの読みやすい行リストに整形する（再帰）。"""
    out: list = []
    pad = "  " * depth
    for it in items:
        if it.get("separator"):
            out.append(f"{pad}  ---")
            continue
        label = _clean_menu_label(it)
        cid = it.get("command_id")
        flags = []
        if not it.get("enabled", True):
            flags.append("disabled")
        if it.get("checked"):
            flags.append("checked")
        if it.get("destructive_guess"):
            flags.append("⚠destructive?")
        suffix = f"  [id={cid}]" if cid is not None else ""
        if flags:
            suffix += "  (" + ", ".join(flags) + ")"
        out.append(f"{pad}- {label}{suffix}")
        if it.get("submenu"):
            out.extend(_render_menu(it["submenu"], depth + 1))
    return out


def _menu_ambiguous(r: dict) -> str | None:
    """曖昧（複数該当）応答なら案内文字列を、そうでなければ None を返す。"""
    if not r.get("ambiguous"):
        return None
    lines = [f"  hwnd={c.get('hwnd')}  {c.get('title')!r}" for c in r.get("candidates", [])]
    return "ambiguous title; re-call with hwnd= of the right window:\n" + "\n".join(lines)


@mcp.tool()
def loophole_menu(action: str, title: str | None = None,
                  hwnd: int | None = None, command_id: int | None = None) -> str:
    """Enumerate or invoke an app's menu bar (blind - no screenshot).

    This drives GUI menus WITHOUT navigating them by keyboard or mouse: list gives you
    every item's command_id, and invoke fires one directly (Windows: PostMessage of
    WM_COMMAND; Linux: AT-SPI Action). Ideal for brute-forcing/regression-testing an
    app's menus deterministically.

    action="list": dump the menu tree of the window matched by title/hwnd. Each
        invokable item shows [id=N]; submenu headers have no id; ⚠destructive? marks
        items whose label looks dangerous (Exit/Delete/... - skip these unless intended).
    action="invoke": fire the command command_id. Pass the hwnd that "list" returned
        (avoids title ambiguity). Invocation is fire-and-observe: it confirms the command
        was sent, not that it finished - observe the result yourself (app log,
        loophole_window list for new dialogs, or re-list to see a toggle flip).

    Windows: classic Win32 menu bars (Notepad, many native apps, FileMaker); Ribbon
    (Office) / Electron / UWP report "no menu bar". Linux: any app exposing accessibility
    (AT-SPI) - the menu targets the active app; apps without a11y report "no menu bar".
    Where unsupported, fall back to screenshots + mouse/keyboard.

    Args:
        action: "list" or "invoke"
        title: window title substring (case-insensitive); ambiguous matches are reported, not acted on
        hwnd: window handle (preferred for invoke; take it from a prior list)
        command_id: the menu command id to fire (required for action="invoke")
    """
    if action not in ("list", "invoke"):
        raise _AgentError("loophole_menu 'action' must be 'list' or 'invoke'")
    target: dict = {}
    if hwnd is not None:
        target["hwnd"] = hwnd
    if title is not None:
        target["title"] = title
    if not target:
        raise _AgentError("loophole_menu needs 'title' or 'hwnd'")

    if action == "list":
        r = _call("menu_enumerate", target, via="loophole_menu")
        amb = _menu_ambiguous(r)
        if amb:
            return amb
        if not r.get("supported"):
            return (f"window hwnd={r.get('hwnd')} exposes no menu bar "
                    f"(Windows ribbon/Electron/UWP, or a Linux app without AT-SPI "
                    f"accessibility). Fall back to a screenshot + mouse/keyboard.")
        body = "\n".join(_render_menu(r.get("items", []))) or "(empty menu)"
        return f"menu of hwnd={r.get('hwnd')} {r.get('title')!r}:\n{body}"

    if command_id is None:
        raise _AgentError("loophole_menu(action='invoke') requires command_id")
    target["command_id"] = command_id
    r = _call("menu_invoke", target, via="loophole_menu")
    amb = _menu_ambiguous(r)
    if amb:
        return amb
    return f"posted command_id={r.get('command_id')} to hwnd={r.get('hwnd')}"


# --- 対話セットアップ（uv run --script mcp_server.py --setup） ----------------
# 文脈ゼロの他人のところでも自力で完結するための入口。賢い AI を当てにせず、成果物自身が
# ユーザー本人に「ふつうの言葉で」必要な事実を聞く。専門概念（鍵・ProxyJump 等）は本人に
# 聞かず裏で処理し、繋がらない時だけ平易な言葉で原因を出す。設定の保存と Claude への登録まで
# やる。リポジトリだけ落とせば誰でも同じに動く。

def _setup_ask(prompt: str) -> str:
    """1 行の自由入力を聞く。"""
    try:
        return input(f"{prompt}: ").strip()
    except EOFError:
        return ""


def _setup_yesno(prompt: str, default_yes: bool = True) -> bool:
    """はい / いいえ を聞く。Enter のみなら既定値。"""
    tag = "（はい / いいえ、未入力なら はい）" if default_yes else "（はい / いいえ、未入力なら いいえ）"
    try:
        val = input(f"{prompt}{tag}: ").strip().lower()
    except EOFError:
        val = ""
    if not val:
        return default_yes
    return val in ("y", "yes", "は", "はい")


def _setup_try_ssh(target: str) -> tuple[bool, str]:
    """対話セットアップ用: _probe_ssh を呼び、結果を画面に出す。(成否, 効いた opts) を返す。"""
    print("  接続を試しています…")
    ok, opts, detail = _probe_ssh(target, "")
    if ok and not opts:
        print("  → つながりました。")
    elif ok:
        print("  → つながりました（VPN・踏み台を回避する設定を自動で追加しました）。")
    else:
        print("  → つながりませんでした。よくある原因:")
        print("     ・対象の PC の電源が入っていない / スリープしている")
        print("     ・IP アドレスかユーザー名が違う")
        print("     ・その PC にまだ SSH の鍵が登録されていない")
        if detail:
            print("     （技術的な詳細）" + detail.replace("\n", "\n     "))
    return ok, opts


def _setup_register(self_cmd: str) -> None:
    """Claude に登録する。claude コマンドが無ければ手で貼る用の指示を出すだけ。"""
    cmd = ["claude", "mcp", "add", "loophole", "--", self_cmd]
    pretty = " ".join(shlex.quote(c) for c in cmd)
    if shutil.which("claude"):
        if _setup_yesno("\nこの PC の Claude に loophole を登録しますか?", True):
            if subprocess.run(cmd).returncode == 0:
                print("  → 登録しました（反映には Claude Code の再起動が必要です）。")
            else:
                print("  → 登録に失敗しました（すでに登録済みかもしれません）。")
                print("     入れ直すには:  claude mcp remove loophole")
                print("     そのあと:      " + pretty)
        return
    print("\nこの PC に 'claude' コマンドが見つかりません。手で登録してください:")
    print("  Claude Code の場合:    " + pretty)
    print("  Claude Desktop の場合:  claude_desktop_config.json の \"mcpServers\" にこれを足す:")
    print(f'      "loophole": {{ "command": "{self_cmd}" }}')


def _run_setup() -> None:
    # 登録に使うコマンド名。インストール済みなら PATH の "loophole"、そうでなければ
    # 今このプロセスを起動した実行ファイルの絶対パスを使う。
    self_cmd = "loophole" if shutil.which("loophole") else os.path.realpath(sys.argv[0])
    if not sys.stdin.isatty():
        print("セットアップは対話式です。ターミナルで次を実行してください:\n"
              "  loophole --setup", file=sys.stderr)
        raise SystemExit(2)

    print("\n=== loophole セットアップ ===")
    print("この PC と、操作したい PC（Windows / Linux）をつなぎます。いくつか質問するので答えてください。")
    print("（設定の保存と Claude への登録まで、これが全部やります）\n")

    if os.path.exists(CONFIG_PATH) and os.environ.get("LOOPHOLE_SSH"):
        print(f"すでに設定があります（{CONFIG_PATH}：接続先 {os.environ['LOOPHOLE_SSH']}）。")
        if not _setup_yesno("上書きして設定し直しますか?", default_yes=False):
            print("今の設定をそのまま使います。登録だけ確認します。\n")
            _setup_register(self_cmd)
            return

    ip = _setup_ask("操作したい PC の IP アドレス（例: 192.168.1.x。Windows なら "
                    "設定→ネットワークとインターネット、Linux なら `ip a` で確認できます）")
    while not ip:
        ip = _setup_ask("IP アドレスを入力してください（例: 192.168.1.x）")
    user = _setup_ask("その PC での あなたのユーザー名（サインインに使う名前）")
    while not user:
        user = _setup_ask("ユーザー名を入力してください")
    target = f"{user}@{ip}"

    # 接続テスト（おすすめ）。踏み台回避が要れば自動で見つけ、その設定を残す。
    opts = ""
    if _setup_yesno("\n今すぐ接続を試しますか?（おすすめ）", default_yes=True):
        _ok, opts = _setup_try_ssh(target)

    lines = ["# loophole 設定 — セットアップが自動生成", f"LOOPHOLE_SSH={target}"]
    if opts:
        lines.append(f"LOOPHOLE_SSH_OPTS={opts}")
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as exc:
        print(f"\n設定ファイルを書けませんでした（{CONFIG_PATH}）: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print(f"\n設定を保存しました: {CONFIG_PATH}")

    _setup_register(self_cmd)

    print("\n完了です。Claude Code を一度終了して開き直してから、")
    print("「loophole で接続できるか確認して」と頼んでください。")
    print("（対象の PC が寝ているときは、先に起こして loophole を動かしておいてください）")


def main() -> None:
    """entry point（pyproject の [project.scripts] loophole = …:main）。

    `loophole --setup` なら対話セットアップ。引数なしなら MCP サーバーとして起動する。
    """
    if "--setup" in sys.argv:
        _run_setup()
    else:
        _open_tunnel()  # LOOPHOLE_SSH があれば ssh -L を内部で張る（無ければ何もしない）
        try:
            mcp.run()
        finally:
            _close_tunnel()


if __name__ == "__main__":
    main()
