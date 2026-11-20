"use strict";

const $ = (selector) => document.querySelector(selector);
const logsElement = $("#logs");
const phaseLabels = {
  idle: "就绪",
  waiting_login: "等待扫码",
  checking: "环境自检中",
  grabbing: "抢票运行中",
  stopping: "正在停止",
  stopped: "已停止",
  error: "需要处理",
};
const activePhases = new Set(["waiting_login", "checking", "grabbing", "stopping"]);
let currentPlatform = "pc";
let currentConfig = {};
let currentAndroidConfig = {};
let currentStatus = { phase: "idle", running: false, enabled_platforms: ["pc", "android"] };
let androidDevices = [];
let managedAppium = false;
let toastTimer;

async function api(path, options = {}) {
  const method = options.method || "GET";
  const headers = { ...(options.headers || {}) };
  if (["POST", "PUT", "DELETE"].includes(method)) headers["X-Requested-With"] = "fetch";
  if (options.body) headers["Content-Type"] = "application/json";
  const response = await fetch(path, { ...options, method, headers });
  const payload = await response.json().catch(() => ({ error: "服务返回了无法识别的内容" }));
  if (!response.ok) throw new Error(payload.error || `请求失败（${response.status}）`);
  return payload;
}

function toast(message, error = false) {
  const element = $("#toast");
  clearTimeout(toastTimer);
  element.textContent = message;
  element.classList.toggle("error", error);
  element.classList.add("visible");
  toastTimer = setTimeout(() => element.classList.remove("visible"), 3200);
}

function splitValues(value) {
  return [...new Set(value.split(/[,，、\n;；]/).map((item) => item.trim()).filter(Boolean))];
}

function readConfig() {
  return {
    ...currentConfig,
    target_url: $("#target-url").value.trim(),
    city: $("#city").value.trim(),
    dates: splitValues($("#dates").value),
    prices: splitValues($("#prices").value),
    users: splitValues($("#users").value),
    fast_mode: $("#fast-mode").checked,
    if_listen: $("#if-listen").checked,
    if_commit_order: $("#if-commit").checked,
  };
}

function fillConfig(config) {
  currentConfig = { ...config };
  $("#target-url").value = config.target_url || "";
  $("#city").value = config.city || "";
  $("#dates").value = (config.dates || []).join("，");
  $("#prices").value = (config.prices || []).join("，");
  $("#users").value = (config.users || []).join("，");
  $("#fast-mode").checked = Boolean(config.fast_mode);
  $("#if-listen").checked = Boolean(config.if_listen);
  $("#if-commit").checked = Boolean(config.if_commit_order);
}

function readAndroidConfig() {
  return {
    ...currentAndroidConfig,
    server_url: $("#android-server-url").value.trim(),
    device_name: $("#android-device-name").value.trim(),
    platform_version: $("#android-platform-version").value.trim(),
    ticket_count: Number($("#android-ticket-count").value),
    sell_start_time: $("#android-sell-start-time").value,
    if_commit_order: $("#android-if-commit").checked,
    fast_mode: $("#android-fast-mode").checked,
    confirm_point: $("#android-confirm-point").value.trim(),
    submit_point: $("#android-submit-point").value.trim(),
    buy_point: $("#android-buy-point").value.trim(),
    flip_template_dir: $("#android-flip-template-dir").value.trim(),
  };
}

function fillAndroidConfig(config) {
  currentAndroidConfig = { ...config };
  $("#android-server-url").value = config.server_url || "http://127.0.0.1:4723";
  $("#android-device-name").value = config.device_name || "";
  $("#android-platform-version").value = config.platform_version || "";
  $("#android-ticket-count").value = config.ticket_count ?? 1;
  $("#android-sell-start-time").value = config.sell_start_time ? config.sell_start_time.slice(0, 19) : "";
  $("#android-if-commit").checked = Boolean(config.if_commit_order);
  $("#android-fast-mode").checked = config.fast_mode !== false;
  $("#android-confirm-point").value = config.confirm_point || "";
  $("#android-submit-point").value = config.submit_point || "";
  $("#android-buy-point").value = config.buy_point || "";
  $("#android-flip-template-dir").value = config.flip_template_dir || "";
}

