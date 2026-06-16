# loophole 運用 runbook（AI オペレータ向け）

このファイルは **loophole を SSH 越しに駆動する自律エージェント（Claude 等）が読んで実行する**
ための手順書。人間向けの解説（[windows-setup.md](windows-setup.md) など）と違い、**状態判定 →
分岐 → 実行 → 検証** の決定木と、失敗 → 対処の対応表で構成する。

> **秘匿情報はここに書かない。** ホスト/アカウント/パスワード/MAC/設置パスは**自分の私的設定**
> （オペレータの CLAUDE.md・メモリ）から埋める。本書は公開リポジトリに載るのでプレースホルダのみ。

---

## 0. 環境を束ねる（実行前に私的設定から確定する）

| 変数 | 例 | 出どころ |
|---|---|---|
| `<host>` | `192.168.1.x` | 私的設定 |
| `<ssh-user>` | 管理者アカウント（SSH で入る側） | 私的設定 |
| `<desktop-user>` | デスクトップにログオンしているユーザー | 私的設定 |
| `<key>` / `<password>` | SSH 鍵 or パスワード | 私的設定 |
| `<install-dir>` | `C:\Users\Public\loophole` | 私的設定 |
| `<python>` | `C:\Users\Public\py310\python.exe` | 私的設定 |
| `<port>` / `<view-port>` | `9999` / `9998` | 既定 9999/9998 |
| `<mac>` | WoL 用 MAC アドレス | 私的設定 |

**SSH の呼び方（落とし穴あり）:** zsh は素の `$VAR` を単語分割しないので、SSH オプションを変数に
入れて `ssh $OPTS ...` とすると 1 単語扱いで壊れる。**配列で渡す**:

```bash
ssho=(-o ProxyJump=none -o IdentitiesOnly=yes -o ConnectTimeout=10 -i <key>)
ssh "${ssho[@]}" <ssh-user>@<host> '<command>'
```

（`ProxyJump=none` は VPN 踏み台を経由させないため。鍵が複数あるなら `IdentitiesOnly=yes` 必須。
鍵不可・パスワードのみのアカウントは `sshpass -p <password> ssh -o PubkeyAuthentication=no
-o PreferredAuthentications=password ...`。）

---

## テスト時の推奨 — 使い捨てアカウントで検証する

本物アカウントのパスワードを AI に渡さず、身元の取り違え（`/RU`・cross-user `/run`・ドメイン
解決）も避けるため、**テストは使い捨てのローカルアカウントを自分で作って行う**。生成パスワードは
throwaway（アカウントごと破棄する）なので会話ログに出てよい。

```bash
# 1. provision（鍵 SSH の管理者で）。まず -WhatIf で計画確認 → 本実行。
ssh "${ssho[@]}" <ssh-user>@<host> \
  'powershell -ExecutionPolicy Bypass -File <install-dir>\scripts\provision-test-account.ps1 -Mode autologon -WhatIf'
ssh "${ssho[@]}" <ssh-user>@<host> \
  'powershell -ExecutionPolicy Bypass -File <install-dir>\scripts\provision-test-account.ps1 -Mode autologon -Reboot'
```
- **`-Mode autologon`**: 再起動でテスト垢がコンソール session 1 に自動ログイン → ONLOGON の
  `/IT` タスクで loophole 起動。空いてるテスト機向け（使用中の人を蹴る）。
- **`-Mode rdp`**: 再起動なし。Mac から `xfreerdp /v:<host> /u:loophole-test /p:'<生成PW>'` で
  対話セッションを作る → ONLOGON で起動。使用中の機でも隔離できる。
  （`xfreerdp` の導入は [xfreerdp-install.md](xfreerdp-install.md)）

2. セッションができたら **§1〜§3 でいつも通り検証**（`hello` が `interactive:true`）。
3. **区切りで破棄を提案する。** アカウント削除は不可逆なので、**ユーザーに確認してから** `-Force`:
```bash
ssh "${ssho[@]}" <ssh-user>@<host> \
  'powershell -ExecutionPolicy Bypass -File <install-dir>\scripts\teardown-test-account.ps1 -Force'
```
teardown は「autologon を先に戻して平文 PW を scrub → タスク削除 → ログオフ → アカウント＆
プロファイル削除」の順（順序を守らないと次回起動でログインループ）。既定はドライラン。

