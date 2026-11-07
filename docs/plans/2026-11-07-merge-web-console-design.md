# 合并 Web 控制台到主项目设计

## 目标

以 `ticket-purchase` 为唯一项目目录，整合原 `ticket-new` 的 Web 控制台、macOS 独立窗口、测试、文档和 DMG 打包能力。已验证的安卓抢票行为保持不变。

## 目录与数据流

将控制台 Python 文件、`static/`、`packaging/`、`requirements*.txt`、`start_web.sh` 和控制台测试合并到项目根目录。原控制台设计文档迁入 `docs/plans/`。`damai/`、`damai_appium/` 及原有测试保持原位置。

开发模式以项目根目录为抢票源码目录。DMG 构建仍在 `build/runtime/ticket-purchase/` 创建只读资源副本，桌面应用继续从该副本运行 worker；用户配置和日志仍位于 `~/Library/Application Support/DamaiAndroid/`。

## 构建与依赖

构建脚本和 PyInstaller spec 的项目根目录改为当前目录，不再引用同级 `ticket-purchase`。保留 requirements 安装方式，并确保干净虚拟环境包含运行构建内测试所需的 pytest 插件。`.gitignore` 覆盖虚拟环境、运行时、构建和 DMG 产物。

## 迁移与清理

不迁移旧 `.venv`、缓存、构建目录或旧 DMG。迁移后创建新虚拟环境。最终 DMG 输出到 `ticket-purchase/dist/DamaiAndroid-arm64.dmg`。当前 Web 服务在迁移前正常停止，避免继续加载旧目录。

全部源码复制、测试、DMG 构建与验证成功后，删除 `ticket-new`。若验证失败，保留旧目录用于排查，不执行删除。

## 验证

1. 控制台 unittest 与原项目 pytest 测试通过。
2. 从新项目构建 DMG，无全局 Node、ADB、Appium 时可启动。
3. 无窗口、独立窗口和提前退出验证通过，退出后无 Appium 残留。
4. 使用已连接小米手机只创建、读取并关闭一次 Appium 会话，不执行购票。
5. 更新 README 中的唯一项目路径、开发命令、DMG 路径和真机验证状态。
