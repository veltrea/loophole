# loophole 開発者ノート（保守ルール＋実機検証のポストモーテム）

**対象読者:** loophole を改修・保守する人。**利用者向けの導入・使い方は
[README.md](../README.md) と [windows-setup.md](windows-setup.md)** にある（この文書には
ユーザーマニュアル的な情報は置かない）。

---

## 保守ルール（普遍・触る前に読む）

### バックエンド（`server/win_backends.py`）を変更したら必ず実機で疎通する

`server/protocol.py` / `server/handlers.py` / `server/agent.py` / `server/history.py` / `server/viewer.py` は依存性注入で
外部 I/O を切り離した純粋ロジックなので Mac でフェイク注入テストできる。**しかし
`server/win_backends.py`（PowerShell によるクリップボード・スクリーンショット、Win32 セッション判定）は
その性質上フェイクで置き換えられ、テストで実コードパスを通らない。** ここがバグの温床。

- Windows 専用バックエンドを触ったら、Windows 不要の回帰テスト
  [`tests/test_server/win_backends.py`](../tests/test_server/win_backends.py) を**必ず追加・更新する**
  （PowerShell 呼び出し `_run_powershell_raw` / `_run_powershell` をフェイク化して契約を検証）。
- それに加えて、**実機（対話ログオンセッション）で最低限のスモークを必ず通す**:
  `loophole shot` が**実画面**を撮る／`clip-set`→`clip-get` で**日本語が往復**する。
  これを通すまで「動く」と言わない。

### バックエンド実装の鉄則

- **クリップボードは base64 で受け渡す。生の日本語を stdin/stdout に流さない。**
  PowerShell の `[Console]::In`（stdin）も `Get-Clipboard`（stdout）も**コンソールの入出力
  コードページ（日本語環境＝CP932）**で解釈される。エージェントは UTF-8 なので、生テキストは
  必ずズレる。base64（`A-Za-z0-9+/=`）はコードページにも CP932 ダメ文字（2 バイト目 `0x5C`）にも
  引用にも左右されない。skill `windows-cmd-japanese-encoding` §2/§4。
- **`_run_powershell_raw` は `handlers.ProcessResult` を返す（属性は `exit_code`）。**
  `subprocess.Popen` の `returncode` と取り違えない。
- **GUI／常駐／ブラウザの起動は `spawn`（出力非捕捉の Popen）で。`run`＋`start` を使わない。**
  `run` 系で起動すると、起動した GUI が捕捉済みの stdout/stderr パイプを継承し、
  `communicate()` が EOF を待ってハングする。
- **エージェントは対話ログオンセッション（`session_id ≥ 1` / `interactive: true`）でしか
  デスクトップ・クリップボードを触れない。** サービス／`SYSTEM`／非対話（session 0）では不可。
  これが配備モデル（自分のログオンセッションで起動）の根拠。
- **stdout を汚さない。** stdout は（`loophole/mcp_server.py` 経由で）MCP の JSONL チャネル。エージェントの
  ログは必ず `stderr`。検証: `python server/agent.py --port N >out 2>err` で `out` が 0 バイト。
- **スクリーンショットは read-only でディスプレイを起こせない。** モニタがスリープだと
  `CopyFromScreen` が単色を読み戻す（＝コードのバグではなく環境状態）。

### ビルド・テスト

- `./run-tests.sh` で全スイート（純 `python3`。MCP ブリッジテストのみ `uv` 経由）。
- テストは `tests/test_*.py`、フェイクは `tests/fakes.py`。

---

## ポストモーテム — 2026-06-14 実機検証で見つけた 2 つのバックエンドバグ

Mac 上の全ユニットテストが緑なのに、実 Windows（25H2・対話ログオンセッション）で
基幹機能が壊れていた。どちらもフェイク注入テストの死角だった。

### ① スクリーンショット／ライブビューが 100% 失敗（commit `f4f46a9`）

`PowerShellScreenshotter.capture()` が `result.returncode` を参照していたが、
`_run_powershell_raw` の戻りは `ProcessResult`（属性は `exit_code`）。撮影のたびに
`AttributeError` で即死し、ライブビュー `/stream` は例外を握り潰して**0 フレーム**を返していた。
→ `exit_code` に修正。`tests/test_server/win_backends.py` で成功・失敗の両パスを回帰検証。

### ② クリップボードの日本語が往復で文字化け（commit `c30d10e`）

`set` が `[Console]::In.ReadToEnd()`（CP932 入力）で UTF-8 を読み違え、`get` も
`Get-Clipboard -Raw` の stdout（CP932 出力）でズレていた。`loophole検証_表予能ソ_…` が
`loophole讀懆ｨｼ_陦ｨ莠郁・繧ｽ_…` になる。loophole 本来の目的（IME を通さない日本語入力）を直撃。
→ base64 転送に変更。ダメ文字・CP932 拡張・波ダッシュ込みで完全往復することを実機確認。

### 教訓

- **「フェイクテスト緑」＝「実機で動く」ではない。** DI でロジックは守れるが、注入で
  置き換えた実バックエンドは別途実機で疎通しないと検証されない。
- 上の保守ルール（実機スモーク必須・`tests/test_server/win_backends.py` 更新）はこの 2 件が根拠。

---

## 環境メモ（検証機 / Windows 11 25H2）

- `notepad.exe` は **System32 に存在しない**（Store アプリ化）。GUI 起動の動作確認題材に使わない。
- `cmd` を SSH 越しに実行するときは `if`／括弧を避ける（パーサが壊れて出力が途中で切れる）。
