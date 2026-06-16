# Windows に OpenSSH サーバーを確実に入れる

Windows 標準の `Add-WindowsCapability`（や 設定 → オプション機能）での OpenSSH サーバー有効化は、
環境がクリーンでも失敗することがある。loophole リポジトリには、一人情シスとして何度もハマった末に
落ち着いた**「GitHub の MSI をサイレント導入する」手順**を 2 本のスクリプトにまとめて同梱している。
万能の正解というわけではないが、FoD 経由で失敗する要因が無いぶん、環境を選ばず成功しやすい。基本は
これを走らせるだけでよい。

## なぜ標準の有効化は失敗しがちか

OpenSSH サーバーは Features on Demand（FoD）＝本体 .cab がローカルに無く、`Add-WindowsCapability
-Online` の実行時に Windows Update から取りに行く方式。これが次の理由でコケる:

- **取得先に届かない** — WSUS／グループポリシー管理下、オフライン、プロキシ/DNS 不調だと
  .cab を取得できず `0x800F0954` / `0x8024402C` などで失敗する。
- **成功表示なのに入っていない** — `RestartNeeded : False` を返して終わるのに、`Get-WindowsCapability`
  で見ると `State : NotPresent`。過去のインストール残骸（古い `sshd` サービス登録、`C:\ProgramData\ssh`
  の鍵/権限）との衝突が主因。
- **sshd 本体が展開されない FoD バグ** — capability は Installed なのにバイナリが無い既知不具合
  （Win32-OpenSSH issue #1492）。
- **コンポーネントストア破損** — `DISM /Online /Cleanup-Image /RestoreHealth` + `sfc /scannow` を
  通すまで無言で失敗し続ける。

公式トラブルシュートはこれらを「環境（WSUS/GPO/ネット）のせい」としか説明しないが、クリーンな個人
マシンでも残骸衝突・FoD バグで失敗する。**FoD を経由しない MSI 導入なら、ここで挙げた失敗要因は起きない。**

---

## いちばん簡単な方法 — 同梱スクリプトを走らせる

管理者 PowerShell で、loophole フォルダから:

```powershell
# （任意）過去の残骸を退避してクリーンな状態にする ── ホスト鍵は ssh.old_* に退避され消えない
powershell -ExecutionPolicy Bypass -File scripts\uninstall_openssh.ps1

# 最新の MSI を取得して導入し、起動・常駐・ファイアウォール・診断まで一括
powershell -ExecutionPolicy Bypass -File scripts\clean_reinstall_ssh.ps1
```

`clean_reinstall_ssh.ps1` が自動でやること:

- **最新版を GitHub API で解決**（AMD64 / ARM64 自動判定。取得失敗時は既知の安定版へフォールバック）
- 別の `msiexec` が走っていたら**中断**（エラー 1618 回避）し、残プロセスを `taskkill` で掃除
- MSI を `ADDLOCAL=Server /quiet` で導入（DL は `Invoke-WebRequest` → `curl.exe` フォールバック）
- sshd の**常駐**・**ファイアウォール（22番）**・**sshd_config 調整**（`Match Group administrators` を
  無効化し、管理者も個人の `~/.ssh/authorized_keys` を使えるようにする）
- **導入後診断**（sshd Running / 22番 Listen / ホスト鍵生成）。全ログは `%TEMP%\OpenSSH_Reinstall_Log_*.txt`

TLS 1.2 強制・管理者権限チェック込み。最初は `uninstall → clean_reinstall` の順で 1 回流せばよい。

---

## 手動でやる場合（フォールバック）

スクリプトを使わず手で入れるとき。すべて管理者 PowerShell。

