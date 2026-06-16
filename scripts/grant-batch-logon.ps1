# grant-batch-logon.ps1 — 指定アカウントに "Log on as a batch job"
# (SeBatchLogonRight) を付与する。
#
# schtasks /run でタスクをオンデマンド起動するには、タスクのプリンシパルに
# このバッチログオン権限が要る（無いと /run が無言で 0x41303 のまま起動しない）。
# install-agent.ps1 のタスク登録前に一度実行する想定。要管理者（昇格）。
#
#   powershell -ExecutionPolicy Bypass -File grant-batch-logon.ps1 -Account <desktop-user>

param([Parameter(Mandatory = $true)][string]$Account)

$ErrorActionPreference = "Stop"

$sid = (New-Object System.Security.Principal.NTAccount($Account)
       ).Translate([System.Security.Principal.SecurityIdentifier]).Value

$tmp = Join-Path $env:TEMP ("sec_" + [guid]::NewGuid().ToString("N"))
$inf = "$tmp.inf"
$db = "$tmp.sdb"

secedit /export /cfg $inf /quiet
$content = Get-Content $inf -Raw

if ($content -notmatch "SeBatchLogonRight") {
    # 行が無ければ [Privilege Rights] セクション直後に追加
    $content = $content -replace "(\[Privilege Rights\])", "`$1`r`nSeBatchLogonRight = *$sid"
} elseif ($content -notmatch "SeBatchLogonRight[^\r\n]*$sid") {
    # 既存行に SID を足す
    $content = $content -replace "(SeBatchLogonRight\s*=\s*)([^\r\n]*)", "`$1`$2,*$sid"
} else {
    Write-Host "$Account already has SeBatchLogonRight ($sid)"
    exit 0
}

Set-Content $inf $content -Encoding Unicode
secedit /configure /db $db /cfg $inf /areas USER_RIGHTS /quiet
$rc = $LASTEXITCODE
Remove-Item $inf, $db -ErrorAction SilentlyContinue

if ($rc -eq 0) {
    Write-Host "Granted SeBatchLogonRight to $Account ($sid)"
} else {
    Write-Error "secedit /configure failed with exit code $rc (need elevation?)"
    exit $rc
}
