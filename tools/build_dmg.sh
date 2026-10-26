#!/usr/bin/env bash
# 打包成 DMG：应用 + 「Applications」快捷方式，拖进去即安装。
# 用法：./tools/build_dmg.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="0.3.0"
APP_NAME="大麦抢票助手"
APP="$ROOT/$APP_NAME.app"
DMG="$ROOT/$APP_NAME-$VERSION.dmg"

say() { printf '%s\n' "$*"; }

# 1) 先构建 .app
"$ROOT/tools/build_app.sh" >/dev/null
[ -d "$APP" ] || { echo "✗ 未找到 $APP"; exit 1; }

# 2) 准备临时目录
STAGE="$(mktemp -d)/dmg"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
cat > "$STAGE/使用说明.txt" <<'TXT'
大麦抢票助手（本地自用构建）

安装：把「大麦抢票助手.app」拖到左侧 Applications 文件夹。

首次打开：若系统提示「无法验证开发者」，请在应用上右键 →「打开」，或执行：
    xattr -dr com.apple.quarantine "/Applications/大麦抢票助手.app"

数据位置：
  账号库、配置、日志、驱动缓存都在
  ~/Library/Application Support/DamaiHelper/
  重新打包或移动 app 都不会丢。

上手三步：
  1. 粘贴演出链接 → 点「读取信息」
  2. 选场次和票档
  3. 账号库里添加账号 → 点「登录」扫码 → 勾选观演人 → 开始抢票
     多个账号可同时勾选，一起并发抢。

说明：本应用是薄壳，指向构建时的仓库目录与 Python 环境。
      仓库移动位置后需要重新执行 tools/build_dmg.sh。
TXT

# 3) 生成压缩 DMG
rm -f "$DMG"
say "▶ 生成 $DMG"
hdiutil create \
  -volname "$APP_NAME" \
  -srcfolder "$STAGE" \
  -fs HFS+ \
  -format UDZO \
  -ov \
  "$DMG" >/dev/null

rm -rf "$(dirname "$STAGE")"

# 4) 校验
hdiutil verify "$DMG" >/dev/null && say "✓ 校验通过"
say "✓ 完成：$DMG ($(du -h "$DMG" | cut -f1))"
