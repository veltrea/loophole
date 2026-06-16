# loophole

**クロスプラットフォーム開発のテスト用ツール。** 手元の Mac で開発したソフトを、リモートの Windows で
動作確認したいとき、別 OS の実機に張り付かずにテストできる。構成は、操作対象の **Windows で待ち受ける
サーバー**と、手元機の **Claude Code から呼ぶクライアント**の2部。Claude の computer use などで Windows の
GUI アプリをテスト自動化するとき、**座標を触らない作業をまとめて肩代わり**する。

computer use の置き換えではなく**併用**するもの。遅い「スクショ→画像認識→座標クリック」の
回数を減らし、トークン消費を抑える。

> **対応（現状）:** 手元機 = Mac、対象 = Windows のみ。Mac→Linux など他の組み合わせは順次対応していく予定。

## できること

手元機（Mac など）から、対象の Windows に対して:

- **アプリ／プロセスを起動**する（GUI も実際に画面に出る）
- **コマンドを実行**して結果（stdout・stderr・終了コード）を受け取る
- **スクリーンショット**を撮る
- **クリップボード**でテキストを渡す／回収する（日本語 IME を通らない）
- **ファイル**を読み書きする／名前で**検索**する
- **キーボードショートカット**を送る（`ctrl+s`・`win+r` など修飾キー＋キーの組）
- **ウィンドウ**を一覧する／タイトルで**前面化**する
- **日本語 IME** の ON/OFF・変換モードを取得／切り替える
- **クラシック Win32 メニューバー**を画面を見ずに列挙し、項目を実行する

さらに、操作の様子を手元のブラウザで**見る**こともできる（read-only・`--view-port` で任意起動）:

- **対象の画面をライブで覗く**（MJPEG ストリーム）
- **実行したコマンドの履歴を見る**（新しい順の一覧）

## 構成

```
手元機（Claude Code ＋ loophole クライアント）  ──ssh -L トンネル──▶  対象 Windows（server/agent.py が常駐）
```

サーバー（`server/agent.py`）は対象 Windows の `127.0.0.1` だけで待ち受け、手元機からは SSH トンネル
経由でのみ届く（LAN にポートを開かず、認証は SSH に任せる）。**使う前に「対象 Windows でサーバーを
起動」しておくのはこのため。** トンネル自体は、初回に Claude へ「loophole の設定をして」と頼んで
接続先を一度答えておけば、以降は MCP クライアントが起動時に自動で張るので、毎回の手動操作は要らない
（[client-setup.md](docs/client-setup.md)）。

## インストール

操作する **手元機（Mac など）** と、操作される **対象 Windows** の**両方**に入れる。手順はそれぞれ別マニュアルに:

- **① 対象 Windows（サーバー側）** — OpenSSH・Python・loophole を入れてデスクトップに常駐させる → [docs/windows-setup.md](docs/windows-setup.md)
- **② 手元機（クライアント側）** — `uv` で入れて、対話セットアップを1回走らせるだけ（宛先を聞かれ、設定も Claude への登録も自動） → [docs/client-setup.md](docs/client-setup.md)

## 使い方

主な用途は、Claude Code で**リモートの Windows 上の GUI アプリをテストする**こと。典型的な流れは
——テスト対象（自作の `.exe` など）を**配置して起動** → **コマンドや操作を実行** → **スクショで状態を確認**
→ **出力やログを回収**。このうち画面を見て座標クリックする所だけ computer use に任せ、残りは loophole が
SSH コマンド並みのコストでこなす。結果として computer use の往復（スクショ→画像認識→クリック）が減り、
遅さとトークン消費が下がる。

## ドキュメント

**導入・運用**

| 知りたいこと | ドキュメント |
|---|---|
| インストール（対象 Windows・サーバー側） | [windows-setup.md](docs/windows-setup.md) |
| インストール（手元機・クライアント側） | [client-setup.md](docs/client-setup.md) |
| OpenSSH サーバーの導入 | [windows-openssh-server.md](docs/windows-openssh-server.md) |
| ログオン時にサーバーを自動起動（タスクスケジューラ） | [agent-autostart.md](docs/agent-autostart.md) |
| 別ユーザーへ代理配備・ヘッドレス運用（上級） | [operator-runbook.md](docs/operator-runbook.md) |
| アンインストール | [uninstall.md](docs/uninstall.md) |

**しくみ・開発**

| 知りたいこと | ドキュメント |
|---|---|
| しくみ・設計（なぜ SSH 越し常駐か／session 0 問題） | [architecture.md](docs/architecture.md) |
| ライブビュー（操作中の画面を read-only で確認） | [architecture.md](docs/architecture.md) |
| CLI（`loophole-cli`）の全コマンド | [cli.md](docs/cli.md) |
| スクショの backend（ddagrab／VNC・RDP の注意） | [vnc-for-computer-use-testing.md](docs/vnc-for-computer-use-testing.md) |
| 改修・テスト方針 | [dev-notes.md](docs/dev-notes.md) |

## セキュリティ

`run` / `shell` / `gui` は任意コード実行に等しい。**ローカルのテスト機専用**で、到達経路は SSH の
内側（loopback＋ポートフォワード）に限る前提。詳しくは [architecture.md](docs/architecture.md)。