function validateConfig(config, platform, options = {}) {
  if (platform === "pc") {
    if (!config.users.length) throw new Error("请至少填写一位观演人");
    if (!config.target_url) throw new Error("请先填写演出详情页链接");
    return;
  }
  if (!config.server_url) throw new Error("请填写 Appium 服务地址");
  if (!config.device_name) throw new Error("请填写 adb devices 显示的设备名");
  if (!config.platform_version) throw new Error("请填写 Android 版本");
  if (!$("#android-ticket-count").value || !Number.isInteger(config.ticket_count) || config.ticket_count < 1 || config.ticket_count > 6) {
    throw new Error("购票数量必须是 1 到 6 之间的整数");
  }
  if (options.skipSaleTime) return;
  if (!config.sell_start_time) throw new Error("请填写抢购时间");
  const saleTimestamp = new Date(config.sell_start_time).getTime();
  if (Number.isNaN(saleTimestamp)) throw new Error("抢购时间格式无效");
  if (saleTimestamp <= Date.now()) throw new Error("抢购时间已过，请选择未来时间");
}

function setPlatform(platform, force = false) {
  if (!force && activePhases.has(currentStatus.phase)) return;
  currentPlatform = platform;
  document.querySelectorAll("[data-platform]").forEach((button) => {
    const selected = button.dataset.platform === platform;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
  document.querySelectorAll("[data-platform-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.platformPanel !== platform;
  });
  const android = platform === "android";
  $("#start-label").textContent = android ? "开始安卓抢票" : "开始 PC 抢票";
  $("#check-button").textContent = android ? "检查安卓环境" : "运行环境自检";
  $("#check-hint").textContent = android
    ? managedAppium
      ? "检查内置 Appium、ADB、设备与大麦 App，无需手动启动服务。"
      : "检查 Appium 客户端、服务、ADB 设备与大麦 App。服务命令：appium --address 127.0.0.1 --port 4723"
    : "自检会检查 Python、Selenium、Chrome 与 ChromeDriver。";
  $("#android-check-results").hidden = !android || !$("#android-check-results").children.length;
}

function updateStatus(status) {
  currentStatus = { ...currentStatus, ...status };
  managedAppium = Boolean(currentStatus.managed_appium);
  const androidOnly = (currentStatus.enabled_platforms || []).join(",") === "android";
  $("#platform-switch").hidden = androidOnly;
  $("#android-server-field").hidden = managedAppium;
  $("#android-server-url").required = !managedAppium;
  if (androidOnly && currentPlatform !== "android") setPlatform("android", true);
  if (currentStatus.running && currentStatus.task) {
    const taskPlatform = currentStatus.task === "android" ? "android" : "pc";
    if (taskPlatform !== currentPlatform) setPlatform(taskPlatform, true);
  }
  const phase = currentStatus.phase || "idle";
  const active = activePhases.has(phase);
  $("#status-text").textContent = phaseLabels[phase] || phase;
  $("#status-message").textContent = currentStatus.message || "就绪";
  $("#status-dot").className = `status-dot${active ? " active" : phase === "error" ? " error" : ""}`;
  $("#start-button").hidden = active;
  $("#stop-button").hidden = !active;
  $("#stop-button").disabled = phase === "stopping";
  $("#login-button").hidden = phase === "waiting_login";
  $("#login-complete-button").hidden = phase !== "waiting_login";
  for (const selector of ["#login-button", "#check-button", "#android-network-test", "#android-performance-test", "#android-dry-run", "#android-run-now"]) $(selector).disabled = active;
  document.querySelectorAll("[data-platform]").forEach((button) => { button.disabled = active; });
  $("#start-button").disabled = active;

  const runtime = currentStatus.runtime || {};
  const runtimeMessages = {
    starting: "内置 Appium 正在启动",
    ready: "内置 Appium 已就绪，无需配置环境",
    error: runtime.message || "内置 Appium 启动失败",
    external: "安卓端不读取 PC Cookie",
  };
  $("#android-runtime-status").textContent = runtimeMessages[runtime.state] || runtime.message || "正在读取服务状态";
  $("#android-prep-title").textContent = managedAppium ? "连接并准备手机" : "手动准备手机";
  $("#android-appium-prep").textContent = managedAppium ? "连接手机并允许 USB 调试授权" : "启动 Appium 服务并连接设备";

  const loggedIn = Boolean(currentStatus.logged_in);
  $("#login-title").textContent = loggedIn ? "本机登录态已保存" : "尚未检测到登录态";
  $("#login-description").textContent = loggedIn ? "启动时仍会由大麦校验 Cookie 是否有效" : "可打开扫码登录，或直接启动后按提示登录";
  $("#account-icon").classList.toggle("logged-in", loggedIn);
  if (currentStatus.project_root) $("#project-root").textContent = currentStatus.project_root;
  $("#project-root").title = currentStatus.project_root || "";
}

function appendLog(entry) {
  const empty = logsElement.querySelector(".empty-log");
  if (empty) empty.remove();
  const line = document.createElement("div");
  line.className = "log-line";
  line.dataset.id = entry.id;
  const time = document.createElement("span");
  time.className = "log-time";
  time.textContent = entry.time;
  const text = document.createElement("span");
  text.className = "log-text";
  text.textContent = entry.text || " ";
  line.append(time, text);
  logsElement.append(line);
  const timing = timingFromLog(entry);
  if (timing) renderTiming(timing);
  while (logsElement.children.length > 1200) logsElement.firstElementChild.remove();
  logsElement.scrollTop = logsElement.scrollHeight;
}

function replaceLogs(entries) {
  logsElement.replaceChildren();
  if (!entries.length) {
    const empty = document.createElement("div");
    empty.className = "empty-log";
    empty.innerHTML = "<span>›_</span><p>暂无输出。启动自检、登录或抢票后，日志会出现在这里。</p>";
    logsElement.append(empty);
    return;
  }
  entries.forEach(appendLog);
}

function renderAndroidChecks(report) {
  const container = $("#android-check-results");
  container.replaceChildren();
  const summary = document.createElement("strong");
  summary.className = report.ok ? "check-summary passed" : "check-summary";
  summary.textContent = report.ok ? "环境检查全部通过" : "请先处理以下检查项";
  container.append(summary);
  const list = document.createElement("ul");
  for (const check of report.checks) {
    const item = document.createElement("li");
    item.className = check.ok ? "passed" : "failed";
    const label = document.createElement("span");
    label.textContent = check.label;
    const message = document.createElement("small");
    message.textContent = check.message;
    item.append(label, message);
    list.append(item);
  }
  container.append(list);
  container.hidden = currentPlatform !== "android";
}

function renderDiagnostics(container, report) {
  container.replaceChildren();
  const summary = document.createElement("strong");
  summary.className = `diagnostic-summary level-${report.level || (report.ok ? "pass" : "fail")}`;
  summary.textContent = report.summary || (report.ok ? "检查通过" : "发现问题");
  container.append(summary);
  const list = document.createElement("ul");
  list.className = "diagnostic-list";
  for (const metric of report.metrics || []) {
    const item = document.createElement("li");
    item.className = `diagnostic-item level-${metric.level || "info"}`;
    const head = document.createElement("div");
    head.className = "diagnostic-head";
    const label = document.createElement("strong");
    label.textContent = metric.label;
    const value = document.createElement("span");
    value.className = "diagnostic-value";
    value.textContent = metric.value;
    head.append(label, value);
    const message = document.createElement("small");
    message.textContent = `${metric.target ? `目标 ${metric.target} · ` : ""}${metric.message || ""}`;
    item.append(head, message);
    list.append(item);
  }
  container.append(list);
  if (report.advice && report.advice.length) {
    const advice = document.createElement("div");
    advice.className = "diagnostic-advice";
    const title = document.createElement("strong");
    title.textContent = "建议";
    advice.append(title);
    const items = document.createElement("ul");
    for (const text of report.advice) {
      const item = document.createElement("li");
      item.textContent = text;
      items.append(item);
    }
    advice.append(items);
    container.append(advice);
  }
  container.hidden = false;
}

async function runAndroidDiagnostic(kind) {
  const network = kind === "network";
  const button = $(network ? "#android-network-test" : "#android-performance-test");
  const hint = $(network ? "#android-network-hint" : "#android-performance-hint");
  const results = $(network ? "#android-network-results" : "#android-performance-results");
  const path = network ? "/api/android/network-test" : "/api/android/performance-test";
  const label = button.textContent;
  const defaultHint = hint.textContent;
  button.disabled = true;
  button.textContent = network ? "网络测试中…" : "性能测试中…";
  hint.textContent = network ? "正在 ping detail/mtop 并读取 Wi-Fi 状态（约 5~15 秒）…" : "正在读取内存、温度、帧率与进程状态（约 5~15 秒）…";
  try {
    const report = await api(path, { method: "POST", body: JSON.stringify(readAndroidConfig()) });
    renderDiagnostics(results, report);
    hint.textContent = report.summary;
    toast(report.summary, report.ok === false);
  } catch (error) {
    results.hidden = true;
    hint.textContent = error.message;
    toast(error.message, true);
  } finally {
    button.disabled = activePhases.has(currentStatus.phase);
    button.textContent = label;
    if (!hint.textContent) hint.textContent = defaultHint;
  }
}

$("#android-network-test").addEventListener("click", () => runAndroidDiagnostic("network"));
$("#android-performance-test").addEventListener("click", () => runAndroidDiagnostic("performance"));

const TIMING_PREFIX = "[TIMING] ";

function timingFromLog(entry) {
  const text = entry && entry.text;
  if (!text || !text.startsWith(TIMING_PREFIX)) return null;
  try {
    return JSON.parse(text.slice(TIMING_PREFIX.length));
  } catch {
    return null;
  }
}

function renderTiming(data) {
  const container = $("#android-timing");
  if (!container || !data || !Array.isArray(data.stages) || !data.stages.length) return;
  container.replaceChildren();
  const summary = document.createElement("div");
  summary.className = "timing-summary";
  const title = document.createElement("strong");
  const scope = data.run_now
    ? "立即演练 · 到确认页"
    : data.dry_run
      ? "演练 · 到确认页"
      : data.submitted && data.submit_verified === false
        ? "提交未确认"
        : data.submitted === false
          ? "到确认页"
          : "合计";
  title.textContent = `${data.flow_label || (data.flow === "fast" ? "极速模式" : "普通模式")} · ${scope} ${(Number(data.total_ms || 0) / 1000).toFixed(2)} 秒`;
  const time = document.createElement("span");
  time.textContent = data.at || "";
  summary.append(title, time);
  container.append(summary);
  const hasSku = data.stages.some((stage) => String(stage.label).includes("票档"));
  const chain = document.createElement("div");
  chain.className = `timing-chain${hasSku ? " warn" : ""}`;
  chain.textContent = hasSku
    ? "链路：详情页 → 票档页 → 确认页（2 跳；票档页是额外的页面跳转）"
    : "链路：详情页 → 确认页（1 跳，已跳过票档页）";
  container.append(chain);
  const rows = document.createElement("div");
  rows.className = "timing-rows";
  const total = Math.max(Number(data.total_ms || 0), 1);
  for (const stage of data.stages) {
    const row = document.createElement("div");
    row.className = "timing-row";
    const label = document.createElement("strong");
    label.textContent = stage.label;
    const bar = document.createElement("div");
    bar.className = "timing-bar";
    const fill = document.createElement("i");
    fill.style.width = `${Math.max(2, Math.min(100, (Number(stage.ms || 0) / total) * 100))}%`;
    bar.append(fill);
    const value = document.createElement("em");
    value.textContent = `${(Number(stage.ms || 0) / 1000).toFixed(2)}s`;
    row.append(label, bar, value);
    rows.append(row);
  }
  container.append(rows);
  const note = document.createElement("p");
  note.className = "timing-note";
  note.textContent = data.run_now
    ? "立即演练：跳过倒计时，已到确认页，未点击“立即提交”，不会生成订单。"
    : data.dry_run
      ? "演练模式：已到确认页，未点击“立即提交”，不会生成订单。"
      : data.submitted && data.submit_verified === false
        ? "提交后仍停在确认页，可能未生效；请检查「立即提交」坐标或按钮状态，程序没有再补点。"
        : data.submitted && data.submit_verified === true
          ? "提交后已离开确认页，请在大麦订单记录中确认。"
          : data.submitted === false
            ? "自动提交关闭：耗时统计到确认页可读；页面跳转通常占 80% 以上。"
            : data.flow === "fast"
              ? "极速模式参考：1.6~2.4 秒；页面跳转通常占 80% 以上。"
              : "普通模式参考：2.5~4.0 秒；票档 OCR、确认页读取和观演人勾选是主要开销。";
  container.append(note);
}

function selectAndroidDevice(name) {
  const device = androidDevices.find((item) => item.name === name && item.online);
  if (!device) return;
  $("#android-device-name").value = device.name;
  if (device.platform_version) $("#android-platform-version").value = device.platform_version;
}

async function refreshAndroidDevices() {
  const button = $("#android-refresh-devices");
  const hint = $("#android-device-hint");
  button.disabled = true;
  hint.textContent = "正在通过内置 ADB 检测安卓设备…";
  try {
    const report = await api("/api/android/devices");
    androidDevices = report.devices || [];
    const online = androidDevices.filter((device) => device.online);
    const list = $("#android-device-list");
    list.replaceChildren();
    for (const device of online) {
      const option = document.createElement("option");
      option.value = device.name;
      option.label = `Android ${device.platform_version || "未知"}`;
      list.append(option);
    }
    const currentName = $("#android-device-name").value;
    if (online.length === 1) selectAndroidDevice(online[0].name);
    else if (currentName) selectAndroidDevice(currentName);
    hint.textContent = report.message || (online.length > 1 ? "检测到多台设备，请从设备名输入框的建议中选择。" : "设备检测完成。");
  } catch (error) {
    hint.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

$("#android-device-name").addEventListener("change", (event) => selectAndroidDevice(event.target.value.trim()));
$("#android-refresh-devices").addEventListener("click", refreshAndroidDevices);

async function checkTimeSync() {
  const button = $("#android-time-sync");
  const hint = $("#android-time-sync-hint");
  button.disabled = true;
  hint.textContent = "正在对时（读取手机时钟与服务器时间）…";
  const format = (ms) => `${ms >= 0 ? "+" : ""}${ms.toFixed(1)} 毫秒`;
  try {
    const result = await api("/api/android/time-sync");
    const parts = [];
    if (result.phone_offset_ms !== null) parts.push(`本机与手机偏差 ${format(result.phone_offset_ms)}`);
    if (result.server_offset_ms !== null) parts.push(`本机与阿里服务器偏差 ${format(result.server_offset_ms)}`);
    hint.textContent = parts.length ? `${parts.join("；")}。抢票放行已自动对齐手机时钟。` : "对时失败：手机与服务器都不可达。";
    toast("对时完成");
  } catch (error) {
    hint.textContent = error.message;
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}
$("#android-time-sync").addEventListener("click", checkTimeSync);

async function calibrateAndroidPoint() {
  const button = $("#android-calibrate");
  const hint = $("#android-calibrate-hint");
  button.disabled = true;
  button.textContent = "识别中…";
  hint.textContent = "正在截图识别当前页面按钮…（首次约 5 秒，为 OCR 预热）";
  try {
    const result = await api("/api/android/calibrate", {
      method: "POST",
      body: JSON.stringify(readAndroidConfig()),
    });
    fillAndroidConfig(result.config);
    hint.textContent = `已在「${result.page}」识别到「${result.button}」，坐标 ${result.point} 已写入上方输入框并保存。${result.template_path ? " 已保存无 OCR 购买按钮模板。" : ""}${result.warning ? ` ${result.warning}` : ""}`;
    toast(`已采集「${result.button}」坐标 ${result.point}`);
  } catch (error) {
    hint.textContent = error.message;
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "采集当前页按钮坐标";
  }
}
$("#android-calibrate").addEventListener("click", calibrateAndroidPoint);

async function calibrateNegativeTemplate() {
  const button = $("#android-calibrate-negative");
  const hint = $("#android-calibrate-hint");
  button.disabled = true;
  button.textContent = "识别中…";
  hint.textContent = "正在识别「已预约 / 未开售」按钮并保存负向模板…";
  try {
    const result = await api("/api/android/calibrate-negative", {
      method: "POST",
      body: JSON.stringify(readAndroidConfig()),
    });
    fillAndroidConfig(result.config);
    const name = String(result.template_path || "").split(/[\\/]/).pop();
    hint.textContent = `已在「${result.page}」识别到「${result.button}」，负向模板 ${name} 已保存；开售后命中该模板会继续等待，不会误点。`;
    toast(`已保存负向模板 ${name}`);
  } catch (error) {
    hint.textContent = error.message;
    toast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "采集「已预约」模板";
  }
}
$("#android-calibrate-negative").addEventListener("click", calibrateNegativeTemplate);

$("#config-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const config = readConfig();
    validateConfig(config, "pc");
    const saved = await api("/api/config", { method: "PUT", body: JSON.stringify(config) });
    fillConfig(saved);
    $("#save-state").textContent = "配置已保存";
    toast("PC 演出与观演人配置已保存");
  } catch (error) {
    toast(error.message, true);
  }
});

$("#android-config-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const config = readAndroidConfig();
    validateConfig(config, "android");
    const saved = await api("/api/android/config", { method: "PUT", body: JSON.stringify(config) });
    fillAndroidConfig(saved);
    $("#android-save-state").textContent = "安卓配置已保存";
    toast("安卓设备与预约数量已保存");
  } catch (error) {
    toast(error.message, true);
  }
});

