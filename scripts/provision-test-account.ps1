# provision-test-account.ps1 — 使い捨てのテスト用ローカルアカウントを作り、loophole を
# その対話セッションで自動起動できるようにする。
#
# 狙い: テスト失敗の主因＝「身元の取り違え」（SSH 接続アカウントとデスクトップ
# アカウントが別で /RU・cross-user /run・ドメイン解決でハマる）を、AI が**自分で作って
# 自分が中身を知る単一の使い捨てアカウント**に寄せて消す。生成パスワードは throwaway
# （このアカウントはテスト後に teardown-test-account.ps1 で丸ごと破棄する前提）なので
# 出力に出してよい。本物アカウントのパスワードは一切要らない。
#
# 鍵 SSH の**管理者**コンテキストから実行する。
#
# 使い方:
#   powershell -ExecutionPolicy Bypass -File provision-test-account.ps1 -Mode autologon -Reboot
#   powershell -ExecutionPolicy Bypass -File provision-test-account.ps1 -Mode rdp
#   powershell -ExecutionPolicy Bypass -File provision-test-account.ps1 -WhatIf   # 計画だけ
#
# 破棄: teardown-test-account.ps1 -Force

param(
    [ValidateSet("autologon","rdp")]
    [string]$Mode = "autologon",
    [string]$User = "loophole-test",
    [string]$Password = "",          # 空なら強いパスワードを生成して表示
    [string]$InstallDir = "C:\Users\Public\loophole",
    [string]$PythonExe = "",         # 空なら自動解決
    [int]$Port = 9999,
    [int]$ViewPort = 9998,
    [string]$TaskName = "loophole-test",   # 本物の "loophole" タスクと衝突させない
    [string]$StateFile = "C:\Users\Public\loophole\.test-account-state.json",
    [switch]$Admin,                  # 既定は標準ユーザー（最小権限）
    [switch]$Reboot,                 # autologon: 設定後に再起動して自動ログインを発火
    [switch]$WhatIf                  # 実際には変更せず計画だけ出す
)
$ErrorActionPreference = "Stop"
$Winlogon = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"

function Assert-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $pr = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this in an elevated (admin) shell."
    }
}
function Get-RegOrNull($name) {
    try { (Get-ItemProperty -Path $Winlogon -Name $name -ErrorAction Stop).$name } catch { $null }
}

Assert-Admin

# python を解決（両アカウントが読める場所が望ましい）
if ($PythonExe -eq "") {
    foreach ($c in @("C:\Users\Public\py310\python.exe",
                     "C:\Program Files\Python312\python.exe",
                     "C:\Program Files\Python311\python.exe")) {
        if (Test-Path $c) { $PythonExe = $c; break }
    }
    if ($PythonExe -eq "") {
        $g = Get-Command python -ErrorAction SilentlyContinue
        if ($g) { $PythonExe = $g.Source }
    }
}
if ($PythonExe -eq "" -or -not (Test-Path $PythonExe)) {
    throw "Python not found; pass -PythonExe <full path>."
}

# パスワードを生成（未指定なら）
if ($Password -eq "") {
    Add-Type -AssemblyName System.Web
    $Password = [System.Web.Security.Membership]::GeneratePassword(20, 4)
}

# 自動起動タスクの実行文字列（pythonw があればコンソール窓無し）
$exe = $PythonExe
$pyw = $PythonExe -replace "python\.exe$", "pythonw.exe"
if (Test-Path $pyw) { $exe = $pyw }
$agent = Join-Path $InstallDir "server\agent.py"
$tr = "`"$exe`" `"$agent`" --host 127.0.0.1 --port $Port --view-port $ViewPort --view-fps 2"

Write-Host "== provision plan =="
Write-Host "  mode       : $Mode"
Write-Host "  account    : $User  (admin=$($Admin.IsPresent), disposable)"
Write-Host "  password   : $Password   <- throwaway; scrubbed at teardown"
Write-Host "  agent task : $TaskName"
Write-Host "  task exec  : $tr"
if ($WhatIf) { Write-Host "(-WhatIf: no changes made)"; return }

