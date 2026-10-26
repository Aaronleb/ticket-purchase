#!/usr/bin/env bash
# 把 tools/damai_app.py 打包成可双击的 macOS 应用（本地自用版，不签名上架）。
#
# 用法：./tools/build_app.sh
# 产物：<repo>/大麦抢票助手.app
#
# 说明：
#   - 应用是"薄壳"：内部指向当前仓库目录与本机 python，仓库移动后需重新构建；
#   - 只做 ad-hoc 签名（codesign -s -），换台机器首次打开需右键 →「打开」；
#   - 不复制 Chrome/chromedriver：driver 由应用在启动时对齐（见 tools/damai_app.py）。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-$(command -v python3)}"
APP="$ROOT/大麦抢票助手.app"
BUNDLE_ID="local.damai.helper"
VER="0.3.0"

say() { printf '%s\n' "$*"; }

[ -n "$PY" ] && [ -x "$PY" ] || { echo "✗ 找不到 python3"; exit 1; }
say "▶ python : $PY"
say "▶ 仓库   : $ROOT"

# ---------- 1. 图标 ----------
say "▶ 生成图标"
rm -rf "$ROOT/tools/AppIcon.iconset"
"$PY" "$ROOT/tools/make_icon.py" "$ROOT/tools/AppIcon.iconset" >/dev/null
iconutil -c icns "$ROOT/tools/AppIcon.iconset" -o "$ROOT/tools/AppIcon.icns"

# ---------- 2. 目录结构 ----------
say "▶ 组装 app bundle"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$ROOT/tools/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
printf 'APPL????' > "$APP/Contents/PkgInfo"

# ---------- 2.5 内嵌工程代码（让 .app 不依赖仓库位置） ----------
say "▶ 内嵌工程代码"
PROJ="$APP/Contents/Resources/project"
mkdir -p "$PROJ/tools"
rsync -a --exclude '__pycache__' --exclude '*.pkl' --exclude 'chromedriver' --exclude '.data' --exclude '.runs' \
    "$ROOT/damai/" "$PROJ/damai/"
rsync -a --exclude '__pycache__' --exclude '*.pkl' --exclude 'config.jsonc' \
    "$ROOT/damai_appium/" "$PROJ/damai_appium/"
cp "$ROOT"/tools/*.py "$PROJ/tools/" 2>/dev/null || true

# ---------- 3. Info.plist ----------
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>大麦抢票助手</string>
    <key>CFBundleDisplayName</key><string>大麦抢票助手</string>
    <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
    <key>CFBundleVersion</key><string>$VER</string>
    <key>CFBundleShortVersionString</key><string>$VER</string>
    <key>CFBundleExecutable</key><string>launcher</string>
    <key>CFBundleIconFile</key><string>AppIcon</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
    <key>LSMinimumSystemVersion</key><string>11.0</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>LSApplicationCategoryType</key><string>public.app-category.utilities</string>
    <key>NSHumanReadableCopyright</key><string>本地构建 · 仅供个人学习使用</string>
</dict>
</plist>
PLIST

# ---------- 4. 启动脚本 ----------
cat > "$APP/Contents/MacOS/launcher" <<LAUNCH
#!/bin/bash
# 由 tools/build_app.sh 生成：优先用 app 内嵌的工程代码，副本放到任何位置都能跑。
HERE="\$(cd "\$(dirname "\$0")/.." && pwd)"
PY="$PY"
if [ -d "\$HERE/Resources/project/tools" ]; then
    ROOT="\$HERE/Resources/project"
else
    ROOT="$ROOT"
fi
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:\$PATH"
export LANG="\${LANG:-zh_CN.UTF-8}"
export DAMAI_HOME="\$ROOT"
cd "\$ROOT" || exit 1
mkdir -p "\$ROOT/.logs"
exec "\$PY" "\$ROOT/tools/damai_app.py" >>"\$ROOT/.logs/app.log" 2>&1
LAUNCH
chmod +x "$APP/Contents/MacOS/launcher"

# ---------- 5. ad-hoc 签名 ----------
say "▶ ad-hoc 签名"
codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || say "  ⚠ 签名失败（不影响本机运行）"

say ""
say "✓ 构建完成：$APP"
say "  双击打开，或执行： open \"$APP\""
