#!/usr/bin/env bash
# loophole の全テストを走らせる（Mac / Linux / 対象 Windows の Git Bash いずれでも可）。
set -u
cd "$(dirname "$0")"

fail=0
for t in tests/test_protocol.py tests/test_protocol_surface.py tests/test_registry.py tests/test_keys.py tests/test_handlers.py tests/test_agent.py tests/test_history.py tests/test_viewer.py tests/test_common_backends.py tests/test_win_backends.py tests/test_linux_parsers.py tests/test_linux_clipboard.py tests/test_linux_screenshot.py tests/test_linux_keyboard.py tests/test_linux_window.py tests/test_linux_ime.py tests/test_linux_menu.py tests/test_linux_mouse.py tests/test_linux_build.py tests/test_darwin_build.py tests/test_darwin_clipboard.py tests/test_darwin_screenshot.py tests/test_darwin_keyboard.py tests/test_darwin_mouse.py tests/test_darwin_window.py tests/test_darwin_ime.py tests/test_e2e_loopback.py; do
    echo "=== $t ==="
    if ! python3 "$t"; then
        fail=1
    fi
    echo
done

# MCP まわりのテストは mcp パッケージが要るので uv 経由（uv が無ければスキップ）
if command -v uv >/dev/null 2>&1; then
    for t in tests/test_mcp_bridge.py tests/test_version_negotiation.py tests/test_tunnel.py tests/test_multitarget.py; do
        echo "=== $t (via uv) ==="
        if ! uv run python "$t"; then
            fail=1
        fi
        echo
    done
else
    echo "(skipping MCP tests: uv not found)"
fi

if [ "$fail" -ne 0 ]; then
    echo "SOME TESTS FAILED"
    exit 1
fi
echo "ALL LOOPHOLE TESTS PASSED"
