$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Build = Join-Path $Root "build"
$Downloads = Join-Path $Build "downloads"
$Runtime = Join-Path $Build "runtime-windows"
$AppRoot = Join-Path $Build "app-windows"
$App = Join-Path $AppRoot "DamaiAndroid"
$PyInstallerWork = Join-Path $Build "pyinstaller-windows"
$Venv = Join-Path $Build "windows-venv"
$Dist = Join-Path $Root "dist"
$Archive = Join-Path $Dist "DamaiAndroid-windows-x64.zip"

if ($env:OS -ne "Windows_NT") {
    throw "此脚本仅支持 Windows"
}
if (-not [Environment]::Is64BitOperatingSystem) {
    throw "Windows 包仅支持 64 位系统"
}
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "未找到 Python 3.12"
}

Set-Location $Root
$Dependencies = @{}
Get-Content (Join-Path $Root "packaging/dependencies.env") | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#")) {
        $parts = $line -split "=", 2
        $Dependencies[$parts[0]] = $parts[1]
    }
}

function Get-Dependency([string]$Name) {
    if (-not $Dependencies.ContainsKey($Name)) {
        throw "dependencies.env 缺少 $Name"
    }
    return $Dependencies[$Name]
}

function Invoke-Checked([string]$Name, [scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Name 失败，退出代码 $LASTEXITCODE"
    }
}

function Get-Download([string]$Url, [string]$Destination, [string]$Checksum) {
    if (-not (Test-Path $Destination)) {
        Invoke-WebRequest -Uri $Url -OutFile $Destination
    }
    $actual = (Get-FileHash -Algorithm SHA256 $Destination).Hash.ToLowerInvariant()
    if ($actual -ne $Checksum.ToLowerInvariant()) {
        Remove-Item $Destination -Force
        throw "下载文件校验失败：$Url"
    }
}

$NodeVersion = Get-Dependency "NODE_VERSION"
$NodeChecksum = Get-Dependency "NODE_WINDOWS_SHA256"
$PlatformToolsVersion = Get-Dependency "PLATFORM_TOOLS_WINDOWS_VERSION"
$PlatformToolsChecksum = Get-Dependency "PLATFORM_TOOLS_WINDOWS_SHA256"
$AppiumVersion = Get-Dependency "APPIUM_VERSION"
$UiAutomator2Version = Get-Dependency "UIAUTOMATOR2_VERSION"
$ScrcpyVersion = Get-Dependency "SCRCPY_VERSION"
$ScrcpyChecksum = Get-Dependency "SCRCPY_SERVER_SHA256"

$ScrcpyServer = Join-Path $Root "resources/scrcpy-server-v$ScrcpyVersion"
if (-not (Test-Path $ScrcpyServer)) {
    throw "未找到 Scrcpy server：$ScrcpyServer"
}
$ScrcpyActual = (Get-FileHash -Algorithm SHA256 $ScrcpyServer).Hash.ToLowerInvariant()
if ($ScrcpyActual -ne $ScrcpyChecksum.ToLowerInvariant()) {
    throw "Scrcpy server 校验失败：$ScrcpyServer"
}

New-Item -ItemType Directory -Force -Path $Downloads, $Dist | Out-Null
$Python = Join-Path $Venv "Scripts/python.exe"
if (-not (Test-Path $Python)) {
    Invoke-Checked "创建 Windows 虚拟环境" { python -m venv $Venv }
}
$env:PIP_CACHE_DIR = Join-Path $Build "pip-cache"
$env:PYINSTALLER_CONFIG_DIR = Join-Path $Build "pyinstaller-config-windows"
$env:npm_config_cache = Join-Path $Build "npm-cache-windows"
Invoke-Checked "安装 Python 依赖" {
    & $Python -m pip install -r requirements.txt -r requirements-build.txt
}
Invoke-Checked "运行完整测试集" { & $Python -m pytest tests -q --no-cov }

