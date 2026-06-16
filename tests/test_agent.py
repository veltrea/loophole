"""agent.py のリクエスト処理ロジックの単体テスト（Mac / Windows どちらでも実行可）。

ソケットは使わず handle_request を直接叩く。ハンドラはフェイク注入。

    python3 tests/test_agent.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agent  # noqa: E402
import handlers  # noqa: E402
from handlers import ProcessResult  # noqa: E402
from fakes import (FakeRunner, FakeClipboard, FakeScreenshotter, FakeFS, FakeKeyboard,  # noqa: E402
                   FakeEnv, FakeWindowManager, FakeIme, FakeMenuController)

failures = 0


def check(cond, label):
    global failures
    if cond:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label}")
        failures += 1


def check_eq(actual, expected, label):
    global failures
    if actual == expected:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label}\n         expected={expected!r}\n         actual  ={actual!r}")
        failures += 1


def make_agent(token=None):
    runner = FakeRunner()
    h = handlers.Handlers(runner, FakeClipboard(), FakeScreenshotter(), FakeFS(), FakeEnv(),
                          FakeKeyboard(), FakeWindowManager(), FakeIme(), FakeMenuController())
    return agent.Agent(h, token=token), runner


print("handle_request (happy path):")
a, runner = make_agent()
resp = a.handle_request({"id": 1, "cmd": "ping"})
check_eq(resp, {"id": 1, "ok": True, "result": {"pong": True}}, "ping ok envelope, id echoed")

runner.next_result = ProcessResult(0, b"hi", b"")
resp = a.handle_request({"id": "abc", "cmd": "run", "args": {"argv": ["echo", "hi"]}})
check(resp["ok"] and resp["result"]["stdout"] == "hi", "run returns decoded stdout")
check_eq(resp["id"], "abc", "string id echoed")

print("handle_request (errors are envelopes, never exceptions):")
resp = a.handle_request({"id": 2, "cmd": "does_not_exist"})
check(resp["ok"] is False and "unknown command" in resp["error"], "unknown command -> error envelope")
resp = a.handle_request({"id": 3, "cmd": "run", "args": {}})
check(resp["ok"] is False and "argv" in resp["error"], "bad args -> error envelope")
resp = a.handle_request({"id": 4})  # cmd 欠落
check(resp["ok"] is False and "bad request" in resp["error"], "missing cmd -> bad request")

print("token auth:")
a, runner = make_agent(token="s3cret")
# ping/hello はトークン不要
check(a.handle_request({"id": 1, "cmd": "ping"})["ok"], "ping exempt from token")
# それ以外はトークン必須
resp = a.handle_request({"id": 2, "cmd": "clipboard_get", "args": {}})
check(resp["ok"] is False and "unauthorized" in resp["error"], "missing token rejected")
resp = a.handle_request({"id": 3, "cmd": "clipboard_get", "args": {"token": "wrong"}})
check(resp["ok"] is False, "wrong token rejected")
resp = a.handle_request({"id": 4, "cmd": "clipboard_get", "args": {"token": "s3cret"}})
check(resp["ok"] is True, "correct token accepted")

print("history recording (opt-in via injected History):")
from history import History  # noqa: E402

clock = {"t": 5000.0}
hist = History(clock=lambda: clock["t"])
runner2 = FakeRunner()
h2 = handlers.Handlers(runner2, FakeClipboard(), FakeScreenshotter(), FakeFS(), FakeEnv(),
                       FakeKeyboard(), FakeWindowManager(), FakeIme(), FakeMenuController())
a2 = agent.Agent(h2, history=hist)

a2.handle_request({"id": 1, "cmd": "ping"})            # プローブは記録しない
a2.handle_request({"id": 2, "cmd": "hello"})           # 同上
runner2.next_result = ProcessResult(0, b"ok", b"")
a2.handle_request({"id": 3, "cmd": "run",
                   "args": {"argv": ["cmd", "/c", "ver"], "via": "loophole_run"}})
a2.handle_request({"id": 4, "cmd": "run", "args": {}})  # bad args -> ok=False でも記録

ents = hist.entries()
check_eq([e["cmd"] for e in ents], ["run", "run"], "ping/hello excluded; runs recorded")
check_eq(ents[0]["via"], "loophole_run", "via label captured from request")
check_eq(ents[0]["target"], "cmd /c ver", "target summarizes the command")
check_eq(ents[0]["ok"], True, "successful run recorded ok=True")
check_eq(ents[1]["ok"], False, "failed run still recorded, ok=False")

# history を渡さなければ一切記録しない（既定 = 完全自動開発でゼロ負荷）
a3, _ = make_agent()
a3.handle_request({"id": 1, "cmd": "run", "args": {"argv": ["x"]}})
check(getattr(a3, "_history", "missing") is None, "no history injected -> recording disabled")

print(f"\n{'ALL PASS' if failures == 0 else 'SOME FAILED'} ({failures} failure(s))")
sys.exit(0 if failures == 0 else 1)
