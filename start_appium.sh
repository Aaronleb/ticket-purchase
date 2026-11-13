#!/usr/bin/env bash
# 启动 Appium 服务（安卓端），自动带上 Android SDK 环境变量。
# 缺 ANDROID_HOME 时 Appium 建会话会直接报：
#   Neither ANDROID_HOME nor ANDROID_SDK_ROOT environment variable was exported.
set -euo pipefail

export ANDROID_HOME="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
PORT="${APPIUM_PORT:-4723}"

if [ ! -d "$ANDROID_HOME" ]; then
  echo "找不到 Android SDK：$ANDROID_HOME" >&2
  echo "可用 brew install --cask android-commandlinetools 安装，或用 ANDROID_HOME=... 指定" >&2
  exit 1
fi
if ! command -v appium >/dev/null 2>&1; then
  echo "找不到 appium，先执行：npm install -g appium && appium driver install uiautomator2" >&2
  exit 1
fi

echo "ANDROID_HOME=$ANDROID_HOME"
echo "Appium 服务：http://127.0.0.1:${PORT}"
exec appium --address 127.0.0.1 --port "$PORT" --log /tmp/appium.log
