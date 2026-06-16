# loophole のしくみと設計

なぜ loophole が必要なのか、どういう構造で SSH 越しに Windows のデスクトップを操作するのか。
導入・使い方は [README.md](../README.md) と [windows-setup.md](windows-setup.md) にある。

## 背景

GUI アプリの自動テストには、座標を狙ったクリックやドラッグができる道具——computer use など、画面を
直接操作するツール——が要る。ただし computer use はスクリーンショット → 画像認識 → 座標クリックを
繰り返すため、実行が遅くトークン消費も大きい。テキスト入力をキーストロークで送ると、日本語 IME が
変換を横取りして不安定になることもある。

loophole は、このうち座標操作を必要としない部分を引き受ける。アプリを起動する、コマンドを実行する、
状態をスクリーンショットで確認する、テキストをクリップボード経由で渡す（IME を通さない）、出力ファイルを
回収する——といった作業を、SSH でコマンドを叩くのと同じコストでこなす。座標をさわる部分だけ computer
use に任せればよくなる。

もともとは computer use で接続する前の準備（アプリの起動・ファイル配置）に使う想定で作り、クリップ
ボード転送やファイル入出力を足していった。

## 意外な落とし穴：SSH では GUI アプリを起動できない問題

SSH で Windows にログインすると、`dir` や `python script.py` のようなコマンドは動く。だが GUI
アプリを起動しても画面には出ず、`0xC0000142` で失敗する。

SSH 経由で起動されたプロセスは、画面・キーボード・マウス・クリップボードを持たない（Windows Vista
以降、ユーザーのデスクトップから分離されている）。Windows では、サービスのような GUI 不要のプロセス
用の区分を「セッション 0」と呼ぶ。このため:

- GUI アプリを起動しても、表示する画面がなく失敗する
- スクリーンショットが撮れない
- クリップボードを読み書きできない

起動するだけなら `schtasks /it`（interactive token）でログオン中のデスクトップにアプリを起動できる。
ただし手間がかかるうえ、AI に手順を的確に事前説明しておかないと、試行錯誤を繰り返してトークンを無駄に
消費する。さらに標準出力が使えないため、テスト結果を受け取りやすいようソフト側を自動テスト向けに作り
込んでも、結果はファイル経由のやり取りになる。これも手間で、やはり AI に毎回の説明が要る。

起動などの簡単な操作をネット越しに実行できるサーバと、その機能をツールのデスクリプションに定義した
MCP サーバをセットで使えば、AI は毎回の手順説明なしに、当たり前のように Windows を操作できるように
なる。

## しくみ

loophole はログオン中のデスクトップ（セッション 1 以降）に常駐するプロセス。SSH からそこへ TCP で
命令を渡す。命令はセッション 0 の外——画面のあるユーザーのデスクトップ——で実行されるので、GUI が
画面に出て、スクリーンショットもクリップボードも扱える。

## アーキテクチャ

```
手元機  loophole/（MCP: mcp_server.py ／ CLI: cli.py）
  │  ssh -L 9999:127.0.0.1:9999 -N …       （転送専用の SSH トンネル）
  ▼
対象 Windows sshd（セッション0）──loopback──▶ server/（agent.py がデスクトップセッションに常駐）
                                          ├ run / shell  : コマンド実行（stdout/stderr/exit）
                                          ├ spawn        : GUI 起動（任意の GUI アプリ・画面に出る）
                                          ├ clipboard_*  : クリップボード読み書き（IME を通らない）
                                          ├ screenshot   : 全画面 PNG
                                          └ read/write_file
```

- **loophole は 対象 Windows のループバック（`127.0.0.1`）だけに bind する。** LAN の他マシンからは
  直接届かない。外から使うには 対象 Windows へ SSH ログインして `ssh -L` トンネルを張る——その出口は
  対象 Windows 側の `127.0.0.1` に繋がるので、loophole には「同じマシン内からの接続」として届く。
  だから認証は SSH（対象 Windows の sshd）に任せられ、LAN に新しい待ち受けポートを開かずに済む。
  任意で共有トークンも足せる。
- プロトコルは **JSONL**（1 行 1 メッセージ + `\n`、**Content-Length なし**＝MCP stdio と同じ）。

## モジュール構成

server と client は別ディレクトリの**自己完結ユニット**。共有ディレクトリは無く、ワイヤ形式は
両者が**同一の `protocol.py` を各自持つ**（`diff server/protocol.py loophole/protocol.py` で一致を確認できる）。

**server/（対象 Windows のデスクトップセッションで常駐）**

| ファイル | 役割 |
|---|---|
| `server/agent.py` | TCP 配線・リクエスト振り分け・トークン認証 |
| `server/handlers.py` | コマンドのロジック。外部 I/O は**依存性注入**で受け取る |
| `server/win_backends.py` | 実 OS バックエンド（subprocess / clipboard・screenshot を Win32 直叩き(ctypes) / Win32 session 判定。GPU 描画キャプチャは FFmpeg(ddagrab) にフォールバック） |
| `server/viewer.py` / `server/history.py` | ライブビュー（read-only MJPEG）とコマンド履歴 |
| `server/keys.py` | キーコード表 |
| `server/protocol.py` | JSONL の encode/decode・TCP ストリーム再構成・出力バイト復号 |

**loophole/（手元機で動く）**

| ファイル | 役割 |
|---|---|
| `loophole/cli.py` | クライアント CLI（`Client` クラスを含む） |
| `loophole/mcp_server.py` | MCP ブリッジ（stdio↔TCP） |
| `loophole/protocol.py` | `server/protocol.py` と同一のワイヤ形式 |

テストの回し方・改修時の注意（Mac はフェイク注入で検証し、実機疎通は対象 Windows で確認する）は
[dev-notes.md](dev-notes.md) にある。

## ライブビュー（任意・read-only）

loophole が操作している**対象の画面**と**コマンド履歴**を、手元のブラウザでライブに見られる
read-only の窓。入力経路は持たないので VNC/RDP のリモート操作とは別物（「今なにをしているか」を
覗くだけ）。**`--view-port` を付けたときだけ**起動し、付けなければゼロ負荷・無表示。

```powershell
python server/agent.py --port 9999 --view-port 9998     # 127.0.0.1:9998 に MJPEG を出す
```

手元機ではビュー用ポートも同じ SSH 接続でフォワードし、ブラウザで開く:

```bash
ssh -i ~/.ssh/id_ed25519 -L 9999:127.0.0.1:9999 -L 9998:127.0.0.1:9998 -N <ユーザー>@<WindowsのIP>
open http://127.0.0.1:9998/        # 左=画面ライブ / 右=コマンド履歴（/log で履歴を全幅表示）
```

履歴には各コマンドの時刻・呼び元（`via`）・対象・成否が新しい順に並ぶ。見ている人がいる時だけ
撮るので、誰も見ていなければ capture は走らない。

