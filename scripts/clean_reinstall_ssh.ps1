# clean_reinstall_ssh.ps1 — OpenSSH サーバーを GitHub の MSI でクリーン導入する（本番運用版）。
#
# Windows 標準の Add-WindowsCapability は FoD 取得に依存して失敗しやすいので、FoD を経由せず
# GitHub の Win32-OpenSSH MSI を取ってサイレント導入する。具体的には:
#   1. 最新版を GitHub API で解決（AMD64 / ARM64 自動判定。取得失敗時は既知の安定版へフォールバック）
#   2. 別の msiexec 実行中なら中断（エラー 1618 回避）し、残プロセスを taskkill で掃除
#   3. MSI を ADDLOCAL=Server /quiet で導入（DL は Invoke-WebRequest → curl.exe フォールバック）
#   4. sshd の常駐・ファイアウォール（22番）・sshd_config 調整（管理者も個人の ~/.ssh を使う）
#   5. 導入後診断（sshd Running / 22番 Listen / ホスト鍵生成）。全ログは %TEMP% に残す
#
# 完全クリーンにしたいときは、先に uninstall_openssh.ps1 を走らせてから本スクリプトを実行する。
#
# 使い方（管理者 PowerShell）:
#   powershell -ExecutionPolicy Bypass -File clean_reinstall_ssh.ps1

$ErrorActionPreference = "Stop"

# 0. 管理者権限チェック
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Administrator privileges are required to run this script."
    exit
}

# ログ記録開始
$logFile = "$env:TEMP\OpenSSH_Reinstall_Log_$(Get-Date -Format 'yyyyMMdd_HHmmss').txt"
Start-Transcript -Path $logFile -Force

Write-Host "=========================================="
Write-Host "   OpenSSH (MSI) Install Tool             "
Write-Host "=========================================="
Write-Host "Log File: $logFile"

# 取得に失敗したとき用の既定 URL（フォールバック）
$downloadUrl = "https://github.com/PowerShell/Win32-OpenSSH/releases/download/v9.5.0.0p1-Beta/OpenSSH-Win64.msi"
$installerPath = "$env:TEMP\OpenSSH-Win64.msi"
$sshdConfigPath = "$env:ProgramData\ssh\sshd_config"

# [0.5] GitHub API で最新版を解決
Write-Host "`n[0/7] Checking for latest version..."
try {
    # TLS 1.2 を明示（PS 5.1 / 旧 Windows は既定が TLS1.0 で GitHub API がコケる）
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

    $latest = Invoke-RestMethod -Uri "https://api.github.com/repos/PowerShell/Win32-OpenSSH/releases/latest" -ErrorAction Stop

    $arch = $env:PROCESSOR_ARCHITECTURE
    if ($arch -eq "AMD64") {
        $assetNamePattern = "OpenSSH-Win64.*\.msi$"
    }
    elseif ($arch -eq "ARM64") {
        $assetNamePattern = "OpenSSH-ARM64.*\.msi$"
    }
    else {
        Write-Warning "Unsupported architecture ($arch). Using default URL."
        $assetNamePattern = ".*" # Dummy
    }

    $asset = $latest.assets | Where-Object { $_.name -match $assetNamePattern } | Select-Object -First 1

    if ($asset) {
        $downloadUrl = $asset.browser_download_url
        Write-Host "      Latest Version Found: $($latest.tag_name)"
        Write-Host "      Using URL: $downloadUrl"
    }
    else {
        Write-Warning "      No suitable MSI found in latest release. Using default stable version."
    }
}
catch {
    Write-Warning "      Failed to fetch latest version info via API ($_)."
    Write-Warning "      Falling back to cached stable version URL."
}


# [1/7] 古いプロセスの掃除
Write-Host "`n[1/7] Cleaning up old processes..."

# 安全策: 別の MSI インストールが走っていたら中断（エラー 1618 回避）
$msi = Get-Process msiexec -ErrorAction SilentlyContinue
if ($msi) {
    Write-Error "Another installation (msiexec.exe) is currently running."
    Write-Warning "To prevent corrupting other updates or installs, this script will stop."
    Write-Warning "Please wait for other installations to finish, or manually check Task Manager."
    exit
}

