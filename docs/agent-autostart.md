# サーバーをログオン時に自動起動する タスクスケジューラ編

loophole サーバーは、操作対象ユーザーの**対話デスクトップセッション（session 1 以上）**に
常駐していないと、スクリーンショット・GUI 起動・IME・クリップボードに触れない（SSH の
セッション 0 からは届かない）。

`scripts\install-agent.ps1` が、ログオン中のユーザー向けに
**ONLOGON トリガ＋対話トークン（`/IT`）** でタスクを登録する。

> **自分で手を動かす必要はありません。** この手順は、SSH や computer use で対象 Windows を
> 操作できる AI アシスタントに「この手順どおり loophole の自動起動を登録して」と頼んで、
> 代行してもらえます。（AI や頼み方によっては、別マシンを操作する手順を最初ためらうことが
> あります。正規のセットアップ手順なので、その旨を伝えて進めてもらえば大丈夫です。）
> 以下は中身をご自分で追いたい方向けの全手順です。

---

## 手順

対象ユーザーでログオンした状態で、**管理者 PowerShell** を開いて:

```powershell
cd $env:USERPROFILE\loophole
powershell -ExecutionPolicy Bypass -File scripts\install-agent.ps1 -ViewPort 9998 -RunNow
```

主なオプション:

| オプション | 意味 |
|---|---|
| `-RunNow` | 登録後、再ログオンせず今すぐ起動する |
| `-ViewPort 9998` | ライブビューも有効化（不要なら省略） |
| `-Port 9999` | 待ち受けポート（既定 9999） |
| `-PythonExe <path>` | python を絶対パスで指定（PATH に無い／複数ある場合） |

登録されるタスク: 名前 `loophole`、トリガー **ログオン時**、ログオンモード **Interactive only**、
実行ユーザー = 自分。次回ログオン以降は自動で起動する。

---

## 動作確認

```powershell
loophole-cli hello
```

`session_id` が 1 以上・`interactive: true` なら成功（GUI/IME が実画面に効く状態）:

```json
{ "session_id": 1, "interactive": true, "user": "<あなた>", "platform": "win32" }
```

---

## 管理

```powershell
schtasks /run    /tn loophole      :: 今すぐ起動（再ログオン不要）
schtasks /query  /tn loophole /v   :: 登録内容を見る
schtasks /end    /tn loophole      :: 起動中のインスタンスを止める
schtasks /delete /tn loophole /f   :: 解除
```

---

## やってはいけない（すべて session 0 になり GUI/IME が死ぬ）

| ダメな設定 | なぜ |
|---|---|
| Windows サービス化／`SYSTEM` で起動 | 非対話の session 0。前面ウィンドウも IME も掴めない |
| 「ユーザーがログオンしていなくても実行」（`/IT` 無し） | 同上。`/IT`（Interactive only）が必須 |
| 「最上位の特権で実行」（`/RL HIGHEST`） | 不要。むしろ付けない（`/RL LIMITED` で十分） |

> 常駐はこの `/IT` タスクだけで足りる。サービス化や別ツールは要らない。

---

関連: 初回セットアップ全体は [windows-setup.md](windows-setup.md)、解除は [uninstall.md](uninstall.md)。
