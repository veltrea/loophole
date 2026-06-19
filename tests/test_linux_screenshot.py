"""test_linux_screenshot.py — スクリーンショット backend（screenshot.py）の Mac 検証分。

X11Screenshotter（XGetImage）と各 Wayland ツールの実撮影は実機でのみ動くため smoke 側で確認する。
ここでは shell-out 系の argv 構築・「grim → デスクトップ別ツール」のフォールバック順序・
build_screenshotter のディスパッチ（未検出/X11 不在/GNOME/KDE でも倒れないこと）を検証する。

    python3 tests/test_linux_screenshot.py
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))
sys.path.insert(0, _HERE)

import linux_backends as lb  # noqa: E402
from handlers import ProcessResult  # noqa: E402
# 新規の Wayland フォールバック撮影器は linux.screenshot から直接取る（シムは触らない）。
from linux.screenshot import (  # noqa: E402
    GnomeScreenshotter,
    SpectacleScreenshotter,
    WaylandScreenshotter,
)
from linux_testlib import Checker, FakeRunner, with_env  # noqa: E402

PNG_SIG = b"\x89PNG\r\n\x1a\n"
c = Checker()


# 一時ファイルに吐くツールだけ「ファイルを作る」挙動を再現する。grim は stdout 専用
# （argv は ["grim", "-"]）なので、ここに入れると "-" という名のファイルを作ってしまう。
_FILE_TOOLS = {"gnome-screenshot": -1, "spectacle": -1}


def _png_writer(table):
    """FakeRunner を包み、ファイル系ツールが呼ばれたら出力 tmp パスに PNG を書く。

    gnome-screenshot/spectacle は stdout でなく一時ファイルに吐くので、テストでも
    「ツールが呼ばれたらそのファイルを作る」挙動を再現しないと capture() が読めない。
    grim は stdout に返す（table の ProcessResult がそのまま使われる）ので書かない。
    """
    fake = FakeRunner(table)
    real_run = fake.run

    def run(argv, cwd, timeout, stdin_text):
        res = real_run(argv, cwd, timeout, stdin_text)
        if res.started and res.exit_code == 0 and argv[0] in _FILE_TOOLS:
            out_path = argv[_FILE_TOOLS[argv[0]]]
            with open(out_path, "wb") as f:
                f.write(PNG_SIG + b"file:" + argv[0].encode())
        return res

    fake.run = run
    return fake


# ---- GrimScreenshotter（既存・wlroots 経路）-------------------------------------
print("GrimScreenshotter (Wayland capture via grim):")
got = lb.GrimScreenshotter(FakeRunner({"grim": ProcessResult(0, PNG_SIG + b"img", b"")})).capture()
c.eq(got, PNG_SIG + b"img", "returns grim's stdout PNG bytes")
raised = None
try:
    lb.GrimScreenshotter(FakeRunner({})).capture()
except RuntimeError as exc:
    raised = str(exc)
c.ok(raised is not None and "grim" in raised, "missing grim -> clear RuntimeError")

# ---- GnomeScreenshotter（argv 構築 + tmp 読み戻し）------------------------------
print("GnomeScreenshotter (GNOME Wayland via gnome-screenshot):")
gfake = _png_writer({"gnome-screenshot": ProcessResult(0, b"", b"")})
gdata = GnomeScreenshotter(runner=gfake).capture()
gargv = gfake.calls[0][0]
c.eq(gargv[:2], ["gnome-screenshot", "-f"], "argv = gnome-screenshot -f <tmp>")
c.ok(gargv[2].endswith(".png"), "writes to a .png tmp path")
c.ok(gdata.startswith(PNG_SIG), "reads the produced PNG back")
c.ok(not os.path.exists(gargv[2]), "tmp file cleaned up after capture")
raised = None
try:
    GnomeScreenshotter(runner=FakeRunner({})).capture()
except RuntimeError as exc:
    raised = str(exc)
c.ok(raised is not None and "gnome-screenshot" in raised, "missing gnome-screenshot -> clear error")

# ---- SpectacleScreenshotter（argv 構築 + tmp 読み戻し）-------------------------
print("SpectacleScreenshotter (KDE Wayland via spectacle):")
sfake = _png_writer({"spectacle": ProcessResult(0, b"", b"")})
sdata = SpectacleScreenshotter(runner=sfake).capture()
sargv = sfake.calls[0][0]
c.eq(sargv[:4], ["spectacle", "-b", "-n", "-o"], "argv = spectacle -b -n -o <tmp>")
c.ok(sargv[4].endswith(".png"), "writes to a .png tmp path")
c.ok(sdata.startswith(PNG_SIG), "reads the produced PNG back")
c.ok(not os.path.exists(sargv[4]), "tmp file cleaned up after capture")

# ---- WaylandScreenshotter（grim → デスクトップ別の順序）------------------------
print("WaylandScreenshotter ordering (grim primary, per-desktop fallback):")
# grim があれば grim を使い、フォールバックツールは一切呼ばない。
wfake = _png_writer({"grim": ProcessResult(0, PNG_SIG + b"grim", b""),
                     "gnome-screenshot": ProcessResult(0, b"", b"")})
with_env({"XDG_CURRENT_DESKTOP": "ubuntu:GNOME"},
         lambda: c.eq(WaylandScreenshotter(runner=wfake).capture(),
                      PNG_SIG + b"grim", "grim present -> uses grim's stdout"))
c.ok(all(call[0][0] == "grim" for call in wfake.calls),
     "grim present -> per-desktop tool never called")

# grim 不在 + GNOME -> gnome-screenshot に委譲。
wfake2 = _png_writer({"gnome-screenshot": ProcessResult(0, b"", b"")})  # grim 未登録 = 未インストール
out = with_env({"XDG_CURRENT_DESKTOP": "GNOME"},
               lambda: WaylandScreenshotter(runner=wfake2).capture())
tools = [call[0][0] for call in wfake2.calls]
c.eq(tools[0], "grim", "tries grim first")
c.ok("gnome-screenshot" in tools, "grim missing + GNOME -> falls back to gnome-screenshot")
c.ok(out.startswith(PNG_SIG), "fallback returns the produced PNG")

# grim 不在 + KDE -> spectacle に委譲。
wfake3 = _png_writer({"spectacle": ProcessResult(0, b"", b"")})
out = with_env({"XDG_CURRENT_DESKTOP": "KDE"},
               lambda: WaylandScreenshotter(runner=wfake3).capture())
c.ok("spectacle" in [call[0][0] for call in wfake3.calls],
     "grim missing + KDE -> falls back to spectacle")
c.ok(out.startswith(PNG_SIG), "KDE fallback returns the produced PNG")

# grim 不在 + フォールバック対象外デスクトップ -> 試した方法を含む明示エラー。
raised = None
try:
    with_env({"XDG_CURRENT_DESKTOP": "XFCE"},
             lambda: WaylandScreenshotter(runner=FakeRunner({})).capture())
except RuntimeError as exc:
    raised = str(exc)
c.ok(raised is not None and "grim" in raised and "XFCE" in raised,
     "no method works -> RuntimeError naming what was tried")

# grim 不在 + GNOME だが gnome-screenshot も不在 -> 両方の失敗を含む明示エラー。
raised = None
try:
    with_env({"XDG_CURRENT_DESKTOP": "GNOME"},
             lambda: WaylandScreenshotter(runner=FakeRunner({})).capture())
except RuntimeError as exc:
    raised = str(exc)
c.ok(raised is not None and "grim" in raised and "gnome-screenshot" in raised,
     "grim + gnome-screenshot both missing -> error names both")

# ---- build_screenshotter ディスパッチ -----------------------------------------
print("build_screenshotter dispatch (no crash on Mac / missing display):")
c.ok(isinstance(with_env({}, lb.build_screenshotter), lb.UnsupportedBackend),
     "no display -> UnsupportedBackend")
# X11 を要求しても、Mac では libX11 が無いので _try が UnsupportedBackend に倒す（落ちない）。
c.ok(isinstance(with_env({"DISPLAY": ":0"}, lb.build_screenshotter), lb.UnsupportedBackend),
     "x11 requested off-Linux -> degrades, not crash")
# Wayland 一般（wlroots）-> WaylandScreenshotter（grim を内包）。
sc = with_env({"WAYLAND_DISPLAY": "wayland-0", "XDG_CURRENT_DESKTOP": "sway"},
              lb.build_screenshotter)
c.ok(isinstance(sc, WaylandScreenshotter), "wayland -> WaylandScreenshotter")
c.ok(isinstance(sc._fallback, type(None)), "sway (wlroots) -> no per-desktop fallback, grim only")
# Wayland + GNOME -> WaylandScreenshotter で fallback が GnomeScreenshotter。
sc = with_env({"WAYLAND_DISPLAY": "wayland-0", "XDG_CURRENT_DESKTOP": "ubuntu:GNOME"},
              lb.build_screenshotter)
c.ok(isinstance(sc, WaylandScreenshotter) and isinstance(sc._fallback, GnomeScreenshotter),
     "wayland + GNOME -> grim primary, gnome-screenshot fallback")
# Wayland + KDE -> fallback が SpectacleScreenshotter。
sc = with_env({"WAYLAND_DISPLAY": "wayland-0", "XDG_CURRENT_DESKTOP": "KDE"},
              lb.build_screenshotter)
c.ok(isinstance(sc, WaylandScreenshotter) and isinstance(sc._fallback, SpectacleScreenshotter),
     "wayland + KDE -> grim primary, spectacle fallback")

c.done()
