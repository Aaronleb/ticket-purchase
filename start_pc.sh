#!/usr/bin/env bash
# 大麦抢票 - PC 端本地启动脚本（macOS / Linux）
#
# 用法：
#   ./start_pc.sh --check   只做环境自检 + 起一次无头 Chrome 验活，不碰大麦
#   ./start_pc.sh           自检通过后真正启动抢票（会打开可见浏览器，需要你扫码登录）
#
# 说明：本脚本只解决"启动前置条件"，不改动 damai/ 里的业务代码。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$ROOT/damai"
PY="${PYTHON:-python3}"
MODE="${1:-run}"

say() { printf '%s\n' "$*"; }
die() { printf '✗ %s\n' "$*" >&2; exit 1; }

# ---------- 0. 基本工具 ----------
say "▶ 运行环境"
command -v "$PY" >/dev/null 2>&1 || die "找不到 python3，请先安装 Python 3.9+"
say "  Python : $("$PY" -V 2>&1)"
case "$(uname -s)" in
  Darwin) CHROME_BIN="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ;;
  *)      CHROME_BIN="$(command -v google-chrome || command -v google-chrome-stable || true)" ;;
esac
[ -n "${CHROME_BIN:-}" ] && [ -x "${CHROME_BIN:-}" ] || die "未找到 Chrome，请先安装 Google Chrome"
say "  Chrome : $("$CHROME_BIN" --version 2>/dev/null | sed 's/^Google Chrome //')"
say "  目录   : $APP_DIR"
say ""

# ---------- 1. Python 依赖 ----------
say "▶ 依赖检查"
"$PY" -c "import selenium" 2>/dev/null || die "缺少 selenium，请执行：$PY -m pip install 'selenium>=4.18.1'"
"$PY" - <<'PY'
import selenium
print(f"  selenium: {selenium.__version__}")
PY
[ -f "$APP_DIR/config.json" ] || die "缺少配置文件 damai/config.json"
say "  config.json: 已找到"
say ""

# ---------- 2. 解析匹配当前 Chrome 的 chromedriver ----------
# 项目里的 check_environment.get_chromedriver_path() 会按 PATH / 当前目录 / Homebrew 路径找 driver，
# 并要求主版本号与 Chrome 完全一致；这里用 Selenium Manager 取一份对齐版本，再软链到 damai/chromedriver。
say "▶ 准备 chromedriver（按 Chrome 主版本对齐）"
export SE_CACHE_PATH="${SE_CACHE_PATH:-$ROOT/.drivers/selenium}"
mkdir -p "$SE_CACHE_PATH"

DRIVER="$("$PY" - <<'PY' | tail -n 1
from selenium import webdriver

opts = webdriver.ChromeOptions()
opts.add_argument("--headless=new")
opts.add_argument("--no-sandbox")
opts.add_argument("--disable-gpu")
drv = webdriver.Chrome(options=opts)
path = drv.service.path
drv.quit()
print(path or "")
PY
)"

[ -n "${DRIVER:-}" ] && [ -x "$DRIVER" ] || die "chromedriver 解析失败（可尝试：rm -rf ~/.cache/selenium 后重试）"
say "  driver : $DRIVER"
"$DRIVER" --version | sed 's/^/  version: /'

ln -sf "$DRIVER" "$APP_DIR/chromedriver"
say "  已软链 : damai/chromedriver -> $DRIVER"
say ""

# ---------- 3. 项目自带环境自检（必须在 damai/ 目录下跑，它按相对路径读 config.json）----------
say "▶ 项目环境自检"
set +e
( cd "$APP_DIR" && "$PY" check_environment.py )
RC=$?
set -e
say ""
if [ "$RC" -ne 0 ]; then
  die "自检未通过，请按上面提示修复后重试"
fi

if [ "$MODE" = "--check" ]; then
  say "✓ 自检模式结束：环境已就绪，未访问大麦。"
  say "  确认 damai/config.json 里的 target_url / dates / prices 是你要抢的场次后，执行：./start_pc.sh"
  exit 0
fi

# ---------- 4. 真正启动 ----------
say "▶ 启动抢票（会打开可见 Chrome，首次需要扫码登录）"
say "  提示：脚本会一直刷新详情页等开售，Ctrl+C 可中断"
say ""
cd "$APP_DIR"
exec "$PY" damai.py
