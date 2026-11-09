# 安卓定时开售与抢票流程优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为现有安卓 worker 增加可选定时开售、严格可购买检测和安全的局部重试，同时减少 Appium 往返并阻止数量或提交状态不明确时继续下单。

**Architecture:** 复用 `AndroidConfigStore → config.jsonc → Config → DamaiBot` 配置链路和现有 Web 表单，不新增进程。`DamaiBot` 在购票前预加载票档并等待本机开售时间，通过独立购买状态检测和元素 ID 点击执行后续流程；外层重试只处理提交前的 WebDriver 异常。

**Tech Stack:** Python 3.9、Appium-Python-Client 4.2.1、Selenium 4.18.1、标准库 `datetime`/`time`、原生 HTML/JavaScript、pytest/unittest.mock。

**Spec:** `docs/superpowers/specs/2026-11-09-android-sale-scheduling-design.md`

## Global Constraints

- `sale_time` 是可选本机 ISO 日期时间，空字符串立即执行；不得把本机时间解释为 UTC。
- 定时购买状态轮询为 100ms，开售按钮等待上限为 30 秒。
- 不修改票档区域、价格排序、观演人去重和自动提交开关语义。
- 提交手势发出后不得因任何异常再次自动提交。
- 不新增产品依赖；测试工具只使用 `pyproject.toml` 已声明的 pytest、pytest-mock、pytest-cov。
- 未经用户明确要求，不执行 Git commit、push、reset 或 rebase。
- 无真实设备时不得把 mock 测试描述为真机抢票成功。

## Review Focus

- 旧配置文件可能存在非法 `sale_time`：worker 必须在建立长时间等待前失败，由 Task 1 和 Task 3 的测试固定。
- 文本“立即预约”同时包含“立即”和“预约”：必须判定为不可购买，由 Task 2 的参数化测试固定。
- 距离开售超过 100 分钟：必须每 60 秒保活，避免超过现有 `newCommandTimeout=6000`，由 Task 3 的 fake clock 测试固定。
- `layout_num` 文本为空或包含无法确认的数量：必须停止后续购买和提交，由 Task 4 的测试固定。
- 提交手势后发生 WebDriver 异常：不得重建会话并再次提交，由 Task 4 的生命周期测试固定。

---

### Task 1: 开售时间配置与 Web 表单

**Files:**
- Modify: `android_config.py:3-83`
- Modify: `damai_appium/config.py:11-43`
- Modify: `static/index.html:96-105`
- Modify: `static/app.js:73-96`
- Test: `tests/test_android_config.py`
- Test: `tests/test_damai_app_v2.py:21-45`

**Interfaces:**
- Consumes: 现有 `validate_android_config(payload) -> dict` 和 `Config.load_config() -> Config`。
- Produces: 规范化配置字段 `sale_time: str`；空字符串或 `YYYY-MM-DDTHH:MM:SS`，以及 `Config.sale_time`。

- [ ] **Step 1: 准备已声明的测试工具**

Run:

```bash
.venv/bin/python -m pytest --version || .venv/bin/python -m pip install "pytest>=8,<9" "pytest-mock>=3.14,<4" "pytest-cov>=5,<6"
```

Expected: pytest 8.x 可用，不安装任何新的产品依赖。

- [ ] **Step 2: 写入配置和界面的失败测试**

在 `AndroidConfigValidationTests` 中增加：

```python
def test_sale_time_is_normalized(self):
    payload = valid_android_config()
    payload["sale_time"] = "2026-12-01T20:30"
    config = validate_android_config(payload)
    self.assertEqual(config["sale_time"], "2026-12-01T20:30:00")


def test_blank_sale_time_preserves_immediate_mode(self):
    config = validate_android_config(valid_android_config())
    self.assertEqual(config["sale_time"], "")
```

在 `AndroidConfigStoreTests.test_save_and_load_round_trip` 使用的 payload 中设置 `sale_time="2026-12-01T20:30"`，并断言 `actual["sale_time"] == "2026-12-01T20:30:00"`。

增加参数化无效值测试（pytest 风格）：

