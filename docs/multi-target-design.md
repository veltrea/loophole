# マルチターゲット運用設計書（接続先レジストリ）

**対象読者:** loophole のクライアント（MCP サーバ）に「複数の検証マシンを同時に扱う」
機能を載せる人／使う人。実装手順は本書下部のロードマップ節、保守原則は
[dev-notes.md](dev-notes.md) を参照。本書は「**何を・なぜ・どう使うか**」を決める文書。

本機能は **MCP サーバ（クライアント）層**だけで完結し、ワイヤープロトコル（client↔agent）は変えない
（[protocol.md](protocol.md) / `PROTOCOL_VERSION` に影響なし・agent 無改修＝サーバ側 0 行）。多重化は
「1 プロセス（= 1 Claude セッション）= 1 ターゲット」のプロセス分離で実現し、1 接続は多重化しない。

**実装状況**: コア（ポート分離・レジストリ・名前付きターゲット解決・`loophole_configure(name=…)`・
`loophole_status`）は**実装済み**。Phase 1 ＝ ssh -L のローカル/リモートポート分離（`LOOPHOLE_REMOTE_PORT`・
対象 agent は 9999 のまま）、Phase 2 ＝ 名前付きターゲット＋手元ポート自動採番のレジストリ
（`~/.loophole/registry.json`・`LOOPHOLE_TARGET` で選択）。**未実装の任意拡張**: 別トンネルの同一性
チェック（§3.2）・`.mcp.json` 自動編集（§4.1）・view_port 自動採番・動的切替（§8）。

---

## 1. 目的と背景

### 1.1 解決したい問題

**この機能の実装前**、loophole には**第一級のマルチターゲット機能が無かった**（本節は解決した問題の
記録）。複数ホストの同時利用が**不可能だったわけではなく**、「セッションごとに別ポートを割り当てる」
ことで実現はできた。問題は、それが**手作業で面倒・間違えやすい**点と、**既定ポートを共有したまま
並列起動すると事故る**点だった。

複数の Claude Code セッションを立ち上げ、各々が**既定ポート 9999 を共有**したまま Windows と
Linux を並列に検証しようとすると、次のいずれかが起きる:

1. 同じ環境を共有してしまう（先勝ちで張られた SSH トンネルに後続セッションが相乗りし、
   設定上は別ホストを向いているつもりが実際は同じホストを叩いている）
2. 後発のセッションがトンネルを張れず接続不能になる

**回避は今でもできる**——ポートを手で分ければよい（例: 対象側で agent を `--port 10000` で起動し、
そのセッションは `LOOPHOLE_PORT=10000`）。本設計が無くしたいのは、この手間と事故りやすさ。根っこは
[`loophole/mcp_server.py`](../loophole/mcp_server.py) の以下:

- `PORT = int(os.environ.get("LOOPHOLE_PORT", "9999"))` — ローカルのトンネルポートは
  `LOOPHOLE_PORT` で変えられるが**既定 9999**。明示しなければ全セッションが 9999 を共有する。
- トンネルは `-L {PORT}:127.0.0.1:{PORT}`（`_open_tunnel`）＝**ローカル/リモート両端が同じ
  `PORT`**。ゆえに「ローカルだけ別ポート」にはできず、別ポートにすると**対象 agent も同じポートで
  起動し直す**必要がある（agent の待受は `--port`・既定 9999）。本設計はこれを
  `-L {local}:127.0.0.1:9999`（ローカルだけ可変・リモート 9999 固定）に変え、agent を無改修にする。
- `LOOPHOLE_SSH` 環境変数で接続先（`user@host`）を 1 プロセス 1 つだけ指定する設計。
- `_open_tunnel()` は「`127.0.0.1:<PORT>` が既に開いていれば SSH を起動せず再利用する」ため、
  既定ポートを共有した 2 個目以降の MCP プロセスは黙って 1 個目のトンネルに相乗りする。

> 補足: 現行 dev 版は既に「自分が張ったトンネルでなければ**警告**する」ガードを入れてある
> （別宛先への相乗りに気づけるよう）。ただし警告するだけで**再利用は止めない**ので衝突自体は
> 未解決。本設計はこれを「ターゲットごとに別ポート＋同一性確認」で根治する（§3.2）。

### 1.2 想定する運用

クロスプラットフォーム開発では、**1 つの変更を Windows・Linux・Mac の 3 環境で同時に
検証**したい場面が常態。loophole は computer-use と組み合わせて検証作業の主役になる
ツールなので、ここが直列だと手元のメイン機が長時間占有され、せっかくのリモート
検証環境（Windows・Linux・Mac の私設テスト機群）が活きない。

