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
    city: $("#android-city").value.trim(),
    ticket_area: $("#android-ticket-area").value.trim(),
    price_strategy: "highest_available",
    users: splitValues($("#android-users").value),
    if_commit_order: $("#android-if-commit").checked,
  };
}

function fillAndroidConfig(config) {
  currentAndroidConfig = { ...config };
  $("#android-server-url").value = config.server_url || "http://127.0.0.1:4723";
  $("#android-device-name").value = config.device_name || "";
  $("#android-platform-version").value = config.platform_version || "";
  $("#android-city").value = config.city || "";
  $("#android-ticket-area").value = config.ticket_area || "内场";
  $("#android-users").value = (config.users || []).join("，");
  $("#android-if-commit").checked = Boolean(config.if_commit_order);
}

function validateConfig(config, platform) {
  if (!config.users.length) throw new Error("请至少填写一位观演人");
  if (platform === "pc") {
    if (!config.target_url) throw new Error("请先填写演出详情页链接");
    return;
  }
  if (!config.server_url) throw new Error("请填写 Appium 服务地址");
  if (!config.device_name) throw new Error("请填写 adb devices 显示的设备名");
  if (!config.platform_version) throw new Error("请填写 Android 版本");
  if (!config.city) throw new Error("请填写城市");
  if (!config.ticket_area) throw new Error("请填写票档区域，例如内场");
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
  for (const selector of ["#login-button", "#check-button", "#android-prepare-tickets"]) $(selector).disabled = active;
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

$("#android-prepare-tickets").addEventListener("click", async () => {
  const button = $("#android-prepare-tickets");
  const hint = $("#android-device-hint");
  button.disabled = true;
  button.textContent = "正在识别票档…";
  hint.textContent = "请保持手机停在票档页面，识别过程不会点击购买或提交订单。";
  try {
    const config = readAndroidConfig();
    validateConfig(config, "android");
    const saved = await api("/api/android/config", { method: "PUT", body: JSON.stringify(config) });
    fillAndroidConfig(saved);
    const result = await api("/api/android/tickets/prepare", {
      method: "POST",
      body: JSON.stringify(saved),
    });
    const prices = result.candidates.map((candidate) => `${candidate.price}元`).join(" > ");
    hint.textContent = `已缓存${result.ticket_area}候选：${prices}。开售后将按此顺序选择可购买票档。`;
    toast("票档识别完成，候选顺序已缓存");
  } catch (error) {
    hint.textContent = `票档识别失败：${error.message}`;
    toast(error.message, true);
  } finally {
    button.disabled = activePhases.has(currentStatus.phase);
    button.textContent = "识别并缓存票档";
  }
});

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
    toast("安卓设备与观演人配置已保存");
  } catch (error) {
    toast(error.message, true);
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
