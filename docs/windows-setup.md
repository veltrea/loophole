# loophole セットアップ手順（Windows マシンで設定する）

## 前提

- Windows 10 / 11
- 自分のアカウントでデスクトップにログオンできること
- loophole は手元の別マシン（Mac など）からこの Windows を操作するためのもの。手順1 の
  OpenSSH サーバー＋鍵認証が無いと接続できない。

---

## 手順1 — OpenSSH サーバーと鍵認証を設定する（手元の Mac から届くようにする）

loophole は「手元の Mac などからこの Windows を遠隔操作する」ためのもの（＝別 OS の手元機から、SSH の
セッション 0 では触れない Windows のデスクトップ・GUI・クリップボードを操作する）。そのため
**OpenSSH サーバーの有効化と鍵認証の設定が必須**で、これが無いと接続できない。loophole は
**127.0.0.1 のみ**で待ち受けるので、外からは SSH のポートフォワード経由でしか届かない（＝LAN に新しい
口を開けない・認証は SSH に任せる）。

OpenSSH サーバーの導入と鍵認証の設定は、まとめて独立記事にした →
**[Windows に OpenSSH サーバーを確実に入れる](windows-openssh-server.md)**。同梱スクリプトを走らせれば、
導入から sshd_config 調整・鍵認証まで一通り済み、手元の Mac からパスワードなしで入れる。

> **ファイアウォール:** loophole 用に 9999/9998 を**開ける必要はない**（loopback 限定のため）。
> 必要なのは SSH（22番）への接続だけで、これは OpenSSH サーバー導入時（[別記事](windows-openssh-server.md)）で許可済み。

---

## 手順2 — Python 3.10 以上をインストール

1. <https://www.python.org/downloads/windows/> から最新の **Python 3.x（64-bit）** インストーラを入手。
2. インストーラ最初の画面で **「Add python.exe to PATH」にチェック** → **Install Now**。
3. 確認: スタート → `cmd` を開いて

   ```bat
   python --version
   ```

   `Python 3.10.x` 以上が出れば OK。`server/agent.py` は**標準ライブラリだけ**で動くので、
   追加パッケージのインストールは不要。

---

## 手順3 — loophole を取得する

**Git があるなら（推奨）:**

```bat
cd %USERPROFILE%
git clone https://github.com/veltrea/loophole.git
```

**Git がないなら:** GitHub の loophole ページ → **Code → Download ZIP** → 展開した中身を
`%USERPROFILE%\loophole`（フォルダ名 `loophole`）に置く。

---

## 手順4 — ログオン時に自動起動（常駐）させる

ログオンしたら自動でエージェントが立ち上がるようにする。**どれか1つ**でよい。
いずれも「**自分のログオンセッションで動く**」ことが肝心。設定したら、次の手順5で確認できるよう
**今すぐ起動**もしておく（各方法に併記）。

### 方法A: スタートアップフォルダ（いちばん簡単・推奨）

1. メモ帳で次の内容のバッチを作り、`%USERPROFILE%\loophole` フォルダ内に `start-loophole.cmd` として保存する。
   `%~dp0` はバッチ自身のあるフォルダを指すので、置き場所に依らず動く（パスの書き換え不要）。
   `pythonw.exe` はコンソール窓を出さずに常駐させる版:

   ```bat
   @echo off
   cd /d "%~dp0"
   start "" pythonw.exe server/agent.py --port 9999 --view-port 9998
   ```

2. `Win + R` → `shell:startup` と入力 → Enter（スタートアップフォルダが開く）。
3. 先ほど作った `start-loophole.cmd` の**ショートカット**をこのフォルダに入れる
   （ファイル右クリック → コピー → スタートアップフォルダで「ショートカットの貼り付け」）。
4. 次回ログオンから自動起動。**今すぐ起動するにはバッチをダブルクリック。**

### 方法B: 付属スクリプトで Task Scheduler に登録

リポジトリの `scripts\install-agent.ps1` が、現在ログオン中のユーザー向けに
**ログオン時・対話トークン**でタスクを登録してくれる。管理者 PowerShell で:

```powershell
cd $env:USERPROFILE\loophole
powershell -ExecutionPolicy Bypass -File scripts\install-agent.ps1
schtasks /run /tn loophole       # 今すぐ起動（再ログオン不要）
```

解除は `schtasks /delete /tn loophole /f`。ライブビューも自動起動に含めるなら
`-ViewPort 9998` を付ける。オプション一覧・管理コマンドの詳細は
[agent-autostart.md](agent-autostart.md)、SSH 越しの代理登録などヘッドレス運用は
[operator-runbook.md](operator-runbook.md) を参照。

### 方法C: Task Scheduler を GUI で手動設定

