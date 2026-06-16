# ヘッドレス運用: PsExec で SSH 越しに loophole を起動・スリープ復帰させる（上級者向け）

> **通常は不要。** 自分のマシンにログオンしているなら [agent-autostart.md](agent-autostart.md) の
> `schtasks /IT` だけでよい（PsExec も管理者特権も使わない）。本書は「**手元に居ない Windows**」
> 「**スリープから WoL で起こした Windows**」を SSH 越しに復旧する運用者だけが対象。
>
> PsExec はセキュリティ系の人しか入れていないことが多い。導入手順（ユーザー向け）には含めない。

---

## なぜ PsExec が要るのか

loophole は対象ユーザーの**対話デスクトップセッション（session 1 以上）**に居ないと、
スクリーンショット・GUI 起動・IME・クリップボードに触れない。ヘッドレスだと次の 2 つで詰まる:

1. **SSH で入るとセッション 0（非対話）。** そこから session 1 へプロセスを送り込むのに
   PsExec `-i`（対話セッションへの注入）を使う。
2. **スリープ→WoL 復帰でセッションが切れる。** 後述の「Disconnected 問題」。物理画面から
   切り離されたセッションを SYSTEM 権限の `tscon` で再接続するのに PsExec `-s` を使う。

ここでの PsExec は **Windows 機の上でローカルに**（SSH 経由でその場で）動かし、自分自身の
セッションを対象にする。`\\host` 越しのリモート実行ではないので SMB 共有を外に開く必要はない。

---

## 1. PsExec を導入する

**方法A（推奨・Chocolatey）:**

```powershell
choco install sysinternals -y
```

→ `C:\ProgramData\chocolatey\bin\PsExec.exe` に入る。

