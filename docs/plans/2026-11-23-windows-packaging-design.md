# Windows 免安装版打包设计

## 目标

为现有安卓独立窗口应用提供 Windows 10/11 x64 免安装包，功能与当前 macOS DMG 对齐。产物为 `DamaiAndroid-windows-x64.zip`，解压后运行 `DamaiAndroid.exe`，最终用户不需要另行安装 Python、Node.js、ADB、Appium 或 UiAutomator2。

## 方案

复用现有桌面应用和安卓抢票逻辑，进行必要的跨平台适配，并通过 GitHub Actions 在 Windows 环境构建。PyInstaller 不支持从 macOS 交叉生成 Windows 可执行文件，因此不在开发 Mac 上直接构建 exe。

不采用浏览器替代独立窗口，也不要求最终用户安装运行环境。

## 应用适配

- Windows 使用 `%LOCALAPPDATA%\DamaiAndroid` 保存配置、日志和可写的 Appium Home；macOS 路径保持不变。
- PyInstaller 的 Windows one-dir 资源目录与 macOS app bundle 分别处理。
- Windows 使用 `node.exe` 和 `adb.exe`，并将对应目录加入进程环境。
- AppKit 退出处理仅在 macOS 加载；Windows 使用 pywebview 的通用窗口关闭行为。
- 现有安卓 worker、Scrcpy 模板感知、配置和订单逻辑保持不变。

## 构建与交付

- 新增 Windows PyInstaller 配置和 PowerShell 构建脚本。
- 使用固定版本的 Node.js Windows x64 和 Android Platform Tools Windows 包，并校验 SHA-256。
- 在 Windows 上安装 Python 与 Node 依赖、安装 UiAutomator2 驱动、运行测试并组装 one-dir 应用。
- GitHub Actions 使用 Windows x64 环境完成构建，将 zip 上传为 Actions Artifact。
- 现有 macOS 构建脚本和 DMG 产物保持可用。
- 测试版不做 Authenticode 签名；Windows SmartScreen 可能提示，用户需确认“仍要运行”。

## 验证

- 运行现有单元测试及新增的平台路径和 Windows 构建配置测试。
- 从最终 zip 解压目录隔离启动 exe，验证本地控制台和内置 Appium 可启动，不执行真实抢票。
- 没有 Windows 真机及已授权安卓设备时，不将真机抢票链路标记为已验证。

## 实施顺序

1. 为资源路径、数据目录、运行时文件名和窗口退出增加平台分支及测试。
2. 新增 Windows 构建脚本、PyInstaller 配置和 GitHub Actions 工作流。
3. 更新 README 的 Windows 构建与使用说明。
4. 在 macOS 运行完整相关测试，检查 macOS 行为未回归。
5. 推送分支并运行 GitHub Actions，下载并检查 Windows zip；若云端测试失败则修复后重新构建。