1. **タスク スケジューラ** を開く → 「基本タスクの作成」。
2. トリガー: **「ログオン時」**。
3. 操作: **プログラムの開始** → プログラム `pythonw.exe`、引数
   `server/agent.py --port 9999 --view-port 9998`、開始(フォルダ) に loophole フォルダの実際のパス
   （エクスプローラで loophole フォルダを開き、アドレスバーをクリックしてコピーしたものを貼り付ける）。
4. 作成後にタスクのプロパティを開き、**「ユーザーがログオンしているときのみ実行する」を選択**
   （← 重要。「ログオンしていなくても実行する」にすると session 0 になり画面に触れない）。
5. 「最上位の特権で実行する」は**不要**（むしろ付けない方が無難）。
6. 作成したら一覧で `loophole` を右クリック →「実行」で**今すぐ起動**。

> ❌ **やってはいけない:** Windows サービス化、`SYSTEM` での実行、
> 「ユーザーがログオンしていなくても実行」。すべて session 0（非対話）になり、
> スクリーンショット／GUI が効かなくなる。

---

## 手順5 — 動作確認

手順4 でエージェントを起動したら、同じ Windows 上で疎通を確認する。`cmd` を開いて:

```bat
cd %USERPROFILE%\loophole
loophole-cli hello
```

次のように **`session_id` が 1 以上・`interactive: true`** なら成功。これが出れば
スクリーンショットも GUI 起動も実画面に効く状態。

```json
{ "session_id": 1, "interactive": true, "user": "<あなた>", "platform": "win32" }
```

```bat
:: ついでに各機能を試す
loophole-cli shell "echo %USERNAME% & ver"
loophole-cli clip-set "テスト日本語"
loophole-cli clip-get
loophole-cli gui "C:/Program Files/Mozilla Firefox/firefox.exe" "https://www.youtube.com"
```

> **`interactive: false` / `session_id: 0` が出たら**、エージェントがサービスや非対話セッションで
> 動いている。手順4 を「ユーザーがログオンしているときのみ実行」で設定し直すこと。
>
> **`connection error` が出たら**、エージェントが起動していないかポート違い。ログを見るには、
> 常駐版を止めてから foreground で起動して確認する:
>
> ```bat
> cd %USERPROFILE%\loophole
> python server/agent.py --port 9999
> ```
>
> `loophole listening on 127.0.0.1:9999` と出れば起動成功（この窓を閉じると止まる。確認用）。

---

## 手順6 — 手元の Mac から接続して操作する

手順1 で OpenSSH サーバーと鍵認証を設定し、手順4 でエージェントが常駐していれば、手元の Mac から
操作できる（Mac 側にも loophole 一式と Python が必要）。

まずフォワードを張る（このターミナルは開いたままにする）:

```bash
ssh -i ~/.ssh/id_ed25519 -L 9999:127.0.0.1:9999 -L 9998:127.0.0.1:9998 <windowsユーザー>@<WindowsのIP>
```

別のターミナルを開き、Mac 側の loophole フォルダからこの Windows を操作:

```bash
loophole-cli hello
open http://127.0.0.1:9998/       # ライブビュー（Windows クライアントなら start、Linux なら xdg-open）
```

---

## ライブビュー（リモートしている Windows の画面をブラウザで見る）

`--view-port 9998` を付けて起動すると、**この Windows の画面**をブラウザでライブに見られる
（read-only・コマンド履歴つき・付けなければ起動しない）。手元の PC から操作しているとき、
コマンドが実際にこの画面へ反映されているかを目で確認できる。手元の PC で、手順6のフォワードを
張ったうえで `http://127.0.0.1:9998/` を開く。

---

## トラブルシュート

| 症状 | 原因・対処 |
|---|---|
| `hello` が `session_id: 0` / `interactive: false` | サービス／非対話で動いている。手動 `cmd` か「ログオン時のみ実行」のタスクで起動し直す |
| `hello` が `connection error` | エージェントが起動していない／ポート違い。起動窓のログ、`--port` を確認 |
| スクリーンショットが単色（白紙） | モニタがスリープ中。画面を点けた状態で撮る（エージェントは画面を起こせない） |
| GUI を `shell` で起動したらフリーズ | `shell`＋`start` は使わない。GUI は `loophole-cli gui <exe> <引数>` で起動する |
| 起動し直したら `hello` が古い状態を返す | 前のエージェントが残って同じポートを二重 listen。タスクマネージャで古い `python(w).exe` を終了 |
| コンソール窓が出て邪魔 | `python.exe` ではなく `pythonw.exe` で起動する（方法A/B/C は対応済み） |

---

アンインストール手順は [uninstall.md](uninstall.md) を参照。
loophole の概要・仕組みは [README.md](../README.md)。