**方法B（手動）:** Microsoft の [PSTools](https://learn.microsoft.com/sysinternals/downloads/psexec)
を入手して解凍し、`PsExec.exe` を PATH の通った場所（例 `C:\Tools`）に置く。

**EULA:** 初回起動で同意ダイアログが出る。無人運用では各呼び出しに **`-accepteula`** を付ければよい
（本書のコマンドは全て付けてある）。一度もクリックできない完全無人環境なら、PsExec が動く
アカウントの HKU ハイブに事前同意を入れておく:

```bat
reg add "HKCU\Software\Sysinternals\PsExec" /v EulaAccepted /t REG_DWORD /d 1 /f
```

---

## 2. ファイルを「両方のアカウントが読める場所」に置く

ヘッドレスでは **SSH で入るアカウント（例: 管理者）とデスクトップのログオンアカウントが別**に
なりがち。個人プロファイル配下（`C:\Users\<誰か>\...`）に置くと相手が読めない。両者が読める
共有の場所に置く:

- loophole 一式 → **`C:\Users\Public\loophole`**
- Python → **全ユーザー向けにインストール**（python.org インストーラの "Install for all users" →
  `C:\Program Files\Python3xx`）。再インストールできないなら `C:\Users\Public\py310` 等へ複製して
  Everyone に読取＋実行を付与する。

---

## 3. SSH 越しに対話セッションへ loophole を起動する

まずデスクトップにログオン中のユーザーとセッション ID を確認する:

```bat
query user
```

`<desktop-user>  console  1  Active` のように、**対話デスクトップ側のセッション ID**（ここでは `1`）を控える。
そのセッションへ、**そのユーザーとして** agent を起動する:

```bat
C:\ProgramData\chocolatey\bin\PsExec.exe -accepteula -nobanner -i 1 -u <desktop-user> -p <password> -d ^
    -w C:\Users\Public\loophole ^
    C:\Users\Public\py310\python.exe server/agent.py --port 9999 --view-port 9998 --view-fps 2
```

- **`-i 1`** … セッション 1（対話デスクトップ）に注入する。
- **`-u <desktop-user> -p <password>` … 必須。** これを省くと PsExec はサービスアカウント
  （SYSTEM/接続ユーザー）として起動し、**ログオンユーザーの可視デスクトップとは別のデスクトップ**に
  載る。すると挙動が非対称になり、UWP アプリは出るのに Win32 GUI は出ない・screenshot が白紙、に
  なる。必ずデスクトップ本人として起動すること。
- **`-d`** … 完了を待たずデタッチ（常駐させる）。
- **`-w`** … 作業ディレクトリ。`server/agent.py` は絶対パスで渡しているので無くても動くが付けておく。

確認（手元の Mac から、SSH ポートフォワード越しに）:

```bash
loophole-cli hello
```

`session_id` が 1 以上・`interactive: true` なら成功。

> **セキュリティ境界（重要）:** `-u -p` は対象ユーザーのパスワードをコマンドラインに置く。
> これを **AI に実行させると、パスワードは AI のコンテキストと会話ログ（プロバイダに送信される
> トランスクリプト）に必ず残る**。よって **`-u -p` の起動は使い捨て／検証用アカウント専用**に
> する。本物のアカウントでは使わない。本物を AI に駆動させたいときは、パスワードを渡さずに済む道へ:
>
> - **復旧（§4 の `tscon`）は元々パスワード不要** — `-s`（SYSTEM）で動き、管理者 SSH は鍵認証で
>   入るので、どこにもアカウントのパスワードは出ない。スリープ復帰は本物アカウントでも安全。
> - **起動は schtasks `/IT` + ONLOGON**（[agent-autostart.md](agent-autostart.md)）— 本人が一度
>   登録すればパスワードは**保存されず**、ログオン時に自動起動。AI は走っている agent を使うだけ。
> - ヘッドレスの cold-start を無資格でやりたいなら、**事前登録した SYSTEM タスク**（ログオン中
>   ユーザーのトークンで agent を起動するランチャ）を AI が `schtasks /run` で叩く形にし、
>   `-u -p` を人間が一度仕込む仕組みへ置き換える。これならパスワードは AI に渡らない。

---

## 4. スリープ→WoL 復帰後の「Disconnected 問題」と復旧

**症状:** Windows をスリープさせ、後で WoL（`wakeonlan <MAC>`）で起こすと、ログオン中だった
セッションが切断状態のまま物理画面から切り離される。`query session` で確認すると:

```
SESSIONNAME   USERNAME   ID  STATE
services                  0  Disc
                <desktop-user>   1  Disc        ← 対話デスクトップが Disconnected
console                   2  Conn
```

このとき **agent プロセス自体はスリープを生き延びて session 1 に残っている**（`ping`/`hello`/`run`/
ファイル系は通る）。だが対話デスクトップが画面から切り離されているので、**screenshot・GUI 起動・
IME・前面ウィンドウ操作だけが効かない**。

**復旧（loophole を再起動せずに）:** SYSTEM 権限で `tscon` を実行し、セッションを物理コンソールへ
再接続する。これでセッションが Active になり（ロックも解除され）、**常駐したままの agent の対話操作が
そのまま復活する**:

```bat
C:\ProgramData\chocolatey\bin\PsExec.exe -accepteula -nobanner -s ^
    C:\Windows\System32\tscon.exe 1 /dest:console
```

- **`-s`** … SYSTEM として実行（`tscon` は他人のセッションを動かすので SYSTEM 権限が要る）。
- **`1`** … 再接続するセッション ID（`query session` の Disc セッション）。
- **`/dest:console`** … 物理コンソールへ繋ぎ直す（= Active 化・アンロック）。

> **なぜ `schtasks /IT` では自動復旧しないのか:** スリープ復帰は「新規ログオン」ではないので
> ONLOGON トリガが**発火しない**。セッションは既にログオン済み（切断されただけ）で、agent も
> 生きている。足りないのは「セッションを画面に繋ぎ直す」一手だけで、それが SYSTEM 権限の
> `tscon`＝PsExec `-s` の出番。新規ログオンを挟めるなら（再起動・サインアウト→再ログオン）、
> `schtasks /IT` の ONLOGON で自動復帰するので PsExec は不要になる。

---

## 5. 後始末

- **SSH 切断で子プロセスが残る:** Windows OpenSSH は SSH を切っても起動した python を残す。
  止めるときは `tasklist /fi "imagename eq python.exe"` で PID を確認して `taskkill /pid <pid> /f`。
- **二重 listen に注意:** agent は `SO_REUSEADDR` を立てるので、古い agent を残したまま新しいのを
  起動すると同じポートを二重に listen し、`hello` が古い方に着く。先に古い python を `taskkill`。
- PsExec が入れた `PSEXESVC` サービスは実行後に自動で撤収される。

---

関連: 通常の自動起動は [agent-autostart.md](agent-autostart.md)、初回セットアップは
[windows-setup.md](windows-setup.md)、SSH サーバーは [windows-openssh-server.md](windows-openssh-server.md)。
