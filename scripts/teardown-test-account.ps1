# teardown-test-account.ps1 — provision-test-account.ps1 が作った使い捨てアカウントと
# その痕跡（autologon・タスク・プロファイル・RDP 設定）を元に戻して削除する。
#
# **破壊的**（アカウント＆プロファイル削除）。既定はドライラン（計画表示のみ）。
# 実行は -Force。AI が自走するときは「破棄していいか」をユーザーに確認してから -Force。
#
# 順序が肝: autologon を**先に**戻してからアカウント削除（逆だと次回起動で
# 「消えたアカウントに autologon」しようとしてログインループになる）。
#
# 使い方:
#   powershell -ExecutionPolicy Bypass -File teardown-test-account.ps1            # ドライラン
#   powershell -ExecutionPolicy Bypass -File teardown-test-account.ps1 -Force     # 実行

param(
    [string]$User = "loophole-test",
    [string]$TaskName = "loophole-test",
    [string]$StateFile = "C:\Users\Public\loophole\.test-account-state.json",
    [switch]$Force
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
Assert-Admin

$state = $null
if (Test-Path $StateFile) { $state = Get-Content $StateFile -Raw | ConvertFrom-Json }

Write-Host "== teardown plan for '$User' =="
Write-Host "  1. revert autologon (Winlogon) and scrub DefaultPassword"
Write-Host "  2. delete scheduled task '$TaskName'"
Write-Host "  3. log off the account, then delete the account and its profile"
if ($state -and $state.mode -eq "rdp" -and $state.rdp_was_enabled -eq $false) {
    Write-Host "  4. re-disable Remote Desktop (we enabled it)"
}
if (-not $Force) { Write-Host "(dry run; pass -Force to execute)"; return }

# 1. autologon を元に戻す（先にやる）
function Restore-Reg($name, $val) {
    if ($null -eq $val -or $val -eq "") {
        Remove-ItemProperty -Path $Winlogon -Name $name -ErrorAction SilentlyContinue
    } else {
        Set-ItemProperty -Path $Winlogon -Name $name -Value $val
    }
}
if ($state -and $state.winlogon_orig) {
    Restore-Reg "AutoAdminLogon"    $state.winlogon_orig.AutoAdminLogon
    Restore-Reg "DefaultUserName"   $state.winlogon_orig.DefaultUserName
    Restore-Reg "DefaultDomainName" $state.winlogon_orig.DefaultDomainName
    Restore-Reg "DefaultPassword"   $state.winlogon_orig.DefaultPassword
} else {
    # 記録が無ければ安全側: autologon を無効化
    Set-ItemProperty -Path $Winlogon -Name "AutoAdminLogon" -Value "0"
}
# 平文 DefaultPassword は確実に消す（元々値が無かったなら残骸を残さない）
if (-not ($state -and $state.winlogon_orig -and $state.winlogon_orig.DefaultPassword)) {
    Remove-ItemProperty -Path $Winlogon -Name "DefaultPassword" -ErrorAction SilentlyContinue
}

# 2. タスク削除
schtasks /delete /tn "$TaskName" /f 2>$null | Out-Null

# 3. ログオフ → アカウント＆プロファイル削除
$u = Get-LocalUser -Name $User -ErrorAction SilentlyContinue
if ($u) {
    $sid = $u.SID.Value
    # 対象ユーザーの対話セッションをログオフ（best-effort。プロファイル使用中だと削除が失敗するため）
    foreach ($line in (query session 2>$null)) {
        if ($line -match [regex]::Escape($User) -and $line -match '\s(\d+)\s') {
            logoff $matches[1] 2>$null
        }
    }
    Start-Sleep -Seconds 1
    # プロファイル（ディレクトリ＋レジストリ ProfileList）を綺麗に削除
    Get-CimInstance Win32_UserProfile -Filter "SID='$sid'" -ErrorAction SilentlyContinue |
        Remove-CimInstance -ErrorAction SilentlyContinue
    Remove-LocalUser -Name $User -ErrorAction SilentlyContinue
}

# 4. RDP を自分で有効化していたら戻す
if ($state -and $state.mode -eq "rdp" -and $state.rdp_was_enabled -eq $false) {
    Set-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server" `
        -Name "fDenyTSConnections" -Value 1
}

# 記録ファイル削除
Remove-Item $StateFile -ErrorAction SilentlyContinue

Write-Host "Torn down '$User'."
Write-Host "If it was auto-logged-in on the console, a reboot clears the leftover session cleanly."
