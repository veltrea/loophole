# xfreerdp を入れる

RDP クライアント `xfreerdp`（FreeRDP）を、接続元マシンに導入する手順。OS ごとに以下。

## バイナリ名（バージョン差）

| FreeRDP | バイナリ | 備考 |
|---|---|---|
| 2.x | `xfreerdp` | X11 クライアント |
| 3.x | `xfreerdp`（Homebrew）/ `xfreerdp3`（一部 Linux パッケージ） | X11 クライアント。要 X サーバー |

macOS の Homebrew では `xfreerdp`（X11／要 X サーバー）と `sdl-freerdp`（SDL／X サーバー不要）の両方が入る。導入後、実際に入ったバイナリ名は次で確認する:

```bash
ls "$(brew --prefix 2>/dev/null || echo /usr)/bin" | grep -i freerdp
```

---

## macOS（Homebrew）

```bash
# 1. Homebrew が無ければ入れる
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# 2. FreeRDP 本体（xfreerdp と sdl-freerdp の両方が入る）
brew install freerdp

# 3. 動作確認
xfreerdp /version
```

macOS では **`sdl-freerdp` を使うのが手軽**。SDL ベースで **XQuartz 不要**、`brew install freerdp` で一緒に入る。接続コマンドの `xfreerdp` を `sdl-freerdp` に置き換えるだけ。

X11 版の `xfreerdp` を使いたいときだけ X サーバー（XQuartz）を入れる。入れないと `$DISPLAY` エラーになる:

```bash
brew install --cask xquartz   # 入れたら一度ログアウト/ログインで反映される
```

`command not found` のときは上の「バイナリ名」確認コマンドで実体名（`sdl-freerdp` / `xfreerdp3` 等）を調べる。

---

## Linux

### Debian / Ubuntu（apt）

```bash
sudo apt update
sudo apt install -y freerdp2-x11      # バイナリ: xfreerdp
xfreerdp /version
```

新しめのディストリで `freerdp2-x11` が無い場合は `freerdp3-x11`（バイナリは `xfreerdp3`）を入れる:

```bash
sudo apt install -y freerdp3-x11
xfreerdp3 /version
```

### RHEL / AlmaLinux / Rocky（dnf）

```bash
sudo dnf install -y epel-release       # 未導入なら
sudo dnf install -y freerdp            # バイナリ: xfreerdp
xfreerdp /version
```

---

## 接続テスト

導入できたら 対象 Windows（<host>）へ繋いで確認する。`/p:` は付けず、パスワードは対話入力させる:

```bash
xfreerdp /v:<host> /u:<ssh-user> /cert:ignore /size:1920x1080 +clipboard
```

- `/cert:ignore` — 自己署名証明書の確認を黙ってスキップ
- `+clipboard` — クリップボード共有
- `/size:WxH` — 画面サイズ（`/f` で全画面、`/dynamic-resolution` でウィンドウ追従）

接続できればウィンドウに 対象 Windows のデスクトップが出る。macOS で XQuartz を入れていないなら、`xfreerdp` を `sdl-freerdp` に置き換える。

---

## アンインストール

```bash
# macOS
brew uninstall freerdp
brew uninstall --cask xquartz          # X11 をもう使わないなら

# Debian / Ubuntu
sudo apt remove --purge -y freerdp2-x11    # または freerdp3-x11

# RHEL / AlmaLinux / Rocky
sudo dnf remove -y freerdp
```