---

## 1. 状態判定（必ず最初に。憶測で起動しない）

```bash
# 1a. 到達性
ping -c 3 <host>                       # 無応答なら → §2-A（WoL）
# 1b. デスクトップセッションの状態
ssh "${ssho[@]}" <ssh-user>@<host> 'query user & query session'
# 1c. agent が応答するか（ポートフォワード越し。無ければ §6 で張る）
loophole-cli --json hello
```

`hello` の `result` と `query session` から、いまの状態を次のどれか1つに分類する:

| 状態 | 兆候 | 進む先 |
|---|---|---|
| A. 正常稼働 | `hello` ok・`interactive:true`・`session_id>=1` | コード変更が無ければ完了。あれば §3（再デプロイ） |
| B. 非対話で稼働 | `hello` ok だが `interactive:false`/`session_id:0` | §2-D（対話セッションで起動し直す） |
| C. agent 停止・デスクトップ Active | `hello` 接続不可、`query session` で対象が `Active` | §2-D（起動） |
| D. スリープ復帰・セッション Disc | `query session` で対象が `Disc`、screenshot/IME が効かない | §2-C（tscon）→ 必要なら §2-D |
| E. 到達不可 | `ping` 無応答 | §2-A（WoL） |

---

## 2. 決定木（分岐 → 対応する道）

### 2-A. 到達不可 → WoL で起こす
```bash
wakeonlan <mac>            # 数回送ってよい
```
ping が通っても **SSH の exec は数十秒遅れることがある**（sshd 初期化）。ping 応答後、SSH を
リトライループで待つ。スリープからの復帰なら、起きたセッションは **Disc のまま** なので §2-C へ。

### 2-B.（このセクションは状態 E から §2-A 経由で D/C へ合流する。単独項目なし）

### 2-C. セッションが Disc → コンソールに再接続（loophole は再起動不要）
スリープ→WoL 復帰でデスクトップが画面から切り離されると、agent は生きていても screenshot/GUI/
IME だけ効かない。SYSTEM 権限の `tscon` で物理コンソールに繋ぎ直す（**PsExec が要る場面**）:
```bash
ssh "${ssho[@]}" <ssh-user>@<host> \
  'C:\ProgramData\chocolatey\bin\PsExec.exe -accepteula -nobanner -s C:\Windows\System32\tscon.exe <sid> /dest:console'
ssh "${ssho[@]}" <ssh-user>@<host> 'query session'   # 対象が Active になったか確認
```
詳細・理由は [psexec-headless.md](psexec-headless.md) §4。

### 2-D. 起動／再起動する（**まず PsExec フリーの道を試す**）
1. **schtasks `/IT`（第一選択・[agent-autostart.md](agent-autostart.md)）。** タスクが登録済みなら
   所有者本人として起動できる場合に:
   ```bash
   ssh "${ssho[@]}" <ssh-user>@<host> 'schtasks /run /tn loophole'
   ```
   未登録なら `scripts\install-agent.ps1`（`-RunNow -ViewPort <view-port>`）で登録＋起動。
   自分用は `-User` を付けない。代理登録は `-User <desktop-user> -Password <pw>`（ただし `/run` は
   所有者本人 or ONLOGON でしか効かない＝失敗表参照）。
2. **PsExec `-i -u`（フォールバック・[psexec-headless.md](psexec-headless.md) §3）。** ヘッドレスで
   別アカウントから対話セッションへ直接載せたいとき:
   ```bash
   ssh "${ssho[@]}" <ssh-user>@<host> \
     'C:\ProgramData\chocolatey\bin\PsExec.exe -accepteula -nobanner -i <sid> -u <desktop-user> -p <password> -d -w <install-dir> <python> server/agent.py --port <port> --view-port <view-port> --view-fps 2'
   ```
   **`-u <desktop-user>` は必須**（無いと別デスクトップに載り Win32 GUI/screenshot が壊れる）。
   **ただし `-u -p` は対象ユーザーのパスワードを AI のコンテキストと会話ログに晒す → 使い捨て／
   検証アカウント専用。本物アカウントは schtasks `/IT` + ONLOGON（パスワード不要）に寄せる。**
   なお復旧用の `tscon`（§2-C）は `-s`（SYSTEM）でパスワード不要なので、本物アカウントでも安全。