$("#android-run-now").addEventListener("click", async () => {
  const button = $("#android-run-now");
  const label = button.textContent;
  button.disabled = true;
  button.textContent = "启动中…";
  try {
    const config = readAndroidConfig();
    validateConfig(config, "android", { skipSaleTime: true });
    await api("/api/android/config", { method: "PUT", body: JSON.stringify(config) });
    fillAndroidConfig(config);
    const status = await api("/api/android/start", {
      method: "POST",
      body: JSON.stringify({ ...config, run_now: true }),
    });
    updateStatus(status);
    toast("立即演练已启动：跳过倒计时，到确认页停止，不会提交");
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = activePhases.has(currentStatus.phase);
    button.textContent = label;
  }
});

$("#android-dry-run").addEventListener("click", async () => {
  const button = $("#android-dry-run");
  const label = button.textContent;
  button.disabled = true;
  button.textContent = "启动演练…";
  try {
    const config = readAndroidConfig();
    validateConfig(config, "android");
    await api("/api/android/config", { method: "PUT", body: JSON.stringify(config) });
    fillAndroidConfig(config);
    const status = await api("/api/android/start", {
      method: "POST",
      body: JSON.stringify({ ...config, dry_run: true }),
    });
    updateStatus(status);
    toast("演练已启动：到确认页即停止，不会提交订单");
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = activePhases.has(currentStatus.phase);
    button.textContent = label;
  }
});

