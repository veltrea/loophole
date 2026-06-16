"""test_e2e_loopback.py — 実ソケットで agent ⇄ Client を通す結合テスト。

実際に 127.0.0.1 でサーバーを立て、Client（loophole の中身）で叩く。run / read_file /
write_file は POSIX バックエンドが動くので Mac でも検証できる（clipboard/screenshot は
Windows 専用なのでここでは触らない）。

    python3 tests/test_e2e_loopback.py
"""

import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent  # noqa: E402
from win_backends import build_handlers  # noqa: E402
from loophole.cli import Client  # noqa: E402

failures = 0


def check(cond, label):
    global failures
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond:
        failures += 1


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


port = free_port()
srv = agent.Agent(build_handlers(), token=None)
thread = threading.Thread(target=agent.serve_forever, args=(srv, "127.0.0.1", port), daemon=True)
thread.start()
time.sleep(0.3)  # listen 確立を待つ

client = Client("127.0.0.1", port, token=None)

print("ping over a real socket:")
resp = client.call("ping")
check(resp.get("ok") and resp["result"] == {"pong": True}, "ping round-trips over TCP")

print("hello reports a real environment:")
resp = client.call("hello")
check(resp["ok"] and "platform" in resp["result"], "hello returns platform info")

print("run executes a real process:")
# OS 非依存な argv を使う（python 自身でエコー）
resp = client.call("run", {"argv": [sys.executable, "-c", "print('loophole_e2e_ok')"]})
check(resp["ok"], "run succeeds")
check(resp["result"]["exit_code"] == 0, "exit code 0")
check(resp["result"]["stdout"].strip() == "loophole_e2e_ok", "stdout captured and decoded")

print("write_file then read_file round-trip (dame-moji):")
tmp = os.path.join("/tmp", "loophole_agent_e2e.txt")
resp = client.call("write_file", {"path": tmp, "text": "表予能 ソ"})
check(resp["ok"], "write_file ok")
resp = client.call("read_file", {"path": tmp})
check(resp["ok"] and resp["result"]["text"] == "表予能 ソ", "read_file returns same text")
os.path.exists(tmp) and os.remove(tmp)

print("two requests on one connection are independent:")
# 別接続だが id が混ざらないこと
r1 = client.call("run", {"argv": [sys.executable, "-c", "print(1)"]})
r2 = client.call("run", {"argv": [sys.executable, "-c", "print(2)"]})
check(r1["result"]["stdout"].strip() == "1" and r2["result"]["stdout"].strip() == "2",
      "sequential calls don't cross results")

print(f"\n{'ALL PASS' if failures == 0 else 'SOME FAILED'} ({failures} failure(s))")
sys.exit(0 if failures == 0 else 1)
