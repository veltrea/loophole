"""test_darwin_build.py — build_handlers のディスパッチ（darwin/__init__.py の配線）。

Mac で agent を起動したときに Handlers が組めて、ping / hello が通り、hello が darwin 固有
フィールド（console_user / interactive / tcc）を返すことを確認する。

各能力の実コードパスは test_darwin_clipboard / screenshot / keyboard / mouse / window / ime
の各テストで個別に検証する（ここでは scaffolding 配線だけ見る）。Mac で送信されてしまうと
危ないコマンド（send_keys / screenshot / mouse_*）はここでは叩かない。

Linux でも Windows でも実行できる（darwin パッケージの import は副作用なし）。
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))
sys.path.insert(0, _HERE)

import darwin_backends as db  # noqa: E402
from linux_testlib import Checker  # noqa: E402

c = Checker()

print("build_handlers dispatch on darwin (Handlers built; ping/hello work):")
h = db.build_handlers()

# ping は backend に触れない → どの OS でも通る。
c.eq(h.dispatch("ping", {}), {"pong": True}, "handlers usable; ping works")

# hello は環境情報を返す。Mac で起動した場合は darwin 固有フィールドが乗る。
hello = h.dispatch("hello", {})
c.ok("platform" in hello, "hello returns environment info")

if sys.platform == "darwin":
    c.ok("console_user" in hello, "darwin hello includes console_user")
    c.ok("interactive" in hello, "darwin hello includes interactive flag")
    c.ok("tcc" in hello, "darwin hello includes tcc probe block")
    c.ok(isinstance(hello.get("tcc"), dict), "tcc field is a dict")

# 一覧として GUI 系コマンドが登録されていること（実呼び出しはしない — Mac で勝手に入力や
# スクショが飛ぶのを避ける）。
commands = set(h.commands())
expected = {"send_keys", "screenshot", "clipboard_get", "clipboard_set",
            "mouse_move", "mouse_click", "mouse_scroll",
            "list_windows", "activate_window", "ime_get", "ime_set"}
missing = expected - commands
c.ok(not missing, f"all GUI commands registered (missing: {missing})")

# 安全に呼べる一覧として list_windows は visible_only=True で呼び、空でもエラーでも
# どちらでも構わない（Automation 権限が無ければ RuntimeError、あれば結果）。エラーは
# actionable な文言を含むこと（呼び元への誘導）。
print("list_windows safety (Automation permission may not be granted):")
ok_or_raised = False
detail = ""
try:
    res = h.dispatch("list_windows", {"visible_only": True})
    # ハンドラは {"windows": [...], "count": N} で包んで返す
    ok_or_raised = (isinstance(res, dict)
                    and isinstance(res.get("windows"), list)
                    and isinstance(res.get("count"), int))
    detail = f"got {res.get('count')} windows"
except RuntimeError as exc:
    ok_or_raised = "Automation" in str(exc) or "osascript" in str(exc)
    detail = f"raised: {str(exc)[:80]}"
c.ok(ok_or_raised,
     "list_windows either returns {windows,count} or fails actionably "
     f"with osascript/Automation hint ({detail})")

c.done()