```python
import pytest


@pytest.mark.parametrize("value", [123, "2026-02-30T20:00", "2026-12-01T20:00:00Z", "2026-12-01"])
def test_rejects_invalid_sale_time(value):
    payload = valid_android_config()
    payload["sale_time"] = value
    with pytest.raises(ConfigError, match="开售时间"):
        validate_android_config(payload)
```

保持 `BASE_CONFIG` 不含 `sale_time`，在 `test_load_config_uses_legacy_device_defaults` 中断言旧配置默认 `config.sale_time == ""`。新增 `test_load_config_reads_sale_time`：用 `dict(BASE_CONFIG, sale_time="2026-12-01T20:30:00")` 作为 `mock_open` 内容并断言该值被加载。

- [ ] **Step 3: 运行测试确认失败**

Run:

```bash
.venv/bin/python -m pytest tests/test_android_config.py tests/test_damai_app_v2.py::test_load_config_uses_legacy_device_defaults -q --no-cov
```

Expected: 新测试因缺少 `sale_time` 规范化或 `Config.sale_time` 失败；不得通过删除断言绕过。

- [ ] **Step 4: 实现严格的本机时间校验和兼容加载**

在 `android_config.py` 导入 `datetime`，默认配置增加 `"sale_time": ""`，并实现：

```python
def _optional_sale_time(value) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ConfigError("开售时间必须是本机日期时间字符串")
    text = value.strip()
    if not text:
        return ""
    for pattern in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            parsed = datetime.strptime(text, pattern)
            return parsed.isoformat(timespec="seconds")
        except ValueError:
            pass
    raise ConfigError("开售时间格式无效，请选择不带时区的本机日期时间")
```

在 `validate_android_config` 返回前执行：

```python
config["sale_time"] = _optional_sale_time(config["sale_time"])
```

在 `Config.__init__` 的现有参数末尾添加 `sale_time=""` 并保存属性，在 `load_config` 的最后一个参数使用 `config.get("sale_time", "")`，避免改变已有位置参数顺序。

- [ ] **Step 5: 增加表单字段及保存回显**

在 `static/index.html` 的安卓用户字段前增加：

```html
<label class="field field-full"><span>开售时间 <b>可选</b></span><input id="android-sale-time" type="datetime-local" step="1"><small class="field-hint">按本机时间执行；留空立即开始。提前启动时会先准备会话和票档。</small></label>
```

在 `readAndroidConfig()` 返回对象中加入：

```javascript
sale_time: $("#android-sale-time").value,
```

在 `fillAndroidConfig(config)` 中加入：

```javascript
$("#android-sale-time").value = config.sale_time || "";
```

- [ ] **Step 6: 运行配置与 worker 回归测试**

Run:

```bash
.venv/bin/python -m pytest tests/test_android_config.py tests/test_android_api.py tests/test_damai_app_v2.py -q --no-cov
```

Expected: 全部通过；若现有测试失败，先修复本任务回归再进入下一任务。

---

### Task 2: 元素 ID 点击与严格可购买状态检测

**Files:**
- Modify: `damai_appium/damai_app_v2.py:74-151`
- Test: `tests/test_damai_app_v2.py`

**Interfaces:**
- Consumes: Selenium `By`、Appium `ANDROID_UIAUTOMATOR` 和现有 driver。
- Produces: `_click_element(element) -> bool`、`_element_is_purchasable(element) -> bool`、`_wait_for_purchase_button(timeout: float = 30, poll_interval: float = 0.1)`，后者返回可点击元素或 `None`。

- [ ] **Step 1: 写入点击与购买状态的失败测试**

使用 `DamaiBot.__new__` 和 mock driver，不连接设备：

```python
def test_fast_click_uses_element_id_without_reading_rect(mocker):
    bot = DamaiBot.__new__(DamaiBot)
    bot.driver = Mock()
    element = Mock()
    element.id = "buy-element"
    wait = Mock()
    wait.until.return_value = element
    wait_class = mocker.patch("damai_appium.damai_app_v2.WebDriverWait", return_value=wait)

    assert bot.ultra_fast_click("id", "btn_buy_view") is True

    bot.driver.execute_script.assert_called_once_with(
        "mobile: clickGesture", {"elementId": "buy-element"}
    )
    assert not element.rect.called
    assert wait_class.call_args.kwargs["poll_frequency"] == 0.1


@pytest.mark.parametrize("text", ["预约", "立即预约", "缺货登记", "售罄", "无票"])
def test_purchase_state_rejects_unavailable_text(text):
    bot = DamaiBot.__new__(DamaiBot)
    element = Mock()
    element.get_attribute.side_effect = lambda name: {
        "enabled": "true", "clickable": "true", "text": text,
    }[name]
    assert bot._element_is_purchasable(element) is False
```

