"""test_tunnel.py — ssh -L argv 組み立て（_tunnel_argv）の純関数テスト。

複数マシン同時利用の核＝「ローカルだけ別ポート・リモートは 9999 固定」が効くことを検証する。
mcp パッケージが要るので uv 経由:
    uv run python tests/test_tunnel.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loophole import mcp_server  # noqa: E402

failures = 0


def check(cond, label):
    global failures
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond:
        failures += 1


ta = mcp_server._tunnel_argv


def dash_L(argv):
    return argv[argv.index("-L") + 1]


print("_tunnel_argv:")
# 既定（両端同じ）= 従来挙動の互換
argv = ta("user@host", 9999, 9999)
check(dash_L(argv) == "9999:127.0.0.1:9999", "両端 9999 -> -L 9999:127.0.0.1:9999（従来互換）")
check(argv[0] == "ssh" and argv[-1] == "user@host", "ssh ... <接続先> の形")
check("-N" in argv, "-N（転送専用）")

# 複数マシン: 手元だけ別ポート・リモートは 9999 固定（agent 無改修）
argv = ta("user@host", 10000, 9999)
check(dash_L(argv) == "10000:127.0.0.1:9999",
      "ローカル 10000・リモート 9999 -> -L 10000:127.0.0.1:9999（agent 無改修で複数マシン可）")

# 鍵・SSH ポート・追加オプション
argv = ta("user@host", 10001, 9999, ssh_port="2222",
          key="~/.ssh/id_ed25519", extra="-o ProxyJump=none")
check("-p" in argv and argv[argv.index("-p") + 1] == "2222", "ssh_port -> -p 2222")
check("-i" in argv and argv[argv.index("-i") + 1] == os.path.expanduser("~/.ssh/id_ed25519"),
      "key -> -i <展開済みパス>")
check("ProxyJump=none" in argv, "extra が展開されて argv に入る")
check(dash_L(argv) == "10001:127.0.0.1:9999", "鍵等を付けても -L のポート対応は維持")

# 後方互換: LOOPHOLE_REMOTE_PORT 未指定なら REMOTE_PORT は PORT と一致
if not os.environ.get("LOOPHOLE_REMOTE_PORT"):
    check(mcp_server.REMOTE_PORT == mcp_server.PORT,
          "REMOTE_PORT 既定 = PORT（明示しなければ従来どおり両端同じ）")

print()
if failures:
    print(f"FAILED: {failures} failure(s)")
    sys.exit(1)
print("ALL PASS (0 failure(s))")
