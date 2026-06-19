# loophole ワイヤープロトコル仕様

`PROTOCOL_VERSION = 1`

手元機の **クライアント**（`loophole/`）と、対象 PC のデスクトップに常駐する **agent**（`server/`）の
あいだで交わす通信の詳細仕様。この一枚だけで互換クライアント／agent を実装できることを目指す。

- 全体像・設計の背景は [architecture.md](architecture.md) を参照。
- バージョン互換の判定ロジック（接続時ネゴシエーション）は [version-negotiation.md](version-negotiation.md)。
- 正準コマンド集合は `server/protocol.py` の `PROTOCOL_COMMANDS`、契約は同 `PROTOCOL_VERSION` で固定。

---

## 1. トランスポート

- agent は **TCP** で待ち受ける。既定 `127.0.0.1:9999`（**ループバックのみ**に bind）。
- LAN の他マシンからは直接届かない。外から使うときは対象 PC へ SSH ログインし、ポートフォワードを張る:
  ```
  ssh -L 9999:127.0.0.1:9999 -N <user>@<host>
  ```
  トンネルの出口は対象 PC 側の `127.0.0.1` なので、agent には「同一マシン内からの接続」として届く。
  認証は SSH（対象 PC の sshd）に委ねられ、LAN に新しい待ち受けポートを開かずに済む。
- 任意で**共有トークン**を足せる（§5）。ループバック外への bind は警告される（推奨しない）。

## 2. フレーミング

- **1 メッセージ = 1 行の JSON + `"\n"`（JSONL / NDJSON）。**
- **`Content-Length` ヘッダーは付けない**（LSP ではなく MCP stdio と同じ流儀）。
- 文字コードは **UTF-8**。日本語はエスケープせずそのまま（`ensure_ascii=false` 相当）。
- TCP はストリームなので、受信側は改行で区切って 1 行ずつ復元する（実装は `LineBuffer`）。1 回の `recv`
  が行の途中で切れても、複数行まとまって届いても、`\n` 単位で 1 メッセージに再構成する。
- **接続モデル**: 1 つの TCP 接続上で、クライアントはリクエスト行を流し、agent は**受け取った行ごとに
  1 行のレスポンス**を順番に返す。リファレンスクライアント（`loophole/cli.py`）は「1 接続＝1 リクエスト」
  （送って 1 行読んだら閉じる）で使うが、agent 側はストリーム処理なので複数リクエストも扱える。
- 過大な行（改行が来ないまま閾値超過）は `"line too long; closing"` を返して接続を閉じる。

## 3. メッセージ封筒

### リクエスト
```json
{"id": <任意>, "cmd": "<コマンド名>", "args": { ... }}
```
- `id`: 任意の JSON 値。レスポンスにそのまま echo される（相関用）。省略可。リファレンス実装は
  接続ごとに 1 から増える整数を入れる。
- `cmd`: 文字列。必須。`PROTOCOL_COMMANDS` のいずれか（§7）。
- `args`: オブジェクト。省略時・`null` 時は `{}` 扱い。

### レスポンス（成功）
```json
{"id": <リクエストと同じ>, "ok": true, "result": <コマンド依存>}
```

### レスポンス（失敗）
```json
{"id": <リクエストと同じ>, "ok": false, "error": "<人間可読メッセージ>"}
```
- `ok` の真偽でまず成否を判定する。失敗時 `result` は無く、`error` に理由が入る。
- リクエストの解析自体に失敗した場合など `id` が取れないときは `id: null`。

## 4. 型・エンコーディングの約束

- **整数引数に真偽値は不可**: `hwnd` / `max_results` / `max_depth` / `command_id` / `conversion` /
  マウスの `x`,`y`,`count`,`dx`,`dy` などは、JSON の `true`/`false` を整数として受け付けず弾く
  （実装で `isinstance(v, bool)` を明示拒否）。
- **`encoding`**（`run`・`read_file` のみ）: `"auto"`（既定）／`"utf-8"`／`"cp932"`。
  - `auto`: まず UTF-8 厳密デコード → 失敗したら CP932 で復号。
  - 先頭の UTF-8 BOM は剥がす。`write_file` は常に UTF-8 固定（`encoding` 引数なし）。
- **スクリーンショット**は PNG を **base64**（ASCII）で返す（§7 `screenshot`）。

## 5. 認証（任意の共有トークン）

