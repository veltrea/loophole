# uninstall_openssh.ps1 — OpenSSH サーバーをクリーンに撤去して「まっさらな状態」に戻す。
#
# sshd / ssh-agent を止め、FoD 版 OpenSSH.Server をアンインストールし、設定フォルダ
# C:\ProgramData\ssh を「削除せずタイムスタンプ付きでリネーム退避」する。退避なのでホスト鍵が
# ssh.old_* に残り、戻せば SSH のホスト鍵 ID を保てる（クライアントの「ホスト鍵が変わった」警告回避）。
# このフォルダは ACL が固く Rename-Item が弾かれやすいので、先に takeown / icacls で握ってから動かす。
# clean_reinstall_ssh.ps1 を走らせる前に、完全クリーンにしたいときだけ実行すればよい。
#
# 使い方（管理者 PowerShell）:
#   powershell -ExecutionPolicy Bypass -File uninstall_openssh.ps1

Write-Host "=========================================="
Write-Host "   OpenSSH Server Clean Uninstall Tool    "
Write-Host "=========================================="

# 1. sshd / ssh-agent を停止
Write-Host "`n[1/3] Stopping sshd service..."
Stop-Service sshd -ErrorAction SilentlyContinue
Stop-Service ssh-agent -ErrorAction SilentlyContinue

# 2. FoD 版 OpenSSH サーバーをアンインストール
Write-Host "`n[2/3] Uninstalling OpenSSH Server (this may take a minute)..."
try {
    Remove-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 | Out-Null
    Write-Host "      Uninstall successful."
}
catch {
    Write-Host "      Request failed. Trying alternative uninstall method or it wasn't installed."
}

# 3. 設定フォルダをリネーム退避（クリーン導入の肝。削除ではなく退避）
$sshDir = "$env:ProgramData\ssh"
if (Test-Path $sshDir) {
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $backupDir = "$env:ProgramData\ssh.old_$timestamp"
    Write-Host "`n[3/3] Renaming corrupt '$sshDir' to '$backupDir'..."

    # 動かせない場合に備えて先に所有権・ACL を握る
    try {
        takeown /f "$sshDir" /r /d y | Out-Null
        icacls "$sshDir" /grant "Administrators:(OI)(CI)F" /t /c /q | Out-Null
        Rename-Item -Path $sshDir -NewName $backupDir -Force
        Write-Host "      Backup successful. Clean state achieved."
    }
    catch {
        Write-Host "      [WARNING] Could not rename folder. You might need to delete C:\ProgramData\ssh manually."
        Write-Host "      Error: $_"
    }
}
else {
    Write-Host "`n[3/3] No existing SSH data folder found. Clean state verified."
}

Write-Host "`nUninstallation Complete."
Pause
