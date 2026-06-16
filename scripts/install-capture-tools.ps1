# install-capture-tools.ps1 — 任意のスクショ強化バックエンド(ddagrab)用に FFmpeg を
# 直ダウンロード + SHA256 検証で導入する。winget は SSH/ヘッドレス/セッション0 で
# 不安定(「A specified logon session does not exist」・SYSTEM 非対応)なので使わない。
#
# なぜ要るか:
#   既定の BitBlt スクショは GPU アクセラレーション描画(ブラウザ等)を黒画面にする。
#   FFmpeg の ddagrab(DXGI Desktop Duplication)はそれを撮れる。loophole は
#   LOOPHOLE_SCREENSHOT_BACKEND=ddagrab のとき LOOPHOLE_FFMPEG(または PATH の ffmpeg)を呼ぶ。
#   注意: ddagrab(DDA)は RDP セッションでは動かない。物理コンソール/ローカルセッション専用。
#
# 使い方(管理者 PowerShell 推奨):
#   powershell -ExecutionPolicy Bypass -File install-capture-tools.ps1
#   powershell -ExecutionPolicy Bypass -File install-capture-tools.ps1 -SetEnv
#   powershell -ExecutionPolicy Bypass -File install-capture-tools.ps1 -Sha256 <hex> -Force
#
# 解除: $Dest(既定 <repo>\bin)の ffmpeg.exe を消すだけ。環境変数は手動で削除。

param(
    [string]$Dest   = "",                                                       # 既定 = <repo>\bin
    [string]$Url    = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    [string]$Sha256 = "",        # 明示すると固定検証(再現可能)。空なら発行元の .sha256 で照合
    [switch]$SetEnv,             # LOOPHOLE_FFMPEG をユーザー環境変数に設定する
    [switch]$Force              # 既存の動く ffmpeg.exe があっても再導入する
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = "SilentlyContinue"   # IWR の進捗バーは大容量 DL を SSH で激遅にする

# 配置先: 既定は <repo>\bin(repo = scripts ディレクトリの 1 つ上)。
if ($Dest -eq "") {
    $repo = Split-Path -Parent $PSScriptRoot
    $Dest = Join-Path $repo "bin"
}
$ffmpegExe = Join-Path $Dest "ffmpeg.exe"

function Test-Ffmpeg([string]$exe) {
    # ffmpeg が起動でき、かつ ddagrab フィルタを持つかを確認する(全ビルドに ddagrab がある
    # わけではない)。半端な導入を「成功」と誤判定しないための番人。
    if (-not (Test-Path $exe)) { return $false }
    try {
        & $exe -hide_banner -version 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) { return $false }
        $filters = & $exe -hide_banner -filters 2>$null
        return [bool]($filters -match "ddagrab")
    } catch {
        return $false
    }
}

# 冪等: 既に動く ffmpeg(ddagrab 入り)があれば再導入しない。
if ((-not $Force) -and (Test-Ffmpeg $ffmpegExe)) {
    Write-Host "FFmpeg already present and ddagrab-capable: $ffmpegExe"
} else {
    New-Item -ItemType Directory -Force -Path $Dest | Out-Null
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("loophole_ffmpeg_" + [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    $zip = Join-Path $tmp "ffmpeg.zip"

    Write-Host "Downloading FFmpeg: $Url"
    try {
        Invoke-WebRequest -Uri $Url -OutFile $zip -UseBasicParsing -UserAgent "NativeHost"
    } catch {
        Write-Warning "Invoke-WebRequest failed ($($_.Exception.Message)); falling back to curl.exe"
        & curl.exe -L --fail -o $zip $Url
        if ($LASTEXITCODE -ne 0) { throw "download failed (curl exit $LASTEXITCODE)" }
    }

    # SHA256 検証(途中切断・改竄の検出)。-Sha256 明示が無ければ発行元の .sha256 で照合する。
    $actual = (Get-FileHash -Algorithm SHA256 -Path $zip).Hash.ToLower()
    $expected = $Sha256.ToLower()
    if ($expected -eq "") {
        try {
            $sidecar = (Invoke-WebRequest -Uri ($Url + ".sha256") -UseBasicParsing).Content
            $expected = (($sidecar.Trim()) -split '\s+')[0].ToLower()   # "<hash>  file" 形式にも対応
        } catch {
            Write-Warning "could not fetch $($Url).sha256; skipping hash verification (pass -Sha256 to enforce)"
            $expected = ""
        }
    }
    Write-Host "  SHA256(downloaded): $actual"
    if ($expected -ne "") {
        if ($actual -ne $expected) {
            throw "SHA256 mismatch! expected=$expected actual=$actual (truncated download or wrong file)"
        }
        Write-Host "  SHA256 verified OK"
    }

    Unblock-File -Path $zip   # ダウンロードの MOTW(zone identifier)を外す

    Write-Host "Extracting..."
    $ex = Join-Path $tmp "x"
    Expand-Archive -Path $zip -DestinationPath $ex -Force
    # zip は ffmpeg-<ver>-essentials_build\bin\ffmpeg.exe の構造。再帰で拾う。
    $src = Get-ChildItem -Path $ex -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
    if (-not $src) { throw "ffmpeg.exe not found inside the archive" }
    Copy-Item -Path $src.FullName -Destination $ffmpegExe -Force
    Unblock-File -Path $ffmpegExe

    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue

    if (-not (Test-Ffmpeg $ffmpegExe)) {
        throw "installed ffmpeg.exe failed its smoke test (won't run or lacks ddagrab): $ffmpegExe"
    }
    Write-Host "Installed and verified (ddagrab present): $ffmpegExe"
}

# 環境変数の設定/案内。
if ($SetEnv) {
    setx LOOPHOLE_FFMPEG "$ffmpegExe" | Out-Null
    Write-Host "Set user env LOOPHOLE_FFMPEG (effective for new processes / next logon)."
}
Write-Host ""
Write-Host "To enable the ddagrab screenshot backend, the loophole agent's process needs:"
Write-Host "  LOOPHOLE_FFMPEG=$ffmpegExe"
Write-Host "  LOOPHOLE_SCREENSHOT_BACKEND=ddagrab"
Write-Host "Note: ddagrab (DXGI Desktop Duplication) does NOT work over RDP; use a physical/console session."
