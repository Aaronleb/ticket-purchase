#Requires -Version 5.1
<#
.SYNOPSIS
    Verify a built DamaiAndroid-windows-x64.zip without a phone.

.DESCRIPTION
    Extracts the archive into a temporary directory, runs the packaged
    self-check, starts the app headless (no window), waits for the bundled
    Appium server to report ready, checks the local console endpoints, and
    stops everything again. No real ticket grabbing is performed.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ZipPath,
    [int]$Port = 18765,
    [int]$StartupTimeoutSeconds = 180
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$buildDir = Join-Path (Split-Path -Parent $PSScriptRoot) "build"
New-Item -ItemType Directory -Force -Path $buildDir | Out-Null
try { Stop-Transcript | Out-Null } catch { }
Start-Transcript -Path (Join-Path $buildDir "verify.log") -Force | Out-Null
trap {
    $details = $_ | Out-String
    Write-Host $details
    Set-Content -LiteralPath (Join-Path $buildDir "verify-error.txt") -Value $details -Encoding UTF8
    try { Stop-Transcript | Out-Null } catch { }
    exit 1
}

$zip = (Resolve-Path -LiteralPath $ZipPath).Path
$work = Join-Path ([System.IO.Path]::GetTempPath()) ("damai-verify-" + [guid]::NewGuid().ToString("N"))
$process = $null
$dataDir = Join-Path $work "data"

function Show-LogTails {
    param([string]$DataDirectory)
    foreach ($name in @("console.log", "appium.log")) {
        $path = Join-Path $DataDirectory "logs\$name"
        if (Test-Path -LiteralPath $path) {
            Write-Host "----- $name (last 40 lines) -----"
            Get-Content -LiteralPath $path -Tail 40 | ForEach-Object { Write-Host $_ }
        }
    }
}

try {
    New-Item -ItemType Directory -Force -Path $work | Out-Null
    Write-Host "==> Extract $zip"
    Expand-Archive -LiteralPath $zip -DestinationPath $work -Force
    $appDir = Join-Path $work "DamaiAndroid"
    $exePath = Join-Path $appDir "DamaiAndroid.exe"
    if (-not (Test-Path -LiteralPath $exePath)) { throw "Missing DamaiAndroid.exe in archive" }

    Write-Host "==> Packaged self-check"
    $selfCheckRaw = & $exePath --self-check 2>&1 | Out-String
    Write-Host $selfCheckRaw.Trim()
    if ($LASTEXITCODE -ne 0) { throw "Self-check failed (exit $LASTEXITCODE)" }
    $selfCheck = $selfCheckRaw | ConvertFrom-Json
    if (-not $selfCheck.ok) { throw "Self-check reported missing resources" }

    Write-Host "==> Bundled binaries"
    $nodeOutput = & (Join-Path $appDir "runtime\node\node.exe") --version
    Write-Host "node $nodeOutput"
    $adbOutput = & (Join-Path $appDir "runtime\android-platform-tools\adb.exe") version 2>&1 | Out-String
    if ($adbOutput -notmatch "Android Debug Bridge") { throw "adb.exe failed: $adbOutput" }
    Write-Host (($adbOutput -split "`n")[0])

    Write-Host "==> Start console headless on 127.0.0.1:$Port"
    $process = Start-Process -FilePath $exePath `
        -ArgumentList @("--no-window", "--port", "$Port", "--data-dir", $dataDir) `
        -PassThru
    $deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
    $status = $null
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 2
        if ($process.HasExited) { throw "App exited early with code $($process.ExitCode)" }
        try {
            $status = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/status" -TimeoutSec 3
        } catch {
            continue
        }
        if ($status.runtime.state -eq "ready") { break }
        if ($status.runtime.state -eq "error") {
            throw "Bundled Appium failed: $($status.runtime.message)"
        }
    }
    if ($null -eq $status -or $status.runtime.state -ne "ready") {
        throw "Console/Appium did not become ready within $StartupTimeoutSeconds seconds"
    }
    if (-not $status.managed_appium) { throw "App did not use its bundled Appium" }
    Write-Host "console ok, appium ready at $($status.runtime.server_url)"

    Write-Host "==> Check local endpoints"
    $page = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 5
    if ($page.StatusCode -ne 200 -or $page.RawContentLength -lt 500) {
        throw "Console page did not render (status $($page.StatusCode))"
    }
    $config = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/android/config" -TimeoutSec 5
    if ($null -eq $config) { throw "Android config endpoint returned nothing" }
    Write-Host "page and config endpoints ok"

    Write-Host "VERIFY OK"
}
catch {
    Write-Host "VERIFY FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Show-LogTails -DataDirectory $dataDir
    throw
}
finally {
    if ($null -ne $process -and -not $process.HasExited) {
        & taskkill /PID $process.Id /T /F 2>&1 | Out-Null
        Start-Sleep -Seconds 2
    }
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $work
}

try { Stop-Transcript | Out-Null } catch { }