> **手動でインストールする方法（設定アプリ / `Add-WindowsCapability` / 手動 MSI）は、どれも sshd_config を既定のまま残す。**
> 既定の sshd_config はセキュリティ的にはむしろ堅牢だが、日常的に SSH を導入していないと、管理者の鍵を置く
> `administrators_authorized_keys` の **Unix と Windows のアクセス権ルールの違いに起因するトラブル**（後述）や非英語 Windows の #2032 でつまずいて「いつの間にか鍵認証
> できなくなる」と悩みがち。慣れていないなら、**手順4 で sshd_config を調整しておくのをお勧めする**
> （同梱スクリプトはこれを自動でやる）。

### 1. 過去の痕跡を退避する（削除ではなくリネーム）

衝突の主因は過去の残骸 ── 古い `sshd` サービスと、`C:\ProgramData\ssh` に残った鍵/設定/権限。
ここは **削除せずタイムスタンプ付きでリネーム退避** する。ホスト鍵が `ssh.old_*` に残るので、後で
戻せば SSH のホスト鍵 ID が変わらず、クライアント側の「ホスト鍵が変わった」警告を避けられる。
このフォルダは ACL が固く（SYSTEM 所有）`Rename-Item` が弾かれることがあるので、先に `takeown` /
`icacls` で握ってからリネームする。

```powershell
Stop-Service sshd,ssh-agent -ErrorAction SilentlyContinue
Remove-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 -ErrorAction SilentlyContinue

$sshDir = "$env:ProgramData\ssh"
if (Test-Path $sshDir) {
    takeown /f $sshDir /r /d y | Out-Null
    icacls  $sshDir /grant "Administrators:(OI)(CI)F" /t /c /q | Out-Null
    Rename-Item $sshDir "ssh.old_$(Get-Date -Format yyyyMMdd_HHmmss)" -Force
}
Remove-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue
```

### 2. GitHub の MSI をサイレント導入

<https://github.com/PowerShell/Win32-OpenSSH/releases> から `OpenSSH-Win64-vX.Y.Z.Z.msi`
（最新の 64-bit。バージョン部 `X.Y.Z.Z` は実物に合わせる）を入手し、サーバーだけ入れる:

```powershell
msiexec /i $HOME\Downloads\OpenSSH-Win64-vX.Y.Z.Z.msi ADDLOCAL=Server /quiet
```

`C:\Program Files\OpenSSH` に展開され、`sshd` サービスが登録される。

### 3. 起動・常駐・ファイアウォール・検証

```powershell
Set-Service sshd -StartupType Automatic
Start-Service sshd
New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22
Get-Service sshd            # Running なら OK
(Get-Command sshd).Source   # C:\Program Files\OpenSSH\sshd.exe を指していること（inbox と二重化していない確認）
```

### 4. sshd_config を調整する（管理者の鍵認証を通すため・推奨）

既定の sshd_config はより堅牢だが、SSH 導入に慣れていないと、Unix と Windows のアクセス権ルールの違いに起因するトラブルや #2032 で管理者の鍵認証にハマりやすい。
`Match Group administrators` ブロックを無効化し、個人の `~/.ssh/authorized_keys` を使えるようにしておくと安定する。
`-Encoding ASCII` は UTF-8 BOM 付きだと sshd が読めない罠の回避:

```powershell
$cfg = "$env:ProgramData\ssh\sshd_config"
(Get-Content $cfg) `
  -replace '^(?!#)\s*Match Group administrators', '# Match Group administrators' `
  -replace '^(?!#)\s*AuthorizedKeysFile\s+__PROGRAMDATA__', '# AuthorizedKeysFile __PROGRAMDATA__' |
  Set-Content $cfg -Encoding ASCII
Restart-Service sshd
```

（鍵の置き場と登録は下の「鍵認証を設定する」。）

---

## 鍵認証を設定する

パスワードなしで鍵だけで入れるようにする（loophole は手元機が `ssh -L -N` のトンネルを無人で張り
続けるので、パスワード認証だと毎回入力を求められて自動化できない）。

