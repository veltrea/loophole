"""agent.py — loophole サーバー本体（対象 Windows の対話デスクトップセッションで常駐）。

127.0.0.1 のみに bind し、外からは既存 SSH のポートフォワード（ssh -L）で届く。
これにより LAN へ新しい口を開けず、認証は SSH に丸投げできる（エージェント自身は
認証なしでよい。任意で共有トークンを付けられる）。

プロトコルは JSONL（1 行 1 メッセージ、Content-Length なし）。各接続を 1 スレッドで
処理し、行ごとに Handlers.dispatch して結果を返す。

起動:
    python agent.py                 # 127.0.0.1:9999
    python agent.py --port 9000
    python agent.py --token SECRET   # hello 以外に {"token": "..."} を要求
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
from typing import Any, Dict, Optional

import protocol
from handlers import HandlerError, Handlers


class Agent:
    def __init__(self, handlers: Handlers, token: Optional[str] = None, history=None):
        self._handlers = handlers
        self._token = token
        # history は opt-in（ライブビュー有効時のみ注入）。None なら一切記録しない。
        self._history = history

    def handle_request(self, obj: Dict[str, Any]) -> Dict[str, Any]:
        """1 リクエスト辞書を処理して 1 レスポンス辞書を返す。

        ソケットに依存しないので単体テストできる（tests/test_agent.py）。
        """
        try:
            request_id, cmd, args = protocol.parse_request(obj)
        except protocol.ProtocolError as exc:
            return protocol.make_error(obj.get("id"), f"bad request: {exc}")

        # トークン認証（設定時のみ）。ping/hello だけは疎通確認のため免除する。
        if self._token is not None and cmd not in ("ping", "hello"):
            if args.get("token") != self._token:
                return protocol.make_error(request_id, "unauthorized: bad or missing token")

        try:
            result = self._handlers.dispatch(cmd, args)
            response = protocol.make_ok(request_id, result)
        except HandlerError as exc:
            response = protocol.make_error(request_id, str(exc))
        except Exception as exc:  # ハンドラ内の想定外もクライアントへ返す（落とさない）
            response = protocol.make_error(request_id, f"internal error: {type(exc).__name__}: {exc}")

        # 実行コマンドを履歴に記録（ping/hello は疎通プローブなので除外）。
        if self._history is not None and cmd not in ("ping", "hello"):
            self._history.record(cmd, args, ok=bool(response.get("ok")))
        return response


# 行が異常に長い（= 壊れた送信元）場合に備えた上限。1 行 16 MiB。
_MAX_PENDING = 16 * 1024 * 1024


def _serve_connection(agent: Agent, conn: socket.socket, addr) -> None:
    buffer = protocol.LineBuffer()
    with conn:
        conn_file_closed = False
        while not conn_file_closed:
            try:
                data = conn.recv(65536)
            except OSError:
                break
            if not data:
                break
            for line in buffer.push(data):
                try:
                    obj = protocol.decode_message(line)
                    response = agent.handle_request(obj)
                except protocol.ProtocolError as exc:
                    response = protocol.make_error(None, f"protocol error: {exc}")
                try:
                    conn.sendall(protocol.encode_message(response))
                except OSError:
                    conn_file_closed = True
                    break
            if buffer.pending > _MAX_PENDING:
                try:
                    conn.sendall(protocol.encode_message(
                        protocol.make_error(None, "line too long; closing")))
                finally:
                    break


def serve_forever(agent: Agent, host: str, port: int) -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(16)
    # 起動・状態ログは必ず stderr へ。stdout は（mcp_server.py 経由で）MCP の
    # JSONL チャネルとして使われうるため、平文を一切混ぜない（誤起動時の保険）。
    print(f"loophole listening on {host}:{port}", file=sys.stderr, flush=True)
    try:
        while True:
            conn, addr = server.accept()
            thread = threading.Thread(target=_serve_connection, args=(agent, conn, addr), daemon=True)
            thread.start()
    except KeyboardInterrupt:
        print("loophole shutting down", file=sys.stderr, flush=True)
    finally:
        server.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="loophole: interactive-session command server")
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address (default 127.0.0.1; keep loopback-only)")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--token", default=None,
                        help="optional shared token required on all commands except ping/hello")
    parser.add_argument("--view-port", type=int, default=None,
                        help="optional read-only live view (MJPEG over HTTP) on this port; "
                             "off by default. Watch with a browser via ssh -L.")
    parser.add_argument("--view-fps", type=float, default=2.0,
                        help="live view frame rate when --view-port is set (default 2)")
    args = parser.parse_args(argv)

    if args.host not in ("127.0.0.1", "::1", "localhost"):
        print(f"WARNING: binding to {args.host} exposes the agent beyond loopback. "
              "Prefer 127.0.0.1 + ssh -L.", file=sys.stderr, flush=True)

    # --view-port 指定時だけ履歴を取り、read-only ライブビューアを別スレッドで起動する。
    # 付けなければ history=None ＝記録もビューアも一切なし（完全自動開発はゼロ負荷・無表示）。
    history = None
    if args.view_port:
        from history import History
        history = History()

    # 実 OS バックエンドでハンドラを組む（Windows なら Win32 API を ctypes で直叩き）
    from win_backends import build_handlers
    agent = Agent(build_handlers(), token=args.token, history=history)

    if args.view_port:
        import viewer
        from win_backends import build_screenshotter
        view_thread = threading.Thread(
            target=viewer.serve_view,
            args=(build_screenshotter(), args.host, args.view_port, args.view_fps, history),
            daemon=True)
        view_thread.start()
        print(f"loophole live view on http://{args.host}:{args.view_port}  "
              f"(/ = screen, /log = command history;  "
              f"forward: ssh -L {args.view_port}:127.0.0.1:{args.view_port})",
              file=sys.stderr, flush=True)

    serve_forever(agent, args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
