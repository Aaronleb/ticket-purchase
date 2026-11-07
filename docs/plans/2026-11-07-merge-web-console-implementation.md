# 合并 Web 控制台实施计划

1. 正常停止当前 `ticket-new` Web 服务及其 Appium。
2. 复制控制台源码、静态页面、打包脚本、测试、依赖文件和设计文档到 `ticket-purchase` 对应位置，不复制虚拟环境、缓存和构建产物。
3. 修改 `packaging/build_dmg.sh`、`packaging/DamaiAndroid.spec` 和开发启动脚本，使项目根目录为当前 `ticket-purchase`。
4. 合并 `.gitignore` 与 README，补齐干净构建环境运行测试所需依赖。
5. 在 `ticket-purchase` 创建新 `.venv`，安装项目与构建依赖，运行控制台和原安卓测试。
6. 构建新的 DMG，执行无窗口、独立窗口和提前退出验证，检查最终应用签名与进程清理。
7. 安装新 DMG 中的应用，保留现有用户配置；用小米手机创建并关闭一次真实 Appium 会话，不执行抢票。
8. 检查新项目文件和最终 DMG，确认无遗漏后删除 `ticket-new`，报告最终路径、大小和校验值。
