#!/usr/bin/env bash
# loophole の全テストを走らせる（Mac / Linux / 対象 Windows の Git Bash いずれでも可）。
set -u
cd "$(dirname "$0")"

fail=0
for t in tests/test_protocol.py tests/test_keys.py tests/test_handlers.py tests/test_agent.py tests/test_history.py tests/test_viewer.py tests/test_win_backends.py tests/test_e2e_loopback.py; do
    echo "=== $t ==="
    if ! python3 "$t"; then
        fail=1
    fi
    echo
done

# MCP ブリッジのテストは mcp パッケージが要るので uv 経由（uv が無ければスキップ）
if command -v uv >/dev/null 2>&1; then
    echo "=== tests/test_mcp_bridge.py (via uv) ==="
    if ! uv run python tests/test_mcp_bridge.py; then
        fail=1
    fi
    echo
else
    echo "(skipping MCP bridge test: uv not found)"
fi

if [ "$fail" -ne 0 ]; then
    echo "SOME TESTS FAILED"
    exit 1
fi
echo "ALL LOOPHOLE TESTS PASSED"
