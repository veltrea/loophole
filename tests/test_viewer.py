"""viewer.py の単体テスト（Mac / Windows どちらでも実行可）。

スクリーンショッタはフェイクを注入するので、実画面も Windows も要らない。純粋関数
（encode_frame / iter_frames / INDEX_HTML）に加え、ループバックで実際に HTTP サーバーを
立てて GET し、ルーティングと multipart 配信の配線まで確認する。

    python3 tests/test_viewer.py
"""

import os
import socket
import sys
import threading
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import viewer  # noqa: E402
from history import History  # noqa: E402
from fakes import FakeScreenshotter  # noqa: E402

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


print("encode_frame:")
png = b"\x89PNG\r\nHELLO"
frame = viewer.encode_frame(png, "B")
check(frame.startswith(b"--B\r\n"), "starts with boundary line")
check(b"Content-Type: image/png\r\n" in frame, "declares image/png")
check(f"Content-Length: {len(png)}\r\n\r\n".encode() in frame, "correct Content-Length")
check(frame.endswith(png + b"\r\n"), "ends with the PNG bytes + CRLF")

print("INDEX_HTML (split dashboard) / LOG_HTML:")
check(b"/stream" in viewer.INDEX_HTML, "dashboard streams the live screen")
check(b"<img" in viewer.INDEX_HTML, "dashboard has the screen <img>")
check(b"/log.json" in viewer.INDEX_HTML, "dashboard fetches command history inline")
check(b"nolog" in viewer.INDEX_HTML and b"hide log" in viewer.INDEX_HTML,
      "dashboard can hide the log panel (toggle)")
check(b"loophole_log_hidden" in viewer.INDEX_HTML, "hide state persists in localStorage")
check(b'href="/log"' in viewer.INDEX_HTML, "dashboard links to the full /log page")
check(b"/log.json" in viewer.LOG_HTML, "log page fetches /log.json")
check(b'href="/"' in viewer.LOG_HTML, "log page links back to live view")

print("iter_frames (injected sleep, bounded):")
shot = FakeScreenshotter()
sleeps = []
frames = list(viewer.iter_frames(shot, "B", 0.5, sleep=sleeps.append, max_frames=3))
check_eq(len(frames), 3, "yields exactly max_frames frames")
check_eq(frames[0], viewer.encode_frame(shot.png, "B"), "frame equals encode_frame(capture())")
check_eq(len(sleeps), 2, "sleeps between frames only, not after the last")
check_eq(sleeps, [0.5, 0.5], "sleeps for the requested interval")


class _BoomShotter:
    def capture(self):
        raise RuntimeError("no desktop")


print("iter_frames (capture failure ends stream, no hang):")
boomed = list(viewer.iter_frames(_BoomShotter(), "B", 0.0, sleep=lambda s: None, max_frames=5))
check_eq(boomed, [], "capture error terminates the stream cleanly")

print("make_server fps -> interval:")
srv = viewer.make_server(FakeScreenshotter(), "127.0.0.1", 0, fps=4.0)
check_eq(srv.interval, 0.25, "fps=4 -> interval 0.25s")
srv.server_close()
srv0 = viewer.make_server(FakeScreenshotter(), "127.0.0.1", 0, fps=0)
check_eq(srv0.interval, 0.5, "fps<=0 falls back to 0.5s")
srv0.server_close()

print("live HTTP server (loopback smoke test):")
import json as _json
hist = History(clock=lambda: 1000.0)
hist.record("run", {"argv": ["cmd", "/c", "ver"], "via": "loophole_run"})
hist.record("spawn", {"argv": ["firefox.exe"], "via": "loophole_gui"}, ok=False)
server = viewer.make_server(FakeScreenshotter(), "127.0.0.1", 0, fps=50.0, history=hist)
port = server.server_address[1]
t = threading.Thread(target=server.serve_forever, daemon=True)
t.start()
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
        body = resp.read()
        ctype = resp.headers.get("Content-Type", "")
    check(b"/stream" in body, "GET / serves the viewer page")
    check("text/html" in ctype, "GET / has text/html content-type")

    with urllib.request.urlopen(f"http://127.0.0.1:{port}/log", timeout=5) as resp:
        check(b"command log" in resp.read(), "GET /log serves the history page")

    with urllib.request.urlopen(f"http://127.0.0.1:{port}/log.json", timeout=5) as resp:
        data = _json.loads(resp.read())
    ents = data["entries"]
    check_eq(len(ents), 2, "GET /log.json returns recorded entries")
    check_eq(ents[0]["via"], "loophole_run", "/log.json entry carries via")
    check_eq(ents[0]["target"], "cmd /c ver", "/log.json entry carries target")
    check(isinstance(ents[0]["time"], str) and len(ents[0]["time"]) == 19, "/log.json time is a formatted string")
    check_eq(ents[1]["ok"], False, "/log.json preserves ok flag")

    # /stream は無限なので、ヘッダ + 先頭 1 フレーム分だけ読んで閉じる（ハング防止）。
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    sock.sendall(b"GET /stream HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
    sock.settimeout(5)
    chunk = b""
    while b"--loopholeframe\r\n" not in chunk and len(chunk) < 65536:
        part = sock.recv(4096)
        if not part:
            break
        chunk += part
    sock.close()
    check(b"multipart/x-mixed-replace; boundary=loopholeframe" in chunk, "/stream sends multipart header")
    check(b"--loopholeframe\r\n" in chunk, "/stream emits at least one boundary frame")
    check(b"Content-Type: image/png" in chunk, "/stream frame is image/png")

    with urllib.request.urlopen(f"http://127.0.0.1:{port}/nope", timeout=5) as resp:
        check(False, "404 path should not return 200")
except urllib.error.HTTPError as exc:
    check(exc.code == 404, "unknown path returns 404")
finally:
    server.shutdown()
    server.server_close()

print(f"\n{'ALL PASS' if failures == 0 else 'SOME FAILED'} ({failures} failure(s))")
sys.exit(0 if failures == 0 else 1)