# 元の状態を記録（teardown で正確に戻すため）
$state = [ordered]@{
    mode = $Mode; user = $User; task = $TaskName; sid = $null
    created_account = $false
    rdp_was_enabled = $null
    winlogon_orig = [ordered]@{
        AutoAdminLogon    = Get-RegOrNull "AutoAdminLogon"
        DefaultUserName   = Get-RegOrNull "DefaultUserName"
        DefaultDomainName = Get-RegOrNull "DefaultDomainName"
        DefaultPassword   = Get-RegOrNull "DefaultPassword"
    }
}

# 1. アカウント（無ければ作成、あれば PW 更新で再現性を保つ）
$sec = ConvertTo-SecureString $Password -AsPlainText -Force
if ($null -eq (Get-LocalUser -Name $User -ErrorAction SilentlyContinue)) {
    New-LocalUser -Name $User -Password $sec -FullName "loophole test (disposable)" `
        -Description "Disposable AI test account. Remove with teardown-test-account.ps1." `
        -PasswordNeverExpires -AccountNeverExpires | Out-Null
    $state.created_account = $true
} else {
    Set-LocalUser -Name $User -Password $sec
}
Add-LocalGroupMember -Group "Users" -Member $User -ErrorAction SilentlyContinue
if ($Admin) { Add-LocalGroupMember -Group "Administrators" -Member $User -ErrorAction SilentlyContinue }
$state.sid = (Get-LocalUser -Name $User).SID.Value

# 2. loophole 自動起動タスク（ONLOGON + /IT）。起動は本人ログオン時に発火する
#    （cross-user /run は使わない＝今日ハマった「要素が見つかりません」を回避）。
schtasks /create /tn "$TaskName" /tr "$tr" /sc onlogon /ru "$User" /rp "$Password" /rl LIMITED /it /f | Out-Null
if ($LASTEXITCODE -ne 0) { throw "schtasks /create failed ($LASTEXITCODE)" }

# 3. モード別に対話セッションへ載せる準備
if ($Mode -eq "autologon") {
    Set-ItemProperty -Path $Winlogon -Name "AutoAdminLogon"    -Value "1"
    Set-ItemProperty -Path $Winlogon -Name "DefaultUserName"   -Value $User
    Set-ItemProperty -Path $Winlogon -Name "DefaultDomainName" -Value $env:COMPUTERNAME
    Set-ItemProperty -Path $Winlogon -Name "DefaultPassword"   -Value $Password
} elseif ($Mode -eq "rdp") {
    $ts = "HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server"
    $state.rdp_was_enabled = ((Get-ItemProperty -Path $ts -Name fDenyTSConnections -ErrorAction SilentlyContinue).fDenyTSConnections -eq 0)
    Set-ItemProperty -Path $ts -Name "fDenyTSConnections" -Value 0
    Enable-NetFirewallRule -DisplayGroup "Remote Desktop" -ErrorAction SilentlyContinue
    Add-LocalGroupMember -Group "Remote Desktop Users" -Member $User -ErrorAction SilentlyContinue
}

# 4. 状態を記録（teardown 用。ローカル限定・repo に入れない）
($state | ConvertTo-Json -Depth 5) | Set-Content -Path $StateFile -Encoding UTF8

Write-Host "Provisioned '$User'."
if ($Mode -eq "autologon") {
    Write-Host "Reboot to auto-login '$User' on the console; the ONLOGON task starts loophole."
    if ($Reboot) { Write-Host "Rebooting in 5s..."; shutdown /r /t 5 }
    else { Write-Host "Reboot now:  shutdown /r /t 0" }
} else {
    Write-Host "Connect via RDP as '$User' to create the interactive session; the ONLOGON task starts loophole."
    Write-Host "  e.g. (Mac) xfreerdp /v:<host> /u:$User /p:'<password above>'"
}
Write-Host "Verify once the session exists:  client\loophole.py hello  -> interactive:true, session_id>=1"
Write-Host "Dispose later:  teardown-test-account.ps1 -Force"