另测 `enabled="false"`、`clickable="false"` 和文本“立即购买”三种情况；前两种返回 False，最后一种返回 True。

- [ ] **Step 2: 写入轮询与超时测试**

模拟第一次只找到“立即预约”、第二次找到“立即购买”，验证 `time.sleep(0.1)` 且只返回第二个元素。将 `time.monotonic` 的返回值依次设为 `0`、`0.05`、`0.2`，超时设为 `0.1` 时验证永不点击并返回 `None`。测试只调用 `_wait_for_purchase_button`，点击行为由主流程负责。

- [ ] **Step 3: 运行新增测试确认失败**

Run:

```bash
.venv/bin/python -m pytest tests/test_damai_app_v2.py -q --no-cov -k "fast_click or purchase_state or purchase_button"
```

Expected: 因 `_element_is_purchasable` 尚不存在或点击仍读取坐标而失败。

- [ ] **Step 4: 实现直接元素点击和状态检测**

在 worker 中定义：

```python
PURCHASE_WORDS = ("购买", "抢购", "预订")
UNAVAILABLE_WORDS = ("预约", "缺货", "售罄", "无票", "缺票")
PURCHASE_SELECTORS = (
    (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches(".*购买.*|.*抢购.*|.*预订.*")'),
    (By.XPATH, '//*[contains(@text,"购买") or contains(@text,"抢购") or contains(@text,"预订")]'),
)
```

实现 `_click_element`：

```python
def _click_element(self, element):
    self.driver.execute_script("mobile: clickGesture", {"elementId": element.id})
    return True
```

让 `ultra_fast_click` 使用 `WebDriverWait(self.driver, timeout, poll_frequency=0.1)` 和 `_click_element`，`smart_wait_and_click` 复用 `ultra_fast_click`，删除坐标读取。

实现 `_element_is_purchasable`：分别读取 `enabled`、`clickable`、`text`，属性必须为字符串 `"true"` 才可用；先拒绝 `UNAVAILABLE_WORDS`，再要求至少一个 `PURCHASE_WORDS`。

实现 `_wait_for_purchase_button`：每轮依次对 `PURCHASE_SELECTORS` 调用 `find_elements`，筛选 `_element_is_purchasable`；找到立即返回，到达单调时钟 deadline 返回 `None`，否则 `time.sleep(poll_interval)`。此函数不得调用点击。

- [ ] **Step 5: 运行本任务与既有观演人测试**

Run:

```bash
.venv/bin/python -m pytest tests/test_damai_app_v2.py -q --no-cov
```

Expected: 新 helper 测试通过；主流程测试若因尚未接入 helper 失败，在 Task 4 更新，不在本任务保留旧坐标点击来迎合测试。

---

### Task 3: 单调时钟定时等待与会话保活

**Files:**
- Modify: `damai_appium/damai_app_v2.py`
- Test: `tests/test_damai_app_v2.py`

**Interfaces:**
- Consumes: Task 1 的 `Config.sale_time`。
- Produces: `seconds_until_sale(sale_time: str, now: datetime) -> float`、`wait_for_deadline(deadline, *, clock, sleep, keepalive) -> None`、`DamaiBot.wait_until_sale(now=None) -> None`、`DamaiBot._session_keepalive() -> None`。

- [ ] **Step 1: 写入空时间、过去时间和非法时间测试**

```python
from datetime import datetime


def test_seconds_until_sale_handles_immediate_modes():
    now = datetime(2026, 12, 1, 20, 29, 50)
    assert seconds_until_sale("", now) == 0
    assert seconds_until_sale("2026-12-01T20:29:50", now) == 0
    assert seconds_until_sale("2026-12-01T20:30:00", now) == 10


def test_wait_until_sale_rejects_invalid_legacy_value():
    bot = DamaiBot.__new__(DamaiBot)
    bot.config = SimpleNamespace(sale_time="not-a-date")
    with pytest.raises(ValueError, match="开售时间"):
        bot.wait_until_sale()
```