$NodeArchive = Join-Path $Downloads "node-v$NodeVersion-win-x64.zip"
$AndroidArchive = Join-Path $Downloads "platform-tools-latest-windows.zip"
Get-Download "https://nodejs.org/dist/v$NodeVersion/node-v$NodeVersion-win-x64.zip" $NodeArchive $NodeChecksum
Get-Download "https://dl.google.com/android/repository/platform-tools-latest-windows.zip" $AndroidArchive $PlatformToolsChecksum

Remove-Item $Runtime, $AppRoot, $PyInstallerWork -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $Runtime | Out-Null
Expand-Archive -LiteralPath $NodeArchive -DestinationPath $Runtime
Move-Item (Join-Path $Runtime "node-v$NodeVersion-win-x64") (Join-Path $Runtime "node")
Expand-Archive -LiteralPath $AndroidArchive -DestinationPath $Runtime
Move-Item (Join-Path $Runtime "platform-tools") (Join-Path $Runtime "android-platform-tools")
$PlatformToolsProperties = Join-Path $Runtime "android-platform-tools/source.properties"
if (-not (Select-String -LiteralPath $PlatformToolsProperties -Pattern "Pkg.Revision=$([regex]::Escape($PlatformToolsVersion))" -Quiet)) {
    throw "Android Platform Tools 版本不是 $PlatformToolsVersion"
}

$Node = Join-Path $Runtime "node/node.exe"
$env:Path = "$(Join-Path $Runtime 'node');$env:Path"
$env:APPIUM_HOME = Join-Path $Runtime "appium-cli"
New-Item -ItemType Directory -Force -Path $env:APPIUM_HOME | Out-Null
Invoke-Checked "安装 Appium" {
    & (Join-Path $Runtime "node/npm.cmd") install --prefix $env:APPIUM_HOME --no-audit --no-fund "appium@$AppiumVersion"
}
$AppiumEntry = Join-Path $env:APPIUM_HOME "node_modules/appium/index.js"
Invoke-Checked "安装 UiAutomator2 驱动" {
    & $Node $AppiumEntry driver install "uiautomator2@$UiAutomator2Version"
}
$Driver = Join-Path $env:APPIUM_HOME "node_modules/appium-uiautomator2-driver"
if (-not (Test-Path $Driver)) {
    throw "UiAutomator2 驱动未正确安装"
}

$BundledProject = Join-Path $Runtime "ticket-purchase/damai_appium"
New-Item -ItemType Directory -Force -Path $BundledProject | Out-Null
foreach ($Name in @("__init__.py", "config.py", "damai_app_v2.py", "sale_clock.py")) {
    Copy-Item (Join-Path $Root "damai_appium/$Name") $BundledProject
}
Copy-Item (Join-Path $Runtime "node/LICENSE") (Join-Path $Runtime "NODE-LICENSE.txt")
Copy-Item (Join-Path $Root "packaging/THIRD_PARTY_NOTICES.md") $Runtime

Invoke-Checked "PyInstaller 打包" {
    & (Join-Path $Venv "Scripts/pyinstaller.exe") --noconfirm --clean --distpath $AppRoot --workpath $PyInstallerWork (Join-Path $Root "packaging/DamaiAndroidWindows.spec")
}
Copy-Item -LiteralPath $Runtime -Destination (Join-Path $App "runtime") -Recurse -Force
Copy-Item (Join-Path $Root "packaging/THIRD_PARTY_NOTICES.md") $App
Copy-Item (Join-Path $Root "packaging/Windows使用说明.txt") (Join-Path $App "使用说明.txt")

Remove-Item $Archive -Force -ErrorAction SilentlyContinue
Compress-Archive -LiteralPath $App -DestinationPath $Archive -CompressionLevel Optimal
Invoke-Checked "验证最终 Windows zip" { & $Python packaging/verify_windows.py }

$Checksum = (Get-FileHash -Algorithm SHA256 $Archive).Hash.ToLowerInvariant()
[System.IO.File]::WriteAllText("$Archive.sha256", "$Checksum  DamaiAndroid-windows-x64.zip`n")
Get-Item $Archive | Select-Object FullName, Length
