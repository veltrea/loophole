# メニュー自動化（`loophole_menu`）使用設計書

**対象読者:** loophole にメニュー列挙・発火機能を載せる人／使う人（AI オペレータ含む）。
実装手順は [menu-automation-impl-plan.md](menu-automation-impl-plan.md)、保守の原則は
[dev-notes.md](dev-notes.md) を参照。本書は「**何を・なぜ・どう使うか**」を決める文書。

---

## 1. 目的と背景

GUI アプリのメニューを「総当たり」で実行して挙動を確かめたい、という用途がある
（自作アプリの回帰テスト、メニュー項目の死活確認など）。素朴にやるなら
`Alt → F → ↓↓ → Enter` のようにキーで辿るが、これは:

- 前面ウィンドウ・フォーカス・タイミングに依存して**脆い**
- 1 手ごとに画面を見ないと「今どこを選んでいるか」分からず**遅い**（スクリーンショット律速）
- メニューの**全体像が事前に分からない**ので「総当たりの母集合」が確定しない

loophole の速度優位は「**ピクセルを見ずに観測・操作できる操作**」に宿る（`shell` /
`find_files` / `clipboard` / `send_keys` が構造化テキストで即返るから速い）。この設計は
その思想をメニューに広げる。

### 速度を決める 3 段モデル（設計の羅針盤）

| 段 | フィードバックループ | 速度 |
|---|---|---|
| ① オープンループ（手順既知） | 観測ゼロで撃つ | 最速 |
| ② クローズドループ・テキスト観測 | `list_windows` / ログ差分 / 再列挙で確認 | まだ速い |
| ③ クローズドループ・ピクセル観測 | スクリーンショット必須 | computer-use と同速 |

メニュー発火を**キー操作ではなくコマンド ID 直接発火**にすると、列挙で全項目を先に
取得し（母集合確定）、各項目を観測ゼロで撃てる（①）。実行結果はアプリのログ差分や
再列挙のチェックビットで観測でき（②）、ピクセルにしか出ない時だけ③に落ちる。

---

## 2. 提供するもの

### MCP ツール（公開面は 1 つ）

```
loophole_menu(action, title=None, hwnd=None, command_id=None)
```

- `action="list"` … 対象ウィンドウのメニューツリーを列挙して返す
- `action="invoke"` … `command_id` のメニューコマンドを発火する（`WM_COMMAND` を Post）

ウィンドウ操作（`loophole_window`）・マウス（`loophole_mouse`）と同じ「**モダリティで
1 ツールに畳む**」規律に従い、列挙と発火を別ツールに割らない。MCP のツールスキーマは
毎ターン載る常駐コンテキストなので、能力を増やしてもツール数は最小に保つ。

### ワイヤープロトコル（エージェント内部）

handler 層はテスタビリティのため 1 コマンド = 1 メソッドに保つ（既存の流儀）:

- `menu_enumerate` … `{title|hwnd}` → メニューツリー
- `menu_invoke` … `{hwnd, command_id}` → 発火結果

MCP の `loophole_menu` がこの 2 コマンドを `action` で振り分ける。

---

## 3. API 契約

### `action="list"`（列挙）

**入力**（`title` か `hwnd` のどちらか必須。`hwnd` 優先）

| 引数 | 型 | 説明 |
|---|---|---|
| `title` | str | ウィンドウタイトルの部分一致（大小無視）。複数該当なら発火せず候補を返す |
| `hwnd` | int | ウィンドウハンドル直指定（`loophole_window` の結果から） |

**戻り値（result）**

```json
{
  "supported": true,
  "hwnd": 133106,
  "title": "*無題 - メモ帳",
  "items": [
    {
      "label": "ファイル(F)",
      "command_id": null,
      "enabled": true, "checked": false, "separator": false,
      "path": "ファイル",
      "submenu": [
        {"label": "新規(N)\tCtrl+N", "command_id": 1, "enabled": true,
         "checked": false, "separator": false, "path": "ファイル > 新規"},
        {"separator": true},
        {"label": "終了(X)", "command_id": 5, "enabled": true, "checked": false,
         "separator": false, "path": "ファイル > 終了", "destructive_guess": true}
      ]
    }
  ]
}
```

