# loophole サーバー セットアップ（対象 macOS 側）

操作される側の Mac に loophole サーバー（`server/agent.py`）を入れて、ログイン中のグラフィカル
セッション（Aqua）に常駐させる手順。手元機（クライアント側）の導入は
[client-setup.md](client-setup.md) で別途行う。loophole が何をするものかは
[README.ja.md](../README.ja.md) を参照。

> **コマンドに不慣れでも大丈夫。** このページを Claude に渡して「この手順どおり対象 Mac に loophole
> サーバーを入れて」と頼めば、下のコマンドを順に実行してくれる。

サーバーは対象機の素の `python3` だけで動く。`pip` で入れる依存は無い。クリップボードは
`pbcopy`/`pbpaste`、スクリーンショットは `screencapture`、ウィンドウ操作は `osascript` を
直接叩く（どれも macOS に最初から入っている）。キーボード・マウス・IME は CoreGraphics /
Text Input Sources framework を `ctypes` で叩く。

---

## 手順1 — SSH ログインを有効にする

対象 Mac の **システム設定 → 一般 → 共有 → リモートログイン** をオンにする
（あるいはターミナルで）:

```bash
sudo systemsetup -setremotelogin on
```

手元機の公開鍵を `~/.ssh/authorized_keys` に追加しておくと、以降パスワード入力が要らない。

---

## 手順2 — Python の準備

macOS 標準の `/usr/bin/python3` でも動くが、Homebrew の `python3` を推奨する（バージョンが
新しい・更新がコントロールしやすい）。

```bash
# Apple Silicon
/opt/homebrew/bin/python3 --version

# 入っていなければ
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install python
```

---

## 手順3 — サーバーを配置

手元機からファイルを送る:

```bash
# 手元機（クライアント側）で実行:
rsync -av --delete --exclude='__pycache__' \
    /path/to/loophole/server/ \
    your-mac.local:~/loophole-agent/server/
```

スクリプトも一緒に置く:

```bash
rsync -av /path/to/loophole/scripts/mac-install-agent.sh \
    your-mac.local:~/loophole-agent/
```

---

## 手順4 — LaunchAgent として登録（推奨）

対象 Mac で:

```bash
cd ~/loophole-agent
chmod +x mac-install-agent.sh
./mac-install-agent.sh ~/loophole-agent
```

このスクリプトが行うこと:

1. `~/Library/LaunchAgents/com.loophole.agent.plist` を生成
2. `launchctl bootstrap gui/$UID` で登録（次回ログインから自動起動）
3. `launchctl kickstart` で即起動

> **なぜ LaunchAgent か:** SSH ターミナルから直接 agent を起動すると、TCC（プライバシー許可）
> ダイアログが sshd 側を許可候補として登録してしまい混乱する。LaunchAgent 経由なら
> Aqua セッション内で動くので、TCC ダイアログが期待どおり対象アプリ（python3）に出る。

登録後の確認:

```bash
launchctl print gui/$UID/com.loophole.agent | grep -E 'state|last exit'
nc -zv 127.0.0.1 9999
```

---

## 手順5 — プライバシー許可（TCC）

初回利用時、macOS が以下の許可ダイアログを出す。それぞれを許可:

| 能力 | システム設定 → プライバシーとセキュリティ |
|---|---|
| `screenshot` | **画面収録** に `python3` を追加 |
| `send_keys` / `mouse_*` / `ime_*` | **アクセシビリティ** に `python3` を追加 |
| `list_windows` / `activate_window` | **オートメーション** で `python3` → `System Events` を許可 |

許可した後はエージェントを再起動する:

```bash
launchctl kickstart -k gui/$UID/com.loophole.agent
```

> **注意:** `brew upgrade python` で Python を入れ替えると、TCC は許可を **新しい Python に対しては未許可** として扱う（cdHash が変わるため）。各許可を付け直して `kickstart -k` する。

---

## 手順6 — 手元機から SSH トンネルを張る

手元機（クライアント側）で:

```bash
ssh -L 9999:127.0.0.1:9999 -L 9998:127.0.0.1:9998 -N your-username@your-mac.local
```

別のターミナルで:

```bash
loophole hello                           # interactive=true, console_user=自分 になっていれば OK
loophole shot /tmp/check.png             # スクショ
open http://127.0.0.1:9998/              # ライブビュー（右上の "log" タブでコマンド履歴）
```

---

## アンインストール

```bash
./mac-install-agent.sh --uninstall
```

`~/loophole-agent/` のファイル本体は残す（必要なら手動で `rm -rf`）。
