"""parsers.py — Linux backend の純ロジック（X11/D-Bus を触らない）。

X11 プロパティ・fcitx/ibus・AT-SPI・sway/Hyprland の各種出力を解くパーサと、
クリップボードツール選択・ディスプレイ/コンポジタ判定などの純関数を集約する。
Mac で単体テストできる（tests/test_linux_backends.py）。
"""

from __future__ import annotations

import ctypes
import os
import re
import struct
from typing import Any, Dict, List, Optional, Tuple


def parse_long_array(data: bytes) -> List[int]:
    """XGetWindowProperty が format=32 で返すデータを整数列にする。

    X11 の「format 32」はワイヤ上は 32bit だが、クライアント側のバッファでは C の
    `long`（LP64 では 8 バイト）配列として返ってくる——ここが定番の罠。sizeof(c_ulong)
    で 1 要素幅を測り、ネイティブエンディアンで読む。Mac/Linux とも LP64 なので一致する。
    """
    width = ctypes.sizeof(ctypes.c_ulong)
    count = len(data) // width
    if count == 0:
        return []
    return list(struct.unpack("@" + "L" * count, data[:count * width]))


def decode_text(data: bytes, is_utf8: bool) -> str:
    """ウィンドウ名プロパティのバイト列を文字列にする（末尾/区切りの NUL を整理）。

    _NET_WM_NAME は UTF8_STRING、旧来の WM_NAME は latin-1 系。どちらも置換デコードして
    末尾 NUL を落とす。複数文字列が NUL 区切りで入る稀なケースは最初の 1 つを採る。
    """
    enc = "utf-8" if is_utf8 else "latin-1"
    text = data.decode(enc, errors="replace")
    if "\x00" in text:
        text = text.split("\x00", 1)[0]
    return text


def clipboard_commands(server: Optional[str], mode: str) -> List[List[str]]:
    """クリップボード読み書きに使う外部コマンドの候補列を、優先順で返す（純粋ロジック）。

    server: "x11" / "wayland" / None。mode: "get"（読む）/ "set"（書く・stdin にテキスト）。
    None（ディスプレイ不明）のときは X11→Wayland 両系統を順に試す候補を返す。
    """
    x11_get = [["xclip", "-selection", "clipboard", "-o"],
               ["xsel", "--clipboard", "--output"]]
    x11_set = [["xclip", "-selection", "clipboard", "-i"],
               ["xsel", "--clipboard", "--input"]]
    wl_get = [["wl-paste", "--no-newline"]]
    wl_set = [["wl-copy"]]
    if server == "wayland":
        return wl_get if mode == "get" else wl_set
    if server == "x11":
        return x11_get if mode == "get" else x11_set
    # 不明: 両方試す（X11 を先に）
    return (x11_get + wl_get) if mode == "get" else (x11_set + wl_set)


# IME 状態を表す conversion ビット（handlers の IME_CMODE_* と同値）。Linux の IME は
# Windows のような変換ビットフィールドを持たないので「ON か OFF か」だけを表現する:
#   OFF（直接入力）= 0、ON（日本語入力）= NATIVE|FULLSHAPE。read 用の粗い要約で、
#   権威があるのは open の方（type が読みに吸われるかどうか）。
_IME_NATIVE = 0x0001
_IME_FULLSHAPE = 0x0008
_IME_CONV_ON = _IME_NATIVE | _IME_FULLSHAPE


def parse_fcitx_state(stdout: str) -> Optional[bool]:
    """fcitx5 Controller1.State の gdbus 出力（例 "(uint32 2,)"）を open(bool) にする。

    fcitx5 の状態: 0=利用不可 / 1=非アクティブ（直接入力）/ 2=アクティブ（IME ON）。
    2 のときだけ open=True。整数が読めなければ None。

    gdbus は "(uint32 2,)" のように型注釈付きで出すので、"uint32" の中の 32 を拾わない
    よう**最後の整数**を状態値とする（値は常に型名の後に来る。busctl の "u 2" 等にも効く）。
    """
    import re
    nums = re.findall(r"\d+", stdout or "")
    if not nums:
        return None
    return int(nums[-1]) == 2


def ibus_engine_is_active(engine: str) -> bool:
    """ibus の現在エンジン名から「IME ON か」を判定する。

    xkb 配列（例 "xkb:us::eng"）= 直接入力 = OFF。それ以外の実エンジン（mozc/anthy 等）= ON。
    """
    e = (engine or "").strip()
    return bool(e) and not e.startswith("xkb:")


def desired_open_from(open_arg: Optional[bool], conversion: Optional[int]) -> Optional[bool]:
    """ime_set の (open, conversion) から「ON にしたいか」を決める純粋ロジック。

    Linux では Windows の変換モードを再現できないので、conversion は NATIVE ビットの有無
    だけを見て ON/OFF に畳む（mode="hiragana"→ON、mode="alphanumeric"(=0)→OFF）。open が
    明示されていればそれを優先。どちらも無ければ None（やることが無い）。
    """
    if open_arg is not None:
        return open_arg
    if conversion is not None:
        return bool(conversion & _IME_NATIVE)
    return None