document.querySelectorAll("[data-platform]").forEach((button) => {
  button.addEventListener("click", () => setPlatform(button.dataset.platform));
});

$("#start-button").addEventListener("click", async () => {
  try {
    const platform = currentPlatform;
    const config = platform === "android" ? readAndroidConfig() : readConfig();
    validateConfig(config, platform);
    if (config.if_commit_order) {
      const dialog = $("#commit-dialog");
      dialog.returnValue = "";
      dialog.showModal();
      const confirmed = await new Promise((resolve) => {
        dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), { once: true });
      });
      if (!confirmed) return;
    }
    $("#start-button").disabled = true;
    const path = platform === "android" ? "/api/android/start" : "/api/start";
    const status = await api(path, { method: "POST", body: JSON.stringify(config) });
    updateStatus(status);
    toast(platform === "android" ? "安卓抢票进程已启动" : "PC 抢票进程已启动");
  } catch (error) {
    toast(error.message, true);
    $("#start-button").disabled = false;
  }
});

$("#stop-button").addEventListener("click", async () => {
  try {
    updateStatus(await api("/api/stop", { method: "POST" }));
    toast("停止指令已执行");
  } catch (error) {
    toast(error.message, true);
  }
});

$("#check-button").addEventListener("click", async () => {
  if (currentPlatform === "pc") {
    try {
      updateStatus(await api("/api/check", { method: "POST" }));
    } catch (error) {
      toast(error.message, true);
    }
    return;
  }
  const button = $("#check-button");
  button.disabled = true;
  button.textContent = "正在检查…";
  try {
    const config = readAndroidConfig();
    validateConfig(config, "android");
    await api("/api/android/config", { method: "PUT", body: JSON.stringify(config) });
    fillAndroidConfig(config);
    const report = await api("/api/android/check", { method: "POST" });
    renderAndroidChecks(report);
    toast(report.ok ? "安卓环境检查全部通过" : "安卓环境检查未通过，请查看检查项", !report.ok);
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = activePhases.has(currentStatus.phase);
    button.textContent = "检查安卓环境";
  }
});

