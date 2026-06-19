"""common_backends.py の OS 非依存ロジックを Mac で検証する。

SubprocessRunner.shell_argv のシェル選択、linux_display_server の env 判定、
HostEnvironment.describe の形、UnsupportedBackend の番人挙動。実プロセスや X11 は触らない。

    python3 tests/test_common_backends.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))

import common_backends as cb  # noqa: E402

failures = 0


def check(cond, label):
    global failures
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond:
        failures += 1


def check_eq(actual, expected, label):
    global failures
    if actual == expected:
        print(f"  [PASS] {label}")
    else:
        print(f"  [FAIL] {label}\n         expected={expected!r}\n         actual  ={actual!r}")
        failures += 1


print("SubprocessRunner.shell_argv (POSIX host wraps with /bin/sh):")
# このテストは Mac/Linux（非 Windows）で走る前提。IS_WINDOWS 分岐は実機 Windows 側で効く。
argv = cb.SubprocessRunner().shell_argv("echo a & echo b")
if cb.IS_WINDOWS:
    check_eq(argv, ["cmd.exe", "/S", "/C", "echo a & echo b"], "Windows -> cmd.exe /S /C")
else:
    check_eq(argv, ["/bin/sh", "-c", "echo a & echo b"], "POSIX -> /bin/sh -c")


def with_env(env, fn):
    """指定キーだけを env に差し替えて fn() を呼び、元に戻す。"""
    keys = ["WAYLAND_DISPLAY", "DISPLAY", "XDG_SESSION_TYPE"]
    saved = {k: os.environ.get(k) for k in keys}
    try:
        for k in keys:
            os.environ.pop(k, None)
        for k, v in env.items():
            os.environ[k] = v
        return fn()
    finally:
        for k in keys:
            if saved[k] is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = saved[k]


print("linux_display_server (env-based detection):")
check_eq(with_env({"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"},
                  cb.linux_display_server), "wayland",
         "WAYLAND_DISPLAY wins even if DISPLAY is also set")
check_eq(with_env({"DISPLAY": ":0"}, cb.linux_display_server), "x11", "DISPLAY only -> x11")
check_eq(with_env({"XDG_SESSION_TYPE": "x11"}, cb.linux_display_server), "x11",
         "XDG_SESSION_TYPE hint when no DISPLAY")
check_eq(with_env({"XDG_SESSION_TYPE": "wayland"}, cb.linux_display_server), "wayland",
         "XDG_SESSION_TYPE wayland hint")
check_eq(with_env({}, cb.linux_display_server), None, "no display env -> None (bare SSH shell)")
check_eq(with_env({"XDG_SESSION_TYPE": "tty"}, cb.linux_display_server), None,
         "tty session -> None")

print("HostEnvironment.describe (common keys present):")
info = cb.HostEnvironment().describe()
check(set(["platform", "pid", "cwd", "user"]).issubset(info), "has platform/pid/cwd/user")
check_eq(info["platform"], sys.platform, "platform reflects the host")

print("UnsupportedBackend (the guard raises with its reason when called):")
stub = cb.UnsupportedBackend("no GUI here")
raised = None
try:
    stub.capture()
except RuntimeError as exc:
    raised = str(exc)
check(raised is not None and "capture" in raised and "no GUI here" in raised,
      "calling any method raises RuntimeError naming the method and reason")

print(f"\n{'ALL PASS' if failures == 0 else 'SOME FAILED'} ({failures} failure(s))")
sys.exit(0 if failures == 0 else 1)
