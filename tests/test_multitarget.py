"""test_multitarget.py — マルチターゲット統合（configure(name) / 起動時解決 / status）。

uv 経由（mcp 依存）:  uv run python tests/test_multitarget.py
本物の ~/.loophole は触らない（import 前に LOOPHOLE_REGISTRY を temp へ向ける）。
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmp = tempfile.mkdtemp(prefix="loophole-mt-")
os.environ["LOOPHOLE_REGISTRY"] = os.path.join(_tmp, "registry.json")
for _k in ("LOOPHOLE_TARGET", "LOOPHOLE_SSH", "LOOPHOLE_PORT", "LOOPHOLE_REMOTE_PORT",
           "LOOPHOLE_SSH_KEY", "LOOPHOLE_SSH_OPTS", "LOOPHOLE_ACTIVE_TARGET"):
    os.environ.pop(_k, None)

from loophole import mcp_server as m  # noqa: E402
from loophole import registry  # noqa: E402

failures = 0


def check(cond, label):
    global failures
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond:
        failures += 1


regpath = os.environ["LOOPHOLE_REGISTRY"]

# 外部 I/O はモック: SSH 疎通は成功扱い、トンネルは張らない。
m._probe_ssh = lambda target, opts="", key="": (True, opts, "")  # 効いた opts を返す（実挙動）
m._open_tunnel = lambda: True
m._close_tunnel = lambda: None

print("loophole_configure(name=...) 登録:")
out = m.loophole_configure("192.168.1.x", "user", name="winpc")
reg = registry.load(regpath)
check("winpc" in reg["targets"], "configure(name=winpc) -> winpc を登録")
check(reg["targets"]["winpc"]["local_port"] == 9999, "1個目 winpc -> 手元ポート 9999")
check(reg["default_target"] == "winpc", "winpc が default_target")

out = m.loophole_configure("192.168.1.x", "user", name="linux1", ssh_opts="-o ProxyJump=none")
reg = registry.load(regpath)
check(reg["targets"]["linux1"]["local_port"] == 10000, "2個目 linux1 -> 手元ポート 10000")
check(reg["targets"]["linux1"]["ssh"] == "user@192.168.1.x", "ssh が記録される")
check("10000" in out, "戻り値に割当ポートを明示")

print("起動時解決（_apply_target_from_registry）:")
for _k in ("LOOPHOLE_SSH", "LOOPHOLE_PORT", "LOOPHOLE_REMOTE_PORT",
           "LOOPHOLE_SSH_OPTS", "LOOPHOLE_ACTIVE_TARGET"):
    os.environ.pop(_k, None)
os.environ["LOOPHOLE_TARGET"] = "linux1"
m._apply_target_from_registry()
check(os.environ.get("LOOPHOLE_SSH") == "user@192.168.1.x", "TARGET=linux1 -> SSH 解決")
check(os.environ.get("LOOPHOLE_PORT") == "10000", "手元ポート 10000 を env へ")
check(os.environ.get("LOOPHOLE_REMOTE_PORT") == "9999", "リモートは 9999 固定")
check(os.environ.get("LOOPHOLE_SSH_OPTS") == "-o ProxyJump=none", "ssh_opts も反映")

print("loophole_status:")
st = m.loophole_status()
check("registered_targets=" in st and "winpc" in st and "linux1" in st, "status に登録ターゲット一覧")
check("active_target=linux1" in st, "status に現ターゲット名")

shutil.rmtree(_tmp, ignore_errors=True)
print()
if failures:
    print(f"FAILED: {failures} failure(s)")
    sys.exit(1)
print("ALL PASS (0 failure(s))")
