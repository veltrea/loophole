# メニュー自動化（`loophole_menu`）実装計画書（テスト含む）

**対象読者:** 実装担当。設計の意図と API 契約は
[menu-automation-design.md](menu-automation-design.md) を先に読むこと。本書は
「**どのファイルを・どの順で・どうテストして**仕上げるか」を決める。

保守原則（[dev-notes.md](dev-notes.md)）の鉄則を踏襲する:

- 純粋ロジックは `server/handlers.py`（Mac で単体テスト）、Win32 直叩きは `server/win_backends.py` に隔離。
- `server/win_backends.py` を触ったら実機（対象 Windows）で疎通する。Mac のテストだけでは Win32 を検証できない。
- 標準出力は MCP の JSONL 専用。ログは stderr（[loophole stdout hygiene] の流儀）。

---

## 1. 触るファイルと責務

| ファイル | 変更 | 責務 |
|---|---|---|
| `server/handlers.py` | `MenuController` Protocol 追加 / `Handlers.__init__` に `menu=` 追加 / `_table()` に `menu_enumerate`・`menu_invoke` 追加 / 2 メソッド実装 / 破壊的ラベル判定・ツリー整形（純粋） | 引数検証・ツリー整形・`destructive_guess` 付与・発火 ID 検証 |
| `server/win_backends.py` | `Win32MenuController` 追加 / `build_handlers()` の `Handlers(...)` に `menu=` を配線 | `GetMenu`/`GetMenuItemInfoW` 再帰列挙、`PostMessageW(WM_COMMAND)` 発火（Win32 直叩き） |
| `loophole/mcp_server.py` | `loophole_menu(action, title, hwnd, command_id)` ツール 1 個追加 | `action` で 2 コマンドに振り分け、ツリーをインデント整形して返す |
| `tests/fakes.py` | `FakeMenuController` 追加 | 既知ツリーを返し、`invoke` 呼び出しを記録 |
| `tests/test_server/handlers.py` | menu 系テスト追加 | 整形・エラー・検証・助言フラグを Mac で検証 |
| `tests/test_server/win_backends.py` | Win32 ゲート付きテスト追加 | 非 Windows では skip。実機でメモ帳メニューを列挙 |
| `tests/test_mcp_bridge.py` | `loophole_menu` の整形テスト追加 | ブリッジの `action` 振り分けと整形 |

`server/agent.py` は `build_handlers()` 経由で組むので**変更不要**（[server/agent.py:143](../server/agent.py)）。
`Handlers(...)` を直接呼ぶのはテストと `build_handlers()` だけ。非 Windows 側は
`_NonWindowsStub`（[server/win_backends.py:766](../server/win_backends.py)）が `__getattr__` で全メソッドを
自動スタブ化するので、`build_handlers()` の else 節は触らなくてよい（`menu` も自動でスタブ）。

---

## 2. インターフェース設計（`server/handlers.py`）

既存の `WindowManager` / `ImeController` Protocol と同じ「backend は生の列挙だけ、整形は
handler」の分担にする。

```python
class MenuController(Protocol):
    def enumerate(self, hwnd: int) -> Optional[list]:
        """hwnd のメニューバーを再帰列挙して生ツリーを返す。
        各ノード: {"label": str, "command_id": int, "enabled": bool,
                   "checked": bool, "separator": bool, "submenu": [...]}。
        メニューを持たないウィンドウは None。"""
        ...

    def invoke(self, hwnd: int, command_id: int) -> bool:
        """command_id を WM_COMMAND として Post する。送れたら True。"""
        ...
```

`Handlers.__init__` に `menu: MenuController` を足し、`_table()`
（[server/handlers.py:166](../server/handlers.py)）に 2 エントリを追加:

```python
"menu_enumerate": self._menu_enumerate,
"menu_invoke": self._menu_invoke,
```

### handler の純粋ロジック

- **ウィンドウ解決**: `hwnd` 指定ならそのまま。`title` 指定なら既存の `activate_window`
  と同じ部分一致＋曖昧判定ロジックを流用（複数該当は発火せず候補返却）。重複を避けるため
  解決処理を小ヘルパ `_resolve_window(args)` に切り出して `activate_window` と共有してもよい。
- **`destructive_guess` 付与**: ラベルを既定正規表現（大小無視・英日）で照合。
  ```python
  _DESTRUCTIVE = re.compile(
      r"exit|quit|close|delete|remove|format|erase|overwrite|send|"
      r"削除|終了|閉じ|消去|初期化|上書き|送信", re.IGNORECASE)
  ```
  一致時のみノードに `destructive_guess: True` を付ける（助言。発火は止めない）。
- **`path` 付与**: 再帰しながらラベル経路を `>` で連結（先頭の `&` アクセラレータ記号と
  末尾 `\t<accel>` は表示用に正規化したラベルで作る）。
- **`menu_invoke` の検証**: `hwnd` は int、`command_id` は正の整数（`<= 0` は
  `HandlerError`）。`menu.invoke` が False なら「window が無い/発火拒否」を `HandlerError`。

---

## 3. Win32 実装（`server/win_backends.py` / `Win32MenuController`）

