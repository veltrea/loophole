# install-agent.ps1 — loophole エージェントを「ログオン中の対話セッション」で
# 自動起動するよう Task Scheduler に登録する（PsExec も SYSTEM も使わない）。
#
# SSH のセッション 0 からは Windows のデスクトップ・GUI・IME・クリップボードに触れない。
# エージェントは必ずログオンセッション（session 1 以上）に居る必要がある。タスクスケジューラの
# ONLOGON トリガ＋対話トークン（/IT）なら、Windows 標準機能だけでそれを実現できる
# （= 誰でも再現できる。外部ツールの導入は不要）。
#
# 使い方（対象ユーザーでログオン中に、管理者 PowerShell で実行するのが確実）:
#   powershell -ExecutionPolicy Bypass -File install-agent.ps1
#   powershell -ExecutionPolicy Bypass -File install-agent.ps1 -ViewPort 9998 -RunNow
#
# 管理者が別ユーザー向けに代理登録する（例: ヘッドレス運用で SSH 越しに仕込む）:
#   powershell -ExecutionPolicy Bypass -File install-agent.ps1 `
#       -PythonExe C:\Users\Public\py310\python.exe -User <desktop-user> -Password <pw> -ViewPort 9998 -RunNow
#
# 解除:
#   schtasks /delete /tn loophole /f

param(
    [int]$Port = 9999,
    [int]$ViewPort = 0,          # 0 = ライブビュー無効。9998 等を渡すと有効化
    [int]$ViewFps = 2,
    [string]$Token = "",
    [string]$PythonExe = "python",
    [string]$User = "",          # 既定 = 実行中のユーザー。代理登録時のみ明示する
    [string]$Password = "",      # 別ユーザー代理登録で schtasks が要求するときだけ
    [string]$TaskName = "loophole",
    [switch]$RunNow              # 登録後に今すぐ起動する（再ログオン不要）
)

$ErrorActionPreference = "Stop"

# このスクリプトの 1 つ上がリポジトリのルート（server/agent.py がある場所）
$repo  = Split-Path -Parent $PSScriptRoot
$agent = Join-Path $repo "server\agent.py"
if (-not (Test-Path $agent)) { throw "server\agent.py not found at $agent" }

# python の実体を絶対パスで解決（PATH 依存・セッション差を避ける）。
# -PythonExe には実行ファイル名でも絶対パスでも渡せる。
$py = (Get-Command $PythonExe -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (Get-Command "py" -ErrorAction SilentlyContinue).Source }
if (-not $py) { throw "Python not found. Install Python 3.10+ or pass -PythonExe <full path>." }

# pythonw があればコンソール窓を出さずに常駐させる
$pyw = $py -replace "python\.exe$", "pythonw.exe"
if (Test-Path $pyw) { $py = $pyw }

# エージェントの引数列を組む（ライブビューは ViewPort>0 のときだけ付ける）
$argLine = "`"$agent`" --host 127.0.0.1 --port $Port"
if ($ViewPort -gt 0) { $argLine += " --view-port $ViewPort --view-fps $ViewFps" }
if ($Token -ne "")   { $argLine += " --token $Token" }

$tr = "`"$py`" $argLine"
$runAs = if ($User -ne "") { $User } else { "$env:USERNAME (current user)" }

Write-Host "Registering scheduled task '$TaskName'"
Write-Host "  run as: $runAs  (interactive token, only while that user is logged on)"
Write-Host "  exec  : $tr"

# /IT = 対話トークンで起動（デスクトップセッションに出る・パスワードは保存しない）。
# /RL LIMITED で十分（最上位特権は付けない）。既存タスクは /f で置き換える。
#
# 自分用に登録するときは /RU を付けない（= 現在のユーザーが principal）。ローカル/
# ワークグループ機では $env:USERDOMAIN が "WORKGROUP" 等になり "WORKGROUP\user" が
# 無効アカウント扱いになる（エラー 1332）ため、明示せず schtasks の既定に任せるのが堅い。
# -User を渡したときだけ代理登録として /RU(+必要なら /RP) を付ける。
if ($User -ne "") {
    if ($Password -ne "") {
        schtasks /create /tn "$TaskName" /tr "$tr" /sc onlogon /ru "$User" /rp "$Password" /rl LIMITED /it /f | Out-Null
    } else {
        schtasks /create /tn "$TaskName" /tr "$tr" /sc onlogon /ru "$User" /rl LIMITED /it /f | Out-Null
    }
} else {
    schtasks /create /tn "$TaskName" /tr "$tr" /sc onlogon /rl LIMITED /it /f | Out-Null
}
if ($LASTEXITCODE -ne 0) { throw "schtasks /create failed (exit $LASTEXITCODE)" }
Write-Host "Registered. It will also start automatically at the next logon."

if ($RunNow) {
    Write-Host "Starting now (no re-logon needed)..."
    schtasks /run /tn "$TaskName" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "schtasks /run failed (exit $LASTEXITCODE)" }
    Start-Sleep -Seconds 2
    Write-Host "Verify on this machine (expect session_id>=1 and interactive:true):"
    Write-Host "  $py `"$repo\client\loophole.py`" hello"
} else {
    Write-Host "Start it now without re-logon:  schtasks /run /tn $TaskName"
}
Write-Host "Remove later:  schtasks /delete /tn $TaskName /f"