- agent を `--token <SECRET>` で起動すると、**`ping` / `hello` 以外**の全コマンドで
  `args.token == SECRET` を要求する。
- クライアントはトークン設定時、`ping`/`hello` 以外のリクエストの `args` に `"token": "<SECRET>"` を足す。
- 不一致・欠落: `{"ok": false, "error": "unauthorized: bad or missing token"}`。
- `args.via`（任意の文字列）: 呼び元ラベル。agent のコマンド履歴（`/log`）に「誰が叩いたか」として残る。
  `token` と同じく `ping`/`hello` には付かない。コマンドの動作には影響しない。

## 6. エラー（共通）

`error` 文字列の代表例:

| 状況 | `error` |
|---|---|
| リクエスト不正（`cmd` 欠落・`args` が object でない等） | `bad request: <詳細>` |
| JSON として壊れた行 | `protocol error: <詳細>`（`id` は `null`） |
| トークン不一致/欠落 | `unauthorized: bad or missing token` |
| 未知コマンド | `unknown command: <cmd> — the deployed agent doesn't implement this command, which usually means it is older than the client. Redeploy server/*.py ...` |
| コマンドの引数検証・実行失敗（`HandlerError`） | コマンド個別のメッセージ（§7） |
| 想定外の例外 | `internal error: <型>: <詳細>` |

**未知コマンドのメッセージはバージョンずれの主要な手掛かり**。クライアントが知っているコマンドを agent が
知らない＝agent が古い、を示唆する（§8・[version-negotiation.md](version-negotiation.md)）。

## 7. コマンドリファレンス（全 20）

各コマンド: **args**（key: 型, 必須/任意, 既定, 意味）／**result**（成功時の形）／**errors**（個別）／
**backend**（依存バックエンド）。`mouse_*` 以外のバックエンドは存在前提（None ガードなし）。

### 接続・診断

#### `ping`
- args: なし
- result: `{"pong": true}`

#### `hello`
- args: なし
- result: 環境情報＋版情報。
  `{"platform": str, "user": str, "session_id": int, "interactive": bool, "cwd": str,`
  `"agent_version": str, "protocol_version": int, "commands": [str, ...]}`
  - `interactive=true` / `session_id>=1` は「画面のあるデスクトップに居る」を意味する。
  - `protocol_version`/`commands` はクライアントの互換判定に使う（§8）。**古い agent はこれらを返さない**
    （その不在自体が「古い」の信号）。

### コマンド実行

#### `run`
- args（`argv` と `command` のどちらか一方必須）:
  - `argv`: [str], 任意 — シェルを介さず起動（execvp 相当）。空配列不可。
  - `command`: str, 任意 — ホストのシェル（Windows は `cmd.exe /S /C`）でワンライナー実行。
  - `cwd`: 任意 — 作業ディレクトリ。
  - `timeout`: 任意 — 秒。
  - `encoding`: 任意, 既定 `"auto"` — stdout/stderr の復号（§4）。
  - `stdin`: str, 任意 — 標準入力に流すテキスト。
- result: `{"exit_code": int, "stdout": str, "stderr": str}`
- errors: `'argv' must be an array of strings` / `'argv' must not be empty` /
  `'command' must be a string` / `run requires 'argv' or 'command'` / `failed to start process: <argv0>`
- backend: runner

#### `spawn`
- args: `argv`: [str], 必須（非空）— GUI/常駐プロセスを起動。`cwd`: 任意。
- result: `{"pid": int}`
- errors: `spawn requires non-empty 'argv' array of strings`
- backend: runner

### クリップボード

#### `clipboard_get`
- args: なし / result: `{"text": str}` / backend: clipboard

#### `clipboard_set`
- args: `text`: str, 必須 / result: `{"ok": true}`
- errors: `clipboard_set requires string 'text'` / backend: clipboard

### 画面

#### `screenshot`
- args:
  - `path`: str, 任意 — 指定すると agent ホスト上の当該パスにも PNG を保存。
  - `data`: bool, 任意, 既定 `true` — 真なら PNG を base64 で返す。
- result: `{"bytes": int}` を基本に、`path` 指定時は `"path": str`、`data` 真時は `"png_base64": str`（ASCII base64）。
- errors: `screenshot 'path' must be a string`
- backend: screenshotter（＋ `path` 指定時は filesystem）

### ファイル