**ゴール:** 3 つの Claude セッションを同時に立ち上げて、それぞれが別の検証ホストを
独立して叩ける状態にする。

---

## 2. 設計の核

### 2.1 開発機（Mac）側に「接続先レジストリ」を持つ

接続先ごとに**ローカル側のトンネルポートを 1 つずつ固定割当**し、その対応表を開発機の
ホームディレクトリに永続化する。サーバ側（agent）は今のまま `127.0.0.1:9999` 固定で
よい。違うのは「開発機の `127.0.0.1` の**どのポート**が**どのホストの 9999**に転送
されるか」だけ。

既存の `~/.loophole/config`（KEY=value・単一ターゲット用）はそのまま残し（後方互換・§5）、レジストリは
別ファイル `~/.loophole/registry.json` として新設する。保存形式は **JSON**（stdlib `json` で読み書きでき
無依存。`tomllib` は読み取り専用なので TOML 書き込みには外部 lib が要る——それを避けて JSON にした）。

`~/.loophole/registry.json`:

```json
{
  "default_target": "winpc",
  "targets": {
    "winpc":   { "ssh": "user@192.168.1.x", "local_port": 9999,  "remote_port": 9999 },
    "linux-a": { "ssh": "user@192.168.1.x", "local_port": 10000, "remote_port": 9999,
                 "ssh_key": "~/.ssh/id_ed25519", "ssh_opts": "-o ProxyJump=none" },
    "linux-b": { "ssh": "user@192.168.1.x", "local_port": 10001, "remote_port": 9999,
                 "ssh_key": "~/.ssh/id_ed25519", "ssh_opts": "-o ProxyJump=none" },
    "mac":     { "ssh": "user@192.168.1.x", "local_port": 10002, "remote_port": 9999,
                 "ssh_opts": "-o ProxyJump=none" }
  }
}
```

`192.168.1.x` は各マシンの実 IP に置き換える（例の 4 つはそれぞれ別マシン）。各ターゲットは
`local_port`（手元の転送ポート・一意）と `remote_port`（対象 agent・既定 9999）を持ち、
`loophole_configure(..., name=...)` が手元ポートを自動採番して書き込む（手で編集してもよい）。

### 2.2 ポート番号の意味付け

| ポート | 意味 |
|---|---|
| **9999** | **出荷時デフォルト**。`loophole_configure` を初めて呼んだ時の初期セットアップ用。最初のターゲットだけがこれを取る。 |
| **10000+** | 2 個目以降のターゲットに自動採番。一度割り当てたら不変。 |
| **`view_port`** | ライブビュー用（任意）。`view_port` フィールドで明示。未使用なら `null`。**固定オフセットにしない**（衝突回避のため空きから採番）。 |

採番ルール:
- 新規ターゲット追加時、レジストリ内で使用中の `local_port` と `view_port` を**全列挙**し、
  `10000` から最初の空きを `local_port` に割り当てる。
- ライブビューを使うターゲットだけ、続けて次の空きを `view_port` に割り当てる（使わないなら `null`）。
- 一度割り当てたポートは不変（剥がして再採番すると既存セッションが迷子になる）。

### 2.3 ターゲット解決の順序

MCP サーバ起動時に「今このプロセスはどのターゲットを担当するか」を以下の順で決める:

1. **環境変数 `LOOPHOLE_TARGET`**（例: `LOOPHOLE_TARGET=linux-a`）が設定されていれば
   それを採用。レジストリから `targets.linux-a` を引き、その `local_port` と `ssh` で
   トンネルを張る。
2. 未設定なら **`registry.json` の `default_target`** を採用。
3. レジストリ自体が無い・かつ `LOOPHOLE_SSH` 環境変数が直接設定されている →
   **後方互換**: その値で暗黙の `targets.default = { ssh = $LOOPHOLE_SSH, local_port = 9999 }`
   を生成して使う（永続化するかは初回 `loophole_configure` 呼び出しまで保留）。
4. どれも該当しなければ「セットアップ未了」として `loophole_status` で
   案内文を返す（既存挙動と同じ）。

### 2.4 各 Claude セッションがどのターゲットに繋がるか

プロジェクト単位で**`.mcp.json` または `.env`** に `LOOPHOLE_TARGET` を書く:

```json
{
  "mcpServers": {
    "loophole": {
      "command": "loophole",
      "args": [],
      "env": { "LOOPHOLE_TARGET": "linux-a" }
    }
  }
}
```