既存 `Win32WindowManager`（[server/win_backends.py:210](../server/win_backends.py)）の `_configure_user32`
パターンに倣い、`argtypes`/`restype` を明示する。使う API:

| API | 用途 |
|---|---|
| `GetMenu(hwnd)` → HMENU | メニューバー取得（NULL なら `enumerate` は None＝supported:false） |
| `GetMenuItemCount(hmenu)` → int | 項目数（-1 はエラー） |
| `GetMenuItemInfoW(hmenu, i, TRUE, &mii)` | 1 項目の情報（位置指定） |
| `PostMessageW(hwnd, WM_COMMAND, wParam, 0)` | 発火。`WM_COMMAND=0x0111`、`wParam = command_id & 0xFFFF`（メニュー由来は HIWORD=0） |

**MENUITEMINFOW 構造体**（ctypes.Structure で定義）と取得フラグ:

```python
MIIM_STATE=0x01; MIIM_ID=0x02; MIIM_SUBMENU=0x04; MIIM_STRING=0x40; MIIM_FTYPE=0x100
MFT_SEPARATOR=0x800
MFS_GRAYED=0x03; MFS_DISABLED=0x03; MFS_CHECKED=0x08
mii.fMask = MIIM_STATE | MIIM_ID | MIIM_SUBMENU | MIIM_STRING | MIIM_FTYPE
```

**ラベル取得は 2 回呼び**（可変長文字列の定石）:
1. `dwTypeData=None, cch=0` で呼ぶ → `mii.cch` に必要長が入る
2. `(cch+1)` の `create_unicode_buffer` を確保し `dwTypeData=buf, cch=cch+1` で再呼び出し

W 系 API なので**ラベルは UTF-16 のまま安全**（CP932 のダメ文字問題と無縁。
[skill windows-cmd-japanese-encoding] の罠を回避できる経路）。

**再帰**: `mii.hSubMenu` が非 NULL ならサブメニューを再帰列挙。循環防止に**深さ上限
（例 8）**と訪問済み HMENU 集合を持つ。`MFT_SEPARATOR` は `{"separator": True}` のみ。

**配線**: `build_handlers()`（[server/win_backends.py:796](../server/win_backends.py)）の `Handlers(...)`
呼び出しに `menu=Win32MenuController()`（Windows）を追加。else 節は `_NonWindowsStub` が
自動対応するので変更不要。

### フェーズ 2（任意・動的メニュー対応）

`WM_INITMENUPOPUP(0x0117)` をサブメニュー HMENU に対して `SendMessageW` で送ってから列挙すると、
開いた瞬間に項目を生成するアプリでも中身が取れる。副作用（アプリ側ハンドラが走る）があるため
**既定オフのオプション**にする。フェーズ 1 では静的列挙のみ。

---

## 4. MCP ツール（`loophole/mcp_server.py`）

`loophole_menu` を 1 個追加（[loophole/mcp_server.py](../loophole/mcp_server.py) の既存ツールに倣う）:

```python
@mcp.tool()
def loophole_menu(action: str, title: str | None = None,
                  hwnd: int | None = None, command_id: int | None = None) -> str:
    """Enumerate or invoke a classic Win32 window's menu (blind, no screenshot).

    action="list": dump the menu tree (labels + command_id) of the window matched by
        title/hwnd. Use the command_id values with action="invoke".
    action="invoke": fire the menu command command_id on the window (PostMessage
        WM_COMMAND) without navigating the menu by keyboard. Pass the hwnd that
        "list" returned.

    Only classic Win32 menu bars are supported (Notepad, many native apps). Ribbon/
    Electron/UWP report unsupported; fall back to loophole_mouse there.
    """
```

- `action="list"` → `_call("menu_enumerate", {...}, via="loophole_menu")` → ツリーを
  **インデント整形**（各発火可能項目に `[id=N]`、`destructive_guess` に `⚠`、disabled に
  `(disabled)`、separator は `---`）。
- `action="invoke"` → `_call("menu_invoke", {"hwnd":…, "command_id":…}, via=…)` →
  `f"posted command_id={…} to hwnd={…}"`。
- 不正 `action` は `_AgentError`。

---

## 5. テスト計画

### 5-1. Mac で回る純粋ロジック（`tests/test_server/handlers.py` + `tests/fakes.py`）

`FakeMenuController`（`tests/fakes.py`、[既存フェイク群:92](../tests/fakes.py) の隣に追加）:

```python
class FakeMenuController:
    def __init__(self, tree=None, supported=True):
        self.tree = tree or []          # enumerate が返す生ツリー
        self.supported = supported
        self.invoked = []               # (hwnd, command_id) を記録
    def enumerate(self, hwnd):
        return self.tree if self.supported else None
    def invoke(self, hwnd, command_id):
        self.invoked.append((hwnd, command_id)); return True
```

検証ケース:

| # | ケース | 期待 |
|---|---|---|
| 1 | 既知ツリーを `menu_enumerate` | `supported:true`、`path` 連結、`command_id` 透過 |
| 2 | メニュー無し（`supported=False`） | `{"supported": false}` |
| 3 | ラベルに「終了」「Delete」 | 該当ノードに `destructive_guess: true` |
| 4 | separator ノード | `{"separator": true}` で整形、`command_id` 無し |
| 5 | `title` 曖昧（複数該当） | `activate_window` 同様 `ambiguous` 候補返却・**未発火** |
| 6 | `title` 該当なし | `HandlerError` |
| 7 | `menu_invoke(hwnd, command_id=5)` | `FakeMenuController.invoked == [(hwnd,5)]`、`posted:true` |
| 8 | `menu_invoke` で `command_id<=0` / 非 int | `HandlerError`（発火しない） |
| 9 | `menu_invoke` で backend が False | `HandlerError` |
| 10 | checked / disabled フラグ | 整形結果に保持 |

### 5-2. MCP ブリッジ（`tests/test_mcp_bridge.py`）

- `loophole_menu("list", title=...)` がツリーをインデント整形（`[id=N]` 併記）。
- `loophole_menu("invoke", hwnd=…, command_id=…)` が `menu_invoke` を正しい引数で呼ぶ
  （既存ブリッジテストのフェイク Client に `menu_*` 応答を足す）。
- 不正 `action` でエラー。

### 5-3. Win32 実機（`tests/test_server/win_backends.py`、非 Windows は skip）

既存の Win32 ゲート（`@pytest.mark.skipif(not IS_WINDOWS, ...)`）に倣う:

- メモ帳（`notepad.exe`）を起動 → その hwnd で `Win32MenuController.enumerate` →
  「ファイル」「編集」「書式」「表示」「ヘルプ」相当のトップ項目が取れる。
- 各トップに `command_id` 付きの子があり、separator が混じる。
- `invoke` の単体は副作用が読みにくいので、安全な**トグル項目**（書式→右端で折り返し /
  Word Wrap）で「`invoke` → 再 `enumerate` で `checked` 反転」を確認（純テキスト観測）。

### 5-4. 既存スイート

`./run-tests.sh`（[run-tests.sh](../run-tests.sh)）が Mac 側の純粋ロジック＋結合を回す。
menu 系の Mac テスト（5-1・5-2）はここに自動で乗る。Win32 実機（5-3）は 対象 Windows で別途。

---

## 6. 実装フェーズ（推奨順）

1. **列挙だけ（読み取り専用＝安全）**
   `MenuController.enumerate` + `_menu_enumerate` + `Win32MenuController.enumerate` +
   `loophole_menu(action="list")` + テスト 5-1(1–6,10) / 5-2(list) / 5-3(enumerate)。
   → 対象 Windows のメモ帳でツリーが取れることを実機確認（[dev-notes.md] の疎通鉄則）。
2. **発火**
   `invoke` 系 + テスト 5-1(7–9) / 5-2(invoke) / 5-3(トグル往復)。
   → メモ帳のトグル項目で `invoke → 再列挙で checked 反転` を実機確認。
3. **安全フェーズ**
   `destructive_guess` 正規表現（テスト 5-1(3)）と、CLAUDE.md のテストループ規則
   （[menu-automation-design.md](menu-automation-design.md) §4–5）の文書化。
4. **（任意）フェーズ 2** 動的メニュー（`WM_INITMENUPOPUP`）対応。

各フェーズの終わりにコミット（自然な区切りで）。`server/win_backends.py` を変えたフェーズ 1・2 の
後は必ず 対象 Windows 実機疎通を挟む。

---

## 7. 受け入れ基準

- [ ] Mac で `./run-tests.sh` が緑（menu 系の純粋ロジック・ブリッジを含む）。
- [ ] 対象 Windows のメモ帳で `loophole_menu(list, title="メモ帳")` がツリー＋`command_id` を返す。
- [ ] `loophole_menu(invoke, hwnd, command_id)` でトグル項目が反転し、再列挙で確認できる。
- [ ] メニューを持たないアプリ（例: 設定アプリ）で `supported:false` が返る。
- [ ] 破壊的ラベルに `destructive_guess` が付く。
- [ ] MCP ツールは `loophole_menu` の **1 個だけ**増える（公開面のツール数最小）。
- [ ] README / `loophole.mcp.json` のツール一覧に `loophole_menu` を追記。

---

## 8. リスク・未決事項

- **動的メニュー**: フェーズ 1 では取りこぼしうる。`supported:true` でも子が空のサブメニューは
  動的の可能性 → フェーズ 2 か、その項目だけマウスにフォールバック。
- **発火の完了待ち**: `PostMessage` は非同期。`invoke` 直後の観測は早すぎることがある →
  テストループでは観測前に `loophole_window(list)` を一拍挟む／短い待ちを許容する。
- **破壊的判定の取りこぼし**: 正規表現は万能でない（独自ラベルの危険操作を見逃す）。
  最終防壁は app 単位の denylist（CLAUDE.md）であって正規表現ではない、と設計書に明記済み。
- **`title` 解決の共有**: `activate_window` とロジックを共有するなら、片方の変更が他方に
  波及する。共有ヘルパに切り出すなら既存テストの回帰を確認する。
