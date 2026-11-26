#Requires -Version 5.1
<#
.SYNOPSIS
    Build the portable Windows Android app package (DamaiAndroid-windows-x64.zip).

.DESCRIPTION
    Assembles an onedir PyInstaller build with a bundled Node.js, Android
    platform-tools (adb), Appium and the UiAutomator2 driver, then zips the
    result. The target machine does not need Python, Node.js, ADB or Appium.

.PARAMETER SkipTests
    Skip the unit tests (used for quick local packaging experiments).
#>
[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$SkipInstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

$build = Join-Path $Root "build"
New-Item -ItemType Directory -Force -Path $build | Out-Null
$transcript = Join-Path $build "build.log"
$errorLog = Join-Path $build "build-error.txt"
Remove-Item -LiteralPath $errorLog -Force -ErrorAction SilentlyContinue
try { Stop-Transcript | Out-Null } catch { }
Start-Transcript -Path $transcript -Force | Out-Null

# 把终止错误写进文件，便于 Actions 失败时下载 artifact 排查
trap {
    $details = $_ | Out-String
    Write-Host $details
    Set-Content -LiteralPath $errorLog -Value $details -Encoding UTF8
    try { Stop-Transcript | Out-Null } catch { }
    exit 1
}

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message"
}

function Read-EnvFile([string]$Path) {
    $values = @{}
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) { continue }
        $index = $trimmed.IndexOf("=")
        if ($index -lt 1) { continue }
        $values[$trimmed.Substring(0, $index).Trim()] = $trimmed.Substring($index + 1).Trim()
    }
    return $values
}

function Get-RequiredValue($Values, [string]$Name) {
    if (-not $Values.ContainsKey($Name) -or -not $Values[$Name]) {
        throw "packaging/dependencies.env is missing $Name"
    }
    return $Values[$Name]
}

function Get-VerifiedDownload {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][string]$Sha256,
        [string[]]$FallbackUrls = @()
    )
    if (Test-Path -LiteralPath $Destination) {
        $actual = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToLower()
        if ($actual -eq $Sha256) { return }
        Write-Host "Checksum mismatch, re-downloading $Destination"
        Remove-Item -LiteralPath $Destination -Force
    }
    $candidates = @($Url) + $FallbackUrls
    foreach ($candidate in $candidates) {
        Write-Host "Downloading $candidate"
        try {
            Invoke-WebRequest -Uri $candidate -OutFile "$Destination.part" -UseBasicParsing -TimeoutSec 900
            $actual = (Get-FileHash -LiteralPath "$Destination.part" -Algorithm SHA256).Hash.ToLower()
            if ($actual -ne $Sha256) {
                throw "SHA256 mismatch: expected $Sha256, got $actual"
            }
            Move-Item -LiteralPath "$Destination.part" -Destination $Destination -Force
            return
        } catch {
            Write-Warning "$candidate failed: $($_.Exception.Message)"
            Remove-Item -LiteralPath "$Destination.part" -Force -ErrorAction SilentlyContinue
        }
    }
    throw "Unable to download $Url"
}

$dependencies = Read-EnvFile (Join-Path $PSScriptRoot "dependencies.env")
$nodeVersion = Get-RequiredValue $dependencies "NODE_VERSION"
$nodeSha = Get-RequiredValue $dependencies "NODE_WINDOWS_SHA256"
$platformToolsVersion = Get-RequiredValue $dependencies "PLATFORM_TOOLS_WINDOWS_VERSION"
$platformToolsSha = Get-RequiredValue $dependencies "PLATFORM_TOOLS_WINDOWS_SHA256"
$appiumVersion = Get-RequiredValue $dependencies "APPIUM_VERSION"
$uiautomator2Version = Get-RequiredValue $dependencies "UIAUTOMATOR2_VERSION"

$downloads = Join-Path $build "downloads-windows"
$runtime = Join-Path $build "runtime-windows"
$appDist = Join-Path $build "app-windows"
$dist = Join-Path $Root "dist"
$appName = "DamaiAndroid"
$archive = Join-Path $dist "DamaiAndroid-windows-x64.zip"

