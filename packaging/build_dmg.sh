#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="$ROOT"
BUILD="$ROOT/build"
RUNTIME="$BUILD/runtime"
DOWNLOADS="$BUILD/downloads"
DIST="$ROOT/dist"
APP="$BUILD/app/DamaiAndroid.app"

cd "$ROOT"
# shellcheck source=dependencies.env
source "$ROOT/packaging/dependencies.env"

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "当前构建脚本仅支持 Apple 芯片 macOS" >&2
  exit 1
fi
for tool in curl tar unzip shasum hdiutil codesign lipo python3; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "缺少构建工具：$tool" >&2
    exit 1
  fi
done
if [[ ! -f "$PROJECT_ROOT/damai_appium/damai_app_v2.py" ]]; then
  echo "未找到原安卓项目：$PROJECT_ROOT" >&2
  exit 1
fi

mkdir -p "$DOWNLOADS" "$DIST"
if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
  python3 -m venv "$ROOT/.venv"
fi
export PIP_CACHE_DIR="$BUILD/pip-cache"
export PYINSTALLER_CONFIG_DIR="$BUILD/pyinstaller-config"
export npm_config_cache="$BUILD/npm-cache"
"$ROOT/.venv/bin/pip" install -r requirements.txt -r requirements-build.txt
"$ROOT/.venv/bin/python" -m unittest discover -s tests -v
(
  cd "$PROJECT_ROOT"
  "$ROOT/.venv/bin/python" -m pytest tests/test_damai_app_v2.py -q --no-cov
)

download() {
  local url="$1"
  local destination="$2"
  local checksum="$3"
  if [[ ! -f "$destination" ]]; then
    curl -fL --retry 3 "$url" -o "$destination"
  fi
  if [[ "$(shasum -a 256 "$destination" | cut -d ' ' -f 1)" != "$checksum" ]]; then
    rm -f "$destination"
    echo "下载文件校验失败：$url" >&2
    exit 1
  fi
}

NODE_ARCHIVE="$DOWNLOADS/node-v$NODE_VERSION-darwin-arm64.tar.gz"
ANDROID_ARCHIVE="$DOWNLOADS/platform-tools_r$PLATFORM_TOOLS_VERSION-darwin.zip"
download "https://nodejs.org/dist/v$NODE_VERSION/node-v$NODE_VERSION-darwin-arm64.tar.gz" "$NODE_ARCHIVE" "$NODE_SHA256"
download "https://dl.google.com/android/repository/platform-tools_r$PLATFORM_TOOLS_VERSION-darwin.zip" "$ANDROID_ARCHIVE" "$PLATFORM_TOOLS_SHA256"

rm -rf "$RUNTIME" "$BUILD/app" "$BUILD/pyinstaller" "$BUILD/dmg"
mkdir -p "$RUNTIME" "$BUILD/dmg"
tar -xzf "$NODE_ARCHIVE" -C "$RUNTIME"
mv "$RUNTIME/node-v$NODE_VERSION-darwin-arm64" "$RUNTIME/node"
unzip -q "$ANDROID_ARCHIVE" -d "$RUNTIME"
mv "$RUNTIME/platform-tools" "$RUNTIME/android-platform-tools"

export PATH="$RUNTIME/node/bin:/usr/bin:/bin:/usr/sbin:/sbin"
hash -r
mkdir -p "$RUNTIME/appium-cli"
npm install --prefix "$RUNTIME/appium-cli" --no-audit --no-fund "appium@$APPIUM_VERSION"
APPIUM_ENTRY="$RUNTIME/appium-cli/node_modules/appium/index.js"
export APPIUM_HOME="$RUNTIME/appium-cli"
"$RUNTIME/node/bin/node" "$APPIUM_ENTRY" driver install "uiautomator2@$UIAUTOMATOR2_VERSION"
if [[ ! -d "$RUNTIME/appium-cli/node_modules/appium-uiautomator2-driver" ]]; then
  echo "UiAutomator2 驱动未正确安装" >&2
  exit 1
fi
DRIVER_PEER="$RUNTIME/appium-cli/node_modules/appium-uiautomator2-driver/node_modules/appium"
if [[ -L "$DRIVER_PEER" ]]; then
  rm "$DRIVER_PEER"
  ln -s ../../appium "$DRIVER_PEER"
fi

mkdir -p "$RUNTIME/ticket-purchase/damai_appium"
cp "$PROJECT_ROOT/damai_appium/__init__.py" "$RUNTIME/ticket-purchase/damai_appium/"
cp "$PROJECT_ROOT/damai_appium/config.py" "$RUNTIME/ticket-purchase/damai_appium/"
cp "$PROJECT_ROOT/damai_appium/damai_app_v2.py" "$RUNTIME/ticket-purchase/damai_appium/"
cp "$RUNTIME/node/LICENSE" "$RUNTIME/NODE-LICENSE.txt"
cp "$ROOT/packaging/THIRD_PARTY_NOTICES.md" "$RUNTIME/THIRD_PARTY_NOTICES.md"

"$ROOT/.venv/bin/pyinstaller" \
  --noconfirm \
  --clean \
  --distpath "$BUILD/app" \
  --workpath "$BUILD/pyinstaller" \
  "$ROOT/packaging/DamaiAndroid.spec"

RESOURCES="$APP/Contents/Resources"
mkdir -p "$RESOURCES"
cp -R "$RUNTIME" "$RESOURCES/runtime"
cp "$ROOT/packaging/THIRD_PARTY_NOTICES.md" "$RESOURCES/"

while IFS= read -r -d '' binary; do
  codesign --force --sign - --timestamp=none "$binary"
done < <(find "$APP" -type f \( -name '*.dylib' -o -name '*.so' -o -name '*.node' \) -print0)
codesign --force --sign - --timestamp=none "$APP/Contents/Resources/runtime/node/bin/node"
codesign --force --sign - --timestamp=none "$APP/Contents/Resources/runtime/android-platform-tools/adb"
codesign --force --sign - --timestamp=none "$APP/Contents/MacOS/DamaiAndroid"
codesign --force --sign - --timestamp=none "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"

case "$(lipo -info "$APP/Contents/MacOS/DamaiAndroid")" in
  *arm64*) ;;
  *) echo "应用主程序不是 arm64" >&2; exit 1 ;;
esac
case "$(lipo -info "$APP/Contents/Resources/runtime/node/bin/node")" in
  *arm64*) ;;
  *) echo "内置 Node.js 不支持 arm64" >&2; exit 1 ;;
esac

cp "$ROOT/packaging/首次打开说明.txt" "$BUILD/dmg/"
cp -R "$APP" "$BUILD/dmg/"
ln -s /Applications "$BUILD/dmg/Applications"
hdiutil create \
  -volname "DamaiAndroid" \
  -srcfolder "$BUILD/dmg" \
  -ov \
  -format UDZO \
  "$DIST/DamaiAndroid-arm64.dmg"
DMG_CHECK="$BUILD/dmg-check"
mkdir -p "$DMG_CHECK"
hdiutil attach "$DIST/DamaiAndroid-arm64.dmg" -readonly -nobrowse -mountpoint "$DMG_CHECK"
hdiutil detach "$DMG_CHECK"
rmdir "$DMG_CHECK"
ls -lh "$DIST/DamaiAndroid-arm64.dmg"
