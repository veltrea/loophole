---
name: loophole-ops
description: Bring up, redeploy, or recover the loophole agent on a remote Windows host over SSH — WoL wake, reconnecting a sleep-disconnected desktop session (tscon), launching into the interactive session (schtasks /IT preferred, PsExec fallback), and verifying it is interactive. Use when asked to start/restart/redeploy/recover loophole on a Windows machine, when bringing the agent up after the host slept or rebooted, or when loophole_* MCP calls fail because the agent is down or reports interactive:false / session_id:0.
---

# loophole-ops — エージェントを SSH 越しに立ち上げ/復旧する

loophole エージェントは対象ユーザーの**対話デスクトップセッション（session 1 以上）**に常駐して
いないと screenshot/GUI/IME/clipboard が効かない。このスキルはそれを SSH 越しに整える作業の
**進め方**だけを示す。**正確なコマンド・決定木・失敗対応表は [docs/operator-runbook.md](../../../docs/operator-runbook.md) に全部ある** ので、実作業はそれを開いて従う。

## 進め方（5 ステップ）

1. **環境を束ねる。** ホスト/アカウント/パスワード/設置パス/MAC は**自分の私的設定
   （CLAUDE.md・メモリ）から埋める**。公開手順にハードコードしない。SSH オプションは zsh の
   単語分割対策で**配列**で渡す（`ssho=(-o ProxyJump=none ...)` → `ssh "${ssho[@]}" ...`）。
2. **状態判定（憶測で起動しない）。** `ping` → `query user`/`query session` → `loophole hello`
   を見て、runbook §1 の表で「正常/非対話/停止/Disc/到達不可」のどれかに分類する。
3. **分岐して実行。** runbook §2 の決定木に従う。優先順位:
   - **PsExec フリーを第一選択**: `schtasks /IT`（[agent-autostart.md](../../../docs/agent-autostart.md)）。
   - **PsExec はフォールバック/特定用途のみ**: 別アカウントから対話セッションへ直接載せる
     `-i -u`、およびスリープ復帰後に Disc セッションを繋ぎ直す `-s tscon`
     （[psexec-headless.md](../../../docs/psexec-headless.md)）。
   - コード変更時は古い agent を `taskkill`（二重 listen 回避）→ 全コア .py を scp → 起動。
4. **検証ゲート（必須）。** 起動直後に `loophole hello` で **`interactive:true` かつ
   `session_id>=1` かつ pid が新しい**ことを確認。**満たすまで他の操作をしない。**
5. **後始末。** 自分で張ったポートフォワードは止める。agent は常駐物なので通常は残す。

## 必ず守る要点

- **PsExec は最後の手段／特定用途**（対話セッションへの注入と Disc 復旧）。常駐の通常導線は
  `schtasks /IT` で足りる。導入手順を PsExec 前提にしない（再現性が落ちる）。
- **本物アカウントのパスワードをコマンドラインに置かない。** `PsExec -i -u <user> -p <pass>` や
  `sshpass -p <pass>` はパスワードを AI のコンテキストと会話ログに残す → **使い捨て／検証
  アカウント専用**。本物は鍵 SSH ＋ `schtasks /IT`+ONLOGON で起動し、復旧は `PsExec -s tscon`
  （SYSTEM・パスワード不要）を使う。どちらも AI にパスワードを渡さずに済む。
- **テストは使い捨てアカウントで。** 本物アカウントを使い回さず、`scripts\provision-test-account.ps1`
  （`-Mode autologon|rdp`）で自分用の throwaway 垢を作って検証する（生成 PW は捨てる前提なので扱える）。
  **区切りでユーザーに破棄の可否を確認してから** `scripts\teardown-test-account.ps1 -Force` で消す
  （アカウント削除は不可逆＝確認ゲート必須）。詳細は runbook の「テスト時の推奨」節。
- **起動後は必ず `interactive:true` を確認**してから次へ進む。session 0 で動いていたら GUI/IME は
  全部死んでいるので、検証を飛ばすと後段が静かに壊れる。
- **踏みやすい罠**（詳細は runbook §4）: zsh の配列・`.ps1` は UTF-8 BOM 必須・`schtasks /run` は
  所有者本人・自分用は `/RU` 省略・WoL 復帰は `Disc` なので `tscon`。