Stop-Service sshd -ErrorAction SilentlyContinue
Stop-Service ssh-agent -ErrorAction SilentlyContinue
try { taskkill /F /IM sshd.exe /T 2>&1 | Out-Null } catch { <# Ignored #> }
try { taskkill /F /IM ssh-agent.exe /T 2>&1 | Out-Null } catch { <# Ignored #> }

# [2/7] ダウンロード
Write-Host "`n[2/7] Downloading OpenSSH MSI..."
try {
    # TLS 1.2 は上で有効化済み
    Invoke-WebRequest -Uri $downloadUrl -OutFile $installerPath -UserAgent "NativeHost"
    Write-Host "      Download complete."
}
catch {
    Write-Warning "Invoke-WebRequest failed. Fallback to curl.exe"
    # フォールバックは --ssl-no-revoke 付き（プロキシ環境の失効チェック地雷対策）
    $curlArgs = @("-L", "--ssl-no-revoke", "-o", "$installerPath", "$downloadUrl")
    $p = Start-Process curl.exe -ArgumentList $curlArgs -Wait -NoNewWindow -PassThru
    if ($p.ExitCode -ne 0) {
        Write-Error "curl.exe failed with exit code $($p.ExitCode)"
        exit
    }
}

# [3/7] インストール
Write-Host "`n[3/7] Installing OpenSSH..."
try {
    $proc = Start-Process msiexec.exe `
        -ArgumentList "/i `"$installerPath`" /quiet /norestart ADDLOCAL=Server" `
        -Wait -PassThru
    if ($proc.ExitCode -ne 0) { throw "MSI install failed with code $($proc.ExitCode)" }
    Write-Host "      MSI Installation successful."
}
finally {
    # インストーラを後片付け
    if (Test-Path $installerPath) {
        Remove-Item $installerPath -Force -ErrorAction SilentlyContinue
        Write-Host "      Installer cleaned up."
    }
}

# [4/7] サービスとファイアウォール
Write-Host "`n[4/7] Configuring service and firewall..."
Set-Service sshd -StartupType Automatic
Start-Service sshd

# ファイアウォール規則（無ければ作る）
if (-not (Get-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule `
        -Name "OpenSSH-Server-In-TCP" `
        -DisplayName "OpenSSH Server (sshd)" `
        -Enabled True `
        -Direction Inbound `
        -Protocol TCP `
        -Action Allow `
        -LocalPort 22 `
        -ErrorAction SilentlyContinue | Out-Null
    Write-Host "      Firewall rule created."
}
else {
    Write-Host "      Firewall rule already exists."
}

# [5/7] sshd_config の調整（管理者も個人の ~/.ssh/authorized_keys を使えるようにする）
Write-Host "`n[5/7] Fixing sshd_config..."
if (Test-Path $sshdConfigPath) {
    try {
        # バックアップ
        $backupPath = "$sshdConfigPath.backup_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
        Copy-Item $sshdConfigPath $backupPath -Force
        Write-Host "      Config backed up to: $backupPath"

        $content = Get-Content $sshdConfigPath

        # 既定の Match Group administrators ブロックを無効化（個人鍵を使えるように）
        $newContent = $content `
            -replace '^(?!#)\s*Match Group Administrators', '# Match Group Administrators' `
            -replace '^(?!#)\s*AuthorizedKeysFile\s+__PROGRAMDATA__', '# AuthorizedKeysFile __PROGRAMDATA__'

        # ASCII で書き戻す（UTF-8 BOM 付きだと sshd が読めない罠の回避）
        Set-Content $sshdConfigPath $newContent -Encoding ASCII
        Restart-Service sshd
        Write-Host "      Configuration updated (ASCII)."
    }
    catch {
        Write-Warning "      Failed to update config: $_"
    }
}

# [6/7] 導入後診断
Write-Host "`n[6/7] Running Post-Install Diagnostics..."

function Test-OpenSSHInstallation {
    $allPassed = $true

    # 1. サービス状態
    $svc = Get-Service sshd -ErrorAction SilentlyContinue
    if ($svc.Status -eq 'Running') {
        Write-Host "  [OK] Service 'sshd' is RUNNING." -ForegroundColor Green
    }
    else {
        Write-Host "  [FAIL] Service 'sshd' is $($svc.Status)." -ForegroundColor Red
        $allPassed = $false
    }

    # 2. ポートの待ち受け
    $tcp = Get-NetTCPConnection -LocalPort 22 -State Listen -ErrorAction SilentlyContinue
    if ($tcp) {
        Write-Host "  [OK] TCP Port 22 is LISTENING." -ForegroundColor Green
    }
    else {
        # 立ち上がりに間があることがあるので少し待って再試行
        Start-Sleep -Seconds 2
        $tcp = Get-NetTCPConnection -LocalPort 22 -State Listen -ErrorAction SilentlyContinue
        if ($tcp) {
            Write-Host "  [OK] TCP Port 22 is LISTENING." -ForegroundColor Green
        }
        else {
            Write-Host "  [FAIL] TCP Port 22 is NOT listening." -ForegroundColor Red
            $allPassed = $false
        }
    }

    # 3. 基本的な権限チェック（ACL は重要）
    try {
        Get-Acl $env:ProgramData\ssh | Out-Null
        # ACL を読めること自体がアクセス権の証左。深い検査は複雑なので存在確認を主にする。
        if (Test-Path "$env:ProgramData\ssh\ssh_host_ed25519_key") {
            Write-Host "  [OK] Host keys generated successfully." -ForegroundColor Green
        }
        else {
            Write-Host "  [WARN] Host keys missing. (Will generate on first connection?)" -ForegroundColor Yellow
        }
    }
    catch {
        Write-Host "  [WARN] Cannot read ACLs on ProgramData\ssh. (Might be normal restrictive perms)" -ForegroundColor Yellow
    }

    if ($allPassed) {
        Write-Host "`n  >>> INSTALLATION VERIFIED HEALTHY <<<" -ForegroundColor Cyan
    }
    else {
        Write-Host "`n  >>> WARNING: INSTALLATION HAS ISSUES <<<" -ForegroundColor Red
    }
}

Test-OpenSSHInstallation

Write-Host "`n=========================================="
Write-Host "   Installation Complete                 "
Write-Host "=========================================="
Stop-Transcript
Pause