`seconds_until_sale` 对非法值应抛出带“开售时间”的 `ValueError`，而不是原生难以理解的消息。

- [ ] **Step 2: 写入 fake clock 保活测试**

```python
class FakeClock:
    def __init__(self):
        self.value = 0.0
        self.sleeps = []

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += seconds


def test_long_wait_keeps_session_alive_every_sixty_seconds():
    clock = FakeClock()
    keepalive = Mock()

    wait_for_deadline(125, clock=clock, sleep=clock.sleep, keepalive=keepalive)

    assert clock.value == 125
    assert keepalive.call_count == 2
    assert max(clock.sleeps) <= 1
```

另测空/过去时间不调用模块级 `wait_for_deadline`，未来时间调用一次且 `keepalive` 指向 bot 方法。

- [ ] **Step 3: 运行定时测试确认失败**

Run:

```bash
.venv/bin/python -m pytest tests/test_damai_app_v2.py -q --no-cov -k "sale or deadline or keepalive"
```

Expected: 因定时 helper 尚不存在而失败。

- [ ] **Step 4: 实现定时与保活 helper**

导入 `datetime` 并实现：

```python
def seconds_until_sale(sale_time: str, now: datetime) -> float:
    if not sale_time:
        return 0.0
    try:
        target = datetime.fromisoformat(sale_time)
        if target.tzinfo is not None:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("开售时间必须是不带时区的本机 ISO 日期时间") from exc
    return max(0.0, (target - now).total_seconds())
```

实现 `wait_for_deadline`：初始化 `next_keepalive = clock() + 60`；每轮计算剩余时间，剩余小于等于零即返回，否则 `sleep(min(1.0, remaining))`；醒后若 `clock() >= next_keepalive`，调用一次 `keepalive()` 并把下一次保活点设为当前时间加 60 秒。

实现：

```python
def _session_keepalive(self):
    return self.driver.current_activity


def wait_until_sale(self, now=None):
    sale_time = getattr(self.config, "sale_time", "")
    remaining = seconds_until_sale(sale_time, now or datetime.now())
    if remaining <= 0:
        return
    print(f"等待开售，剩余 {remaining:.1f} 秒")
    wait_for_deadline(
        time.monotonic() + remaining,
        clock=time.monotonic,
        sleep=time.sleep,
        keepalive=self._session_keepalive,
    )
```

- [ ] **Step 5: 运行定时与配置测试**

Run:

```bash
.venv/bin/python -m pytest tests/test_damai_app_v2.py tests/test_android_config.py -q --no-cov -k "sale or deadline or keepalive or config"
```

Expected: PASS；fake clock 不实际等待 125 秒。

---

### Task 4: 主流程预加载、数量校验与安全重试

**Files:**
- Modify: `damai_appium/damai_app_v2.py:153-284`
- Test: `tests/test_damai_app_v2.py`
- Test: `tests/test_desktop_app.py:90-105`

**Interfaces:**
- Consumes: Task 2 的购买检测和 Task 3 的 `wait_until_sale`。
- Produces: `_load_ticket_candidates() -> list[dict]`、`_select_city() -> bool`、`_set_quantity() -> bool`、`run_ticket_grabbing() -> bool`、保留签名的 `run_with_retry(max_retries=3) -> bool`；实例字段 `_submit_attempted: bool`。

- [ ] **Step 1: 调整主流程测试夹具并写预加载/定时顺序测试**

在 `make_bot` 中设置 `bot.ticket_candidates = []`、`bot._submit_attempted = False`，mock `_load_ticket_candidates`、`wait_until_sale`、`_wait_for_purchase_button` 和 `_set_quantity`。票档页测试直接返回容器，非票档页测试返回 mock 购买元素。

记录方法调用顺序，断言：票档预加载先于 `wait_until_sale`，定时等待先于购买按钮检测，购买检测先于 `select_ticket`。空 `sale_time` 时购买等待 timeout 为 `1.5`，非空时为 `30`，两者 `poll_interval` 都为 `0.1`。