$("#login-button").addEventListener("click", async () => {
  try {
    updateStatus(await api("/api/login", { method: "POST" }));
    toast("已打开 Chrome，请完成扫码登录");
  } catch (error) {
    toast(error.message, true);
  }
});

$("#login-complete-button").addEventListener("click", async () => {
  try {
    updateStatus(await api("/api/login/complete", { method: "POST" }));
  } catch (error) {
    toast(error.message, true);
  }
});

$("#clear-logs").addEventListener("click", () => replaceLogs([]));
$("#help-button").addEventListener("click", () => $("#help-dialog").showModal());
$("#help-close").addEventListener("click", () => $("#help-dialog").close());
$("#help-done").addEventListener("click", () => $("#help-dialog").close());

function connectEvents() {
  const events = new EventSource("/api/events");
  events.addEventListener("open", () => {
    $("#event-connection").textContent = "日志实时连接";
    $("#event-connection").classList.remove("offline");
  });
  events.addEventListener("initial", (event) => {
    const data = JSON.parse(event.data);
    updateStatus(data.status);
    replaceLogs(data.logs);
  });
  events.addEventListener("status", (event) => updateStatus(JSON.parse(event.data)));
  events.addEventListener("log", (event) => appendLog(JSON.parse(event.data)));
  events.addEventListener("error", () => {
    $("#event-connection").textContent = "连接中断，正在重连";
    $("#event-connection").classList.add("offline");
  });
}

async function initializeAndroid() {
  try {
    fillAndroidConfig(await api("/api/android/config"));
    if (managedAppium) $("#android-save-state").textContent = "配置保存在当前用户的应用数据目录";
  } catch (error) {
    $("#android-save-state").textContent = `安卓配置加载失败：${error.message}`;
  }
  await refreshAndroidDevices();
}

async function initialize() {
  try {
    const [status, logs] = await Promise.all([api("/api/status"), api("/api/logs")]);
    updateStatus(status);
    if ((status.enabled_platforms || ["pc"]).includes("pc")) fillConfig(await api("/api/config"));
    replaceLogs(logs.logs);
    await initializeAndroid();
  } catch (error) {
    toast(`初始化失败：${error.message}`, true);
    updateStatus({ phase: "error", message: error.message });
  }
  connectEvents();
}

setPlatform("pc", true);
initialize();
