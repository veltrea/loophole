"""menu.py — Linux のメニュー backend（AT-SPI アクセシビリティツリーを gdbus で辿る）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from common_backends import SubprocessRunner
from .parsers import (
    _STATE_ACTIVE, _STATE_CHECKED, _STATE_SENSITIVE,
    parse_atspi_ref, parse_atspi_state, parse_atspi_string, parse_atspi_uint,
)


_ATSPI_ROOT = ("org.a11y.atspi.Registry", "/org/a11y/atspi/accessible/root")


class LinuxMenuController:
    """Linux アプリのメニューを AT-SPI（アクセシビリティツリー）で列挙・発火する。

    Windows の GetMenu に当たる OS レベルのメニューオブジェクトは Linux に無い。AT-SPI2
    （スクリーンリーダーが使う経路）が唯一の汎用手段で、アプリが a11y を公開していれば
    menu bar → menu → menu item を辿れ、Action.DoAction(0)（"click"）で発火できる。stdlib
    のみの制約のため python-dbus は使わず gdbus に委譲する（a11y は session bus とは別バス）。

    handlers の MenuController は (hwnd, command_id) を取るが、AT-SPI ノードは object path で
    識別され hwnd と素直に対応しない。そこで列挙時に各ノードへ合成 command_id を振って
    {id:(bus,path)} を覚え、invoke でそれを引いて DoAction する。

    対象フレームの選び方（T6）: まず hwnd から window backend（X11WindowManager）越しに
    ウィンドウのタイトルを引き、AT-SPI フレームの Name がそれに一致するものを優先する
    （完全一致 → 大文字小文字無視の部分一致）。タイトルが引けない（Wayland／backend 不在／
    一致無し）ときは従来挙動に退避する＝アクティブなフレーム、無ければメニューバーを持つ
    最初のフレーム。handlers.py には触れず、menu.py の中だけで対象解決を完結させる。

    限界: アプリが a11y 非公開／メニュー遅延生成だと列挙不可（None＝supported:false。Win32 で
    リボン/Electron が None になるのと同じ扱い）。
    """

    _MENU_ROLES = {"menu", "menu item", "check menu item", "radio menu item"}
    _DEPTH = 8

    def __init__(self, runner=None):
        self._runner = runner or SubprocessRunner()
        self._addr: Optional[str] = None
        self._id_map: Dict[int, Tuple[str, str]] = {}
        self._next_id = 1
        # 1 回の enumerate() 内だけ有効な role/name キャッシュ（T9）。同じノードの
        # role/name を走査と node 構築で重複問い合わせしないため。ツリーは enumerate 間で
        # 変わりうるので、enumerate() の先頭で必ずクリアして跨いで使い回さない。
        self._role_cache: Dict[Tuple[str, str], str] = {}
        self._name_cache: Dict[Tuple[str, str], str] = {}

    # ---- gdbus 下回り ----
    def _gdbus(self, args: List[str], address: Optional[str] = None) -> Optional[str]:
        cmd = ["gdbus", "call"] + (["--address", address] if address else ["--session"]) + args
        r = self._runner.run(cmd, None, 5.0, None)
        if not (r.started and r.exit_code == 0):
            return None
        return (r.stdout or b"").decode("utf-8", "replace")

    def _a11y(self) -> Optional[str]:
        if self._addr:
            return self._addr
        out = self._gdbus(["--dest", "org.a11y.Bus", "--object-path", "/org/a11y/bus",
                           "--method", "org.a11y.Bus.GetAddress"])
        self._addr = (parse_atspi_string(out) or None) if out else None
        return self._addr

    def _acc(self, addr, ref, method, *args):
        return self._gdbus(["--dest", ref[0], "--object-path", ref[1],
                            "--method", "org.a11y.atspi.Accessible." + method, *args], address=addr)

    def _children(self, addr, ref) -> List[Tuple[str, str]]:
        cc = self._gdbus(["--dest", ref[0], "--object-path", ref[1],
                          "--method", "org.freedesktop.DBus.Properties.Get",
                          "org.a11y.atspi.Accessible", "ChildCount"], address=addr)
        n = parse_atspi_uint(cc) if cc else 0
        refs: List[Tuple[str, str]] = []
        for i in range(min(n or 0, 50)):
            out = self._acc(addr, ref, "GetChildAtIndex", str(i))
            r = parse_atspi_ref(out) if out else None
            if r:
                refs.append(r)
        return refs

    def _name(self, addr, ref) -> str:
        # enumerate() 内ではキャッシュを引く（gdbus spawn 削減・T9）。
        ref = tuple(ref)
        if ref in self._name_cache:
            return self._name_cache[ref]
        out = self._gdbus(["--dest", ref[0], "--object-path", ref[1],
                           "--method", "org.freedesktop.DBus.Properties.Get",
                           "org.a11y.atspi.Accessible", "Name"], address=addr)
        name = parse_atspi_string(out) if out else ""
        self._name_cache[ref] = name
        return name

    def _rolename(self, addr, ref) -> str:
        # _search_role と _build_node で同じノードの role を重複問い合わせしないようキャッシュ（T9）。
        ref = tuple(ref)
        if ref in self._role_cache:
            return self._role_cache[ref]
        out = self._acc(addr, ref, "GetRoleName")
        role = parse_atspi_string(out) if out else ""
        self._role_cache[ref] = role
        return role

    def _state(self, addr, ref) -> Optional[int]:
        out = self._acc(addr, ref, "GetState")
        return parse_atspi_state(out) if out else None

    # ---- ウィンドウ → タイトル解決（T6） ----
    def _resolve_target_title(self, hwnd: int) -> Optional[str]:
        """hwnd から対象ウィンドウのタイトルを引く（window backend 越し・全例外を握りつぶす）。

        window.py を遅延 import して X11WindowManager を作り、list_windows(False) の中から
        hwnd 一致のエントリのタイトルを返す。backend 不在／Wayland／一致無し／例外は None を
        返し、呼び出し側は従来挙動（アクティブフレーム）に退避する。決してクラッシュしない。
        """
        if not hwnd:
            return None
        try:
            from .window import X11WindowManager
            wm = X11WindowManager()
            for w in wm.list_windows(False):
                if int(w.get("hwnd", -1)) == int(hwnd):
                    title = w.get("title") or ""
                    return title or None
        except Exception:
            return None
        return None

    @staticmethod
    def _title_match_rank(frame_name: str, target_title: str) -> Optional[int]:
        """フレーム名と対象タイトルの一致度を順位で返す（純粋・Mac でテスト可能）。

        0 = 完全一致（最優先）、1 = 大文字小文字無視の部分一致、None = 不一致。
        どちらかが空なら一致対象にしない（None）。数値が小さいほど優先。
        """
        if not frame_name or not target_title:
            return None
        if frame_name == target_title:
            return 0
        fn = frame_name.lower()
        tt = target_title.lower()
        if tt in fn or fn in tt:
            return 1
        return None

    # ---- メニューバー探索 ----
    def _find_menubar(self, addr, target_title: Optional[str] = None):
        """対象フレームのメニューバーを探す。

        target_title が引けていれば、その名前に一致するフレーム（完全一致 → 部分一致）を
        最優先する（T6）。一致が無い／タイトル不明のときは従来挙動: アクティブなフレーム、
        無ければメニューバーを持つ最初のフレーム。
        """
        first = None              # メニューバーを持つ最初のフレーム（最終退避先）
        active = None             # アクティブなフレームのメニューバー
        best_title = None         # タイトル一致したメニューバー
        best_rank = None          # その一致順位（小さいほど良い）
        for app in self._children(addr, _ATSPI_ROOT):
            for frame in self._children(addr, app):
                if self._rolename(addr, frame) != "frame":
                    continue
                mb = self._search_role(addr, frame, "menu bar", 0)
                if not mb:
                    continue
                if first is None:
                    first = mb
                if target_title is not None:
                    rank = self._title_match_rank(self._name(addr, frame), target_title)
                    if rank is not None and (best_rank is None or rank < best_rank):
                        best_rank, best_title = rank, mb
                        if rank == 0:
                            return mb  # 完全一致はこれ以上探す必要が無い
                if active is None:
                    st = self._state(addr, frame)
                    if st is not None and (st >> _STATE_ACTIVE) & 1:
                        active = mb
        if best_title is not None:
            return best_title      # タイトル一致を最優先
        if active is not None:
            return active          # 次にアクティブなフレーム
        return first               # 最後に最初のフレーム

    def _search_role(self, addr, ref, target, depth):
        if depth > self._DEPTH:
            return None
        if self._rolename(addr, ref) == target:
            return ref
        for c in self._children(addr, ref):
            found = self._search_role(addr, c, target, depth + 1)
            if found:
                return found
        return None

    # ---- 公開 API ----
    def enumerate(self, hwnd: int) -> Optional[List[Dict[str, Any]]]:
        addr = self._a11y()
        if not addr:
            return None
        # role/name キャッシュは enumerate ごとにクリア（ツリーは呼び出し間で変わる・T9）。
        self._role_cache = {}
        self._name_cache = {}
        # hwnd から対象ウィンドウのタイトルを引いて、一致するフレームを優先する（T6）。
        target_title = self._resolve_target_title(hwnd)
        mb = self._find_menubar(addr, target_title)
        if not mb:
            return None
        self._id_map = {}
        self._next_id = 1
        items: List[Dict[str, Any]] = []
        for top in self._children(addr, mb):
            node = self._build_node(addr, top, 0)
            if node:
                items.append(node)
        return items

    def _build_node(self, addr, ref, depth):
        role = self._rolename(addr, ref)
        if role == "separator":
            return {"separator": True}
        if role not in self._MENU_ROLES:
            return None
        cid = self._next_id
        self._next_id += 1
        self._id_map[cid] = ref
        st = self._state(addr, ref)
        node: Dict[str, Any] = {
            "label": self._name(addr, ref),
            "command_id": cid,
            "enabled": True if st is None else bool((st >> _STATE_SENSITIVE) & 1),
            "checked": False if st is None else bool((st >> _STATE_CHECKED) & 1),
        }
        if depth < self._DEPTH:
            children = [n for n in (self._build_node(addr, c, depth + 1)
                                    for c in self._children(addr, ref)) if n]
            if children:
                node["submenu"] = children
        return node

    def invoke(self, hwnd: int, command_id: int) -> bool:
        ref = self._id_map.get(command_id)
        if not ref:
            return False
        addr = self._a11y()
        if not addr:
            return False
        out = self._gdbus(["--dest", ref[0], "--object-path", ref[1],
                           "--method", "org.a11y.atspi.Action.DoAction", "0"], address=addr)
        return bool(out and "true" in out.lower())


def build_menu(runner):
    return LinuxMenuController(runner)