- [ ] **Step 2: 写入数量失败和购买按钮失败测试**

```python
def test_unreadable_quantity_stops_before_buy_and_submit(mocker):
    bot = make_bot(mocker, if_commit_order=True)
    bot._set_quantity.return_value = False

    assert bot.run_ticket_grabbing() is False

    bot.ultra_fast_click.assert_not_called()
    bot.ultra_batch_click.assert_not_called()


def test_sale_button_timeout_does_not_click_or_recreate_session(mocker):
    bot = make_bot(mocker, if_commit_order=True)
    bot._wait_for_purchase_button.return_value = None

    assert bot.run_ticket_grabbing() is False

    bot._click_element.assert_not_called()
    bot.driver.quit.assert_not_called()
```

为 `_set_quantity` 增加独立测试：`layout_num` 返回“2张”且两位观演人时成功；返回空文本、错误数字，或多位观演人找不到数量区域时返回 False；单位观演人且无数量区域时允许继续。

- [ ] **Step 3: 写入业务失败和提交后异常的生命周期测试**

普通 `False` 返回时，`run_with_retry` 只调用一次 `run_ticket_grabbing`、不调用 `_setup_driver`，最终 `driver.quit()` 一次。

让 `run_ticket_grabbing` 第一次抛出 `WebDriverException`、第二次返回 True，且 `_submit_attempted=False`，验证重建一次会话。另设 `_submit_attempted=True` 并抛出同一异常，验证不重建、返回 False，并通过 capsys 检查“订单状态未知”提示。

- [ ] **Step 4: 运行新增测试确认失败**

Run:

```bash
.venv/bin/python -m pytest tests/test_damai_app_v2.py -q --no-cov -k "quantity or preload or sale_button or retry or submit_after"
```

Expected: 因主流程尚未预加载、校验数量或区分异常而失败。

- [ ] **Step 5: 重构主流程并接入所有 helper**

将缓存加载拆为：

```python
def _load_ticket_candidates(self):
    if self.ticket_candidates is None:
        self.ticket_candidates = load_prepared_tickets(
            "ticket_candidates.json", self.config.ticket_area
        )
    return self.ticket_candidates
```

`select_ticket` 调用 `_load_ticket_candidates()` 后继续使用 `select_prepared_ticket`。

`run_ticket_grabbing` 的严格顺序为：

```python
self._submit_attempted = False
started = time.perf_counter()
self._load_ticket_candidates()
on_price_page = bool(self.driver.find_elements(
    By.ID, "cn.damai:id/project_detail_perform_price_flowlayout"
))
if not on_price_page and not self._select_city():
    return False
self.wait_until_sale()
if not on_price_page:
    timeout = 30 if getattr(self.config, "sale_time", "") else 1.5
    purchase = self._wait_for_purchase_button(timeout=timeout, poll_interval=0.1)
    if purchase is None:
        return False
    self._click_element(purchase)
if not self.select_ticket():
    return False
if not self._set_quantity():
    return False
if not self.ultra_fast_click(By.ID, "btn_buy_view"):
    fallback = AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches(".*确定.*|.*购买.*")'
    if not self.ultra_fast_click(*fallback):
        return False
user_clicks = [
    (AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().text("{user}")')
    for user in self.config.users
]
if not self.ultra_batch_click(user_clicks):
    return False
if self.config.if_commit_order:
    submit_selectors = [
        (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().text("立即提交")'),
        (AppiumBy.ANDROID_UIAUTOMATOR, 'new UiSelector().textMatches(".*提交.*|.*确认.*")'),
        (By.XPATH, '//*[contains(@text,"提交")]'),
    ]
    self._submit_attempted = True
    if not self.smart_wait_and_click(*submit_selectors[0], submit_selectors[1:]):
        return False
return True
```

将城市选择提取为 `_select_city()`，内容只包含现有城市三个选择器和 `smart_wait_and_click`，不得把购买入口混入该方法。