#### `read_file`
- args: `path`: str, 必須（非空）。`encoding`: 任意, 既定 `"auto"`。
- result: `{"text": str}`
- errors: `read_file requires string 'path'` / `file not found: <path>`
- backend: filesystem

#### `write_file`
- args: `path`: str, 必須（非空）。`text`: str, 必須。**常に UTF-8 で書く**。
- result: `{"ok": true}`
- errors: `write_file requires string 'path'` / `write_file requires string 'text'`
- backend: filesystem

#### `find_files`
- args:
  - `root`: str, 必須（非空）— 探索開始ディレクトリ。
  - `pattern`: str, 必須（非空）。
  - `match`: 任意, 既定 `"glob"` — `"glob"`（`fnmatch`・大小無視）か `"substring"`（大小無視の部分一致）。
  - `max_results`: int, 任意, 既定 `200`（正整数）— 超過時 `truncated=true`。
  - `max_depth`: int, 任意 — `root` を 0 とする深さ（非負整数）。
  - `include_dirs`: bool, 任意, 既定 `false` — ディレクトリ名も対象に。
- result: `{"matches": [{"path": str, "size": int, "mtime": float}, ...], "truncated": bool, "scanned": int}`
  - `stat` 失敗時の要素は `size=-1, mtime=0.0`。
- errors: `find_files requires string 'root'` / `... 'pattern'` / `'match' must be 'glob' or 'substring'` /
  `'max_results' must be a positive integer` / `'max_depth' must be a non-negative integer` / `root not found: <root>`
- backend: filesystem

### キーボード

#### `send_keys`
- args: `keys`: 必須 — 文字列（単一 `"ctrl+s"`／空白区切り複数 `"win+r enter"`）か文字列配列 `["win+r","enter"]`。
  **ショートカット送出専用**（文字入力はクリップボード貼り付けで行う）。
- result: `{"sent": str, "count": int}`（`sent` は正規化後、`count` は和音数）
- errors: `send_keys requires 'keys' (a string or array of strings)` / キー解析失敗時はその ValueError メッセージ
- backend: keyboard

### マウス（`mouse` バックエンドは任意。未注入なら全 `mouse_*` がエラー）

#### `mouse_move`
- args: `x`: int 必須, `y`: int 必須 — 絶対スクリーン座標。
- result: `{"moved": true, "x": int, "y": int}`
- errors: `mouse control is not available on this agent` / `mouse requires integer 'x'`（/`'y'`）

#### `mouse_click`
- args:
  - `button`: 任意, 既定 `"left"` — `"left"/"middle"/"right"`（大小無視）か int `1..3`。
  - `x`,`y`: int, 任意 — どちらか在れば先にそこへ移動（両方 int 必須に）。
  - `count`: int, 任意, 既定 `1` — 正整数。2 でダブルクリック。
- result: `{"clicked": int, "button": str}`
- errors: `mouse control is not available on this agent` / `mouse 'button' must be left/middle/right` /
  `... (or 1..3)` / `mouse requires integer 'x'`（/`'y'`） / `mouse_click 'count' must be a positive integer`

#### `mouse_scroll`
- args: `dx`: int 任意 既定 `0`（>0 右）, `dy`: int 任意 既定 `0`（>0 下）。少なくとも一方が非ゼロ。
- result: `{"scrolled": true, "dx": int, "dy": int}`
- errors: `mouse control is not available on this agent` / `mouse_scroll 'dx' must be an integer`（/`'dy'`） /
  `mouse_scroll requires a non-zero 'dx' or 'dy'`

### ウィンドウ

#### `list_windows`
- args: `pattern`: str, 任意 — タイトル部分一致（大小無視）。`visible_only`: bool, 任意, 既定 `true`。
- result: `{"windows": [{"hwnd": int, "title": str, "pid": int, "minimized": bool}, ...], "count": int}`
- errors: `list_windows 'pattern' must be a string`
- backend: windows

#### `activate_window`
- args（`title`/`hwnd` のどちらか必須。`hwnd` 優先）:
  - `hwnd`: int, 任意 — ウィンドウハンドル。
  - `title`: str, 任意（非空）— 部分一致。複数該当なら**何も前面化せず候補を返す**。
- result:
  - hwnd 指定: `{"activated": true, "hwnd": <hwnd>}`
  - title 一意: `{"activated": true, "hwnd": <hwnd>, "title": <title>}`
  - 曖昧: `{"activated": false, "ambiguous": true, "candidates": [<window>, ...]}`
