# loophole CLI リファレンス（`loophole-cli`）

`loophole-cli` は手元機から対象 Windows の loophole を**手動で叩く**ためのクライアント CLI。
通常の利用は Claude Code から MCP で行う（[README.md](../README.md)）が、CLI は疎通確認・
切り分け・スクリプトからの呼び出しに使える。MCP と同じ操作を 1 コマンドで実行する。

接続条件は MCP と同じ。先に対象 Windows で loophole を起動し、`ssh -L 9999:127.0.0.1:9999` の
トンネルを張っておく。

## 共通オプション

| オプション | 既定 | 説明 |
|---|---|---|
| `--host` | `127.0.0.1` | 接続先（トンネルの手元側） |
| `--port` | `9999` | 接続先ポート |
| `--token` | なし | エージェントに `--token` を設定している場合に必須 |
| `--json` | — | 整形テキストではなく生の JSON レスポンスを表示 |

```bash
loophole-cli [--host H] [--port P] [--token T] [--json] <サブコマンド> [引数...]
```

## サブコマンド

| コマンド | 引数 | することと例 |
|---|---|---|
| `ping` | — | 疎通プローブ。`loophole-cli ping` |
| `hello` | — | セッション情報（`session_id` / `interactive` / `user` / `platform`）。`loophole-cli hello` |
| `run` | `-- <argv...>` | シェルを介さず argv を直接実行（`--` 以降が argv）。`loophole-cli run -- cmd /c dir` |
| `shell` | `<command>` `[--encoding auto]` | `cmd.exe /S /C` でワンライナー実行。`loophole-cli shell "echo %USERNAME% & ver"` |
| `gui` | `<argv...>` | GUI/常駐プロセスを起動して pid を返す。`loophole-cli gui "C:/Program Files/Mozilla Firefox/firefox.exe" https://example.com` |
| `clip-set` | `<text>` | クリップボードに文字列を入れる。`loophole-cli clip-set "貼り付ける文字列"` |
| `clip-get` | — | クリップボードを回収。`loophole-cli clip-get` |
| `shot` | `<remote_path>` | **エージェント側**のパスへスクリーンショットを保存。`loophole-cli shot "C:/Users/you/Desktop/shot.png"` |
| `read` | `<path>` `[--encoding auto]` | ファイルを読む。`loophole-cli read "C:/path/to/report.txt"` |
| `write` | `<path> <text>` | ファイルを書く。`loophole-cli write "C:/tmp/a.txt" "本文"` |
| `keys` | `<stroke...>` | キーボードショートカットを送る。`loophole-cli keys ctrl+s` / `loophole-cli keys win+r enter` |
| `find` | `<root> <pattern>` `[--substring] [--max N] [--depth N] [--dirs]` | 名前でファイル検索。`loophole-cli find "C:/Users/you" "*.log"` |
| `windows` | `[pattern]` | 開いているトップレベルウィンドウを列挙（任意でタイトル部分一致）。`loophole-cli windows メモ帳` |
| `activate` | `[title]` `[--hwnd N]` | タイトル部分一致または `--hwnd` でウィンドウを前面化。`loophole-cli activate Firefox` |
| `ime-get` | — | 前面ウィンドウの IME 状態を読む。`loophole-cli ime-get` |
| `ime-set` | `[--on\|--off] [--mode ...] [--roman\|--kana] [--conversion N]` | IME 状態を変更。`loophole-cli ime-set --off`（直接入力）/ `loophole-cli ime-set --on --mode hiragana --roman` |

`ime-set --mode` は `hiragana` / `katakana` / `katakana-half` / `alphanumeric` / `alphanumeric-full`。
`--conversion` は生の変換ビットフィールド（上級者向け・`--mode`/`--roman` を上書き）。

各リクエストには呼び元ラベル `loophole:<サブコマンド>` が付き、ライブビューの履歴（`/log`）に残る。
