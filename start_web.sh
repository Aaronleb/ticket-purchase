#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-$APP_DIR/.venv/bin/python}"
export DAMAI_PROJECT_ROOT="${DAMAI_PROJECT_ROOT:-$APP_DIR}"
export SE_CACHE_PATH="${SE_CACHE_PATH:-$APP_DIR/.drivers/selenium}"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  printf '未找到独立 Python 环境，请先运行：\n' >&2
  printf 'cd %s && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt\n' "$APP_DIR" >&2
  exit 1
fi
if [ ! -f "$DAMAI_PROJECT_ROOT/damai/damai.py" ]; then
  printf '未找到原抢票项目：%s\n' "$DAMAI_PROJECT_ROOT" >&2
  printf '请通过 DAMAI_PROJECT_ROOT 指定 ticket-purchase 的路径。\n' >&2
  exit 1
fi

cd "$APP_DIR"
exec "$PYTHON" app.py "$@"
