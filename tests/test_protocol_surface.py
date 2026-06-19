"""test_protocol_surface.py — プロトコルの版・表面のインバリアントを機械的に守る。

スキルや暗黙の規律ではなく決定論的なテストで担保する（docs/version-negotiation.md §9）:
  (1) agent の実コマンド集合 == protocol.PROTOCOL_COMMANDS
      コマンドを追加/削除/改名すると必ずここで落ちる → PROTOCOL_COMMANDS を直し、
      契約が変わったなら PROTOCOL_VERSION を上げることを促す。
  (2) pyproject の version == protocol.AGENT_VERSION（人向け semver の取りこぼし＝
      0.1.0/0.2.0 ズレの再発防止）。
  (3) PROTOCOL_VERSION は正の整数。

mcp パッケージ不要。`python3 tests/test_protocol_surface.py` で走る。
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

import handlers  # noqa: E402
import protocol  # noqa: E402

failures = 0


def check(cond, label):
    global failures
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond:
        failures += 1


# (1) プロトコル表面（コマンド集合）のスナップショット。
# Handlers は引数を保持するだけなので全 None で構築でき、_table() のキーが取れる。
print("プロトコル表面（コマンド集合）:")
h = handlers.Handlers(None, None, None, None, None, None, None, None, None)
live = set(h.commands())
canon = set(protocol.PROTOCOL_COMMANDS)
added = sorted(live - canon)
removed = sorted(canon - live)
check(live == canon,
      "handlers._table() == protocol.PROTOCOL_COMMANDS "
      f"(追加={added} 欠落={removed} — 不一致なら PROTOCOL_COMMANDS を更新し、"
      "契約変更なら PROTOCOL_VERSION を上げること)")

# (2) 版同期: pyproject の version と AGENT_VERSION（人向け semver）の取りこぼし防止。
print("版同期:")
pyproject = open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8").read()
m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', pyproject)
pyver = m.group(1) if m else None
check(pyver == protocol.AGENT_VERSION,
      f"pyproject version == AGENT_VERSION (pyproject={pyver} / AGENT_VERSION={protocol.AGENT_VERSION})")

# (3) PROTOCOL_VERSION は機械互換用の正の整数。
print("PROTOCOL_VERSION 型:")
check(isinstance(protocol.PROTOCOL_VERSION, int) and not isinstance(protocol.PROTOCOL_VERSION, bool)
      and protocol.PROTOCOL_VERSION >= 1,
      f"PROTOCOL_VERSION は正の整数 (={protocol.PROTOCOL_VERSION!r})")

# (4) 公開仕様書 docs/protocol.md が全コマンドを記載しているか（公開仕様のドリフト防止）。
print("公開仕様書(docs/protocol.md)の網羅:")
spec = open(os.path.join(ROOT, "docs", "protocol.md"), encoding="utf-8").read()
undocumented = sorted(c for c in protocol.PROTOCOL_COMMANDS if f"#### `{c}`" not in spec)
check(not undocumented,
      f"全コマンドが `#### <cmd>` 見出しで記載されている (未記載={undocumented})")
check(f"PROTOCOL_VERSION = {protocol.PROTOCOL_VERSION}" in spec,
      "先頭に現行 PROTOCOL_VERSION が明記されている")

print()
if failures:
    print(f"FAILED: {failures} failure(s)")
    sys.exit(1)
print("ALL PASS (0 failure(s))")