- `command_id`: 発火に使う数値 ID（`wID`）。サブメニューを束ねる項目は `null`。
- `path`: ルートからのラベル経路（人間とログ向け。`>` 区切り）。
- `destructive_guess`: ラベルが破壊的操作の語に一致したときだけ付く**助言フラグ**（§5）。
- メニューを持たないウィンドウ（リボン UI / Electron / UWP 等）: `{"supported": false}`。

MCP ツールはこのツリーを**インデント付きの読みやすい一覧**に整形して返す（各発火可能項目に
`command_id` を併記）。

### `action="invoke"`（発火）

**入力**

| 引数 | 型 | 説明 |
|---|---|---|
| `hwnd` | int | 対象ウィンドウ（**`list` が返した `hwnd` をそのまま渡す**のが安全） |
| `command_id` | int | 発火するコマンド ID（`list` の `command_id`）。正の整数のみ |

`title` でも発火できるが、列挙時に確定した `hwnd` を渡す方が曖昧さが無い。

**戻り値**

```json
{"posted": true, "hwnd": 133106, "command_id": 5}
```

`PostMessage` は非同期（戻り値は「送信できた」であって「コマンドが完了した」ではない）。
完了とその結果は呼び側が観測する（§4）。

---

## 4. テストループ（CLAUDE.md に置くルールの骨子）

```
1. loophole_menu(list, title="MyApp")        # コマンド ID ツリーを取得（母集合確定）
2. 除外フィルタ                                # destructive_guess と明示 denylist を落とす
3. 各 安全コマンドについて:
     a. 観測ベースラインを取る                 # ログサイズ / loophole_window(list) のスナップ
     b. loophole_menu(invoke, hwnd=…, command_id=…)
     c. ダイアログ検知: loophole_window(list)  # 新規トップレベル窓が増えたか
          → 増えていれば: タイトル記録 → Esc / 閉じる で必ず畳む（次の発火が漏れないように）
     d. 結果観測:
          - アプリのログ差分（loophole_read_file）   ← 自作アプリで最強
          - トグル項目なら再 list して checked 反転を確認（ログ不要・純テキスト）
          - 上記で分からない時だけ loophole_screenshot（③に落ちる）
     e. 表に記録（path / command_id / 観測結果 / ダイアログ有無）
4. レポート
```

観測の優先順位は「**ログ差分・再列挙 → 窓検知 → 最後にスクショ**」。テキストで観測できる
限り②の速度を保てる。

---

## 5. 安全設計（必須・省略不可）

AI が総当たりでメニューを撃つと、「削除」「上書き保存」「送信」「終了」のような
**戻せない操作**を踏みうる。次を設計に内蔵する。

1. **dry-run 先行。** 初回は `list` だけで発火しない。人間が母集合と除外対象を確認してから
   `invoke` フェーズに進む。
2. **破壊的コマンドの助言フラグ。** `list` の handler が、ラベルを既定の正規表現
   （大小無視・英日両対応: `exit|quit|close|delete|remove|format|erase|overwrite|`
   `削除|終了|閉じ|消去|初期化|上書き|送信` 等）と照合し、一致項目に
   `destructive_guess: true` を付ける。**handler 自身は発火を止めない**（どの ID が
   破壊的かは `command_id` だけからは判定できず、ラベルを持つ列挙側でしか分からないため）。
   実際の除外は呼び側（CLAUDE.md ルール）が `destructive_guess` と明示 denylist で行う。
3. **ダイアログトラップ。** 各発火後に `loophole_window(list)` で新規窓を検知し、modal が
   出たら次の発火前に必ず畳む。畳まないと後続の発火がダイアログに吸われ、母集合がずれる。
4. **発火 ID の検証。** `invoke` は `command_id` が正の整数であることだけ検証する
   （`0` = コマンドなし/ポップアップ束ね項目は拒否）。

> **境界の明示はユーザーの責務。** 「このアプリのどのメニューを総当たりしてよいか／絶対に
> 押すな」は app 固有なので、テスト対象ごとに CLAUDE.md（または専用スキル）に
> denylist を書く。本機能は「安全に撃つ道具」を提供するが、「何を撃ってよいか」は決めない。

---

## 6. 対応範囲と境界（正直に）

