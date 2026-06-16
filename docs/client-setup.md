# loophole クライアント セットアップ（手元機側）

手元機（Mac など）に loophole クライアントを置き、Claude Code から使えるようにする手順。
対象 Windows 側のサーバーは別途 [windows-setup.md](windows-setup.md) で常駐させておくこと。

> **コマンドに不慣れでも大丈夫。** このページを Claude（いま使っている AI）に渡して
> 「この手順どおり loophole をセットアップして」と頼めば、下のコマンドを順に実行してくれる。
> 自分の手で進めたい人のために、全コマンドもそのまま載せてある。

---

## 手順1 — uv を入れる

loophole は `uv`（Python 製のツールを入れて動かす道具）で導入する。まだ入っていなければ:

```bash
brew install uv
```

（`brew` は macOS 定番のインストーラ。無ければ [brew.sh](https://brew.sh) の案内どおり1行で入る。）
依存パッケージを自分で入れる作業はこの先も無い——`uv` が loophole の依存をまとめて面倒みる。

---

## 手順2 — loophole を入れる

`loophole` コマンドとしてインストールする。**全員これと同じ**——個人情報は何も含まない:

```bash
uv tool install git+https://github.com/veltrea/loophole.git
```

これで `loophole` というコマンドが使えるようになる（clone 不要）。`server/`（対象 Windows 用）は
手元機には入らない。

`uv: command not found` と出たら、手順1の `brew install uv` がまだ。先に済ませてからもう一度。

---

## 手順3 — Claude に登録する（1行）

```bash
claude mcp add loophole -- loophole
```

接続先などの設定はここでは要らない（次の手順でチャットから入れる）。登録できたら **Claude Code を一度再起動**する。

---

## 手順4 — チャットで設定を終わらせる

Claude に **「loophole の設定をして」** と頼むだけ。Claude が、ふつうの言葉で順に聞いてくる:

- **操作したい Windows PC の IP アドレス**（例: `192.168.1.x`。Windows の 設定→ネットワークとインターネット で確認できる）
- **その PC でのあなたのユーザー名**（サインインに使う名前）

答えると、Claude が `loophole_configure` を呼び、**接続テスト → 設定保存（`~/.loophole/config`）→ トンネル開通 → 疎通確認**まで自動でやって、結果を返す。SSH 鍵や踏み台といった専門設定は聞かれない（既定で動く・繋がらなければ回避設定を裏で自動で試す）。ターミナルに戻る必要はない。

> ターミナルだけで済ませたい人は、手順3・4 の代わりに `loophole --setup` を1回叩いてもいい（同じことを対話で行い、登録までやる）。

---

## これで完了 — ふだんは Claude を使うだけ

設定はここまで（手順は手順4で終わり）。**以降、手元機で毎回やる操作は無い。** Claude が loophole の
ツールを呼ぶと、MCP サーバーが自動で立ち上がって SSH トンネルを張り（終了時に畳む）、対象 Windows に
繋ぐ。前提は「対象 Windows 側で loophole サーバーが起動していること」だけ。

ちゃんと繋がっているか確かめたいとき（任意）は、Claude に `loophole_hello` を呼んでもらう
（`session_id` 1 以上・`interactive: true` なら OK）。最初の一手として「対象 Windows でメモ帳を起動して
スクショを見せて」と頼むと、相手の画面にそれが出て画像が返ってくる。

---

うまくいかないときは、まず対象 Windows 側（[windows-setup.md](windows-setup.md) のトラブルシュート）で
サーバーが対話セッションで動いているかを確認する。

### 手で設定する場合（任意）

`--setup` を使わず自分で書いてもいい。`~/.loophole/config` に `KEY=value` を並べ、登録は
`claude mcp add loophole -- loophole` を打つだけ:

```
LOOPHOLE_SSH=me@192.168.1.x
LOOPHOLE_SSH_KEY=~/.ssh/id_ed25519
```

### 手動でトンネルを張る場合（任意）

自分でトンネルを管理したいときは、`~/.loophole/config` の `LOOPHOLE_SSH` を空のままにして、
使う前に手元機からトンネルを張る（このターミナルは開いたまま）:

```bash
ssh -i ~/.ssh/id_ed25519 -L 9999:127.0.0.1:9999 -N me@192.168.1.x
```

ポートが既に開いていれば、`LOOPHOLE_SSH` を書いていても MCP サーバーは新たに ssh を起動せず、
その既存トンネルをそのまま使う（手動と自動が衝突しない）。
