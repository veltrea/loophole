"""history.py の単体テスト（Mac / Windows どちらでも実行可）。

clock 注入で時刻も決定論的に検証する。

    python3 tests/test_history.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import history  # noqa: E402
from history import History  # noqa: E402

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


print("caller_of (via label):")
check_eq(history.caller_of({"via": "loophole_shell"}), "loophole_shell", "reads via label")
check_eq(history.caller_of({"via": "  loophole:run  "}), "loophole:run", "trims whitespace")
check_eq(history.caller_of({}), "?", "missing via -> ?")
check_eq(history.caller_of({"via": ""}), "?", "blank via -> ?")
check_eq(history.caller_of({"via": 123}), "?", "non-string via -> ?")

print("summarize_target (what was acted on):")
check_eq(history.summarize_target("run", {"command": "echo %USERNAME% & ver"}),
         "echo %USERNAME% & ver", "run command string")
check_eq(history.summarize_target("run", {"argv": ["cmd", "/c", "dir"]}),
         "cmd /c dir", "run argv joined")
check_eq(history.summarize_target("spawn", {"argv": ["firefox.exe", "https://example.com"]}),
         "firefox.exe https://example.com", "spawn = launched software")
check_eq(history.summarize_target("clipboard_set", {"text": "sample text"}),
         'set "sample text"', "clipboard_set previews value")
check_eq(history.summarize_target("clipboard_get", {}), "(read clipboard)", "clipboard_get label")
check_eq(history.summarize_target("screenshot", {"path": "C:/x.png"}), "C:/x.png", "screenshot path")
check_eq(history.summarize_target("screenshot", {}), "(inline)", "screenshot inline (no path)")
check_eq(history.summarize_target("read_file", {"path": "C:/r.txt"}), "C:/r.txt", "read_file path")
long = "x" * 500
check(len(history.summarize_target("run", {"command": long})) <= 201, "long target truncated")
check_eq(history.summarize_target("clipboard_set", {"text": "a\r\nb"}), 'set "a b"',
         "newlines flattened in preview")
check_eq(history.summarize_target("send_keys", {"keys": "ctrl+s"}), "keys: ctrl+s",
         "send_keys string summarized")
check_eq(history.summarize_target("send_keys", {"keys": ["win+r", "enter"]}), "keys: win+r enter",
         "send_keys list summarized")
check_eq(history.summarize_target("find_files", {"root": "C:/proj", "pattern": "*.txt"}),
         "*.txt in C:/proj", "find_files = pattern in root")
check_eq(history.summarize_target("list_windows", {"pattern": "Notepad"}), 'filter "Notepad"',
         "list_windows shows the title filter")
check_eq(history.summarize_target("list_windows", {}), "(all windows)",
         "list_windows without a filter")
check_eq(history.summarize_target("activate_window", {"hwnd": 12345}), "hwnd 12345",
         "activate_window by hwnd")
check_eq(history.summarize_target("activate_window", {"title": "Word"}), 'title "Word"',
         "activate_window by title")
check_eq(history.summarize_target("ime_get", {}), "(read IME state)", "ime_get label")
check_eq(history.summarize_target("ime_set", {"open": False}), "set off", "ime_set open=False")
check_eq(history.summarize_target("ime_set", {"open": True, "mode": "hiragana", "roman": True}),
         "set on hiragana roman", "ime_set summarizes all axes")
check_eq(history.summarize_target("ime_set", {"conversion": 25}), "set conv=25",
         "ime_set shows raw conversion")

print("History.record / entries:")
clock = {"t": 1000.0}
h = History(capacity=3, clock=lambda: clock["t"])
e = h.record("run", {"argv": ["cmd", "/c", "ver"], "via": "loophole_run", "token": "secret"})
check_eq(e["via"], "loophole_run", "entry records via")
check_eq(e["cmd"], "run", "entry records cmd")
check_eq(e["target"], "cmd /c ver", "entry records target")
check_eq(e["ts"], 1000.0, "entry stamps the injected clock")
check_eq(e["ok"], True, "default ok=True")
check("token" not in str(e["target"]), "token never leaks into target")

clock["t"] = 1001.0
h.record("spawn", {"argv": ["firefox.exe"]}, ok=False)
ents = h.entries()
check_eq([x["seq"] for x in ents], [1, 2], "monotonic seq, oldest-first order")
check_eq(ents[1]["ok"], False, "ok=False recorded")

print("ring buffer caps at capacity:")
for i in range(5):
    h.record("run", {"command": f"c{i}"})
ents = h.entries()
check_eq(len(ents), 3, "keeps only the last `capacity` entries")
check_eq(ents[-1]["target"], "c4", "newest entry retained")
check(all(e["seq"] < ents[0]["seq"] + 3 for e in ents), "old entries evicted")

print("format_ts / as_display:")
ts_str = history.format_ts(1000.0)
check(isinstance(ts_str, str) and len(ts_str) == len("2026-06-13 12:00:00"),
      "format_ts yields a YYYY-MM-DD HH:MM:SS string")
disp = h.as_display()
check(all(set(d.keys()) == {"seq", "time", "via", "cmd", "target", "ok"} for d in disp),
      "as_display entries have display fields")
check(all(isinstance(d["time"], str) for d in disp), "as_display formats ts to string")

print(f"\n{'ALL PASS' if failures == 0 else 'SOME FAILED'} ({failures} failure(s))")
sys.exit(0 if failures == 0 else 1)
