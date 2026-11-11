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
5. 在大麦 App 登录、添加观演人，打开目标演出详情页；也可以手动选好场次后停在票档页。
6. 在控制台检测设备，填写城市、票档区域和观演人。默认区域为“内场”，首次使用保持自动提交关闭。
7. 手机停在票档页面，点击“识别并缓存票档”。本地 OCR 只识别一次并缓存价格顺序，确认候选无误后再开始抢票。
8. 正式抢票时按缓存顺序选择当前最高可购买价位，不重复 OCR。关闭窗口或退出应用会停止抢票 worker、控制台和 Appium。

OCR 不上传手机截图。识别失败、候选不明确或价格核对失败时脚本会停止，不回退到序号。脚本不搜索演出，也不负责按日期或时间选择场次。

小米手机如果提示 `Appium Settings app is not running`，请在系统的 Appium Settings 权限页允许“后台弹出界面”，然后重试。

配置保存在 `~/Library/Application Support/DamaiAndroid/config.jsonc`，票档缓存位于同目录的 `ticket_candidates.json`，日志位于 `logs/`。升级应用不会主动覆盖用户配置或票档缓存；更换演出或票档页面结构变化后应重新识别。订单结果以大麦 App 的订单记录为准。

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

源码 Web 模式需要额外准备 Appium 服务、UiAutomator2 驱动和 Android SDK Platform-Tools，并确保 `adb` 在 `PATH` 中。手机安装官方大麦 App，提前登录并添加观演人。

```bash
npm install -g appium
appium driver install uiautomator2
appium --address 127.0.0.1 --port 4723
```

用 `adb devices` 确认设备在线，输出第一列就是控制台需要填写的设备名。在控制台选择“安卓”，填写设备、城市、票档区域和观演人，保存后点击“检查安卓环境”。检查通过后，在大麦 App 登录并打开目标详情页，保持手机亮屏，再点击“识别并缓存票档”。

可按本机时间填写“开售时间”后提前启动：脚本会先准备 Appium 会话和票档缓存，到点后每 100ms 检查一次明确可购买的按钮，不会把“预约”当作可购买；30 秒仍不可买会报错。留空开售时间则立即执行。定时等待期间可点击“停止任务”。普通页面失败不会重建整个会话，提交前发生驱动异常才会重连；提交后发生任何异常都不会自动重试，需在大麦 App 核对订单，避免重复下单。关闭自动提交时，脚本会停在订单确认页。

安卓配置写入 `damai_appium/config.jsonc`。源码 Web 模式不会管理外部 Appium 服务，也不会把 PC Cookie 用作安卓登录态。

### 坐标快路（可选，显著提速）

默认行为是先查元素再点击，每步 2–5 次 Appium 往返。若在开售前把关键控件坐标采集下来，抢票时会直接按坐标点击，往返次数能降到原来的五分之一左右。

控制台安卓面板下方有「采集页面坐标」三步，按提示依次操作，每步只读取当前页面的控件位置，不会替你点击或提交：

1. **票档页**：手机停在票档页时采集（保存购买条、确定购买和每张票档卡片坐标）。
2. **订单确认页**：手动在手机上点“确定购买”进入观演人列表后采集（保存价格行和每位观演人勾选框坐标）。
3. **提交按钮**：确认观演人已显示后采集；采集完请自行退出，不要提交订单。

坐标存在与票档缓存同目录的 `layout_cache.json`，并与屏幕分辨率绑定。以下任一情况会自动退回元素查找路径，不影响抢票正确性：

- 没有 `layout_cache.json`，或配置里 `fast_mode` 为 `false`；
- 手机分辨率与采集时不同；
- 缓存里缺少当前要点的控件（例如从未采过观演人页）。

多个观演人时，勾选动作会合并成一条 `adb shell` 命令一次下发；adb 不可用会自动改回逐个点击。无论走哪条路径，提交开关、观演人核对和“提交后不重试”的规则都不变。

想量化提速效果，用 `DAMAI_PROFILE=1` 启动 worker，会按命令类型和阶段打印 Appium 往返次数与平均耗时。

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
