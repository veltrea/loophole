"""win_uia_menu.py — Windows のメニュー UIA フォールバック（comtypes 経由の UI Automation）。

win_backends.Win32MenuController の「2 段目」。GetMenu が NULL を返すモダンアプリ
（WPF / WinForms / UWP / WinUI 等＝クラシック HMENU を持たない）でも、UI Automation
（アクセシビリティ）でメニューバー → メニュー項目を辿って列挙・発火する。Linux の
LinuxMenuController（AT-SPI フォールバック）と対称の役割。

設計上の要点:
  - 依存: comtypes（pure-Python の COM ラッパー。手書きの COM vtable の罠を避けるため採用）。
    無ければ import 時点で例外 → 上位 Win32MenuController が UIA 段を黙って無効化する
    （回帰なし＝従来どおり supported:false）。任意依存なので server/requirements-optional.txt 参照。
  - スレッド: agent.py は接続ごとに別スレッドで dispatch する。UIA を MTA（フリースレッド）で
    使えば、生成した COM ポインタをスレッドを跨いで使える。そこで comtypes import の前に
    sys.coinit_flags=0（MTA）を立て、enumerate/invoke の頭で当該スレッドの COM を初期化する。
  - 合成 command_id: UIA 要素は hwnd と素直に対応しない。列挙時に各メニュー項目へ正の整数 ID を
    振り、ID→「メニューバーからの index パス」を覚える。invoke はそのパスを新しい
    ElementFromHandle から再ナビゲートして発火する（畳んだ後に要素が無効化されても堅牢。
    Linux が AT-SPI object path で再解決するのと同じ）。
  - 遅延メニュー: モダンなメニューは「開いた時だけ」子項目が生成される。列挙では各項目を
    ExpandCollapse で開いて子を読み、読み終えたら畳む（副作用あり・対話セッション前提）。

handlers.MenuController の契約（enumerate→生ツリー or None、invoke→bool）にそのまま乗る。
ツリー整形・破壊的ラベル判定・command_id<=0 拒否は handlers 側の純粋ロジックが担う。
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional

# UIA は MTA で使う（agent の worker スレッドを跨いで COM ポインタを共有するため）。
# この指定は comtypes の import より前でなければ効かない。comtypes をプロジェクト内で
# 最初に掴むのはこのモジュールだけなので、ここで立てれば確実に MTA になる。
sys.coinit_flags = 0  # 0 = COINIT_MULTITHREADED
import comtypes          # noqa: E402  (sys.coinit_flags の後で import する必要がある)
import comtypes.client   # noqa: E402

IS_WINDOWS = sys.platform == "win32"

# --- UIA の定数（UIAutomationClient.h の固定値。typelib に無いものがあるので自前定義）----
_PROP_CONTROLTYPE = 30003
_CT_MENUBAR = 50010
_CT_MENU = 50009
_CT_MENUITEM = 50011
_CT_SEPARATOR = 50038
_PAT_INVOKE = 10000
_PAT_EXPANDCOLLAPSE = 10005
_PAT_TOGGLE = 10015
_PAT_LEGACY = 10018        # UIA_LegacyIAccessiblePatternId（MSAA ブリッジ）
_SCOPE_CHILDREN = 2
_SCOPE_SUBTREE = 7
_ECS_EXPANDED = 1
_ECS_LEAFNODE = 3          # 子を持たない＝展開不能（Expand を呼ばない）
_TOGGLE_ON = 1
_STATE_SYSTEM_CHECKED = 0x10   # MSAA STATE_SYSTEM_CHECKED（WinForms 等はここでチェックを出す）

_DEPTH = 8                 # 循環・異常に深いメニューに対する安全弁（クラシック側と同値）
_MAX_CHILDREN = 200        # 1 メニューの子の上限（暴走防止）


class UiaMenuController:
    """UI Automation でモダンアプリのメニューを列挙・発火する。Windows 専用・comtypes 依存。

    __init__ は comtypes の import と UIAutomationCore.dll の typelib 生成だけ行う（重い
    CUIAutomation の生成は初回 enumerate まで遅延）。どちらかが失敗したら例外を投げ、上位の
    Win32MenuController が UIA 段を無効化する。
    """

    def __init__(self):
        if not IS_WINDOWS:
            raise RuntimeError("UiaMenuController is Windows-only")
        # typelib から IUIAutomation 系のラッパーを生成（comtypes.gen に一度だけ書き出される）。
        # 失敗（dll 無し・gen 書き込み不可など）は例外として上位に伝え、UIA 段を諦めさせる。
        comtypes.client.GetModule("UIAutomationCore.dll")
        import comtypes.gen.UIAutomationClient as UIA
        self._UIA = UIA
        self._iuia = None            # CUIAutomation の遅延生成（None=未試行 / False=不可 / obj）
        self._true = None            # CreateTrueCondition のキャッシュ
        self._id_map: Dict[int, List[int]] = {}   # 合成 ID → メニューバーからの index パス
        self._next_id = 1

    # ---- COM 下回り --------------------------------------------------------
    def _ensure_com(self) -> None:
        """呼び出しスレッドの COM を MTA で初期化する（冪等。既に初期化済みなら無害）。"""
        try:
            comtypes.CoInitializeEx()  # sys.coinit_flags（MTA）に従う
        except OSError:
            # S_FALSE（初期化済み）/ RPC_E_CHANGED_MODE（別モードで初期化済み）。
            # どちらでも MTA 経路で使えるので無視して続行する。
            pass

    def _automation(self):
        """CUIAutomation を遅延生成して返す（失敗は False を覚えて以後 None）。"""
        if self._iuia is None:
            try:
                self._iuia = comtypes.client.CreateObject(
                    self._UIA.CUIAutomation, interface=self._UIA.IUIAutomation)
            except Exception:
                self._iuia = False
        return self._iuia or None

    def _true_cond(self, iuia):
        if self._true is None:
            self._true = iuia.CreateTrueCondition()
        return self._true

    def _pattern(self, element, pattern_id, iface):
        """element の指定パターンを取得して iface に QI する。無ければ None。例外は握り潰す。"""
        try:
            unk = element.GetCurrentPattern(pattern_id)
        except Exception:
            return None
        if not unk:
            return None
        try:
            return unk.QueryInterface(iface)
        except Exception:
            return None

    # ---- メニューバー探索 --------------------------------------------------
    def _find_menubar(self, iuia, root):
        """root の配下からメニューバー（無ければメニュー）を探す。見つからなければ None。"""
        for ct in (_CT_MENUBAR, _CT_MENU):
            try:
                cond = iuia.CreatePropertyCondition(_PROP_CONTROLTYPE, ct)
                mb = root.FindFirst(_SCOPE_SUBTREE, cond)
            except Exception:
                mb = None
            if mb:
                return mb
        return None

    def _child_menu_items(self, iuia, element):
        """element 直下のメニュー項目を順序どおりに返す（[(element, controltype), ...]）。

        モダン UI では、展開後の子項目が「element の直接の子」のこともあれば、いったん
        Menu コンテナ（ポップアップ）を挟むこともある。直接の子に項目が無く Menu があれば
        その中を見る。index は enumerate と invoke で同じ並びになる（再ナビゲートの前提）。
        """
        items: List[Any] = []
        menu_container = None
        try:
            arr = element.FindAll(_SCOPE_CHILDREN, self._true_cond(iuia))
            n = min(arr.Length if arr else 0, _MAX_CHILDREN)
            for i in range(n):
                c = arr.GetElement(i)
                try:
                    ct = c.CurrentControlType
                except Exception:
                    continue
                if ct in (_CT_MENUITEM, _CT_SEPARATOR):
                    items.append((c, ct))
                elif ct == _CT_MENU and menu_container is None:
                    menu_container = c
        except Exception:
            return items
        if items or menu_container is None:
            return items
        # 直接の子に項目が無い → Menu コンテナの中を見る。
        try:
            arr2 = menu_container.FindAll(_SCOPE_CHILDREN, self._true_cond(iuia))
            n2 = min(arr2.Length if arr2 else 0, _MAX_CHILDREN)
            for i in range(n2):
                c = arr2.GetElement(i)
                try:
                    ct = c.CurrentControlType
                except Exception:
                    continue
                if ct in (_CT_MENUITEM, _CT_SEPARATOR):
                    items.append((c, ct))
        except Exception:
            pass
        return items

    def _checked(self, element) -> bool:
        """チェック状態を読む。WPF 等は TogglePattern、WinForms の ToolStripMenuItem は
        LegacyIAccessible の STATE_SYSTEM_CHECKED で出すので両方見る（実機で確定した差）。"""
        tp = self._pattern(element, _PAT_TOGGLE, self._UIA.IUIAutomationTogglePattern)
        if tp is not None:
            try:
                if tp.CurrentToggleState == _TOGGLE_ON:
                    return True
            except Exception:
                pass
        lg = self._pattern(element, _PAT_LEGACY, self._UIA.IUIAutomationLegacyIAccessiblePattern)
        if lg is not None:
            try:
                return bool(lg.CurrentState & _STATE_SYSTEM_CHECKED)
            except Exception:
                pass
        return False

    # ---- 列挙 --------------------------------------------------------------
    def enumerate(self, hwnd: int) -> Optional[List[Dict[str, Any]]]:
        self._ensure_com()
        iuia = self._automation()
        if iuia is None:
            return None
        try:
            root = iuia.ElementFromHandle(int(hwnd))
        except Exception:
            return None
        if not root:
            return None
        mb = self._find_menubar(iuia, root)
        if mb is None:
            return None  # メニューバーが無い（リボン/Electron 等）→ supported:false
        self._id_map = {}
        self._next_id = 1
        items: List[Dict[str, Any]] = []
        for i, child in enumerate(self._child_menu_items(iuia, mb)):
            node = self._build(iuia, child, 0, [i])
            if node:
                items.append(node)
        return items

    def _build(self, iuia, item, depth: int, path: List[int]) -> Optional[Dict[str, Any]]:
        element, ct = item
        if ct == _CT_SEPARATOR:
            return {"separator": True}
        try:
            name = element.CurrentName or ""
        except Exception:
            name = ""
        try:
            enabled = bool(element.CurrentIsEnabled)
        except Exception:
            enabled = True
        cid = self._next_id
        self._next_id += 1
        self._id_map[cid] = list(path)
        node: Dict[str, Any] = {
            "label": name,
            "command_id": cid,
            "enabled": enabled,
            "checked": self._checked(element),
        }
        if depth < _DEPTH:
            kids = self._expand_and_read(iuia, element, depth, path)
            if kids:
                node["submenu"] = kids
        return node

    def _expand_and_read(self, iuia, element, depth: int, path: List[int]) -> List[Dict[str, Any]]:
        """element を（必要なら）展開して子項目を読み、読後に畳む。子ツリーを返す。"""
        ec = self._pattern(element, _PAT_EXPANDCOLLAPSE, self._UIA.IUIAutomationExpandCollapsePattern)
        expanded_here = False
        if ec is not None:
            try:
                state = ec.CurrentExpandCollapseState
                if state not in (_ECS_EXPANDED, _ECS_LEAFNODE):
                    ec.Expand()
                    expanded_here = True
            except Exception:
                pass
        out: List[Dict[str, Any]] = []
        try:
            for j, kid in enumerate(self._child_menu_items(iuia, element)):
                node = self._build(iuia, kid, depth + 1, path + [j])
                if node:
                    out.append(node)
        finally:
            if expanded_here and ec is not None:
                try:
                    ec.Collapse()
                except Exception:
                    pass
        return out

    # ---- 発火 --------------------------------------------------------------
    def invoke(self, hwnd: int, command_id: int) -> bool:
        path = self._id_map.get(command_id)
        if path is None:
            return False
        self._ensure_com()
        iuia = self._automation()
        if iuia is None:
            return False
        try:
            root = iuia.ElementFromHandle(int(hwnd))
        except Exception:
            return False
        if not root:
            return False
        mb = self._find_menubar(iuia, root)
        if mb is None:
            return False
        # メニューバーから index パスを辿る。末端でない段は展開して次の段を出す。
        cur = mb
        opened: List[Any] = []
        try:
            for step, idx in enumerate(path):
                kids = self._child_menu_items(iuia, cur)
                if idx >= len(kids):
                    return False
                nxt = kids[idx][0]
                if step < len(path) - 1:
                    ec = self._pattern(nxt, _PAT_EXPANDCOLLAPSE,
                                       self._UIA.IUIAutomationExpandCollapsePattern)
                    if ec is not None:
                        try:
                            ec.Expand()
                            opened.append(ec)
                        except Exception:
                            pass
                    cur = nxt
                else:
                    return self._fire(nxt)
            return False
        finally:
            # 開けたメニューは逆順で畳んで UI を元に戻す。
            for ec in reversed(opened):
                try:
                    ec.Collapse()
                except Exception:
                    pass

    def _fire(self, element) -> bool:
        """末端のメニュー項目を発火する。Invoke → Toggle → Expand の順に試す。"""
        inv = self._pattern(element, _PAT_INVOKE, self._UIA.IUIAutomationInvokePattern)
        if inv is not None:
            try:
                inv.Invoke()
                return True
            except Exception:
                pass
        tp = self._pattern(element, _PAT_TOGGLE, self._UIA.IUIAutomationTogglePattern)
        if tp is not None:
            try:
                tp.Toggle()
                return True
            except Exception:
                pass
        ec = self._pattern(element, _PAT_EXPANDCOLLAPSE, self._UIA.IUIAutomationExpandCollapsePattern)
        if ec is not None:
            try:
                ec.Expand()
                return True
            except Exception:
                pass
        return False
