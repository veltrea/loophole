"""protocol.py の単体テスト（Mac / Windows どちらでも実行可）。

    python3 tests/test_protocol.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))

import protocol  # noqa: E402

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


print("encode/decode round-trip:")
msg = protocol.make_request(7, "run", {"argv": ["cmd", "/c", "echo", "日本語"]})
line = protocol.encode_message(msg)
check(line.endswith(b"\n"), "encoded line ends with one newline")
check(line.count(b"\n") == 1, "exactly one newline in an encoded message")
check(b"\xef\xbb\xbf" not in line[:3], "no UTF-8 BOM is emitted")
check_eq(protocol.decode_message(line), msg, "round-trips through decode")

print("decode_message errors:")
try:
    protocol.decode_message(b"not json\n")
    check(False, "garbage should raise ProtocolError")
except protocol.ProtocolError:
    check(True, "garbage raises ProtocolError")
try:
    protocol.decode_message(b"[1,2,3]\n")
    check(False, "non-object JSON should raise")
except protocol.ProtocolError:
    check(True, "top-level array raises ProtocolError")
try:
    protocol.decode_message("   \n")
    check(False, "blank line should raise")
except protocol.ProtocolError:
    check(True, "blank line raises ProtocolError")

print("parse_request:")
rid, cmd, args = protocol.parse_request({"id": 1, "cmd": "ping", "args": {"x": 1}})
check(rid == 1 and cmd == "ping" and args == {"x": 1}, "extracts id/cmd/args")
rid, cmd, args = protocol.parse_request({"cmd": "ping"})
check(rid is None and cmd == "ping" and args == {}, "missing id/args default sensibly")
try:
    protocol.parse_request({"args": {}})
    check(False, "missing cmd should raise")
except protocol.ProtocolError:
    check(True, "missing cmd raises ProtocolError")

print("ok / error envelopes:")
check_eq(protocol.make_ok(3, {"v": 1}), {"id": 3, "ok": True, "result": {"v": 1}}, "make_ok shape")
check_eq(protocol.make_error(3, "boom"), {"id": 3, "ok": False, "error": "boom"}, "make_error shape")

print("LineBuffer (TCP stream reassembly):")
buf = protocol.LineBuffer()
# 1 行が 2 回の recv に割れて届くケース
got = list(buf.push(b'{"a":'))
check_eq(got, [], "partial line yields nothing")
got = list(buf.push(b'1}\n'))
check_eq(got, [b'{"a":1}'], "completes when newline arrives")
# 複数行がまとめて届くケース + 末尾の不完全分
got = list(buf.push(b'{"b":2}\n{"c":3}\n{"d"'))
check_eq(got, [b'{"b":2}', b'{"c":3}'], "splits multiple lines, keeps remainder")
check(buf.pending == 4, "remainder is buffered until its newline")

print("decode_output:")
check_eq(protocol.decode_output("日本語".encode("utf-8"), "auto"), "日本語", "auto decodes UTF-8")
check_eq(protocol.decode_output("日本語".encode("cp932"), "auto"), "日本語", "auto falls back to CP932")
check_eq(protocol.decode_output("表予能".encode("cp932"), "cp932"), "表予能", "explicit cp932 (dame-moji)")
check_eq(protocol.decode_output(b"\xef\xbb\xbfhi", "utf-8"), "hi", "UTF-8 BOM stripped")
check_eq(protocol.decode_output(b"", "auto"), "", "empty bytes -> empty string")

print(f"\n{'ALL PASS' if failures == 0 else 'SOME FAILED'} ({failures} failure(s))")
sys.exit(0 if failures == 0 else 1)
