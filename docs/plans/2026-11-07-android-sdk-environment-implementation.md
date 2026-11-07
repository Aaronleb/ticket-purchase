# Android SDK 环境修复实施计划

1. 在 `desktop_app.py` 中提取内置安卓运行环境配置函数，设置 `PATH`、`ANDROID_HOME` 和 `ANDROID_SDK_ROOT`，并由主启动流程调用。
2. 在 `tests/test_desktop_app.py` 添加环境配置回归测试，覆盖两个 SDK 变量、内置 ADB 路径和保留原 PATH。
3. 在 `tests/test_appium_service.py` 检查 Appium 子进程继承 SDK 环境；在 `packaging/verify_dmg.py` 清除外部 SDK 变量并验证隔离启动。
4. 运行完整控制台单元测试与原项目安卓测试。
5. 执行 `./packaging/build_dmg.sh` 重建应用和 DMG，再运行无窗口、窗口和提前退出验证。
6. 停止旧应用，安装新应用到 `/Applications`，保留用户数据目录。
7. 用新应用的内置 Appium 和已连接小米手机创建一次真实 UiAutomator2 会话，确认 ADB 定位和会话初始化成功，读取当前页面信息后关闭会话，不触发购票。
8. 更新 README 中真机验证状态，计算最终 DMG 大小和 SHA-256，清理本次临时构建文件。

9. 修正源码 Web worker 的入口脚本、资源参数和 `damai_appium` 导入路径，并覆盖源码与打包两种启动命令测试。
10. 在安卓脚本中检测已有票档容器，兼容直接位于 `NcovSkuActivity` 的入口，添加对应单元测试后同步到当前 Web 使用的内置项目。