New-Item -ItemType Directory -Force -Path $downloads, $dist | Out-Null
$env:PYINSTALLER_CONFIG_DIR = Join-Path $build "pyinstaller-config"
$env:npm_config_cache = Join-Path $build "npm-cache"

if (-not $SkipInstall) {
    Write-Step "Install Python dependencies"
    python -m pip install --disable-pip-version-check --no-input -r requirements.txt -r requirements-build.txt
    if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
}

if (-not $SkipTests) {
    Write-Step "Run unit tests"
    python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw "unittest failed" }
    python -m pytest tests/test_flip_watch.py -q --no-cov
    if ($LASTEXITCODE -ne 0) { throw "pytest failed" }
}

Write-Step "Download Node.js $nodeVersion and platform-tools $platformToolsVersion"
$nodeArchive = Join-Path $downloads "node-v$nodeVersion-win-x64.zip"
Get-VerifiedDownload `
    -Url "https://nodejs.org/dist/v$nodeVersion/node-v$nodeVersion-win-x64.zip" `
    -Destination $nodeArchive `
    -Sha256 $nodeSha `
    -FallbackUrls @("https://npmmirror.com/mirrors/node/v$nodeVersion/node-v$nodeVersion-win-x64.zip")
$platformToolsArchive = Join-Path $downloads "platform-tools_r$platformToolsVersion-windows.zip"
Get-VerifiedDownload `
    -Url "https://dl.google.com/android/repository/platform-tools_r$platformToolsVersion-windows.zip" `
    -Destination $platformToolsArchive `
    -Sha256 $platformToolsSha

Write-Step "Unpack bundled runtime"
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $runtime
New-Item -ItemType Directory -Force -Path $runtime | Out-Null
Expand-Archive -LiteralPath $nodeArchive -DestinationPath $runtime -Force
Move-Item -LiteralPath (Join-Path $runtime "node-v$nodeVersion-win-x64") -Destination (Join-Path $runtime "node")
Expand-Archive -LiteralPath $platformToolsArchive -DestinationPath $runtime -Force
Move-Item -LiteralPath (Join-Path $runtime "platform-tools") -Destination (Join-Path $runtime "android-platform-tools")

$nodeExe = Join-Path $runtime "node\node.exe"
$adbExe = Join-Path $runtime "android-platform-tools\adb.exe"
$npmCmd = Join-Path $runtime "node\npm.cmd"
$env:Path = "$(Join-Path $runtime 'node');$env:Path"

Write-Step "Install Appium $appiumVersion"
$appiumHome = Join-Path $runtime "appium-cli"
New-Item -ItemType Directory -Force -Path $appiumHome | Out-Null
& $npmCmd install --prefix $appiumHome --no-audit --no-fund --loglevel=error "appium@$appiumVersion"
if ($LASTEXITCODE -ne 0) { throw "npm install appium failed" }

$appiumEntry = Join-Path $appiumHome "node_modules\appium\index.js"
if (-not (Test-Path -LiteralPath $appiumEntry)) { throw "Appium entry not found: $appiumEntry" }

Write-Step "Install UiAutomator2 driver $uiautomator2Version"
$env:APPIUM_HOME = $appiumHome
& $nodeExe $appiumEntry driver install "uiautomator2@$uiautomator2Version"
if ($LASTEXITCODE -ne 0) { throw "Appium driver install failed" }
$driverDir = Join-Path $appiumHome "node_modules\appium-uiautomator2-driver"
if (-not (Test-Path -LiteralPath $driverDir)) { throw "UiAutomator2 driver not installed" }
$extensionsManifest = Join-Path $appiumHome "node_modules\.cache\appium\extensions.yaml"
if (-not (Test-Path -LiteralPath $extensionsManifest)) {
    throw "Appium extensions.yaml not found: $extensionsManifest"
}

Write-Step "Copy Android worker sources"
$ticketProject = Join-Path $runtime "ticket-purchase\damai_appium"
New-Item -ItemType Directory -Force -Path $ticketProject | Out-Null
foreach ($name in @("__init__.py", "config.py", "sale_clock.py", "damai_app_v2.py")) {
    Copy-Item -LiteralPath (Join-Path $Root "damai_appium\$name") -Destination $ticketProject -Force
}
# 只带源码，不带开发机上的配置、登录态和校准模板
foreach ($name in @("config.json", "config.jsonc", "damai_cookies.pkl", "login.signal")) {
    Remove-Item -LiteralPath (Join-Path $ticketProject $name) -Force -ErrorAction SilentlyContinue
}
foreach ($name in @("flip-templates", "logs", ".runtime", "__pycache__")) {
    Remove-Item -LiteralPath (Join-Path $ticketProject $name) -Recurse -Force -ErrorAction SilentlyContinue
}
Copy-Item -LiteralPath (Join-Path $runtime "node\LICENSE") -Destination (Join-Path $runtime "NODE-LICENSE.txt") -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "THIRD_PARTY_NOTICES.md") -Destination (Join-Path $runtime "THIRD_PARTY_NOTICES.md") -Force

Write-Step "Build DamaiAndroid.exe with PyInstaller"
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $appDist
python -m PyInstaller --noconfirm --clean `
    --distpath $appDist `
    --workpath (Join-Path $build "pyinstaller-windows") `
    (Join-Path $PSScriptRoot "DamaiAndroid.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$appDir = Join-Path $appDist $appName
$exePath = Join-Path $appDir "$appName.exe"
if (-not (Test-Path -LiteralPath $exePath)) { throw "Missing $exePath" }

Write-Step "Assemble portable directory"
Copy-Item -LiteralPath $runtime -Destination (Join-Path $appDir "runtime") -Recurse -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "THIRD_PARTY_NOTICES.md") -Destination $appDir -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "windows-android\使用说明.txt") -Destination $appDir -Force

