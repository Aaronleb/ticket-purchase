# 大麦 PC / 安卓抢票工具

本目录是唯一项目目录，包含 PC 与安卓抢票脚本、本地 Web 控制台、macOS 独立窗口应用及 DMG 打包文件。

## Mac DMG 安卓版

DMG 仅包含安卓功能，面向 Apple 芯片 Mac，内嵌 Python、Node.js、ADB、Appium 和 UiAutomator2。最终用户不需要安装这些依赖，也不需要保留项目源码。

在开发机上构建：

```bash
./packaging/build_dmg.sh
```

产物为 `dist/DamaiAndroid-arm64.dmg`。构建需要联网下载 `packaging/dependencies.env` 中固定版本和校验值对应的依赖。

用户使用顺序：

1. 打开 DMG，将 `DamaiAndroid.app` 拖入“应用程序”。
2. 当前测试版没有 Developer ID 签名和公证，首次在 Finder 中右键应用并选择“打开”，不要关闭 macOS 安全保护。
3. 手机开启 USB 调试并连接 Mac，在手机上允许调试授权。
4. 双击应用，自动启动内置 Appium，并在独立 Mac 窗口中显示控制台。
5. 在大麦 App 登录并完成原生预约，提前选好目标场次、票档和观演人，然后停在目标演出详情页。
6. 在控制台检测设备，填写与预约一致的购票数量和北京时间抢购时间。首次使用保持自动提交关闭。
7. 环境检查通过后开始抢票，程序先建立 Appium 会话，再自动等待到设定时间，无需再次点击。关闭窗口或退出应用会停止抢票 worker、控制台和 Appium。

安卓端仅支持原生预约快路：定时到点后点击“立即购买”，核对确认页金额和已选观演人数，不修改预约选择；不符合预期时停止，自动提交最多点击一次，不自动重试。过期时间不会补点购买。金额只做有效性检查，场次和票档以大麦预约结果为准。不支持预约的场次无法使用安卓端。使用时保持 Mac 与手机唤醒，并在系统中启用自动设置时间。

小米手机如果提示 `Appium Settings app is not running`，请在系统的 Appium Settings 权限页允许“后台弹出界面”，然后重试。

配置保存在 `~/Library/Application Support/DamaiAndroid/config.jsonc`，日志位于同目录的 `logs/`。升级应用不会主动覆盖用户配置。订单结果以大麦 App 的订单记录为准。

## 开发模式安装与 Web 启动

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
./start_web.sh
```

终端会显示 `http://127.0.0.1:8765`，并自动打开浏览器。可使用其他端口：

```bash
./start_web.sh --port 8080
```

服务只监听本机地址，不接收账号密码。开发模式默认直接控制本项目的 `damai/` 和 `damai_appium/`。

### PC 使用顺序

1. 选择“PC”，填写演出链接、城市、日期、票价和观演人，保存配置。
2. 点击“运行环境自检”。
3. 没有登录态时，点击“打开扫码登录”，扫码后回到页面点击“我已完成登录”。
4. 点击“开始 PC 抢票”；需要停止时点击“停止任务”。

PC 配置写入 `damai/config.json`，登录 Cookie 写入 `damai/damai_cookies.pkl`。

### 安卓开发模式

源码 Web 模式需要额外准备 Appium 服务、UiAutomator2 驱动和 Android SDK Platform-Tools，并确保 `adb` 在 `PATH` 中。手机安装官方大麦 App，提前登录、添加观演人并完成目标演出的原生预约。

```bash
npm install -g appium
appium driver install uiautomator2
appium --address 127.0.0.1 --port 4723
```

用 `adb devices` 确认设备在线，输出第一列就是控制台需要填写的设备名。在控制台选择“安卓”，填写设备、购票数量和抢购时间，保存后点击“检查安卓环境”。检查通过后再开始；程序会自动等待到设定时间，关闭自动提交时，脚本会停在订单确认页。

安卓配置写入 `damai_appium/config.jsonc`。源码 Web 模式不会管理外部 Appium 服务，也不会把 PC Cookie 用作安卓登录态。

## 订单结果

页面展示的是进程状态，不代表订单结果。观演人必须显示已勾选；无论是否开启自动提交，都要在大麦订单记录中确认是否生成待支付订单，并及时处理。

## 测试与 DMG 验证

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m pytest tests/test_damai_app_v2.py -q --no-cov
```

从实际 DMG 做隔离启动验证：

```bash
.venv/bin/python packaging/verify_dmg.py
.venv/bin/python packaging/verify_dmg.py --window
.venv/bin/python packaging/verify_dmg.py --early-exit
```

验证脚本使用独立测试数据目录和 ADB 端口，不启动真实抢票。真机验证需要单独连接已授权的安卓设备。

## 项目结构

```text
ticket-purchase/
├── damai/             # PC 抢票
├── damai_appium/      # 安卓抢票
├── static/            # 本地控制台页面
├── tests/             # 控制台与抢票测试
├── packaging/         # DMG 构建和验证
├── docs/              # 设计与截图
├── app.py             # Web 控制台
├── desktop_app.py     # macOS 独立窗口与 worker 入口
└── start_web.sh       # 开发模式启动
```