- errors: `activate_window 'hwnd' must be an integer` /
  `could not activate window hwnd=<n> (no such window or focus refused)` /
  `activate_window requires 'title' (substring) or 'hwnd' (integer)` /
  `no visible window's title contains <title>` / `could not activate window <title> (focus refused)`
- backend: windows

### IME（日本語入力）

#### `ime_get`
- args: なし
- result: IME 有り `{"supported": true, "open": bool, "conversion": int, "mode": str|null, "roman": bool}` /
  IME 無し `{"supported": false}`
  - `mode`: `"hiragana"`/`"katakana"`/`"katakana-half"`/`"alphanumeric"`/`"alphanumeric-full"`、不明ビットは `null`。
- backend: ime

#### `ime_set`
- args（`open`/`mode`/`roman`/`conversion` のうち最低 1 つ必須）:
  - `open`: bool — IME の ON/OFF。
  - `conversion`: int — 生の変換ビットフィールド。**`mode`/`roman` より優先**。
  - `mode`: str — 上記 5 種のいずれか。
  - `roman`: bool — ローマ字入力(true) / かな入力(false)。
- result: 設定後の状態（`ime_get` と同形）。
- errors: `ime_set 'open' must be a boolean` / `... 'conversion' must be an integer` /
  `... 'mode' must be one of <modes>` / `... 'roman' must be a boolean` /
  `ime_set requires at least one of 'open', 'mode', 'roman', 'conversion'` /
  `ime_set failed: the foreground window has no IME or refused the change`
- backend: ime

### メニュー（ネイティブメニューバーの列挙・実行）

#### `menu_enumerate`
- args: `title`（部分一致）/`hwnd`（int）のどちらか（`hwnd` 優先）。読み取り専用。
- result:
  - メニュー有り `{"supported": true, "hwnd": <hwnd>, "title": <title>, "items": [<item>, ...]}`
  - メニュー無し `{"supported": false, "hwnd": <hwnd>}`
  - 曖昧 `{"ambiguous": true, "candidates": [<window>, ...]}`
  - `<item>`: セパレータ `{"separator": true}` ／ 通常
    `{"label": str, "command_id": int|null, "enabled": bool, "checked": bool, "separator": false, "path": str}`
    （`command_id` はサブメニューを持つ項目では `null`。`path` はラベルのパンくず。破壊的推測時 `"destructive_guess": true`、
    子があるとき `"submenu": [<item>, ...]`）。
- errors: ターゲット解決時 — `'hwnd' must be an integer` /
  `menu command requires 'title' (substring) or 'hwnd' (integer)` / `no visible window's title contains <title>`
- backend: menu（＋ windows）

#### `menu_invoke`
- args: `title`/`hwnd`（`hwnd` 優先。`menu_enumerate` が返した `hwnd` を渡すのが推奨）。
  `command_id`: int, 必須（正整数）。
- result: `{"posted": true, "hwnd": <hwnd>, "command_id": <id>}`（`posted` は「送った」＝非同期。完了保証ではない）
  ／ 曖昧時 `{"ambiguous": true, "candidates": [...]}`
- errors: `menu_invoke requires a positive integer 'command_id'` /
  `could not post command_id=<id> to hwnd=<hwnd> (no such window, or it has no menu)` / ターゲット解決エラー
- backend: menu（＋ windows）

## 8. バージョン互換

- `hello` が返す **`protocol_version`（整数）でクライアントが機械的に互換判定**する（semver の大小では判定しない）。
- 古い agent は `protocol_version`/`commands` を返さない → 「古い」と判定。
- クライアントは `commands` と自分が必要とするコマンドを突き合わせ、**満たせないツールは公開しない**等の
  お行儀よい縮退をする。詳細は [version-negotiation.md](version-negotiation.md)。

## 9. 拡張・互換のルール

- **コマンドの追加/削除/改名、または既存コマンドの引数・戻り値（契約）を変えたら、
  `server/protocol.py` の `PROTOCOL_VERSION` を必ず +1 する。**
- 正準コマンド集合は `PROTOCOL_COMMANDS`。これと `handlers` の実コマンドの一致は
  `tests/test_protocol_surface.py` が機械的に検証する（ドリフトすればテストが落ちる）。
- `server/protocol.py` と `loophole/protocol.py` は**同一のワイヤ形式**を持つ（`diff` で一致を確認できる）。