これで「Win 検証プロジェクトを開けば winpc に・Linux 検証プロジェクトを開けば linux-a に・
Mac 検証プロジェクトを開けば mac に」が**プロジェクトディレクトリの切り替え
だけで自動成立**する。3 プロジェクトを並列で開けば 3 OS 同時テストが成立する。

**1 セッションから複数ターゲットを叩く機能（ツール引数の `target=`）は本設計に
含めない。** 認知負荷とツールスキーマ膨張のコストに見合うユースケースが現時点では
無く、Claude が「今どこを叩いているか」を取り違えた時のリカバリが極端に面倒に
なるため。将来本当に必要になった時に乗せる。

---

## 3. トンネル管理

### 3.1 起動時

`mcp_server.py` のグローバル状態 `_tunnel: Popen | None` は、**現プロセスが担当する
1 ターゲット分のトンネル**を保持する（複数ターゲットを管理しない）。

```python
target = resolve_target()              # §2.3 の解決順
tunnel = open_tunnel(
    ssh        = target.ssh,
    local_port = target.local_port,    # ホストごとに異なる
    remote_port= 9999,                 # サーバは固定
    extra_opts = target.ssh_opts,
)
```

### 3.2 既存トンネル再利用の判定厳格化

現状の「`127.0.0.1:9999` が開いていたら再利用」は危険（別ターゲット行きのトンネルに
相乗りしてしまう）。これを「**自分のターゲットの local_port に対して張ったトンネルが
既にあれば再利用、無ければ張る**」に変える。判定方法:

1. プロセス内の `_tunnel` が `None` でなく `local_port` も一致 → 再利用
2. `_tunnel` が無いが `127.0.0.1:<local_port>` がリッスン中 → 別プロセスが既に同じ
   ターゲット用にトンネルを張っている可能性。**ポートはターゲットごとに一意**なので、自分の
   `local_port` のリスナーは原則「自分のターゲット」のはず。念のため `hello` を投げ、返る
   `user`/`platform` 等が当該ターゲットの想定と一致すれば再利用、食い違えばエラー（別ターゲットの
   剥がし忘れトンネルが居座っている）。※ `hello` はホスト名を返さないので照合は粗い——
   一意ポートが主防御、`hello` 照合は補助。
3. リッスンしていない → 新規に `ssh -L` を起動

### 3.3 終了時

`atexit` で自分のトンネルだけ閉じる。他プロセスのトンネルには触らない。

---

## 4. MCP ツール変更

### 4.1 `loophole_configure(host_ip, username, name=None, ...)`

**変更:** `name` 引数を追加（任意）。`name` が省略された場合は対話的に「このターゲットに
名前を付けてください（例: winpc, linux-a, mac）」と返し、もう一度呼んでもらう。

挙動:
1. SSH 疎通テスト
2. レジストリを読み（無ければ新規）
3. `name` のエントリが既にあれば上書き確認、無ければ新規追加
4. `local_port` を採番（初回なら 9999、以降は §2.2 の規則で 10000+）
5. レジストリを書き戻す（書き込みは atomic: tmpfile + rename）
6. 現在の作業ディレクトリの `.mcp.json` を検出して、`env.LOOPHOLE_TARGET` を `name`
   に更新する（既存 `LOOPHOLE_SSH` があれば削除）。ファイルが無ければ案内文だけ返す
7. トンネルを張り直して `hello` で疎通確認

### 4.2 `loophole_status()`

**変更:** 「今このセッションはどのターゲットに繋がっている」を返す。レジストリの内容
（ターゲット名一覧）と現在のターゲット名・local_port を含める。

```
loophole status:
  configured = true
  current_target = "linux-a"
  ssh = "user@192.168.1.x"
  local_port = 10000
  reachable = true
  registered_targets = ["winpc", "linux-a", "linux-b", "mac"]
```

### 4.3 `loophole_list_targets()`（新規・任意）

レジストリの内容を読みやすく一覧して返す。`loophole_status` の `registered_targets`
で代替できるなら作らない（ツール数最小化原則：[dev-notes.md](dev-notes.md)）。

### 4.4 既存ツールへの影響

`loophole_run`・`loophole_screenshot` 等の**全ツールに引数追加は無い**。各ツールは現状
通り「現プロセスが担当する 1 ターゲット」を黙って叩く。マルチターゲットはプロセス
（=Claude セッション）の分離で実現する。

---

## 5. 後方互換

