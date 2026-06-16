# loophole アンインストール

導入方法（[windows-setup.md](windows-setup.md) の手順4で選んだもの）に応じて、自動起動の
登録を解除する。loophole は**レジストリを一切使わない**ので、最後にフォルダを消せば完全に消える。

## 1. 自動起動の解除（設定した方法だけ）

- **スタートアップフォルダ（方法A）:** `Win + R` → `shell:startup` →
  入れた `start-loophole.cmd` のショートカットを削除。
- **Task Scheduler（方法B / 方法C）:** 管理者 `cmd` で `schtasks /delete /tn loophole /f`、
  またはタスク スケジューラを開いてタスク `loophole` を削除。

## 2. 動作中のエージェントを停止

タスク マネージャーで `python.exe` / `pythonw.exe`（`server/agent.py` を実行しているもの）を終了。

## 3. ファイルを削除

loophole フォルダを削除すれば完全に消える（レジストリ・他の場所には何も残さない）。