`_set_quantity` 从 `layout_num` 元素读取 `get_attribute("text")`，用 `re.fullmatch(r"\D*(\d+)\D*", text)` 提取文本中的唯一数字；加号复用已找到的元素 ID 点击，保留每次加号后的 20ms 休眠。读取不到唯一数字或数字不等于 `len(users)` 时打印原因并返回 False。无数量区域仅在单位观演人时返回 True。

主流程不再执行 `finally: driver.quit()`；`WebDriverException` 必须向外抛出，其他异常记录后返回 False。使用 `time.perf_counter()` 在各阶段边界打印耗时。

`run_with_retry` 只在捕获 `WebDriverException` 且 `_submit_attempted=False` 时重建会话，普通 False 直接返回；提交后异常打印“订单状态未知，请在大麦 App 核对”，不重建。整个方法在 finally 中最多执行一次 `driver.quit()`，并保留 `max_retries=3` 和桌面 worker 调用方式。

- [ ] **Step 6: 更新旧断言并运行完整 worker/API/桌面测试**

把旧测试中购买入口的 `smart_wait_and_click` 调用次数断言改为 `_wait_for_purchase_button` 和 `_click_element` 断言；保留“票档页跳过城市和购买”“未选观演人不提交”“提交按钮缺失返回 False”等行为断言。

Run:

```bash
.venv/bin/python -m pytest tests/test_damai_app_v2.py tests/test_ticket_selection.py tests/test_android_api.py tests/test_desktop_app.py -q --no-cov
```

Expected: 全部通过，无真实 Appium 连接。

---

### Task 5: 文档、完整回归与仅安卓 Web 验证

**Files:**
- Modify: `README.md:59-71`
- Modify: `damai_appium/damai_app_v2.py` 中已过时注释和 docstring
- Test: 现有完整测试套件

**Interfaces:**
- Consumes: Tasks 1-4 已完成的行为。
- Produces: 与实际定时、准备、停止和真机限制一致的用户文档与验证结果。

- [ ] **Step 1: 更新使用说明和旧行为注释**

在安卓开发模式说明中加入：先登录并打开详情页、识别并缓存票档、可选填写本机开售时间、留空立即开始、定时等待可停止、按钮 30 秒不可买会失败、提交后异常需人工核对订单。删除或改写“普通失败两秒后重建会话”“坐标点击更快”等不再成立的注释，不添加解释修改历史的注释。

- [ ] **Step 2: 运行聚焦测试和完整标准测试**

Run:

```bash
.venv/bin/python -m pytest tests/test_damai_app_v2.py tests/test_ticket_selection.py tests/test_android_config.py tests/test_android_api.py -q --no-cov
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m pytest
```

Expected: 聚焦测试和 unittest 全通过；完整 pytest 无失败。若完整覆盖率门槛暴露与本改动相关缺口，补行为测试；不得删除失败测试或降低 80% 门槛。记录任何原已存在且与改动无关的失败。

- [ ] **Step 3: 验证仅安卓 Web 的真实配置链路**

保持现有仅安卓服务 `http://127.0.0.1:8765` 用于检查新字段展示，不在该服务保存测试配置。另用临时目录中的 `config.jsonc` 和独立端口启动隔离的 `create_server(android_only=True)`，通过浏览器验证时间输入、保存后回显和刷新持久化，并通过隔离服务的 `/api/android/config` 核对规范化 ISO 字符串；验证后关闭隔离服务并删除临时目录。

空时间启动链路只使用测试 fake worker 或 API mock，不触发真实抢票。若 8765 旧进程仍在运行，仅在 Python worker 代码需要重新加载时停止并重启该进程；静态 HTML/JavaScript 修改由现有服务直接读取，不需要为页面修改重启。

- [ ] **Step 4: 检查最终变更和生成物**

Run:

```bash
git status --short
git diff --check
```

Expected: 无空白错误；只保留设计、计划、实现、测试和文档所需文件。删除测试生成的日志、coverage 页面等未被项目忽略且本任务不需要的产物，不删除用户已有文件。

- [ ] **Step 5: 汇报验证边界**

最终说明包含：修改内容、实际测试命令及结果、Web 配置验证结果、真机未验证项（购买按钮真实文本、`layout_num` 数量文本、完整下单链路），以及提交后异常不会自动重试的安全行为。