| 旧設定 | 新設計での扱い |
|---|---|
| `LOOPHOLE_SSH=user@host` のみ環境変数で設定 | 起動時に暗黙の `targets.default` を生成して動作。レジストリ書き込みは `loophole_configure` を呼ぶまで遅延（既存ユーザの env を壊さない） |
| `LOOPHOLE_PORT=NNNN` で 9999 以外を使っていた | `targets.default.local_port = NNNN` として扱う |
| `LOOPHOLE_SSH_KEY` / `LOOPHOLE_SSH_OPTS` / `LOOPHOLE_SSH_PORT` | `targets.default` の同名フィールドにマップ |

レジストリが存在する状態で `LOOPHOLE_SSH` 環境変数が両方ある場合 → **環境変数を優先**
し、レジストリの同名 `default` エントリは無視（明示が暗黙に勝つ原則）。

---

## 6. ファイル変更計画

| ファイル | 変更内容 | 規模感 |
|---|---|---|
| `loophole/registry.py` （新規） | JSON の読み書き（atomic）、`load`/`save`/`get_target`/`add_target`/`allocate_local_port` | 80〜120 行 |
| `loophole/mcp_server.py` の `PORT` 定数 / `_open_tunnel` | 定数廃止、`resolve_target()` 呼び出し、トンネル起動引数を `target.local_port` / `target.ssh` / `target.ssh_opts` から組み立てる | 40〜60 行差分 |
| `loophole/mcp_server.py` の `loophole_configure` ツール | レジストリ更新・名前採番・`.mcp.json` 更新ロジック | 60〜80 行差分 |
| `loophole/mcp_server.py` の `loophole_status` ツール | 戻り値拡張 | 10 行差分 |
| `loophole/cli.py` | `Client` 側で接続先ポートを `target.local_port` から受け取れるよう引数追加（既に `HOST, PORT` 引数があるので渡し方の変更のみ） | 5〜10 行差分 |
| `docs/client-setup.md` | 多ターゲット運用の章を追記 | ドキュメントのみ |
| `tests/test_registry.py` （新規） | JSON 読み書き、採番、衝突回避、後方互換のテスト | 100 行前後 |

**合計コード差分: 約 250 行・サーバ側 0 行。**

---

## 7. 実装順

1. **`loophole/registry.py`** を切る（テスト同時）。プロセス起動とは独立して動くので
   先に固める
2. **`mcp_server.py` の `_open_tunnel`** を registry ベースに差し替え、`PORT` グローバル
   定数を削除（または `LEGACY_DEFAULT_PORT = 9999` にリネームし、registry 初期化時の
   既定値としてのみ使う）
3. **`loophole_configure` ツール**を新仕様（`name` + レジストリ書き込み）に拡張
4. **`loophole_status` ツール**の戻り値を拡張
5. **`docs/client-setup.md`** の多ターゲット節と、本設計書からのリンク追加
6. **検証**: 3 プロジェクト（Windows / Linux / Mac）を同時に開き、3 並列で
   `loophole_screenshot` を撃って各ホストの正しい画面が返ることを確認

---

## 8. 将来の拡張（本設計には含めない）

- **ツール引数 `target=`** で 1 セッションから複数ホストを叩く（§2.4 の通り、当面不要）
- **`loophole_use_target(name)`** での動的切替（プロセス内でトンネルを張り替える）。
  これは「1 セッションで複数ホストを順に検証する」用途が出てきた時に追加する
- **ターゲットのグループ化**（例: `group.all_linux = ["linux-a", "linux-b", "linux-c"]`）。
  同じスクリプトを複数 OS に投げる時に便利だが、まずは並列セッションで足りるはず
- **レジストリのチーム共有**（Git 管理可能な `registry.json` をリポジトリに置く）。
  個人マシンの IP を含むので慎重に。当面は個人ホームに置く

---

## 9. 受け入れ条件

- 3 つの Claude セッションを別プロジェクトディレクトリで起動し、各 `.mcp.json` に
  異なる `LOOPHOLE_TARGET` を設定すると、3 セッションが衝突なく 3 ホストを叩ける
- 既存ユーザ（`LOOPHOLE_SSH` 環境変数のみで使っている）は、本変更後も何も設定し直さず
  に従来通り動く
- `loophole_status` を見れば「今このセッションがどのホストを向いているか」が一目で
  分かる
- `loophole_configure` を呼ぶたびに、レジストリと `.mcp.json` が自動更新され、次回
  起動時に同じターゲットに繋がる
