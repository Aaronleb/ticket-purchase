#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""大麦抢票助手 —— 本机图形界面启动器。

依赖：Python 3.9+、tkinter（系统自带 / conda 自带）、selenium（本仓库 damai 侧需要）。
用途：把 damai/ 和 damai_appium/ 的脚本包装成可双击的 mac 应用入口：
      - 表单化编辑 config.json / config.jsonc
      - 一键环境自检、启动、停止
      - 实时日志

用法：
    python3 tools/damai_app.py            # 直接运行
    python3 tools/damai_app.py --selftest # 只构建界面并退出（用于自动化验证）
"""

from __future__ import annotations

import json
import os
import plistlib
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

sys.path.insert(0, str(Path(__file__).resolve().parent))

import account_store  # noqa: E402
import ui_kit  # noqa: E402
from ui_kit import (  # noqa: E402
    Avatar,
    Badge,
    Card,
    Chip,
    Dot,
    IconButton,
    PillButton,
    Segmented,
    Toggle,
    divider,
    font as ui_font,
    mono as ui_mono,
    rounded,
)
import run_manager  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA_HOME = Path(os.environ.get("DAMAI_DATA") or (Path.home() / "Library" / "Application Support" / "DamaiHelper"))
DAMAI_DIR = ROOT / "damai"
APPIUM_DIR = ROOT / "damai_appium"
DRIVER_CACHE = DATA_HOME / "drivers" / "cache"
LOG_DIR = DATA_HOME / "logs"
DRIVER_LINK = DATA_HOME / "drivers" / "chromedriver"
CONFIG_STORE = DATA_HOME / "config.json"

FONT = "PingFang SC"
MONO = "Menlo"

BG = ui_kit.BG
WHITE = ui_kit.SURFACE
SURFACE_2 = ui_kit.SURFACE_2
INK = ui_kit.INK
INK2 = ui_kit.INK2
INK3 = ui_kit.INK3
LINE = ui_kit.LINE
LINE_SOFT = ui_kit.LINE_SOFT
BRAND = ui_kit.BRAND
BRAND_DARK = ui_kit.BRAND_HOVER
BRAND_SOFT = ui_kit.BRAND_SOFT
BRAND_LINE = ui_kit.BRAND_LINE
BLUE = "#1d4ed8"
GREEN = ui_kit.OK
OK = ui_kit.OK
WARN = ui_kit.WARN
DARK = ui_kit.DARK

LOG_BG = "#0f172a"
LOG_FG = "#dbe3ee"

TAG_COLORS = {
    "info": "#aeb9c9",
    "ok": "#7ee787",
    "warn": "#e3b341",
    "err": "#ff7b72",
    "cmd": "#7cb7ff",
    "time": "#56617a",
}

TITLE = "大麦抢票助手"
VERSION = "0.3.0"


# --------------------------------------------------------------------------- #
# 环境探测
# --------------------------------------------------------------------------- #
def chrome_binary() -> str | None:
    for candidate in (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        shutil.which("google-chrome") or "",
        shutil.which("chromium") or "",
    ):
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def chrome_version() -> tuple[str | None, str | None]:
    """取 Chrome 版本。优先读 Info.plist（无子进程，毫秒级），失败再退回 --version。"""
    exe = chrome_binary()
    if not exe:
        return None, None

    candidates = []
    if ".app/Contents/MacOS/" in exe:
        candidates.append(str(Path(exe).parents[2] / "Info.plist"))
    candidates.append("/Applications/Google Chrome.app/Contents/Info.plist")
    for plist in candidates:
        try:
            with open(plist, "rb") as fh:
                version = plistlib.load(fh).get("CFBundleShortVersionString")
            if version:
                return str(version), str(version).split(".")[0]
        except Exception:
            continue

    try:
        out = subprocess.run(
            [exe, "--version"], capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except Exception:
        return None, None
    match = re.search(r"(\d+)\.", out)
    return out.replace("Google Chrome ", "").strip(), (match.group(1) if match else None)


def driver_major(path: Path) -> str | None:
    try:
        out = subprocess.run([str(path), "--version"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return None
    match = re.search(r"ChromeDriver\s+(\d+)\.", out or "")
    return match.group(1) if match else None


def ensure_driver(log) -> tuple[bool, str]:
    """保证 damai/chromedriver 与当前 Chrome 主版本一致；否则用 Selenium Manager 取一份。"""
    os.environ.setdefault("SE_CACHE_PATH", str(DRIVER_CACHE))
    version, major = chrome_version()
    if not major:
        return False, "未检测到 Google Chrome，请先安装"

    candidates = [DRIVER_LINK, ROOT / "damai" / "chromedriver"]
    which = shutil.which("chromedriver")
    if which:
        candidates.append(Path(which))
    for candidate in candidates:
        if not (candidate.exists() or candidate.is_symlink()):
            continue
        dmaj = driver_major(candidate)
        if dmaj != major:
            continue
        if candidate != DRIVER_LINK:
            try:
                DRIVER_LINK.parent.mkdir(parents=True, exist_ok=True)
                if DRIVER_LINK.is_symlink() or DRIVER_LINK.exists():
                    DRIVER_LINK.unlink()
                DRIVER_LINK.symlink_to(candidate.resolve())
                log(f"复用已有 driver：{candidate} → {DRIVER_LINK}", "ok")
            except OSError as exc:
                return False, f"创建 driver 软链失败：{exc}"
        else:
            log(f"driver 就绪：{DRIVER_LINK}（主版本 {dmaj} 与 Chrome 一致）", "ok")
        return True, "ok"

    log(f"未找到匹配的驱动，正在下载 chromedriver（首次约 1 分钟，之后走缓存）…", "warn")
    DRIVER_CACHE.mkdir(parents=True, exist_ok=True)
    DRIVER_LINK.parent.mkdir(parents=True, exist_ok=True)
    os.environ["SE_CACHE_PATH"] = str(DRIVER_CACHE)
    try:
        from selenium import webdriver
    except ImportError:
        return False, "缺少 selenium，请先执行：pip install 'selenium>=4.18.1'"

    options = webdriver.ChromeOptions()
    for arg in ("--headless=new", "--no-sandbox", "--disable-gpu", "--disable-extensions"):
        options.add_argument(arg)
    try:
        drv = webdriver.Chrome(options=options)
        path = drv.service.path
        drv.quit()
    except Exception as exc:  # noqa: BLE001
        return False, f"Selenium Manager 解析失败：{exc}"

    if not path or not os.path.exists(path):
        return False, "Selenium Manager 未返回可用的 chromedriver"

    try:
        if DRIVER_LINK.is_symlink() or DRIVER_LINK.exists():
            DRIVER_LINK.unlink()
        DRIVER_LINK.symlink_to(path)
    except Exception as exc:  # noqa: BLE001
        return False, f"创建 driver 软链失败：{exc}"

    log(f"已软链 {DRIVER_LINK.name} -> {path}", "ok")
    return True, "ok"


def probe_android() -> dict:
    info = {"appium": shutil.which("appium"), "adb": shutil.which("adb"), "devices": [], "package": False}
    if info["adb"]:
        try:
            out = subprocess.run(
                [info["adb"], "devices"], capture_output=True, text=True, timeout=10
            ).stdout
            info["devices"] = [l.split("\t")[0] for l in out.splitlines()[1:] if l.strip().endswith("device")]
        except Exception:
            pass
        try:
            out = subprocess.run(
                [info["adb"], "shell", "pm", "list", "packages", "cn.damai"],
                capture_output=True, text=True, timeout=10,
            ).stdout
            info["package"] = "cn.damai" in out
        except Exception:
            pass
    return info


# --------------------------------------------------------------------------- #
# 子进程管理
# --------------------------------------------------------------------------- #
class Runner:
    def __init__(self, app: "DamaiApp"):
        self.app = app
        self.proc: subprocess.Popen | None = None

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, cmd: list[str], cwd: Path, extra_env: dict | None = None) -> None:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["LANG"] = env.get("LANG") or "zh_CN.UTF-8"
        if extra_env:
            env.update(extra_env)
        self.app.log("$ " + " ".join(cmd), "cmd")
        self.proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
            env=env,
        )
        threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()

    def _pump(self, proc: subprocess.Popen) -> None:
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                self.app.push_log(line.rstrip("\n"))
        finally:
            code = proc.wait()
            self.app.on_proc_exit(code)

    def stop(self) -> bool:
        if not self.running():
            return False
        try:
            os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
        except ProcessLookupError:
            return False
        try:
            self.proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
        return True


# --------------------------------------------------------------------------- #
# 界面
# --------------------------------------------------------------------------- #
class DamaiApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{TITLE} v{VERSION}")
        self.configure(bg=BG)
        self.minsize(1040, 760)
        self._center(1180, 840)

        os.environ.setdefault("SE_CACHE_PATH", str(DRIVER_CACHE))
        self.runner = Runner(self)
        self.multi = run_manager.MultiRunner(self.on_multi_line, self.on_multi_exit)
        self.log_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.ui_queue: queue.Queue = queue.Queue()
        self.busy = False
        self.mode = "idle"                 # idle / checking / logging_in / preparing / grabbing
        self.login_account_id = ""
        self.login_signal: Path | None = None
        self.accounts: list[dict] = []
        self.android_ready = False
        self._pc_cfg: dict = {}
        self._android_cfg: dict = {}
        self.known_dates: list[str] = []
        self.known_prices: list[str] = []

        self._prepare_data_home()
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.log_path = LOG_DIR / time.strftime("app-%Y%m%d.log")

        self._init_style()
        self._build()
        self._load_configs()
        self._sync_buttons()
        self.after(120, self._drain_log)
        self.after(80, self._startup_probe)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.log(f"{TITLE} v{VERSION} 已启动", "ok")
        self.log(f"工程目录：{self._short_path(ROOT)}", "info")
        self.log("数据目录：~/Library/Application Support/DamaiHelper", "info")
        self.log("用法：③ 账号库里添加账号 → 点「登录」扫码 → 勾观演人 → 开始抢票（可多账号同时抢）", "info")

    @staticmethod
    def _short_path(path, keep: int = 2) -> str:
        parts = Path(path).parts
        return "…/" + "/".join(parts[-keep:]) if len(parts) > keep else str(path)

    def _prepare_data_home(self) -> None:
        """把仓库里的旧数据（账号库 / 基准配置）迁到数据目录，只做一次。"""
        import shutil

        DATA_HOME.mkdir(parents=True, exist_ok=True)
        legacy_data = ROOT / ".data"
        if not (DATA_HOME / "accounts.json").exists() and (legacy_data / "accounts.json").exists():
            try:
                shutil.copytree(legacy_data, DATA_HOME, dirs_exist_ok=True)
            except Exception:
                pass
        if not CONFIG_STORE.exists():
            legacy_cfg = DAMAI_DIR / "config.json"
            if legacy_cfg.exists():
                try:
                    shutil.copy2(legacy_cfg, CONFIG_STORE)
                except Exception:
                    pass

    def _on_close(self) -> None:
        """关窗前确认：有进程在跑就先停掉，避免留下孤儿进程。"""
        if self.runner.running() or self.multi.running():
            from tkinter import messagebox

            if not messagebox.askokcancel("退出应用", "还有进程在运行，退出会一起停止。继续？"):
                return
            self.multi.stop()
            self.runner.stop()
        self.destroy()
    def _center(self, w: int, h: int) -> None:
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{max(0, (sh - h) // 2 - 20)}")

    def _init_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Field.TEntry",
            fieldbackground=WHITE,
            foreground=INK,
            bordercolor=LINE,
            lightcolor=WHITE,
            darkcolor=WHITE,
            padding=(10, 8),
            relief="flat",
            insertcolor=INK,
        )
        style.map("Field.TEntry", bordercolor=[("focus", BRAND)])
        style.configure(
            "Field.TCombobox",
            fieldbackground=WHITE,
            background=WHITE,
            foreground=INK,
            bordercolor=LINE,
            arrowcolor=INK2,
            arrowsize=13,
            padding=(10, 7),
        )
        style.map(
            "Field.TCombobox",
            bordercolor=[("focus", BRAND)],
            fieldbackground=[("readonly", WHITE)],
            selectbackground=[("readonly", WHITE)],
            selectforeground=[("readonly", INK)],
        )
        style.configure(
            "Slim.Vertical.TScrollbar",
            gripcount=0,
            background="#d7dce5",
            darkcolor=WHITE,
            lightcolor=WHITE,
            troughcolor=WHITE,
            bordercolor=WHITE,
            arrowcolor=WHITE,
            arrowsize=0,
            relief="flat",
            width=8,
        )
        style.map("Slim.Vertical.TScrollbar", background=[("active", "#c2c9d6")])
        style.configure(
            "Dark.Vertical.TScrollbar",
            gripcount=0,
            background="#2a3346",
            darkcolor=DARK,
            lightcolor=DARK,
            troughcolor=DARK,
            bordercolor=DARK,
            arrowcolor=DARK,
            arrowsize=0,
            relief="flat",
            width=8,
        )
        style.map("Dark.Vertical.TScrollbar", background=[("active", "#3a465e")])
        style.configure(
            "Dark.Horizontal.TScrollbar",
            gripcount=0,
            background="#2a3346",
            darkcolor=DARK,
            lightcolor=DARK,
            troughcolor=DARK,
            bordercolor=DARK,
            arrowcolor=DARK,
            arrowsize=0,
            relief="flat",
            width=8,
        )
    def _build(self) -> None:
        self._build_header()
        self._build_status()

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=22, pady=(16, 12))
        body.columnconfigure(0, weight=57, uniform="col")
        body.columnconfigure(1, weight=43, uniform="col")
        body.rowconfigure(0, weight=1)

        left = tk.Frame(body, bg=BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        self.left_card = Card(left, padding=20, radius=16)
        self.left_card.pack(fill="both", expand=True)
        lb = self.left_card.body

        seg_row = tk.Frame(lb, bg=WHITE)
        seg_row.pack(fill="x")
        self.seg = Segmented(seg_row, ["PC 端", "Android 端"], self._on_tab, height=34)
        self.seg.pack(side="left")
        self.tab_hint = tk.Label(seg_row, text="Selenium · Chrome", bg=WHITE, fg=INK3, font=ui_font(10))
        self.tab_hint.pack(side="left", padx=(12, 0))

        self.tab_holder = tk.Frame(lb, bg=WHITE)
        self.tab_holder.pack(fill="both", expand=True, pady=(12, 0))
        self.pc_tab = tk.Frame(self.tab_holder, bg=WHITE)
        self.android_tab = tk.Frame(self.tab_holder, bg=WHITE)
        self.pc_tab.pack(fill="both", expand=True)

        self._build_pc_tab()
        self._build_android_tab()

        right = tk.Frame(body, bg=BG)
        right.grid(row=0, column=1, sticky="nsew")
        self._build_log(right)

    def _on_tab(self, index: int) -> None:
        if index == 0:
            self.android_tab.pack_forget()
            self.pc_tab.pack(fill="both", expand=True)
            self.tab_hint.configure(text="Selenium · Chrome")
        else:
            self.pc_tab.pack_forget()
            self.android_tab.pack(fill="both", expand=True)
            self.tab_hint.configure(text="Appium · Android")
    def _build_header(self) -> None:
        header = tk.Frame(self, bg=WHITE, height=88)
        header.pack(fill="x")
        header.pack_propagate(False)

        left = tk.Frame(header, bg=WHITE)
        left.pack(side="left", padx=24, pady=20)
        mark = tk.Canvas(left, width=44, height=44, bg=WHITE, highlightthickness=0)
        mark.pack(side="left")
        rounded(mark, 1, 1, 43, 43, 13, fill=BRAND, outline=BRAND)
        mark.create_text(22, 22.5, text="抢", fill="#ffffff", font=ui_font(19, True))

        titles = tk.Frame(left, bg=WHITE)
        titles.pack(side="left", padx=(12, 0))
        tk.Label(titles, text=TITLE, bg=WHITE, fg=INK, font=ui_font(19, True)).pack(anchor="w")
        tk.Label(titles, text="多账号抢票 · 本地运行", bg=WHITE, fg=INK3, font=ui_font(11)).pack(
            anchor="w", pady=(1, 0)
        )

        right = tk.Frame(header, bg=WHITE)
        right.pack(side="right", padx=22)
        PillButton(right, "环境自检", self.action_selfcheck, kind="ghost", height=36, size=11).pack(
            side="right", padx=(8, 0)
        )
        PillButton(right, "数据目录", self.action_open_dir, kind="subtle", height=36, size=11).pack(side="right")

        tk.Frame(self, bg=LINE, height=1).pack(fill="x")
    def _field_label(self, parent, text: str, hint: str = ""):
        row = tk.Frame(parent, bg=WHITE)
        tk.Label(row, text=text, bg=WHITE, fg=INK2, font=(FONT, 11)).pack(side="left")
        if hint:
            tk.Label(row, text=hint, bg=WHITE, fg=INK3, font=(FONT, 10)).pack(side="left", padx=(6, 0))
        return row

    def _text_area(self, parent, height=4) -> tk.Text:
        wrap = tk.Frame(parent, bg=WHITE, highlightthickness=1, highlightbackground=LINE)
        area = tk.Text(
            wrap,
            height=height,
            width=30,
            bg=WHITE,
            fg=INK,
            font=ui_font(11),
            relief="flat",
            padx=8,
            pady=6,
            insertbackground=INK,
            highlightthickness=0,
            wrap="word",
        )
        bar = ttk.Scrollbar(wrap, orient="vertical", command=area.yview, style="Slim.Vertical.TScrollbar")
        area.configure(yscrollcommand=bar.set)
        area.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        wrap.pack(fill="x", pady=(4, 0))
        return area

    # ---------------- 简化界面的小部件 ---------------- #
    def _step_header(self, parent, num: str, title: str, hint: str = "", top: int = 16) -> tk.Frame:
        row = tk.Frame(parent, bg=WHITE)
        row.pack(fill="x", pady=(top - 4 if top > 4 else 0, 0))
        badge = tk.Canvas(row, width=22, height=22, bg=WHITE, highlightthickness=0)
        badge.pack(side="left")
        badge.create_oval(0.5, 0.5, 21.5, 21.5, fill=BRAND_SOFT, outline=BRAND_LINE)
        badge.create_text(11, 11.5, text=num, fill=BRAND, font=ui_font(11, True))
        tk.Label(row, text=title, bg=WHITE, fg=INK, font=ui_font(13, True)).pack(side="left", padx=(8, 0))
        if hint:
            tk.Label(row, text=hint, bg=WHITE, fg=INK3, font=ui_font(10)).pack(side="left", padx=(8, 0))
        return row
    def _collapsible(self, parent, title: str) -> tk.Frame:
        wrap = tk.Frame(parent, bg=WHITE)
        wrap.pack(fill="x", pady=(10, 0))
        head = tk.Frame(wrap, bg=WHITE)
        head.pack(fill="x")
        chevron = tk.Label(head, text="▸", bg=WHITE, fg=INK3, font=ui_font(11), cursor="pointinghand")
        chevron.pack(side="left")
        label = tk.Label(head, text=title, bg=WHITE, fg=INK2, font=ui_font(11), cursor="pointinghand")
        label.pack(side="left", padx=(6, 0))
        body = tk.Frame(wrap, bg=WHITE)

        def toggle(_event=None):
            if body.winfo_ismapped():
                body.pack_forget()
                chevron.configure(text="▸")
            else:
                body.pack(fill="x", pady=(10, 0))
                chevron.configure(text="▾")

        for widget in (chevron, label):
            widget.bind("<Button-1>", toggle)
        return body
    def _find_account(self, acc_id: str) -> dict | None:
        return next((a for a in self.accounts if a["id"] == acc_id), None)

    def _persist_accounts(self) -> None:
        account_store.save(self.accounts)

    def _account_color(self, name: str) -> str:
        palette = ui_kit.ACCOUNT_COLORS
        return palette[sum(ord(c) for c in (name or "?")) % len(palette)]

    def _render_accounts(self) -> None:
        for child in self.accounts_inner.winfo_children():
            child.destroy()
        if not self.accounts:
            box = tk.Frame(self.accounts_inner, bg=WHITE)
            box.pack(fill="x", padx=2, pady=14)
            tk.Label(box, text="还没有账号", bg=WHITE, fg=INK, font=ui_font(12, True)).pack(anchor="w")
            tk.Label(
                box,
                text="点右上角「添加账号」，再点「登录」扫码。\n每个账号可以带自己的观演人，开抢时一起并发。",
                bg=WHITE,
                fg=INK3,
                font=ui_font(10),
                justify="left",
            ).pack(anchor="w", pady=(4, 0))
            self._update_summary()
            return
        for account in self.accounts:
            self._render_account_card(account)
        self._update_summary()

    def _render_account_card(self, account: dict) -> None:
        acc_id = account["id"]
        logged = account_store.has_cookie(account)
        logging_this = self.mode == "logging_in" and self.login_account_id == acc_id

        row = tk.Frame(self.accounts_inner, bg=WHITE)
        row.pack(fill="x", pady=(2, 8))

        top = tk.Frame(row, bg=WHITE)
        top.pack(fill="x")
        Avatar(top, account["name"], 38, self._account_color(account["name"])).pack(side="left")

        info = tk.Frame(top, bg=WHITE)
        info.pack(side="left", padx=(10, 0), fill="x", expand=True)
        tk.Label(info, text=account["name"], bg=WHITE, fg=INK, font=ui_font(12, True)).pack(anchor="w")
        stat = tk.Frame(info, bg=WHITE)
        stat.pack(anchor="w", pady=(1, 0))
        Dot(stat, OK if logged else "#c6ccd6", 7).pack(side="left", pady=(1, 0))
        text = ("已登录 · " + (account.get("last_login") or "")[-5:]) if logged else "未登录"
        tk.Label(stat, text=text, bg=WHITE, fg=OK if logged else INK3, font=ui_font(10)).pack(
            side="left", padx=(6, 0)
        )

        right = tk.Frame(top, bg=WHITE)
        right.pack(side="right")
        IconButton(right, "✕", lambda: self.action_remove_account(acc_id), size=30, kind="danger").pack(
            side="right", padx=(8, 0)
        )
        if logging_this:
            PillButton(
                right, "完成登录", self.action_finish_login, kind="primary", height=32, size=11, padding=14
            ).pack(side="right")
        else:
            PillButton(
                right,
                "重新登录" if logged else "登录",
                lambda: self.action_login_account(acc_id),
                kind="ghost",
                height=32,
                size=11,
                padding=16,
            ).pack(side="right")
        Toggle(
            right,
            account.get("enabled", True),
            lambda value, a=acc_id: self._toggle_account_enabled(a, value),
        ).pack(side="right", padx=(0, 10))

        chips = tk.Frame(row, bg=WHITE)
        chips.pack(fill="x", padx=(44, 0), pady=(6, 0))
        users = account.get("users") or []
        selected = account.get("selected") or []
        if not users:
            tk.Label(chips, text="暂无观演人", bg=WHITE, fg=INK3, font=ui_font(10)).pack(side="left")
        used_width, col, grid_row = 0, 0, 0
        for name in users[:10]:
            picked = name in selected
            chip = Chip(
                chips,
                name,
                selected=picked,
                height=26,
                size=10,
                command=lambda n=name, a=acc_id: self._toggle_account_user(a, n),
            )
            width = chip.winfo_reqwidth()
            if col and used_width + width > 520:
                grid_row += 1
                col = 0
                used_width = 0
            chip.grid(row=grid_row, column=col, sticky="w", padx=(0, 6), pady=3)
            used_width += width + 6
            col += 1

        add = tk.Frame(row, bg=WHITE)
        add.pack(fill="x", padx=(44, 0), pady=(4, 0))
        tk.Label(add, text="姓名", bg=WHITE, fg=INK3, font=ui_font(10)).pack(side="left", padx=(0, 6))
        var = tk.StringVar()
        entry = ttk.Entry(add, textvariable=var, width=9, style="Field.TEntry")
        entry.pack(side="left")
        entry.bind("<Return>", lambda _e, v=var, a=acc_id: self._add_account_user(a, v))
        PillButton(
            add,
            "添加观演人",
            lambda a=acc_id, v=var: self._add_account_user(a, v),
            kind="subtle",
            height=30,
            size=10,
            padding=12,
        ).pack(side="left", padx=(6, 0))

        tk.Frame(row, bg=LINE_SOFT, height=1).pack(fill="x", pady=(10, 0), padx=(44, 0))

    def _toggle_account_enabled(self, acc_id: str, value: bool) -> None:
        account = self._find_account(acc_id)
        if account:
            account["enabled"] = bool(value)
            self._persist_accounts()
            self._update_summary()
            self._sync_buttons()

    def _toggle_account_user(self, acc_id: str, name: str) -> None:
        account = self._find_account(acc_id)
        if not account:
            return
        selected = account.setdefault("selected", [])
        if name in selected:
            selected.remove(name)
        else:
            selected.append(name)
        self._persist_accounts()
        self._render_accounts()
        self._sync_buttons()

    def _add_account_user(self, acc_id: str, var) -> None:
        name = (var.get() or "").strip()[:20]
        if not name:
            return
        account = self._find_account(acc_id)
        if not account:
            return
        users = account.setdefault("users", [])
        if name not in users:
            users.append(name)
        selected = account.setdefault("selected", [])
        if name not in selected:
            selected.append(name)
        var.set("")
        self._persist_accounts()
        self._render_accounts()
        self._sync_buttons()

    def action_add_account(self) -> None:
        name = self.var_new_account.get().strip()
        if not name:
            self.log("先在输入框里填账号名称（如「主号」），再点添加", "warn")
            return
        account_store.add(name)
        self.accounts = account_store.load()
        self.var_new_account.set("")
        self._render_accounts()
        self._sync_buttons()
        self.log(f"已添加账号「{name}」，点它的「登录」扫码", "ok")

    def action_remove_account(self, acc_id: str) -> None:
        account = self._find_account(acc_id)
        if not account:
            return
        from tkinter import messagebox

        if not messagebox.askokcancel("删除账号", f"删除「{account['name']}」及其本地登录态？"):
            return
        account_store.remove(acc_id)
        self.accounts = account_store.load()
        self._render_accounts()
        self._sync_buttons()
        self.log(f"已删除账号「{account['name']}」", "warn")

    def _update_summary(self) -> None:
        enabled = [a for a in self.accounts if a.get("enabled") and a.get("selected")]
        tickets = sum(len(a["selected"]) for a in enabled)
        logged = sum(1 for a in enabled if account_store.has_cookie(a))
        if not self.accounts:
            text = "还没有账号"
        elif not enabled:
            text = f"{len(self.accounts)} 个账号 · 勾选观演人后可开抢"
        else:
            text = f"{len(enabled)} 个账号 · {tickets} 张票 · {logged} 个已登录"
        self.lbl_summary.configure(text=text, fg=BRAND if enabled else INK3)

    # ---------------- 登录 ----------------
    def action_login_account(self, acc_id: str) -> None:
        if self.mode != "idle" or self.runner.running() or self.multi.running():
            self.log("有任务在跑，先停止再登录", "warn")
            return
        account = self._find_account(acc_id)
        if not account:
            return

        self.mode = "logging_in"
        self.login_account_id = acc_id
        self.login_signal = account_store.account_dir(account) / "login.done"
        if self.login_signal.exists():
            self.login_signal.unlink()
        self.var_state.set(f"登录中 · {account['name']}")
        self.state_label.configure(fg=BRAND)
        self._render_accounts()
        self._sync_buttons()
        self.log(f"▶ 登录「{account['name']}」：浏览器打开后扫码，扫完点卡片上的「完成登录」", "cmd")

        def work() -> None:
            try:
                ok, msg = ensure_driver(self.push_log)
            except Exception as exc:  # noqa: BLE001
                ok, msg = False, str(exc)
            if not ok:
                self.push_log(f"登录失败：{msg}", "err")
                self.call_soon(self._login_failed)
                return
            script = Path(__file__).parent / "account_login.py"
            cmd = [
                sys.executable,
                str(script),
                "--cookie",
                str(account_store.cookie_path(account)),
                "--signal",
                str(self.login_signal),
                "--driver",
                str(DRIVER_LINK),
                "--timeout",
                "600",
            ]
            self.call_soon(
                lambda: self.runner.start(cmd, ROOT, {"SE_CACHE_PATH": str(DRIVER_CACHE)})
            )

        threading.Thread(target=work, daemon=True).start()

    def action_finish_login(self) -> None:
        if not self.login_signal:
            return
        try:
            self.login_signal.parent.mkdir(parents=True, exist_ok=True)
            self.login_signal.write_text("done")
            self.log("已通知保存登录态…", "info")
        except OSError as exc:
            self.log(f"通知失败：{exc}", "err")

    def _login_failed(self) -> None:
        self.mode = "idle"
        self.login_account_id = ""
        self.login_signal = None
        self.var_state.set("登录失败")
        self._render_accounts()
        self._sync_buttons()

    # ---------------- 多账号启动 ----------------
    def on_multi_line(self, name: str, text: str) -> None:
        self.push_log(f"[{name}] {text}")

    def on_multi_exit(self, name: str, code: int) -> None:
        self.call_soon(lambda: self._multi_one_exit(name, code))

    def _multi_one_exit(self, name: str, code: int) -> None:
        self.log(f"[{name}] 进程结束，退出码 {code}", "ok" if code == 0 else "warn")
        if not self.multi.running():
            self.mode = "idle"
            self.var_state.set("待机")
            self.state_label.configure(fg=INK3)
            self.log("全部账号已结束", "ok")
            self._sync_buttons()

    # ---------------- 简化后的 PC 标签页 ---------------- #
    # ---------------- 简化后的 PC 标签页 ---------------- #
    def _build_pc_tab(self) -> None:
        p = self.pc_tab

        self._step_header(p, "1", "演出链接", "从大麦详情页复制地址", top=2)
        row = tk.Frame(p, bg=WHITE)
        row.pack(fill="x", pady=(10, 0))
        self.link_row = row
        self.var_target_url = tk.StringVar()
        self.entry_url = ttk.Entry(row, textvariable=self.var_target_url, style="Field.TEntry")
        self.entry_url.pack(side="left", fill="x", expand=True)
        self.btn_probe = PillButton(row, "读取信息", self.action_probe_url, kind="ghost", height=36)
        self.btn_probe.pack(side="left", padx=(10, 0))

        self.card = tk.Frame(p, bg=SURFACE_2)
        tk.Frame(self.card, bg=BRAND, width=3).pack(side="left", fill="y")
        self.var_show = tk.StringVar(value="")
        tk.Label(
            self.card,
            textvariable=self.var_show,
            bg=SURFACE_2,
            fg=INK2,
            font=ui_font(11),
            anchor="w",
            justify="left",
            wraplength=540,
        ).pack(fill="x", padx=14, pady=10)

        tk.Frame(p, bg=LINE_SOFT, height=1).pack(fill="x", pady=(14, 0))

        self._step_header(p, "2", "场次与票档", "自动带出，也可直接改")
        grid = tk.Frame(p, bg=WHITE)
        grid.pack(fill="x", pady=(10, 0))
        grid.columnconfigure(0, weight=1, uniform="half")
        grid.columnconfigure(1, weight=1, uniform="half")
        tk.Label(grid, text="场次", bg=WHITE, fg=INK2, font=ui_font(11)).grid(row=0, column=0, sticky="w")
        tk.Label(grid, text="票档文案", bg=WHITE, fg=INK2, font=ui_font(11)).grid(
            row=0, column=1, sticky="w", padx=(12, 0)
        )
        self.var_date = tk.StringVar()
        self.cb_date = ttk.Combobox(grid, textvariable=self.var_date, values=[], style="Field.TCombobox")
        self.cb_date.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self.var_price = tk.StringVar()
        self.cb_price = ttk.Combobox(grid, textvariable=self.var_price, values=[], style="Field.TCombobox")
        self.cb_price.grid(row=1, column=1, sticky="ew", pady=(6, 0), padx=(12, 0))
        self.cb_date.bind("<<ComboboxSelected>>", lambda _e: self._update_summary())
        self.cb_price.bind("<<ComboboxSelected>>", lambda _e: self._update_summary())
        self.var_price.trace_add("write", lambda *_: self._update_summary())

        tk.Frame(p, bg=LINE_SOFT, height=1).pack(fill="x", pady=(12, 0))

        head = self._step_header(p, "3", "账号库", "多账号可同时抢")
        add_box = tk.Frame(head, bg=WHITE)
        add_box.pack(side="right")
        self.var_new_account = tk.StringVar()
        tk.Label(add_box, text="账号名", bg=WHITE, fg=INK3, font=ui_font(10)).pack(side="left", padx=(0, 6))
        entry = ttk.Entry(add_box, textvariable=self.var_new_account, width=10, style="Field.TEntry")
        entry.pack(side="left")
        entry.bind("<Return>", lambda _e: self.action_add_account())
        PillButton(
            add_box, "添加账号", self.action_add_account, kind="subtle", height=36, size=11, padding=14
        ).pack(side="left", padx=(8, 0))

        wrap = tk.Frame(p, bg=WHITE)
        wrap.pack(fill="both", expand=True, pady=(10, 0))
        self.accounts_wrap = wrap
        self.accounts_canvas = tk.Canvas(wrap, bg=WHITE, highlightthickness=0, bd=0)
        scroll = ttk.Scrollbar(
            wrap, orient="vertical", command=self.accounts_canvas.yview, style="Slim.Vertical.TScrollbar"
        )
        self.accounts_canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y", padx=(8, 0))
        self.accounts_canvas.pack(side="left", fill="both", expand=True)
        self.accounts_inner = tk.Frame(self.accounts_canvas, bg=WHITE)
        self._accounts_window = self.accounts_canvas.create_window((0, 0), window=self.accounts_inner, anchor="nw")
        self.accounts_inner.bind(
            "<Configure>",
            lambda _e: self.accounts_canvas.configure(scrollregion=self.accounts_canvas.bbox("all")),
        )
        self.accounts_canvas.bind(
            "<Configure>",
            lambda e: self.accounts_canvas.itemconfigure(self._accounts_window, width=e.width),
        )
        self.accounts_canvas.bind(
            "<MouseWheel>", lambda e: self.accounts_canvas.yview_scroll(int(-e.delta / 60), "units")
        )

        tk.Label(
            p,
            text="提示：多个账号会同时开浏览器；关掉开关可临时停用某个账号",
            bg=WHITE,
            fg=INK3,
            font=ui_font(10),
        ).pack(anchor="w", pady=(6, 0))

        tk.Frame(p, bg=LINE_SOFT, height=1).pack(fill="x", pady=(10, 0))

        actions = tk.Frame(p, bg=WHITE)
        actions.pack(fill="x", pady=(14, 0))
        self.btn_pc_start = PillButton(
            actions, "开始抢票", self.action_start_pc, kind="primary", height=46, size=14, padding=34, radius=12
        )
        self.btn_pc_start.pack(side="left")
        self.btn_pc_stop = PillButton(
            actions, "停止", self.action_stop, kind="danger", height=46, size=12, padding=22, radius=12
        )
        self.btn_pc_stop.pack(side="left", padx=(10, 0))
        self.btn_pc_save = PillButton(
            actions, "仅保存配置", self.action_save_pc, kind="ghost", height=46, size=11, padding=16, radius=12
        )
        self.btn_pc_save.pack(side="left", padx=(10, 0))
        self.lbl_summary = tk.Label(actions, text="", bg=WHITE, fg=INK3, font=ui_font(11, True))
        self.lbl_summary.pack(side="right")
        self.pc_hint = tk.Label(actions, text="", bg=WHITE, fg=INK3, font=ui_font(10))
        self.pc_hint.pack(side="right", padx=(0, 12))

        adv = self._collapsible(p, "更多设置")
        row1 = tk.Frame(adv, bg=WHITE)
        row1.pack(fill="x")
        self.var_city = tk.StringVar()
        tk.Label(row1, text="城市", bg=WHITE, fg=INK3, font=ui_font(11)).pack(side="left")
        ttk.Entry(row1, textvariable=self.var_city, width=10, style="Field.TEntry").pack(side="left", padx=(6, 20))
        self.var_retries = tk.StringVar(value="10000")
        tk.Label(row1, text="刷新轮数", bg=WHITE, fg=INK3, font=ui_font(11)).pack(side="left")
        ttk.Entry(row1, textvariable=self.var_retries, width=8, style="Field.TEntry").pack(side="left", padx=(6, 0))

        row2 = tk.Frame(adv, bg=WHITE)
        row2.pack(fill="x", pady=(10, 0))
        self.var_fast = tk.BooleanVar(value=True)
        self.var_listen = tk.BooleanVar(value=True)
        self.var_commit = tk.BooleanVar(value=False)
        for text, var in (("快速模式", self.var_fast), ("缺货自动登记", self.var_listen), ("自动提交订单", self.var_commit)):
            cb = tk.Checkbutton(
                row2,
                text=text,
                variable=var,
                bg=WHITE,
                fg=INK2,
                activebackground=WHITE,
                activeforeground=INK,
                font=ui_font(11),
                bd=0,
                highlightthickness=0,
                selectcolor=WHITE,
                padx=0,
            )
            cb.pack(side="left", padx=(0, 16))
    def _build_android_tab(self) -> None:
        p = self.android_tab

        self._step_header(p, "1", "Appium 服务", "默认 127.0.0.1:4723", top=2)
        self.var_server = tk.StringVar(value="http://127.0.0.1:4723")
        ttk.Entry(p, textvariable=self.var_server, style="Field.TEntry").pack(fill="x", pady=(10, 0))

        self._step_header(p, "2", "演出与票档")
        grid = tk.Frame(p, bg=WHITE)
        grid.pack(fill="x", pady=(10, 0))
        for col in range(4):
            grid.columnconfigure(col, weight=1, uniform="and")

        def cell(col: int, label: str, var: tk.StringVar):
            box = tk.Frame(grid, bg=WHITE)
            box.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 10, 0))
            tk.Label(box, text=label, bg=WHITE, fg=INK3, font=ui_font(10)).pack(anchor="w")
            ttk.Entry(box, textvariable=var, style="Field.TEntry").pack(fill="x", pady=(5, 0))

        self.var_keyword = tk.StringVar()
        self.var_and_city = tk.StringVar()
        self.var_and_date = tk.StringVar()
        self.var_and_price = tk.StringVar()
        cell(0, "关键词", self.var_keyword)
        cell(1, "城市", self.var_and_city)
        cell(2, "日期", self.var_and_date)
        cell(3, "票价文案", self.var_and_price)

        row = tk.Frame(p, bg=WHITE)
        row.pack(fill="x", pady=(12, 0))
        tk.Label(row, text="票价序号 price_index", bg=WHITE, fg=INK3, font=ui_font(10)).pack(side="left")
        self.var_price_index = tk.StringVar(value="1")
        ttk.Entry(row, textvariable=self.var_price_index, width=6, style="Field.TEntry").pack(side="left", padx=(8, 0))
        self.var_and_commit = tk.BooleanVar(value=False)
        tk.Checkbutton(
            row,
            text="自动提交订单",
            variable=self.var_and_commit,
            bg=WHITE,
            fg=INK2,
            activebackground=WHITE,
            activeforeground=INK,
            font=ui_font(11),
            selectcolor=WHITE,
            bd=0,
            highlightthickness=0,
        ).pack(side="left", padx=(20, 0))

        self._step_header(p, "3", "观演人", "每行一个，需与 APP 中一致")
        self.txt_and_users = self._text_area(p, height=3)

        self.android_env = tk.Frame(p, bg=WHITE)
        self.android_env.pack(fill="x", pady=(14, 0))
        self._render_android_env(None)

        actions = tk.Frame(p, bg=WHITE)
        actions.pack(fill="x", pady=(14, 0))
        self.btn_and_start = PillButton(
            actions, "开始抢票", self.action_start_android, kind="primary", height=44, size=13, padding=28, radius=12
        )
        self.btn_and_start.pack(side="left")
        PillButton(
            actions, "依赖检查", self.action_probe_android, kind="ghost", height=44, size=11, padding=16, radius=12
        ).pack(side="left", padx=(10, 0))

        flow = tk.Frame(p, bg=SURFACE_2)
        flow.pack(fill="x", pady=(10, 0))
        tk.Frame(flow, bg=BRAND, width=3).pack(side="left", fill="y")
        inner = tk.Frame(flow, bg=SURFACE_2)
        inner.pack(fill="x", padx=12, pady=8)
        tk.Label(inner, text="Android 端使用流程", bg=SURFACE_2, fg=INK2, font=ui_font(10, True)).pack(anchor="w")
        for line in (
            "1. 启动 Appium：appium --port 4723",
            "2. 设备上打开大麦 APP，进入目标演出详情页",
            "3. 回到这里点「依赖检查」，就绪后点「开始抢票」",
        ):
            tk.Label(inner, text=line, bg=SURFACE_2, fg=INK3, font=ui_font(10)).pack(anchor="w", pady=(1, 0))
    def _build_log(self, parent) -> None:
        card = Card(parent, padding=16, radius=16, fill=DARK, border="#1b2130", shadow=False)
        card.pack(fill="both", expand=True)
        body = card.body

        head = tk.Frame(body, bg=DARK)
        head.pack(fill="x")
        tk.Label(head, text="运行日志", bg=DARK, fg="#e8edf6", font=ui_font(12, True)).pack(side="left")
        tk.Label(head, text="实时输出", bg=DARK, fg="#5b6577", font=ui_font(10)).pack(side="left", padx=(8, 0))
        PillButton(head, "清空", self.action_clear_log, kind="dark", height=28, size=10, padding=12).pack(side="right")

        wrap = tk.Frame(body, bg=DARK)
        wrap.pack(fill="both", expand=True, pady=(10, 0))
        self.log_text = tk.Text(
            wrap,
            bg=DARK,
            fg="#c9d1d9",
            font=ui_mono(10),
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=2,
            pady=2,
            wrap="none",
            insertbackground="#c9d1d9",
            state="disabled",
            spacing1=1,
            spacing3=1,
            selectbackground="#243044",
        )
        scroll = ttk.Scrollbar(
            wrap, orient="vertical", command=self.log_text.yview, style="Dark.Vertical.TScrollbar"
        )
        self.log_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y", padx=(6, 0))
        self.log_text.pack(side="left", fill="both", expand=True)
        hbar = ttk.Scrollbar(
            body, orient="horizontal", command=self.log_text.xview, style="Dark.Horizontal.TScrollbar"
        )
        self.log_text.configure(xscrollcommand=hbar.set)
        hbar.pack(fill="x", pady=(4, 0))
        for tag, color in TAG_COLORS.items():
            self.log_text.tag_configure(tag, foreground=color)
        self._account_tags: dict[str, str] = {}
        self.log_text.configure(state="normal")
        self.log_text.insert("end", "就绪 · 操作会实时输出到这里\n", "time")
        self.log_text.configure(state="disabled")
    def _build_status(self) -> None:
        bar = tk.Frame(self, bg=WHITE, height=36)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)
        tk.Frame(bar, bg=LINE, height=1).pack(fill="x", side="top")

        left = tk.Frame(bar, bg=WHITE)
        left.pack(side="left", padx=24)
        self.status_dot = Dot(left, color=OK, size=8)
        self.status_dot.pack(side="left", pady=(1, 0))
        self.var_status = tk.StringVar(value="正在检查环境…")
        tk.Label(left, textvariable=self.var_status, bg=WHITE, fg=INK2, font=ui_font(11)).pack(
            side="left", padx=(8, 0)
        )

        self.var_state = tk.StringVar(value="待机")
        self.state_label = tk.Label(bar, textvariable=self.var_state, bg=WHITE, fg=INK3, font=ui_font(11, True))
        self.state_label.pack(side="right", padx=24)
    def _load_configs(self) -> None:
        pc_path = CONFIG_STORE if CONFIG_STORE.exists() else DAMAI_DIR / "config.json"
        if pc_path.exists():
            try:
                self._pc_cfg = json.loads(pc_path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                self.log(f"读取 config.json 失败：{exc}", "err")
                self._pc_cfg = {}
        cfg = self._pc_cfg

        self.known_dates = list(
            dict.fromkeys(
                [str(d) for d in (cfg.get("known_dates") or [])]
                + [str(d) for d in (cfg.get("dates") or [])]
            )
        )
        self.known_prices = list(
            dict.fromkeys(
                [str(p) for p in (cfg.get("known_prices") or [])]
                + [str(p) for p in (cfg.get("prices") or [])]
            )
        )
        self.var_target_url.set(cfg.get("target_url") or "")
        self.cb_date["values"] = self.known_dates
        self.cb_price["values"] = self.known_prices
        dates = cfg.get("dates") or []
        prices = cfg.get("prices") or []
        self.var_date.set(str(dates[0]) if dates else "")
        self.var_price.set(str(prices[0]) if prices else "")
        self.var_city.set(cfg.get("city") or "")
        self.var_retries.set(str(cfg.get("max_retries", 10000)))
        self.var_fast.set(bool(cfg.get("fast_mode", True)))
        self.var_listen.set(bool(cfg.get("if_listen", True)))
        self.var_commit.set(bool(cfg.get("if_commit_order", False)))

        # 账号库：首次使用把旧配置的观演人迁成「默认账号」
        self.accounts = account_store.load()
        if not self.accounts:
            legacy = [str(u) for u in (cfg.get("users") or [])]
            self.accounts = account_store.migrate_from_config(legacy)
            if self.accounts:
                self.log("已把旧配置的观演人迁成「默认账号」，需要重新扫码登录一次", "warn")
        self._render_accounts()
        self.var_target_url.trace_add("write", lambda *_: self._sync_buttons())

        and_path = APPIUM_DIR / "config.jsonc"
        if and_path.exists():
            try:
                self._android_cfg = json.loads(and_path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                self.log(f"读取 config.jsonc 失败：{exc}", "err")
                self._android_cfg = {}
        cfg = self._android_cfg
        self.var_server.set(cfg.get("server_url", "http://127.0.0.1:4723"))
        self.var_keyword.set(cfg.get("keyword", ""))
        self.var_and_city.set(cfg.get("city", ""))
        self.var_and_date.set(cfg.get("date", ""))
        self.var_and_price.set(cfg.get("price", ""))
        self.var_price_index.set(str(cfg.get("price_index", 1)))
        self.txt_and_users.insert("1.0", "\n".join(cfg.get("users") or []))
        self.var_and_commit.set(bool(cfg.get("if_commit_order", False)))
    def _split(text: str) -> list[str]:
        parts = re.split(r"[,，、\n;；]+", text)
        return [p.strip() for p in parts if p.strip()]

    @staticmethod
    def _lines(text: str) -> list[str]:
        return [l.strip() for l in text.splitlines() if l.strip()]

    def _collect_pc(self) -> tuple[dict, str | None]:
        target = self.var_target_url.get().strip()
        if not target.startswith("http"):
            return {}, "先粘贴以 http 开头的演出详情页链接"
        try:
            retries = int(self.var_retries.get().strip() or "10000")
        except ValueError:
            return {}, "刷新轮数需为整数"

        date = self.var_date.get().strip()
        price = self.var_price.get().strip()
        if date and date not in self.known_dates:
            self.known_dates.insert(0, date)
        if price and price not in self.known_prices:
            self.known_prices.insert(0, price)

        union: list[str] = []
        for account in self.accounts:
            for user in account.get("selected") or []:
                if user not in union:
                    union.append(user)

        cfg = dict(self._pc_cfg)
        cfg.update(
            {
                "index_url": cfg.get("index_url", "https://www.damai.cn/"),
                "login_url": cfg.get("login_url", "https://passport.damai.cn/login"),
                "target_url": target,
                "users": union,
                "city": self.var_city.get().strip() or None,
                "dates": [date] if date else [],
                "known_dates": self.known_dates,
                "prices": [price] if price else [],
                "known_prices": self.known_prices,
                "if_listen": bool(self.var_listen.get()),
                "if_commit_order": bool(self.var_commit.get()),
                "max_retries": retries,
                "fast_mode": bool(self.var_fast.get()),
            }
        )
        return cfg, None
    def _collect_android(self) -> tuple[dict, str | None]:
        users = self._lines(self.txt_and_users.get("1.0", "end"))
        if not users:
            return {}, "观演人至少填一个"
        try:
            price_index = int(self.var_price_index.get().strip() or "0")
        except ValueError:
            return {}, "price_index 需为整数"
        cfg = dict(self._android_cfg)
        cfg.update(
            {
                "server_url": self.var_server.get().strip() or "http://127.0.0.1:4723",
                "keyword": self.var_keyword.get().strip(),
                "users": users,
                "city": self.var_and_city.get().strip(),
                "date": self.var_and_date.get().strip(),
                "price": self.var_and_price.get().strip(),
                "price_index": price_index,
                "if_commit_order": bool(self.var_and_commit.get()),
            }
        )
        return cfg, None

    def _write_json(self, path: Path, data: dict) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # ---------------- 动作 ---------------- #
    def action_open_dir(self) -> None:
        subprocess.run(["open", str(DATA_HOME)], check=False)

    def action_clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def action_save_pc(self) -> bool:
        cfg, err = self._collect_pc()
        if err:
            self.log(f"配置未保存：{err}", "err")
            self._flash_hint(err)
            return False
        self._pc_cfg = cfg
        self._write_json(CONFIG_STORE, cfg)
        self.log(f"已保存基准配置（{len(cfg['users'])} 位观演人，来自 {len(self.accounts)} 个账号）", "ok")
        return True
    def action_save_android(self) -> None:
        cfg, err = self._collect_android()
        if err:
            self.log(f"配置未保存：{err}", "err")
            return
        self._android_cfg = cfg
        self._write_json(APPIUM_DIR / "config.jsonc", cfg)
        self.log(f"已保存 {APPIUM_DIR / 'config.jsonc'}", "ok")

    def action_probe_url(self) -> None:
        """只读打开详情页，带出演出名/时间/城市。"""
        if self.busy or self.runner.running():
            self.log("当前有任务在运行，稍后再试", "warn")
            return
        url = self.var_target_url.get().strip()
        if not url.startswith("http"):
            self.log("先粘贴演出详情页链接，再点「读取信息」", "err")
            return

        self.busy = True
        self._sync_buttons()
        self.pc_hint.configure(text="正在读取页面…", fg=INK3)
        self.log("▶ 读取演出信息（只读，不会下单）", "cmd")

        def work() -> None:
            try:
                ok, msg = ensure_driver(self.push_log)
                if not ok:
                    self.push_log(f"读取失败：{msg}", "err")
                    self.call_soon(self._probe_done)
                    return
                sys.path.insert(0, str(Path(__file__).parent))
                from venue_probe import probe_summary

                data = probe_summary(url, driver_path=str(DRIVER_LINK))
            except Exception as exc:  # noqa: BLE001
                data = {"error": f"读取异常：{type(exc).__name__}: {exc}"}
            self.call_soon(lambda: self._apply_probe(data))

        threading.Thread(target=work, daemon=True).start()

    def _probe_done(self) -> None:
        self.busy = False
        self.pc_hint.configure(text="", fg=INK3)
        self._sync_buttons()

    def _apply_probe(self, data: dict) -> None:
        self.busy = False
        self.pc_hint.configure(text="", fg=INK3)
        title = (data.get("title") or "").strip()
        bits = [b for b in (data.get("city"), data.get("datetime")) if b]
        if data.get("error") and not title:
            self.log(data["error"], "err")
            self._sync_buttons()
            return

        self.var_show.set(title + (("\n" + " · ".join(bits)) if bits else ""))
        if not self.card.winfo_ismapped():
            self.card.pack(fill="x", pady=(8, 0), after=self.link_row)
        if data.get("city"):
            self.var_city.set(data["city"])
        moment = (data.get("datetime") or "").strip()
        if moment:
            if moment not in self.known_dates:
                self.known_dates.insert(0, moment)
            self.cb_date["values"] = self.known_dates
            self.var_date.set(moment)
        self.update_idletasks()
        if data.get("error"):
            self.log(data["error"], "warn")
        self.log(f"已识别：{title or '（无标题）'}" + (f" · {' · '.join(bits)}" if bits else ""), "ok")
        self._update_summary()
        self._sync_buttons()

    def action_selfcheck(self) -> None:
        if self.busy or self.runner.running():
            self.log("当前有任务在运行，稍后再试", "warn")
            return
        self._run_selfcheck()

    def _run_selfcheck(self) -> None:
        self.busy = True
        self._sync_buttons()
        self.var_state.set("自检中…")
        self.log("▶ 环境自检（damai/check_environment.py）", "cmd")
        self.runner.start([sys.executable, "check_environment.py"], DAMAI_DIR)

    def action_start_pc(self) -> None:
        if self.mode != "idle" or self.runner.running() or self.multi.running():
            self.log("已有任务在运行，先停止", "warn")
            return
        cfg, err = self._collect_pc()
        if err:
            self.log(err, "err")
            self._flash_hint(err)
            return

        ready: list[dict] = []
        for account in self.accounts:
            if not account.get("enabled"):
                continue
            if not account.get("selected"):
                continue
            if not account_store.has_cookie(account):
                self.log(f"「{account['name']}」未登录，已跳过", "warn")
                continue
            ready.append(account)
        if not ready:
            self.log("没有可用的账号：先添加账号 → 扫码登录 → 勾选观演人", "err")
            self._flash_hint("先添加并登录账号")
            return
        if not self.var_date.get().strip() or not self.var_price.get().strip():
            self.log("未填场次/票档，脚本会按页面默认值继续；建议填上以免抢错", "warn")

        self._write_json(CONFIG_STORE, cfg)
        self.mode = "preparing"
        self.busy = True
        self.var_state.set("准备环境…")
        self._render_accounts()
        self._sync_buttons()
        self.log(f"▶ 准备 {len(ready)} 个账号并发抢票", "cmd")

        def work() -> None:
            try:
                ok, msg = ensure_driver(self.push_log)
            except Exception as exc:  # noqa: BLE001
                ok, msg = False, f"{type(exc).__name__}: {exc}"
            if not ok:
                self.push_log(f"环境准备失败：{msg}", "err")
                self.call_soon(self._start_failed)
                return
            try:
                jobs = run_manager.build_jobs(ready, cfg, DRIVER_LINK.resolve(), DRIVER_CACHE)
            except Exception as exc:  # noqa: BLE001
                self.push_log(f"准备运行目录失败：{exc}", "err")
                self.call_soon(self._start_failed)
                return
            self.call_soon(lambda: self._launch_pc(jobs))

        threading.Thread(target=work, daemon=True).start()

    def _launch_pc(self, jobs: list[dict]) -> None:
        self.busy = False
        self.mode = "grabbing"
        self.var_state.set(f"抢票中 · {len(jobs)} 个账号")
        self.state_label.configure(fg=BRAND)
        for job in jobs:
            self.log(f"→ 启动「{job['name']}」", "cmd")
        self.multi.start(jobs)
        self._render_accounts()
        self._sync_buttons()
    def _start_failed(self) -> None:
        self.busy = False
        self.mode = "idle"
        self.var_state.set("环境未就绪")
        self.state_label.configure(fg=BRAND)
        self.log("环境准备失败，可点「环境自检」查看原因", "err")
        self._sync_buttons()
    def action_start_android(self) -> None:
        if self.busy or self.runner.running():
            self.log("已有任务在运行，请先停止", "warn")
            return
        cfg, err = self._collect_android()
        if err:
            self.log(f"无法启动：{err}", "err")
            return
        info = probe_android()
        if not info["appium"]:
            self.log("未安装 appium：npm i -g appium && appium driver install uiautomator2", "err")
            return
        if not info["adb"]:
            self.log("未找到 adb：请安装 Android SDK platform-tools 并设置 ANDROID_HOME", "err")
            return
        if not info["devices"]:
            self.log("未检测到已连接的 Android 设备/模拟器", "err")
            return
        if not info["package"]:
            self.log("设备上未安装大麦 APP（cn.damai）", "err")
            return
        self._android_cfg = cfg
        self._write_json(APPIUM_DIR / "config.jsonc", cfg)
        self.var_state.set("运行中 · Android")
        self.state_label.configure(fg=BRAND)
        self.runner.start([sys.executable, "damai_app_v2.py"], APPIUM_DIR)
        self._sync_buttons()

    def action_stop(self) -> None:
        stopped = 0
        if self.multi.running():
            stopped = self.multi.stop()
            self.log(f"已停止 {stopped} 个账号进程", "warn")
        if self.runner.running():
            self.runner.stop()
            self.log("已停止当前进程", "warn")
        self.mode = "idle"
        self.busy = False
        self.var_state.set("已停止")
        self.state_label.configure(fg=INK3)
        self._render_accounts()
        self._sync_buttons()
    def on_proc_exit(self, code: int) -> None:
        self.call_soon(lambda: self._on_exit(code))

    def _on_exit(self, code: int) -> None:
        mode = self.mode
        self.busy = False
        if mode == "logging_in":
            account = self._find_account(self.login_account_id)
            self.login_account_id = ""
            self.login_signal = None
            self.mode = "idle"
            if account and account_store.has_cookie(account):
                account_store.mark_logged_in(account)
                self.accounts = account_store.load()
                self.log(f"✓「{account['name']}」登录态已保存，可以开始抢票了", "ok")
            else:
                self.log("没有保存到登录态，可再点一次「登录」重试", "err")
            self._render_accounts()
        elif mode == "checking":
            self.mode = "idle"
        self.var_state.set("待机")
        self.state_label.configure(fg=INK3)
        self._sync_buttons()
    def _sync_buttons(self) -> None:
        busy_now = self.runner.running() or self.multi.running()
        idle = self.mode == "idle" and not busy_now and not self.busy
        has_input = bool(self.var_target_url.get().strip())
        ready = any(
            a.get("enabled") and a.get("selected") and account_store.has_cookie(a)
            for a in self.accounts
        )
        self.btn_pc_start.configure(state="normal" if (idle and has_input and ready) else "disabled")
        android_ok = getattr(self, "android_ready", False)
        self.btn_and_start.configure(state="normal" if (idle and android_ok) else "disabled")
        self.btn_pc_save.configure(state="normal" if not busy_now else "disabled")
        self.btn_pc_stop.configure(state="normal" if (busy_now or self.mode != "idle") else "disabled")
        self.btn_probe.configure(state="normal" if idle else "disabled")
        if self.mode == "grabbing" and self.multi.running():
            self.state_label.configure(fg=BRAND)
        if hasattr(self, "status_dot"):
            if busy_now:
                self.status_dot.set_color(BRAND)
            elif self.mode in ("logging_in", "preparing"):
                self.status_dot.set_color(WARN)
            else:
                self.status_dot.set_color(OK)
        elif not busy_now and self.mode == "idle" and self.var_state.get().startswith("抢票中"):
            self.var_state.set("待机")
    def _flash_hint(self, text: str) -> None:
        self.pc_hint.configure(text=text, fg=BRAND)
        self.after(4000, lambda: self.pc_hint.configure(text="", fg=INK3))

    # ---------------- 自检辅助 ---------------- #
    def dump_layout(self, widget=None, depth: int = 0) -> None:
        widget = widget or self
        for child in widget.winfo_children():
            try:
                text = child.cget("text")
            except Exception:
                text = ""
            text = (str(text)[:28] + "…") if len(str(text)) > 28 else str(text)
            print(
                f"{'  ' * depth}{child.winfo_class():<12} "
                f"{child.winfo_width():>4}x{child.winfo_height():<4} "
                f"@{child.winfo_x():>4},{child.winfo_y():<4} {text}"
            )
            self.dump_layout(child, depth + 1)

    def check_layout(self) -> list[str]:
        problems: list[str] = []
        for name in ("log_text", "accounts_canvas", "entry_url", "cb_date", "cb_price", "btn_pc_start", "btn_and_start"):
            widget = getattr(self, name, None)
            if widget is None:
                problems.append(f"{name} 不存在")
                continue
            if not widget.winfo_ismapped():
                continue
            if widget.winfo_width() < 40 or widget.winfo_height() < 18:
                problems.append(f"{name} 尺寸异常 {widget.winfo_width()}x{widget.winfo_height()}")
        if self.log_text.winfo_height() < 100:
            problems.append(f"日志区过矮 {self.log_text.winfo_height()}px")
        if self.left_card.winfo_width() < 500:
            problems.append(f"左栏过窄 {self.left_card.winfo_width()}px")
        return problems
    def log(self, text: str, tag: str = "info") -> None:
        self.push_log(text, tag)

    def push_log(self, text: str, tag: str = "info") -> None:
        self.log_queue.put((text, tag))

    def call_soon(self, fn) -> None:
        """把回调排到主线程执行（tkinter 不允许子线程直接操作控件）。"""
        self.ui_queue.put(fn)

    def _drain_log(self) -> None:
        try:
            while True:
                text, tag = self.log_queue.get_nowait()
                self._append(text, tag)
        except queue.Empty:
            pass
        try:
            while True:
                callback = self.ui_queue.get_nowait()
                try:
                    callback()
                except Exception as exc:  # noqa: BLE001
                    self._append(f"界面回调异常：{type(exc).__name__}: {exc}", "err")
        except queue.Empty:
            pass
        self.after(120, self._drain_log)

    def _append(self, text: str, tag: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{stamp} ", "time")
        if text.startswith("[") and "]" in text[:26]:
            cut = text.index("]")
            prefix, rest = text[: cut + 1], text[cut + 1 :]
            key = "acc:" + prefix[1:]
            if key not in self._account_tags:
                color = ui_kit.ACCOUNT_COLORS[len(self._account_tags) % len(ui_kit.ACCOUNT_COLORS)]
                self.log_text.tag_configure(key, foreground=color)
                self._account_tags[key] = color
            self.log_text.insert("end", prefix, key)
            self.log_text.insert("end", rest + "\n", tag)
        else:
            self.log_text.insert("end", text + "\n", tag)
        self.log_text.configure(state="disabled")
        self.log_text.see("end")
        try:
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(f"{stamp} [{tag}] {text}\n")
        except Exception:
            pass
    def _startup_probe(self) -> None:
        def work() -> None:
            version, major = chrome_version()
            if not version:
                self.push_log("未检测到 Google Chrome", "err")
                self.call_soon(lambda: self.var_status.set("环境异常：未安装 Chrome"))
                return
            dmaj = driver_major(DRIVER_LINK) if (DRIVER_LINK.exists() or DRIVER_LINK.is_symlink()) else None
            selenium_ok = True
            try:
                import selenium  # noqa: F401
            except ImportError:
                selenium_ok = False
            parts = [f"Chrome {version}"]
            parts.append(f"driver {dmaj or '未就绪'}")
            parts.append("selenium 已安装" if selenium_ok else "selenium 缺失")
            detail = " · ".join(parts)
            self.push_log(f"环境探测：{detail}", "ok" if dmaj == major and selenium_ok else "warn")
            self.call_soon(lambda: self.var_status.set(detail))
            if dmaj != major:
                self.push_log("driver 与 Chrome 主版本不一致，点击「开始抢票」时会自动修复", "warn")
            self.call_soon(self._probe_android_ui)

        threading.Thread(target=work, daemon=True).start()

    def action_probe_android(self) -> None:
        threading.Thread(target=self._probe_android_ui, daemon=True).start()

    def _render_android_env(self, info: dict | None) -> None:
        for child in self.android_env.winfo_children():
            child.destroy()
        if info is None:
            tk.Label(
                self.android_env, text="检查依赖中…", bg=WHITE, fg=INK3, font=ui_font(10)
            ).pack(anchor="w")
            return
        rows = [
            ("Appium 服务", bool(info.get("appium")), "已安装" if info.get("appium") else "未安装（npm i -g appium）"),
            ("adb 工具", bool(info.get("adb")), "已就绪" if info.get("adb") else "未找到（需 Android SDK）"),
            ("安卓设备", bool(info.get("devices")), ", ".join(info.get("devices") or []) or "未检测到连接"),
            ("大麦 APP", bool(info.get("package")), "已安装" if info.get("package") else "未安装 cn.damai"),
        ]
        ready = all(flag for _, flag, _ in rows)
        self.android_ready = ready
        for label, flag, detail in rows:
            row = tk.Frame(self.android_env, bg=WHITE)
            row.pack(fill="x", pady=1)
            Dot(row, OK if flag else "#cfd4dc", 7).pack(side="left", pady=(2, 0))
            tk.Label(row, text=label, bg=WHITE, fg=INK2, font=ui_font(10), width=12, anchor="w").pack(
                side="left", padx=(8, 0)
            )
            tk.Label(row, text=detail, bg=WHITE, fg=OK if flag else INK3, font=ui_font(10)).pack(side="left")

    def _probe_android_ui(self) -> None:
        info = probe_android()
        self.call_soon(lambda: self._render_android_env(info))
        self.call_soon(self._sync_buttons)


def main() -> int:
    selftest = "--selftest" in sys.argv
    app = DamaiApp()
    if selftest:
        app.update_idletasks()
        app.update()
        print(f"selftest ok: {app.winfo_width()}x{app.winfo_height()}")
        if "--layout" in sys.argv:
            app.dump_layout()
        problems = app.check_layout()
        print("layout problems:", problems or "无")
        app.destroy()
        return 0
    # 先把窗口映射出来，再尝试抬到最前（不调用 focus_force，避免无交互会话下阻塞）
    app.update_idletasks()
    app.lift()

    def _bring_front() -> None:
        try:
            app.attributes("-topmost", True)
            app.after(1200, lambda: app.attributes("-topmost", False))
        except Exception:  # noqa: BLE001
            pass

    app.after(60, _bring_front)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