### 2-E.（合流）コードを更新して再デプロイする場合（§3）
古い agent が動いていれば**先に止める**（二重 listen 回避）。
```bash
ssh "${ssho[@]}" <ssh-user>@<host> 'tasklist /fi "imagename eq python.exe"'   # PID 特定
ssh "${ssho[@]}" <ssh-user>@<host> 'taskkill /pid <pid> /f'
ssh "${ssho[@]}" <ssh-user>@<host> 'netstat -ano | findstr :<port>'           # 解放確認
scp "${ssho[@]}" -r server/ \
    <ssh-user>@<host>:'<install-dir>/'                                         # 全コア同期
# その後 §2-D で起動
```
`.ps1` を送るときは **UTF-8 BOM 付き**で（失敗表参照）。

---

## 3. 検証ゲート（各起動の直後に必須。通らなければ先へ進まない）

```bash
loophole-cli --json hello
```
- **必須条件**: `ok:true` かつ `interactive:true` かつ `session_id>=1`、そして **pid が新しい**
  （古い agent の使い回しでない）。満たさなければ §4 で原因を切り分け、満たすまで他操作をしない。
- GUI/IME 系を使う前の追加確認: `loophole-cli --json ime-get` が `supported:true`、または
  `loophole-cli shot /tmp/x.png` が単色でない（数百 KB・多色）こと。

---

## 4. 失敗 → 対処（今セッションで実際に踏んだもの）

| 症状 | 原因 | 対処 |
|---|---|---|
| `ssh: Could not resolve hostname none` 等、SSH が壊れる | zsh が `$OPTS` を単語分割していない | SSH オプションは**配列** `"${ssho[@]}"` で渡す（§0） |
| `hello` が `interactive:false` / `session_id:0` | session 0（非対話）で起動した | §2-D で対話セッションに起動し直す |
| `schtasks /run` → 「要素が見つかりません」 | 他人の `/IT` タスクを非所有者が `/run` した | 所有者本人で実行、または ONLOGON（再ログオン）で起動 |
| `.ps1` 実行で `MissingEndParenthesis...` パースエラー | BOM 無し UTF-8 を PS 5.1 が CP932 で誤読 | `utf-8-sig` で保存し直して再 scp |
| `schtasks /create` →「アカウント名と…マッピング」(1332) | `/RU` が `WORKGROUP\user` 等の無効アカウント | 自分用は `/RU` を省く（既定ユーザーに任せる） |
| WoL 後に screenshot/IME だけ効かない | セッションが `Disc`（画面から切断） | §2-C の `tscon` で再接続 |
| `hello` が古い状態/別 cwd を返す | 古い agent が同ポートを二重 listen（`SO_REUSEADDR`） | 古い `python(w).exe` を `taskkill` してから起動 |
| screenshot が単色（白紙） | モニタがスリープ、またはセッション Disc | 画面を起こす／§2-C の tscon |
| ポートフォワードが `Address already in use` | 既存の `ssh -L` が同ポートを張っている | 新規に張らず既存を流用（§6） |

---

## 5. 後始末

- 自分で張ったポートフォワードは止める（既存のものは残す）。
- agent は常駐物なので通常は止めない。クリーンにするなら `taskkill /pid <pid> /f`。
- SSH 切断後も Windows 側に python が残ることがある → 残存 PID を `taskkill`。
- 省電力運用なら最後にホストをシャットダウン（オペレータの方針に従う）。

---

関連: [agent-autostart.md](agent-autostart.md)（schtasks /IT・PsExec フリー） /
[psexec-headless.md](psexec-headless.md)（PsExec・スリープ復帰） /
[windows-setup.md](windows-setup.md) / [windows-openssh-server.md](windows-openssh-server.md)。
