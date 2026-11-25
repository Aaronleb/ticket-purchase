#!/usr/bin/env bash
# 在 macOS/Linux 上组装 Windows 免安装 PC 版目录和 zip。
#
# 产物：
#   dist/ticket-purchase-windows-pc/     可直接拷贝到 Windows 解压使用
#   dist/ticket-purchase-windows-pc.zip  分发压缩包
#
# 说明：PyInstaller 无法跨平台编译，本脚本改用 Windows 官方嵌入式
# Python + win_amd64 wheel 的方式组装，不生成 exe，也不需要目标机安装 Python。
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PACKAGING="$APP_DIR/packaging"
DOWNLOADS="$PACKAGING/downloads"
PYTHON_VERSION="${PYTHON_VERSION:-3.12.10}"
PYTHON_EMBED="python-$PYTHON_VERSION-embed-amd64.zip"
# python.org 在国内经常超时，优先用镜像，失败再回退官方源
PYTHON_URLS=(
  "https://mirrors.huaweicloud.com/python/$PYTHON_VERSION/$PYTHON_EMBED"
  "https://mirrors.aliyun.com/python-release/windows/$PYTHON_EMBED"
  "https://www.python.org/ftp/python/$PYTHON_VERSION/$PYTHON_EMBED"
)
PYTHON_BIN="${PYTHON:-$APP_DIR/.venv/bin/python}"
# 允许 PYTHON=python 这类命令名（Windows/Git Bash 上没有 .venv）
case "$PYTHON_BIN" in
  */*|*\\*) ;;
  *) PYTHON_BIN="$(command -v "$PYTHON_BIN" 2>/dev/null || printf '%s' "$PYTHON_BIN")" ;;
esac
# 与 chromedriver 同版本的 Chrome for Testing，保证解压即用、无需安装 Chrome
CHROME_VERSION="${CHROME_VERSION:-158.0.8127.36}"
CHROME_ARCHIVE="chrome-win64.zip"
DRIVER_ARCHIVE="chromedriver-win64.zip"
OUTPUT="$APP_DIR/dist/ticket-purchase-windows-pc"
ARCHIVE="$APP_DIR/dist/ticket-purchase-windows-pc.zip"

log() { printf '\n==> %s\n' "$*"; }

# Git for Windows 自带的 bash 没有 rsync，退回 cp + 清理缓存文件
copy_tree() {
  local source="$1" destination="$2"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a \
      --exclude '__pycache__' \
      --exclude '*.pyc' \
      --exclude '.DS_Store' \
      "$source" "$destination"
    return 0
  fi
  mkdir -p "$destination"
  cp -R "$source/." "$destination/"
  find "$destination" -type d -name '__pycache__' -prune -exec rm -rf {} +
  find "$destination" -type f \( -name '*.pyc' -o -name '.DS_Store' \) -delete
}

download_file() {
  local destination="$1"
  shift
  if [ -s "$destination" ]; then
    return 0
  fi
  local url
  for url in "$@"; do
    echo "下载 $url"
    if curl -fL --retry 2 --connect-timeout 15 --max-time 600 \
      "$url" -o "$destination.part"; then
      mv "$destination.part" "$destination"
      return 0
    fi
    rm -f "$destination.part"
  done
  return 1
}

if [ ! -x "$PYTHON_BIN" ]; then
  echo "未找到 Python：$PYTHON_BIN（可用 PYTHON=/path/to/python 指定）" >&2
  exit 1
fi

mkdir -p "$DOWNLOADS" "$APP_DIR/build" "$APP_DIR/dist"

log "准备 Python $PYTHON_VERSION 嵌入式运行时"
PYTHON_ZIP="$DOWNLOADS/$PYTHON_EMBED"
if [ ! -s "$PYTHON_ZIP" ]; then
  for url in "${PYTHON_URLS[@]}"; do
    echo "下载 $url"
    if curl -fL --retry 2 --connect-timeout 15 --max-time 180 \
      "$url" -o "$PYTHON_ZIP.part"; then
      mv "$PYTHON_ZIP.part" "$PYTHON_ZIP"
      break
    fi
    rm -f "$PYTHON_ZIP.part"
  done
fi
if [ ! -s "$PYTHON_ZIP" ]; then
  echo "Python 嵌入式包下载失败，请手动下载后放到 $PYTHON_ZIP" >&2
  exit 1
fi
"$PYTHON_BIN" -m zipfile -t "$PYTHON_ZIP" >/dev/null

rm -rf "$OUTPUT" "$ARCHIVE"
mkdir -p "$OUTPUT/python"
"$PYTHON_BIN" -m zipfile -e "$PYTHON_ZIP" "$OUTPUT/python"

log "下载 Windows (win_amd64 / cp312) 依赖 wheel"
WHEEL_DIR="$DOWNLOADS/wheels-win64"
rm -f "$WHEEL_DIR"/*.whl
mkdir -p "$WHEEL_DIR"
"$PYTHON_BIN" -m pip download \
  --disable-pip-version-check \
  --dest "$WHEEL_DIR" \
  --only-binary=:all: \
  --platform win_amd64 \
  --implementation cp \
  --python-version 3.12 \
  --abi cp312 \
  -r "$PACKAGING/requirements-windows-pc.txt"

log "展开依赖到 python/Lib/site-packages"
SITE_PACKAGES="$OUTPUT/python/Lib/site-packages"
mkdir -p "$SITE_PACKAGES"
for wheel in "$WHEEL_DIR"/*.whl; do
  "$PYTHON_BIN" -m zipfile -e "$wheel" "$SITE_PACKAGES"
done

log "下载 Chrome for Testing $CHROME_VERSION 与匹配的 chromedriver"
CHROME_DIR="$DOWNLOADS/chrome-$CHROME_VERSION"
mkdir -p "$CHROME_DIR"
CHROME_MIRRORS=(
  "https://registry.npmmirror.com/-/binary/chrome-for-testing/$CHROME_VERSION/win64"
  "https://storage.googleapis.com/chrome-for-testing-public/$CHROME_VERSION/win64"
)
for archive in "$CHROME_ARCHIVE" "$DRIVER_ARCHIVE"; do
  urls=()
  for mirror in "${CHROME_MIRRORS[@]}"; do
    urls+=("$mirror/$archive")
  done
  if ! download_file "$CHROME_DIR/$archive" "${urls[@]}"; then
    echo "下载 $archive 失败，可用 CHROME_VERSION=... 指定其他版本" >&2
    exit 1
  fi
  "$PYTHON_BIN" -m zipfile -t "$CHROME_DIR/$archive" >/dev/null
done

log "展开浏览器到 browser/"
rm -rf "$OUTPUT/browser"
mkdir -p "$OUTPUT/browser"
"$PYTHON_BIN" -m zipfile -e "$CHROME_DIR/$CHROME_ARCHIVE" "$OUTPUT/browser"
"$PYTHON_BIN" -m zipfile -e "$CHROME_DIR/$DRIVER_ARCHIVE" "$OUTPUT/browser"
printf '%s\n' "$CHROME_VERSION" > "$OUTPUT/browser/VERSION"
for required in \
  "$OUTPUT/browser/chrome-win64/chrome.exe" \
  "$OUTPUT/browser/chromedriver-win64/chromedriver.exe"; do
  if [ ! -f "$required" ]; then
    echo "浏览器文件缺失：$required" >&2
    exit 1
  fi
done

# 嵌入式 Python 默认不加载 site-packages，且 ._pth 会进入隔离模式，
# 必须显式加入项目根目录和 damai/，否则 start.bat 与 damai.py 无法导入本地模块
PTH_FILE="$(find "$OUTPUT/python" -maxdepth 1 -name 'python*._pth' | head -1)"
if [ -z "$PTH_FILE" ]; then
  echo "未找到 Python 的 ._pth 文件" >&2
  exit 1
fi
{
  printf 'Lib\\site-packages\n'
  printf '..\n'
  printf '..\\damai\n'
  printf 'import site\n'
} >> "$PTH_FILE"

log "复制项目代码"
copy_tree "$APP_DIR/damai/" "$OUTPUT/damai/"
for name in app.py config_store.py process_manager.py android_config.py \
            android_calibrate.py android_diagnostics.py android_environment.py \
            android_prewarm.py time_sync.py adb_session.py activity_watch.py \
            flip_watch.py scrcpy_stream.py; do
  cp "$APP_DIR/$name" "$OUTPUT/$name"
done
copy_tree "$APP_DIR/static/" "$OUTPUT/static/"
mkdir -p "$OUTPUT/tools"
cp "$APP_DIR/tools/account_login.py" "$OUTPUT/tools/account_login.py"
mkdir -p "$OUTPUT/damai_appium"
for name in __init__.py config.py sale_clock.py damai_app_v2.py; do
  cp "$APP_DIR/damai_appium/$name" "$OUTPUT/damai_appium/$name"
done

# app.py 在模块级导入这些本地模块，缺一个启动就会失败
for name in app.py config_store.py process_manager.py android_config.py \
            android_calibrate.py android_diagnostics.py android_environment.py \
            android_prewarm.py time_sync.py adb_session.py activity_watch.py \
            flip_watch.py scrcpy_stream.py tools/account_login.py \
            damai_appium/__init__.py damai_appium/config.py \
            damai_appium/sale_clock.py damai_appium/damai_app_v2.py; do
  if [ ! -f "$OUTPUT/$name" ]; then
    echo "打包文件缺失：$OUTPUT/$name" >&2
    exit 1
  fi
done

# 不带出本机设备、场次、观演人和登录态
"$PYTHON_BIN" - "$OUTPUT/damai/config.json" <<'PY'
import json
import sys

config = {
    "index_url": "https://www.damai.cn/",
    "login_url": "https://passport.damai.cn/login",
    "target_url": "",
    "users": [],
    "city": "",
    "dates": [],
    "prices": [],
    "if_listen": True,
    "if_commit_order": False,
    "max_retries": 1000,
    "fast_mode": True,
    "page_load_delay": 2,
}
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(config, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
PY

cp "$PACKAGING/windows/启动抢票.bat" "$OUTPUT/启动抢票.bat"
cp "$PACKAGING/windows/使用说明.txt" "$OUTPUT/使用说明.txt"
cp "$PACKAGING/THIRD_PARTY_NOTICES.md" "$OUTPUT/THIRD_PARTY_NOTICES.md"

# .bat 需要 CRLF，避免部分 Windows 环境下命令解析异常
"$PYTHON_BIN" - "$OUTPUT/启动抢票.bat" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
path.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
PY

log "打包 zip"
# 用 Python zipfile 写入 UTF-8 文件名标记，避免 Windows 解压中文文件名乱码
"$PYTHON_BIN" - "$APP_DIR/dist" "$(basename "$OUTPUT")" "$ARCHIVE" <<'PY'
import os
import sys
import zipfile

dist_dir, folder, archive = sys.argv[1:4]
source = os.path.join(dist_dir, folder)
with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
    for dirpath, dirnames, filenames in os.walk(source):
        dirnames.sort()
        for filename in sorted(filenames):
            full_path = os.path.join(dirpath, filename)
            arcname = os.path.relpath(full_path, dist_dir).replace(os.sep, "/")
            bundle.write(full_path, arcname)
PY

cat <<EOF

完成：
  目录  $OUTPUT
  压缩包 $ARCHIVE
  大小  $(du -sh "$ARCHIVE" | cut -f1)

把 zip 拷到 Windows 解压后，双击“启动抢票.bat”即可。
已内置 Python $PYTHON_VERSION 与 Chrome for Testing $CHROME_VERSION，无需安装任何软件。
EOF
