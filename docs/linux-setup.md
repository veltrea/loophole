# loophole サーバー セットアップ（対象 Linux 側）

操作される側の Linux に loophole サーバー（`server/agent.py`）を入れて、グラフィカルセッションに
常駐させる手順。手元機（クライアント側）の導入は [client-setup.md](client-setup.md) で別途行う。
loophole が何をするものかは [README.ja.md](../README.ja.md) を参照。

> **コマンドに不慣れでも大丈夫。** このページを Claude に渡して「この手順どおり対象 Linux に loophole
> サーバーを入れて」と頼めば、下のコマンドを順に実行してくれる。

サーバーは対象機の素の `python3`（標準ライブラリだけ）で動く。X11 系の操作は `libX11` / `libXtst` を、
IME・メニューは `gdbus` を直接叩くので、`pip` で入れる依存は無い。能力ごとに必要なシステム
パッケージだけ入れる。

---

## 前提 — X11 か Wayland か

対応範囲が変わるので、対象機が X11 と Wayland のどちらで動いているかを先に確認する:

```bash
echo "$XDG_SESSION_TYPE"      # x11 もしくは wayland
```

- **X11** … スクショ・キー送出・ウィンドウ操作・クリップボードまでフル対応（追加ツールほぼ不要）。
- **Wayland** … スクショ・クリップボード・キー送出・ウィンドウ操作（sway/Hyprland のみ）に追加ツールが要る。

IME とメニューはどちらでも同じ手順で動く。

---

## 手順1 — SSH サーバーを入れる

手元機からは SSH トンネル越しにしか繋がらないので、対象機で sshd を有効にする。

Debian / Ubuntu:

```bash
sudo apt install -y openssh-server
sudo systemctl enable --now ssh
```

Fedora / RHEL 系:

```bash
sudo dnf install -y openssh-server
sudo systemctl enable --now sshd
```

手元機の公開鍵を `~/.ssh/authorized_keys` に追加しておくと、以降パスワード入力が要らない。

---

## 手順2 — 能力ごとのパッケージを入れる

`python3` と、使いたい能力に対応するパッケージを入れる。X11 デスクトップなら次の1行でひととおり揃う:

Debian / Ubuntu:

```bash
sudo apt install -y python3 libx11-6 libxtst6 libglib2.0-bin at-spi2-core
```

Fedora / RHEL 系:

```bash
sudo dnf install -y python3 libX11 libXtst glib2 at-spi2-core
```

各パッケージがどの機能に対応するか（不要なものは省いてよい）:

| 機能 | 必要なもの | 備考 |
|---|---|---|
| `run` / `shell` / `read`/`write_file` / `find_files` | python3 のみ | 追加不要 |
| スクショ・キー送出・ウィンドウ操作（X11） | `libx11-6` `libxtst6` | X11 デスクトップなら通常すでに入っている |
| クリップボード（X11） | 追加不要 | サーバーがプロセス内でセレクションを所有する。`xclip`/`xsel` があれば構築失敗時のフォールバックに使う |
| IME（`ime_get`/`set`） | `libglib2.0-bin`（gdbus）＋ fcitx5 か ibus が常駐 | 日本語入力環境なら通常すでに動いている |
| メニュー（`menu_*`） | `libglib2.0-bin`（gdbus）＋ `at-spi2-core` | アプリ側がアクセシビリティ（AT-SPI）を公開している必要がある |
| スクショ（Wayland） | `grim` | |
| クリップボード（Wayland） | `wl-clipboard` | |
| キー送出（Wayland） | `ydotool` ＋ `/dev/uinput` への権限 | ydotool 1.0 以降は `ydotoold` の常駐も要る |
| ウィンドウ操作（Wayland） | `sway` か `Hyprland` | GNOME/KDE Wayland は対象外（窓操作 IPC が無い） |

Wayland で追加が要るときは:

```bash
sudo apt install -y grim wl-clipboard ydotool     # 使うものだけ
```

ydotool は `/dev/uinput` への書き込み権限が要る。`input` グループに入れて udev ルールを置くか、ydotoold を
root で常駐させる:

```bash
sudo usermod -aG input "$USER"     # 入れ直し（再ログイン）後に有効
```

---

## 手順3 — サーバーを配置して動かす

公開リポジトリを対象機に取得し、グラフィカルセッション内でサーバーを起動する。

```bash
git clone https://github.com/veltrea/loophole.git ~/loophole
python3 ~/loophole/server/agent.py
```

`server/` は手元機には入れない（手元機は loophole クライアントだけ）。`git` が無ければ手元機から
`server/` を `scp` で送ってもよい。

`loophole listening on 127.0.0.1:9999` と stderr に出れば待ち受け開始。これは `127.0.0.1` だけで待ち
受け、手元機からは SSH トンネル経由でのみ届く。

> **必ずグラフィカルセッション内で起動すること。** SSH の素のシェルから起動すると `DISPLAY` /
> `WAYLAND_DISPLAY` が無く、GUI 操作（スクショ・キー・ウィンドウ）が届かない。デスクトップに
> ログインしている状態で（手順4 の自動起動、または対象機の端末から）起動する。

---

## 手順4 — ログイン時に自動起動（任意）

毎回手で起動しなくて済むよう、デスクトップのログイン時に立ち上げる。autostart エントリが最も簡単で、
グラフィカルセッションの環境（`DISPLAY` / `WAYLAND_DISPLAY`）を自動で引き継ぐ:

```bash
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/loophole.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=loophole agent
Exec=python3 /home/me/loophole/server/agent.py
X-GNOME-Autostart-enabled=true
EOF
```

`Exec=` のパスは手順3 で clone した実際のパスに合わせる（`/home/me/...` を自分のホームに）。次回ログイン
から自動で常駐する。

---

## 手順5 — 繋がるか確認する

手元機側のセットアップ（[client-setup.md](client-setup.md)）まで終わったら、Claude に `loophole_hello`
を呼んでもらう。次が返れば疎通 OK:

- `platform=linux`
- `display_server=x11`（または `wayland`）
- `interactive=true`

最初の一手として「対象 PC でテキストエディタを起動してスクショを見せて」と頼むと、相手の画面に
それが出て画像が返ってくる。

---

## うまくいかないとき

- **`interactive=false` / `display_server` が空** — サーバーをグラフィカルセッション外（素の SSH シェル等）
  で起動している。デスクトップにログインした状態で起動し直す（手順3 の注記）。
- **スクショが真っ黒** — GPU 直描画のウィンドウは X11 の取得方式では黒くなることがある。
- **キー送出が効かない（Wayland）** — `ydotool` 未インストール、`/dev/uinput` の権限不足、または
  ydotoold 未常駐（手順2）。
- **メニューが「メニューバー無し」になる** — そのアプリがアクセシビリティ（AT-SPI）を公開していない
  （Electron 等）か、`at-spi2-core` が動いていない。
- **ウィンドウ操作が効かない（Wayland）** — GNOME/KDE は対象外。sway / Hyprland で使う。
