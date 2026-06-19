"""test_linux_ime.py — IME backend（ime.py）の純ヘルパと LinuxImeController（fcitx5/ibus）。

実機の gdbus 契約は smoke 側（fcitx5+mozc）で確認する。ここでは出力パースと、fcitx/ibus/未起動の
各経路を runner フェイクで検証する。

    python3 tests/test_linux_ime.py
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

print("IME pure helpers (fcitx state / ibus engine / desired-open):")
c.eq(lb.parse_fcitx_state("(uint32 2,)"), True, "fcitx State 2 -> open True")
c.eq(lb.parse_fcitx_state("(uint32 1,)"), False, "fcitx State 1 -> open False (direct)")
c.eq(lb.parse_fcitx_state("(uint32 0,)"), False, "fcitx State 0 -> open False")
c.eq(lb.parse_fcitx_state(""), None, "empty -> None (unparseable)")
c.eq(lb.ibus_engine_is_active("mozc"), True, "ibus mozc -> active")
c.eq(lb.ibus_engine_is_active("xkb:us::eng"), False, "ibus xkb layout -> inactive")
c.eq(lb.ibus_engine_is_active("anthy\n"), True, "ibus anthy (trailing nl) -> active")
c.eq(lb.ibus_engine_is_active(""), False, "ibus empty -> inactive")
c.eq(lb.desired_open_from(True, None), True, "open=True -> on")
c.eq(lb.desired_open_from(False, None), False, "open=False -> off")
c.eq(lb.desired_open_from(None, 0x09), True, "conversion with NATIVE bit -> on")
c.eq(lb.desired_open_from(None, 0), False, "conversion 0 (alphanumeric) -> off")
c.eq(lb.desired_open_from(None, None), None, "nothing given -> None")
c.eq(lb.desired_open_from(False, 0x09), False, "explicit open wins over conversion")


def fcitx_responder(state_reply):
    """fcitx5 を模す: State は state_reply、Activate/Deactivate は成功。ibus は不在。"""
    def respond(argv):
        if argv[0] == "gdbus":
            method = argv[-1].rsplit(".", 1)[-1]
            if method == "State":
                return ProcessResult(0, state_reply.encode(), b"")
            if method in ("Activate", "Deactivate"):
                return ProcessResult(0, b"()", b"")
        return ProcessResult(-1, b"", b"", started=False)
    return respond


print("LinuxImeController (fcitx5 path):")
c.eq(lb.LinuxImeController(ResponderRunner(fcitx_responder("(uint32 2,)"))).get(),
     (True, lb._IME_CONV_ON), "fcitx active -> (open True, native conv)")
c.eq(lb.LinuxImeController(ResponderRunner(fcitx_responder("(uint32 1,)"))).get(),
     (False, 0), "fcitx inactive -> (open False, 0)")
r_set = ResponderRunner(fcitx_responder("(uint32 2,)"))
c.ok(lb.LinuxImeController(r_set).set(False, None) is True, "set(open=False) succeeds on fcitx")
c.ok(any(a[0] == "gdbus" and a[-1].endswith(".Deactivate") for a in r_set.calls),
     "set(open=False) issued Deactivate")
r_set2 = ResponderRunner(fcitx_responder("(uint32 1,)"))
c.ok(lb.LinuxImeController(r_set2).set(None, lb._IME_CONV_ON) is True,
     "set(conversion=native) maps to Activate")
c.ok(any(a[-1].endswith(".Activate") for a in r_set2.calls), "issued Activate")
c.ok(lb.LinuxImeController(ResponderRunner(fcitx_responder("(uint32 2,)"))).set(None, None) is False,
     "set() with no axis -> False")


def ibus_responder(engine_reply, list_reply="mozc\nxkb:us::eng\n"):
    """ibus を模す: fcitx は不在、`ibus engine` は engine_reply を返す。"""
    def respond(argv):
        if argv[0] == "gdbus":
            return ProcessResult(-1, b"", b"", started=False)  # fcitx 無し
        if argv[:2] == ["ibus", "engine"] and len(argv) == 2:
            return ProcessResult(0, engine_reply.encode(), b"")
        if argv[:2] == ["ibus", "engine"] and len(argv) == 3:
            return ProcessResult(0, b"", b"")  # set engine
        if argv[:2] == ["ibus", "list-engine"]:
            return ProcessResult(0, list_reply.encode(), b"")
        return ProcessResult(-1, b"", b"", started=False)
    return respond


print("LinuxImeController (ibus fallback path):")
c.eq(lb.LinuxImeController(ResponderRunner(ibus_responder("anthy\n"))).get(),
     (True, lb._IME_CONV_ON), "ibus active engine -> open True")
c.eq(lb.LinuxImeController(ResponderRunner(ibus_responder("xkb:us::eng\n"))).get(),
     (False, 0), "ibus xkb layout -> open False")
r_ibset = ResponderRunner(ibus_responder("xkb:us::eng\n"))
c.ok(lb.LinuxImeController(r_ibset).set(True, None) is True,
     "set(open=True) picks a real IME engine on ibus")
c.ok(any(a == ["ibus", "engine", "mozc"] for a in r_ibset.calls),
     "switched to first non-xkb engine (mozc)")

print("LinuxImeController (no IME daemon -> unsupported, not a crash):")
none_runner = ResponderRunner(lambda argv: ProcessResult(-1, b"", b"", started=False))
ime_none = lb.LinuxImeController(none_runner)
c.ok(ime_none.get() is None, "no daemon -> get() None (handler reports supported:false)")
c.ok(ime_none.set(False, None) is False, "no daemon -> set() False")


def stateful_ibus(initial="mozc", engines=("mozc", "anthy", "xkb:us::eng")):
    """状態を持つ ibus フェイク: `ibus engine <name>` で現在エンジンが本当に変わる。

    OFF→ON のラウンドトリップで「直前エンジンが復元されるか」を駆動するために、現在エンジンを
    保持して引数なし `ibus engine` で返す。fcitx は不在（gdbus は失敗）。
    """
    state = {"engine": initial}
    list_reply = "".join(e + "\n" for e in engines)

    def respond(argv):
        if argv[0] == "gdbus":
            return ProcessResult(-1, b"", b"", started=False)  # fcitx 無し
        if argv[:2] == ["ibus", "engine"] and len(argv) == 2:
            return ProcessResult(0, state["engine"].encode(), b"")
        if argv[:2] == ["ibus", "engine"] and len(argv) == 3:
            state["engine"] = argv[2]  # 実際に切り替える
            return ProcessResult(0, b"", b"")
        if argv[:2] == ["ibus", "list-engine"]:
            return ProcessResult(0, list_reply.encode(), b"")
        return ProcessResult(-1, b"", b"", started=False)

    return respond


def engine_switches(calls):
    """runner.calls から `ibus engine <name>` の切替え先（name）だけ順に拾う。"""
    return [a[2] for a in calls if a[:2] == ["ibus", "engine"] and len(a) == 3]


print("LinuxImeController (ibus remembers the engine across OFF then ON):")
# anthy がアクティブな状態から OFF→ON すると、先頭の mozc ではなく anthy が復元される。
rr = ResponderRunner(stateful_ibus(initial="anthy"))
ime = lb.LinuxImeController(rr)
c.ok(ime.set(False, None) is True, "OFF from anthy succeeds")
c.ok(ime.set(True, None) is True, "ON after OFF succeeds")
sw = engine_switches(rr.calls)
c.eq(sw, ["xkb:us::eng", "anthy"],
     "OFF switches to xkb, ON restores the remembered anthy (not the first engine mozc)")

# 記憶が無い初回の ON は list-engine 先頭の非 xkb（mozc）に倒れる（従来挙動を維持）。
rr2 = ResponderRunner(stateful_ibus(initial="xkb:us::eng"))
ime2 = lb.LinuxImeController(rr2)
c.ok(ime2.set(True, None) is True, "ON with no memory succeeds")
c.eq(engine_switches(rr2.calls), ["mozc"],
     "no remembered engine -> first non-xkb (mozc)")

print("LinuxImeController (ibus on/off robustness):")
# 既に直接入力（xkb）で OFF は冪等成功し、無駄な切替コマンドを出さない。
rr3 = ResponderRunner(stateful_ibus(initial="xkb:us::eng"))
c.ok(lb.LinuxImeController(rr3).set(False, None) is True, "OFF when already xkb -> idempotent True")
c.eq(engine_switches(rr3.calls), [], "OFF when already off issues no engine switch")

# 既に目的の実エンジンが選択中なら ON も切替を省く（堅牢化）。
rr4 = ResponderRunner(stateful_ibus(initial="mozc"))
c.ok(lb.LinuxImeController(rr4).set(True, None) is True, "ON when already on a real engine -> True")
c.eq(engine_switches(rr4.calls), [], "ON when already on issues no engine switch")

# 覚えたエンジンが list-engine から消えていたら先頭非 xkb にフォールバックする。
rr5 = ResponderRunner(stateful_ibus(initial="anthy", engines=("mozc", "xkb:us::eng")))
ime5 = lb.LinuxImeController(rr5)
ime5.set(False, None)  # anthy を記憶
ime5.set(True, None)   # anthy はもう list に無い → mozc へ
c.eq(engine_switches(rr5.calls)[-1:], ["mozc"],
     "remembered engine no longer available -> falls back to first non-xkb")


def flaky_rc_ibus(initial="xkb:us::eng", engines=("mozc", "anthy", "xkb:us::eng")):
    """ibus 1.5.x で再現する罠を模す: `ibus engine NAME` が成功時にも exit=1 を返す。

    状態は本当に変わる（read-back では新しいエンジンが見える）が、exit code だけ非ゼロ。
    実機検証で見つかった現象を pinpoint で再現するためのフェイク。
    """
    state = {"engine": initial}
    list_reply = "".join(e + "\n" for e in engines)

    def respond(argv):
        if argv[0] == "gdbus":
            return ProcessResult(-1, b"", b"", started=False)
        if argv[:2] == ["ibus", "engine"] and len(argv) == 2:
            return ProcessResult(0, state["engine"].encode(), b"")
        if argv[:2] == ["ibus", "engine"] and len(argv) == 3:
            state["engine"] = argv[2]
            return ProcessResult(1, b"", b"")  # ← rc=1 だが切替自体は成功
        if argv[:2] == ["ibus", "list-engine"]:
            return ProcessResult(0, list_reply.encode(), b"")
        return ProcessResult(-1, b"", b"", started=False)

    return respond


print("LinuxImeController (ibus engine NAME returning rc=1 must verify by read-back, T3):")
rr6 = ResponderRunner(flaky_rc_ibus(initial="xkb:us::eng"))
c.ok(lb.LinuxImeController(rr6).set(True, None) is True,
     "set(True) succeeds even when `ibus engine NAME` exits non-zero (read-back confirms)")
c.eq(engine_switches(rr6.calls), ["mozc"], "still issued the switch")

# 切替が「本当に」失敗した（rc!=0 かつ read-back も一致しない）ケースは False を返すべき。
def truly_failing_ibus():
    def respond(argv):
        if argv[0] == "gdbus":
            return ProcessResult(-1, b"", b"", started=False)
        if argv[:2] == ["ibus", "engine"] and len(argv) == 2:
            return ProcessResult(0, b"xkb:us::eng\n", b"")  # 切替リクエストを無視する ibus
        if argv[:2] == ["ibus", "engine"] and len(argv) == 3:
            return ProcessResult(1, b"", b"")  # rc=1、状態も変わらない
        if argv[:2] == ["ibus", "list-engine"]:
            return ProcessResult(0, b"mozc\nxkb:us::eng\n", b"")
        return ProcessResult(-1, b"", b"", started=False)
    return respond

rr7 = ResponderRunner(truly_failing_ibus())
c.ok(lb.LinuxImeController(rr7).set(True, None) is False,
     "set(True) returns False when read-back disagrees (real failure surfaces)")

c.done()