# PyInstaller 会整目录带 resources/，这里再兜底清掉开发机可能留下的模板/配置
$bundledResources = Join-Path $appDir "_internal\resources"
foreach ($name in @("flip-templates", "logs")) {
    Remove-Item -LiteralPath (Join-Path $bundledResources $name) -Recurse -Force -ErrorAction SilentlyContinue
}
$projectDir = Join-Path $appDir "runtime\ticket-purchase"
$leaked = @()
foreach ($name in @("config.json", "config.jsonc", "damai_cookies.pkl", "login.signal", "flip-templates", "logs", ".runtime")) {
    $candidate = Join-Path $projectDir $name
    if (Test-Path -LiteralPath $candidate) { $leaked += $candidate }
}
foreach ($name in @("config.json", "config.jsonc", "damai_cookies.pkl", "login.signal", "flip-templates")) {
    $candidate = Join-Path $bundledResources $name
    if (Test-Path -LiteralPath $candidate) { $leaked += $candidate }
}
if ($leaked.Count -gt 0) {
    throw "包内残留本机设置：$($leaked -join '; ')"
}

Write-Step "Run packaged self-check"
$selfCheckOutput = & $exePath --self-check 2>&1 | Out-String
Write-Host $selfCheckOutput.Trim()
$code = $LASTEXITCODE
if ($code -ne 0) { throw "Packaged self-check failed (exit $code)" }

Write-Step "Create $archive"
Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
python (Join-Path $PSScriptRoot "zip_dir.py") $appDir $archive
if ($LASTEXITCODE -ne 0) { throw "zip failed" }

$sizeMb = [math]::Round((Get-Item -LiteralPath $archive).Length / 1MB, 1)
Write-Host ""
Write-Host "Done:"
Write-Host "  directory $appDir"
Write-Host "  archive   $archive ($sizeMb MB)"
Write-Host "Copy the zip to Windows 10/11 x64, extract it and run DamaiAndroid.exe."
try { Stop-Transcript | Out-Null } catch { }
