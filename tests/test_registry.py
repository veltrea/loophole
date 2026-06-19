"""test_registry.py — マルチターゲットレジストリ（registry.py）の単体テスト。

stdlib のみ。tmp ファイルを使い、本物の ~/.loophole/registry.json は汚さない。
    python3 tests/test_registry.py
"""

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "loophole"))

import registry  # noqa: E402

failures = 0


def check(cond, label):
    global failures
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond:
        failures += 1


tmpdir = tempfile.mkdtemp(prefix="loophole-reg-test-")
path = os.path.join(tmpdir, "registry.json")

print("空ロード:")
reg = registry.load(path)
check(reg == {"default_target": None, "targets": {}}, "存在しないパス -> 空レジストリ")

print("add_target 採番:")
registry.add_target(reg, "winpc", "user@hostA")
check(reg["targets"]["winpc"]["local_port"] == 9999, "1個目 -> local_port 9999")
check(reg["default_target"] == "winpc", "1個目が default_target になる")
check(reg["targets"]["winpc"]["remote_port"] == 9999, "remote_port 既定 9999")
registry.add_target(reg, "linux1", "user@hostB")
check(reg["targets"]["linux1"]["local_port"] == 10000, "2個目 -> 10000")
registry.add_target(reg, "linux2", "user@hostC")
check(reg["targets"]["linux2"]["local_port"] == 10001, "3個目 -> 10001")
check(reg["default_target"] == "winpc", "default は最初のまま")

print("冪等な local_port:")
registry.add_target(reg, "linux1", "user@hostB2")  # 同名再追加（ssh のみ変更）
check(reg["targets"]["linux1"]["local_port"] == 10000, "同名再追加で local_port は不変")
check(reg["targets"]["linux1"]["ssh"] == "user@hostB2", "ssh は更新される")

print("保存と再読込（atomic）:")
registry.save(reg, path)
reg2 = registry.load(path)
check(reg2 == reg, "save -> load ラウンドトリップ一致")
check(os.path.exists(path), "ファイルが作られる")

print("get_target:")
check(registry.get_target(reg2, "linux1")["ssh"] == "user@hostB2", "名前で引ける")
check(registry.get_target(reg2, None)["name"] == "winpc", "None -> default_target を引く")
check(registry.get_target(reg2, "nope") is None, "未知の名前 -> None")

print("採番は空きを探す:")
check(registry.allocate_local_port(reg2) == 10002, "9999/10000/10001 使用中 -> 10002")

print("ssh_key / ssh_opts / 明示 local_port:")
registry.add_target(reg, "linux3", "user@hostD",
                    ssh_key="~/.ssh/id_ed25519", ssh_opts="-o ProxyJump=none")
check(reg["targets"]["linux3"]["ssh_key"] == "~/.ssh/id_ed25519", "ssh_key 保存")
check(reg["targets"]["linux3"]["ssh_opts"] == "-o ProxyJump=none", "ssh_opts 保存")
registry.add_target(reg, "fixed", "user@hostE", local_port=12345)
check(reg["targets"]["fixed"]["local_port"] == 12345, "明示した local_port を使う")

print("make_default:")
registry.add_target(reg, "linux3", "user@hostD", make_default=True)
check(reg["default_target"] == "linux3", "make_default=True で default 差し替え")

shutil.rmtree(tmpdir, ignore_errors=True)

print()
if failures:
    print(f"FAILED: {failures} failure(s)")
    sys.exit(1)
print("ALL PASS (0 failure(s))")