| 対象 | 列挙 | 発火 | 経路・備考 |
|---|---|---|---|
| クラシック Win32 メニューバー（メモ帳・多くの自作 Win32 アプリ・FileMaker 等） | ✅ | ✅ | **1 段目**: `GetMenu` でメニュー取得、`WM_COMMAND` で発火（高速・ブラインド・副作用なし） |
| WPF / WinForms / UWP / WinUI（クラシック HMENU 無し） | ✅ | ✅ | **2 段目**: UIA フォールバック（§6.1）。`comtypes` が要る。展開の副作用あり |
| リボン UI（Office）/ Electron | △ | △ | UIA でも安定しない best-effort。多くは `{"supported": false}` → ③（ピクセル）に落ちる |
| 動的メニュー（開いた時だけ項目生成） | ✅ | ✅ | UIA 段は ExpandCollapse で開いて読むので拾える。1 段目（クラシック）では取りこぼしうる |
| コンテキストメニュー（右クリック） | ❌ | — | メニューバーが対象。右クリックメニューは別機構（将来検討） |
| システムメニュー（タイトルバー左） | 既定で対象外 | — | 既定では列挙に含めない |

**判定の当たり:** クラシックメニューを持つアプリは 1 段目で確実に効く。HMENU を持たない
モダンアプリ（WPF/WinForms/UWP/WinUI）は 2 段目の UIA フォールバックで拾える。リボン
（Office）/ Electron は best-effort なので、`supported:false` を見たらスクショ＋マウス
（`loophole_mouse`）にフォールバックする。

### 6.1 UIA フォールバック（2 段目）の設計

クラシックの `GetMenu` が NULL を返す（＝ HMENU を持たない）ウィンドウに対して、UI Automation
（アクセシビリティ）でメニューバー → メニュー項目を辿る。**Linux の AT-SPI フォールバックと
対称**の役割で、実装は `server/win_uia_menu.py`（`UiaMenuController`）。

- **依存（任意）:** `comtypes`（pure-Python の COM ラッパー）。手書きの COM vtable の罠を避ける
  ために採用。**無ければ UIA 段は黙って無効化**され、従来どおり `supported:false` を返すだけ
  （回帰なし）。導入は `pip install -r server/requirements-optional.txt`。
- **合成 `command_id`:** UIA 要素は hwnd と素直に対応しないので、列挙時に各項目へ正の整数 ID を
  振り、ID →「メニューバーからの index パス」を覚える。`invoke` はそのパスを新しい
  `ElementFromHandle` から**再ナビゲート**して `InvokePattern.Invoke` する（畳んだ後に要素が
  無効化されても堅牢。Linux が AT-SPI object path で再解決するのと同じ思想）。
- **遅延メニューと副作用:** モダンなメニューは「開いた時だけ」子項目が生成される。列挙では各項目を
  `ExpandCollapse` で**開いて子を読み、読後に畳む**。そのため UIA 段はクラシック段と違い
  **メニューが一瞬開く副作用**があり、**対話セッション（interactive）が前提**。設計の 3 段モデル
  （§1）でいうと、UIA 列挙は「②に近い（テキスト観測だが副作用あり）」に位置する。
- **スレッド:** agent は接続ごとに別スレッドで dispatch するため、UIA は MTA（フリースレッド）で
  使う（`win_uia_menu` が `comtypes` import 前に `sys.coinit_flags=0` を立て、各呼び出しの頭で
  当該スレッドの COM を初期化する）。

---

## 7. なぜキー操作でなくコマンド ID 発火か（設計判断の記録）

- **決定的:** `WM_COMMAND` はフォーカス・前面状態・キーリピートに依存しない。キーナビは
  「窓が前面か」「IME がオフか」などに左右されて脆い。
- **ブラインド:** 列挙で全 ID が手に入るので、撃つ間に画面を見る必要がない（①）。
- **網羅的:** 列挙＝母集合。総当たりの対象集合が事前に確定する。
- **テスト容易:** 列挙ツリーの整形・除外フィルタは純粋ロジックなので Mac で単体テストできる
  （Win32 は `win_backends` に隔離）。既存アーキテクチャにそのまま乗る。

この用途に限れば、マウスもキーナビも要らない。`loophole_mouse` はメニューが
`supported:false` の③ケース専用の保険として残る。
