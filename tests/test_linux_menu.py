"""test_linux_menu.py — メニュー backend（menu.py）の AT-SPI 出力パースと LinuxMenuController。

実機の AT-SPI ツリー走査と DoAction は smoke 側（GTK アプリ）で確認する。ここでは gdbus 出力の
パーサと、id マップ→DoAction の経路を runner フェイクで検証する。

    python3 tests/test_linux_menu.py
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))
sys.path.insert(0, _HERE)

import linux_backends as lb  # noqa: E402
from handlers import ProcessResult  # noqa: E402
from linux_testlib import Checker, ResponderRunner  # noqa: E402

c = Checker()

print("AT-SPI gdbus output parsers (menu via accessibility tree):")
c.eq(lb.parse_atspi_ref("((':1.0', objectpath '/org/a11y/atspi/accessible/12'),)"),
     (":1.0", "/org/a11y/atspi/accessible/12"), "single (so) ref parsed")
c.eq(lb.parse_atspi_refs("([(':1.0', objectpath '/a/1'), (':1.5', objectpath '/a/2')],)"),
     [(":1.0", "/a/1"), (":1.5", "/a/2")], "a(so) array of refs parsed")
c.eq(lb.parse_atspi_string("(<'New'>,)"), "New", "variant-wrapped Name -> string")
c.eq(lb.parse_atspi_string("('unix:path=/run/x',)"), "unix:path=/run/x", "plain string tuple")
c.eq(lb.parse_atspi_uint("(uint32 30,)"), 30, "GetRole uint -> 30 (not 32 from the type tag)")
c.eq(lb.parse_atspi_uint("(<2>,)"), 2, "ChildCount variant -> 2")
# GetState au -> 64bit; SENSITIVE(24) bit set means enabled
st = lb.parse_atspi_state("([uint32 %d, uint32 0],)" % (1 << 24))
c.eq((st >> 24) & 1, 1, "state low word: SENSITIVE bit decoded")
st2 = lb.parse_atspi_state("([uint32 0, uint32 1],)")
c.eq((st2 >> 32) & 1, 1, "state high word folded into bit 32")

print("LinuxMenuController (id map + DoAction via fake gdbus):")
# enumerate せず invoke すると id 未知 -> False（クラッシュしない）。
mc = lb.LinuxMenuController(ResponderRunner(lambda a: ProcessResult(0, b"(true,)", b"")))
mc._addr = "unix:addr"  # a11y 解決済みに見せる
c.ok(mc.invoke(0, 999) is False, "invoke with unknown command_id -> False")
# id_map にマップを仕込んで DoAction が呼ばれることを見る
inv = ResponderRunner(lambda a: ProcessResult(0, b"(true,)", b""))
mc2 = lb.LinuxMenuController(inv)
mc2._addr = "unix:addr"
mc2._id_map = {3: (":1.0", "/org/a11y/atspi/accessible/12")}
c.ok(mc2.invoke(0, 3) is True, "invoke(mapped id) -> DoAction true")
c.ok(any(a[0] == "gdbus" and a[-1] == "0" and "DoAction" in " ".join(a) for a in inv.calls),
     "invoke issued Action.DoAction(0) on the mapped object")

print("Title matching (frame Name vs target window title) — T6 decision logic:")
rank = lb.LinuxMenuController._title_match_rank
c.eq(rank("Untitled Document - gedit", "Untitled Document - gedit"), 0, "exact match -> rank 0")
c.eq(rank("Untitled Document - GEDIT", "untitled document - gedit"), 1,
     "case-insensitive match -> rank 1")
c.eq(rank("gedit", "Untitled Document - gedit"), 1, "frame name is substring of title -> rank 1")
c.eq(rank("Untitled Document - gedit", "gedit"), 1, "title is substring of frame name -> rank 1")
c.eq(rank("Calculator", "gedit"), None, "no overlap -> None")
c.eq(rank("", "gedit"), None, "empty frame name -> None (not matchable)")
c.eq(rank("gedit", ""), None, "empty target title -> None (not matchable)")
# 完全一致は部分一致より優先（rank が小さい）。_find_menubar の選択順を担保する不変条件。
c.ok(rank("X", "X") < rank("X", "X tail"), "exact rank < substring rank")

print("Title resolution degrades gracefully on non-X11 hosts (Mac) — T6 fallback:")
# Mac には X11WindowManager の DISPLAY が無いので construct/list が必ず失敗する。例外は
# 握りつぶして None を返し、enumerate は従来挙動（アクティブ/最初のフレーム）に退避する。
mr = lb.LinuxMenuController(ResponderRunner(lambda a: ProcessResult(0, b"", b"")))
c.ok(mr._resolve_target_title(12345) is None,
     "resolve title without X11 -> None (no crash, falls back)")
c.ok(mr._resolve_target_title(0) is None, "hwnd 0 -> None (nothing to resolve)")

print("Per-enumerate role/name cache avoids duplicate gdbus spawns — T9:")
# 同じノードの role / name を 2 回問い合わせても gdbus は 1 回だけ呼ばれることを確認する。
role_resp = b"(<'frame'>,)"   # GetRoleName / Name の variant 文字列出力
counter = lb.LinuxMenuController(ResponderRunner(lambda a: ProcessResult(0, role_resp, b"")))
counter._addr = "unix:addr"
node = (":1.0", "/org/a11y/atspi/accessible/9")
_ = counter._rolename("unix:addr", node)
_ = counter._rolename("unix:addr", node)
role_calls = [a for a in counter._runner.calls if "GetRoleName" in " ".join(a)]
c.eq(len(role_calls), 1, "_rolename queried gdbus only once for repeated lookups")
_ = counter._name("unix:addr", node)
_ = counter._name("unix:addr", node)
name_calls = [a for a in counter._runner.calls
              if "Properties.Get" in " ".join(a) and a[-1] == "Name"]
c.eq(len(name_calls), 1, "_name queried gdbus only once for repeated lookups")
# enumerate ごとにキャッシュは捨てる（ツリーが変わるため跨いで使わない）。
counter._role_cache = {}
_ = counter._rolename("unix:addr", node)
role_calls2 = [a for a in counter._runner.calls if "GetRoleName" in " ".join(a)]
c.eq(len(role_calls2), 2, "cache cleared -> node re-queried (no stale cross-enumerate reuse)")

c.done()
