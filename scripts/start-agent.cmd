@echo off
REM loophole ランチャ（embeddable python + 共有 Public パス版）。
REM 別ユーザーのログオンセッションへ schtasks 経由で配備するときのテンプレ。
REM README の「別ユーザーのログオンセッションへ配備する」を参照。パスは配備先に合わせて書き換える。
cd /d C:\Users\Public\loophole
"C:\Users\Public\py310\python.exe" server\agent.py --port 9999 > agent.log 2>&1