> **鍵認証を強く勧める ── AI に運用させるなら特に。** パスワード認証のままだと、面倒さからつい AI に
> パスワードを渡してしまいがち。スクリプトに隠して AI に直接見せないようにしても、何らかの原因で
> ログインに失敗すると、AI が原因を追って**ローカルリポジトリの外にあるスクリプトや、その参照先の
> ファイルまで読みに行く**ことがある。コマンドの禁止リストを書いても、AI が別のスクリプトを生成して
> 間接的に読むのは防げない。鍵認証なら**渡すべきパスワードがそもそも無く、秘密鍵は手元から出ない**。

手元の Mac で鍵が無ければ作る:

```bash
ssh-keygen -t ed25519
```

できた公開鍵 `~/.ssh/id_ed25519.pub` の中身（1 行）を、この Windows の **接続するユーザーの
`C:\Users\<ユーザー>\.ssh\authorized_keys`** に登録する（管理者・一般とも同じ場所）。Windows 既定では
管理者グループは `C:\ProgramData\ssh\administrators_authorized_keys` を強制されるが、上の導入スクリプト
（または手動の手順4）で sshd_config の `Match Group administrators` を無効化しているので、管理者も
個人の `~/.ssh/authorized_keys` が使われる。

登録例（この Windows の PowerShell。`<公開鍵>` は上の `.pub` の中身）:

```powershell
$kf = "$env:USERPROFILE\.ssh\authorized_keys"
New-Item -ItemType Directory -Force -Path (Split-Path $kf) | Out-Null
Add-Content -Path $kf -Value "<公開鍵>"
icacls $kf /inheritance:r /grant "$env:USERNAME:F" /grant "SYSTEM:F"
```

確認: 手元から `ssh <ユーザー>@<WindowsのIP>` がパスワードを聞かれずに入れれば成功。

---

## 補足

- **更新は自分で管理する。** 新しい MSI が出たら差し替える（スクリプトを再実行すれば最新を取り直す）。
  その代わり Windows Update / WSUS に依存しないぶん、環境を選ばず成功しやすい。
- **環境がクリーンなら標準機能でも入る:** `Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0`
  → `Start-Service sshd`、または **設定 → システム → オプション機能 → "OpenSSH サーバー"**。ただし
  上記の理由で失敗することがあり、入った後の管理者の鍵認証も手順4 と同じ事情なので、sshd_config の調整を同様に勧める。
- **アンインストール:** `scripts\uninstall_openssh.ps1`、または `msiexec /x ...OpenSSH-Win64-....msi /quiet`。
- **管理者鍵も個人の `~/.ssh` に統一する理由:** 根本は **Unix と Windows でアクセス権のルールが違うこと**。
  OpenSSH は元々 Unix のパーミッション前提で、authorized_keys に「所有者以外が書き込めない」ことを求めるが、
  Windows ではそれを ACL で表すため、`administrators_authorized_keys` は ACL が SYSTEM/Administrators のみ・
  継承無効という形でないと sshd に**サイレントに無視される**。さらに非英語
  Windows では `Match Group administrators` のグループ解決に失敗して admin の鍵認証ごと壊れる既知
  バグ（Win32-OpenSSH #2032）がある。個人鍵に統一するとどちらも回避でき、プロフィール既定の ACL の
  まま通る。引き換えに Windows 既定の集中鍵ファイルによる保護は外れるが、loophole は 127.0.0.1 限定＋
  SSH トンネル経由なので実害は小さい。参考:
  - ACL がサイレント失敗する話（jmmv.dev）: https://jmmv.dev/2020/10/windows-ssh-access.html
  - Key-based auth・administrators_authorized_keys（Microsoft Learn）: https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_keymanagement
  - `Match Group administrators` が公開鍵認証を阻む（Win32-OpenSSH #1948）: https://github.com/PowerShell/Win32-OpenSSH/issues/1948
  - 非英語 Windows の locale バグ（Win32-OpenSSH #2032）: https://github.com/PowerShell/Win32-OpenSSH/issues/2032

---

loophole 全体のセットアップ手順は [windows-setup.md](windows-setup.md)。
