#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多账号并发启动：每个账号一个独立运行目录 + 独立 Cookie + 独立 Chrome 进程。

隔离思路（不改上游 damai.py 一行代码）：
    .runs/<账号>/config.json      # 该账号的配置（观演人不同）
    .runs/<账号>/damai_cookies.pkl
    .runs/<账号>/chromedriver     # 软链，满足 check_environment 的查找
运行时 cwd 设为该目录，于是 damai.py 读到的 config / cookie / driver 都是这个账号的。
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DAMAI_DIR = ROOT / "damai"
DATA_HOME = Path(os.environ.get("DAMAI_DATA") or (Path.home() / "Library" / "Application Support" / "DamaiHelper"))
RUNS_DIR = DATA_HOME / "runs"

import account_store  # noqa: E402  (同目录模块)


class MultiRunner:
    """同时跑多个 damai.py，日志按账号名打标签。"""

    def __init__(self, on_line, on_exit):
        self.on_line = on_line          # (name, text) -> None
        self.on_exit = on_exit          # (name, code) -> None
        self.procs: dict[str, subprocess.Popen] = {}
        self.lock = threading.Lock()

    def running(self) -> bool:
        with self.lock:
            return any(p.poll() is None for p in self.procs.values())

    def names(self) -> list[str]:
        with self.lock:
            return list(self.procs)

    def start(self, jobs: list[dict]) -> None:
        for job in jobs:
            name = job["name"]
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            env["SE_CACHE_PATH"] = job.get("cache", env.get("SE_CACHE_PATH", ""))
            proc = subprocess.Popen(
                job["cmd"],
                cwd=str(job["cwd"]),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=True,
                env=env,
            )
            with self.lock:
                self.procs[name] = proc
            threading.Thread(target=self._pump, args=(name, proc), daemon=True).start()

    def _pump(self, name: str, proc: subprocess.Popen) -> None:
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                self.on_line(name, line.rstrip("\n"))
        finally:
            code = proc.wait()
            self.on_exit(name, code)

    def stop(self) -> int:
        with self.lock:
            procs = list(self.procs.values())
        killed = 0
        for proc in procs:
            if proc.poll() is not None:
                continue
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                killed += 1
            except ProcessLookupError:
                pass
        for proc in procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
        return killed

    def clear(self) -> None:
        with self.lock:
            self.procs.clear()


def prepare_run_dir(account: dict, base_config: dict, driver_path: Path, cache_dir: Path) -> Path:
    """为账号准备干净的运行目录：config + cookie + driver 软链。"""
    name = account_store.safe_slug(account.get("name") or account["id"]) + "_" + account["id"][-4:]
    run_dir = RUNS_DIR / name
    if run_dir.exists():
        shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir(parents=True, exist_ok=True)

    config = dict(base_config)
    config["users"] = [u for u in account.get("selected") or []]
    (run_dir / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    cookie = account_store.cookie_path(account)
    if cookie.exists():
        shutil.copy2(cookie, run_dir / "damai_cookies.pkl")

    link = run_dir / "chromedriver"
    try:
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(driver_path)
    except OSError:
        shutil.copy2(driver_path, link)

    return run_dir


def build_jobs(accounts: list[dict], base_config: dict, driver_path: Path, cache_dir: Path) -> list[dict]:
    jobs = []
    for account in accounts:
        if not account.get("enabled"):
            continue
        if not account.get("selected"):
            continue
        run_dir = prepare_run_dir(account, base_config, driver_path, cache_dir)
        jobs.append(
            {
                "name": account["name"],
                "cwd": run_dir,
                "cmd": [sys.executable, str(DAMAI_DIR / "damai.py")],
                "cache": str(cache_dir),
            }
        )
    return jobs