# ---- AT-SPI の gdbus 出力を解く純パーサ（Mac でテスト可能）----------------------
#
# AT-SPI2 の各オブジェクトは (bus_name, object_path) で参照される。gdbus call の出力は
# GVariant テキスト形式（型タグ付き）なので、そこから必要な値を取り出す。型タグ中の数字
# （uint32 の "32" 等）を拾わないよう、数値は「英字の直後でない」ものだけ拾う。

_ATSPI_REF = re.compile(r"\('([^']*)',\s*objectpath\s*'([^']*)'\)")
# 型タグ（uint32 等）を消してから数値を拾う（"uint32 30" の 30 だけを取り、"32" を拾わない）。
_ATSPI_TYPETAG = re.compile(r"u?int(?:16|32|64)|byte|double|handle|boolean")


def _atspi_ints(out: str) -> List[int]:
    return [int(x) for x in re.findall(r"\d+", _ATSPI_TYPETAG.sub(" ", out or ""))]


def parse_atspi_ref(out: str) -> Optional[Tuple[str, str]]:
    """単一の (so) 参照（例 GetChildAtIndex）を (bus_name, object_path) にする。"""
    m = _ATSPI_REF.search(out or "")
    return (m.group(1), m.group(2)) if m else None


def parse_atspi_refs(out: str) -> List[Tuple[str, str]]:
    """a(so) の配列（例 GetChildren）を [(bus_name, object_path), ...] にする。"""
    return [(m.group(1), m.group(2)) for m in _ATSPI_REF.finditer(out or "")]


def parse_atspi_string(out: str) -> str:
    """Properties.Get の variant 包み（例 (<'New'>,)）や ('unix:...',) から文字列を取り出す。"""
    m = re.search(r"<'(.*)'>", out or "")
    if m:
        return m.group(1)
    m = re.search(r"'([^']*)'", out or "")
    return m.group(1) if m else ""


def parse_atspi_uint(out: str) -> Optional[int]:
    """先頭の数値（型タグの数字を除く）を返す。GetRole / ChildCount など。"""
    nums = _atspi_ints(out)
    return nums[0] if nums else None


def parse_atspi_state(out: str) -> int:
    """GetState の au（[low, high]）を 64bit のビットフィールドにする。"""
    nums = _atspi_ints(out)
    low = nums[0] if len(nums) >= 1 else 0
    high = nums[1] if len(nums) >= 2 else 0
    return low | (high << 32)


# AT-SPI state ビット（atspi-constants の ATSPI_STATE_*）。GetState の 64bit から見る。
_STATE_ACTIVE = 1
_STATE_CHECKED = 4
_STATE_SENSITIVE = 24


def wayland_compositor() -> Optional[str]:
    """Wayland のコンポジタ種別を env から判定する（window 操作の IPC を選ぶため）。

    "hyprland" / "sway" / None。Wayland はクロスクライアントのウィンドウ操作に統一
    プロトコルを持たないので、コンポジタ固有 IPC（hyprctl / swaymsg）に頼る。GNOME/KDE は
    その種の IPC を公開しないため None（= 未対応）。
    """
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return "hyprland"
    if os.environ.get("SWAYSOCK"):
        return "sway"
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or "").lower()
    if "hyprland" in desktop:
        return "hyprland"
    if "sway" in desktop:
        return "sway"
    return None


def parse_sway_tree(tree: Dict[str, Any]) -> List[Dict[str, Any]]:
    """swaymsg -t get_tree の JSON を {hwnd,title,pid,minimized} 列にする（純粋・再帰）。

    実ウィンドウは pid を持つ葉コンテナ（type=con/floating_con）。分割用の構造コンテナは
    pid が null なので落ちる。hwnd には sway の con id（[con_id=N] focus に使う）を入れる。
    """
    out: List[Dict[str, Any]] = []

    def walk(node: Dict[str, Any]) -> None:
        for key in ("nodes", "floating_nodes"):
            for child in node.get(key) or []:
                walk(child)
        if node.get("pid") and node.get("type") in ("con", "floating_con"):
            name = node.get("name") or ""
            app = node.get("app_id") or (node.get("window_properties") or {}).get("class")
            if name or app:
                out.append({"hwnd": int(node["id"]), "title": name,
                            "pid": int(node.get("pid") or 0), "minimized": False})

    walk(tree)
    return out


def parse_hyprland_clients(clients: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """hyprctl -j clients の JSON を {hwnd,title,pid,minimized} 列にする（純粋）。

    hwnd にはウィンドウアドレス（"0x..." を整数化）を入れる（focuswindow address:0x… に使う）。
    """
    out: List[Dict[str, Any]] = []
    for c in clients or []:
        addr = c.get("address") or ""
        try:
            hwnd = int(addr, 16)
        except (ValueError, TypeError):
            continue
        out.append({"hwnd": hwnd, "title": c.get("title") or "",
                    "pid": int(c.get("pid") or 0), "minimized": bool(c.get("hidden"))})
    return out
