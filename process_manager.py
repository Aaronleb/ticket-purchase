from __future__ import annotations

import os
import queue
import signal
import subprocess
import threading
import time
from collections import deque
from pathlib import Path


class TaskError(RuntimeError):
    pass


class TaskBusy(TaskError):
    pass


class TaskNotRunning(TaskError):
    pass


class ProcessManager:
    PHASES = {
        "login": "waiting_login",
        "check": "checking",
        "grab": "grabbing",
        "android": "grabbing",
    }

    def __init__(self):
        self._lock = threading.RLock()
        self._proc = None
        self._task = None
        self._phase = "idle"
        self._message = "就绪"
        self._exit_code = None
        self._started_at = None
        self._stop_requested = False
        self._logs = deque(maxlen=1200)
        self._subscribers = set()
        self._sequence = 0

    def snapshot(self) -> dict:
        with self._lock:
            running = self._proc is not None and self._proc.poll() is None
            return {
                "task": self._task,
                "phase": self._phase,
                "running": running,
                "message": self._message,
                "exit_code": self._exit_code,
                "started_at": self._started_at,
                "pid": self._proc.pid if running else None,
            }

    def logs(self) -> list[dict]:
        with self._lock:
            return list(self._logs)

    def subscribe(self) -> queue.Queue:
        channel = queue.Queue(maxsize=300)
        with self._lock:
            self._subscribers.add(channel)
        return channel

    def unsubscribe(self, channel) -> None:
        with self._lock:
            self._subscribers.discard(channel)

    def _publish(self, event: dict) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for channel in subscribers:
            try:
                channel.put_nowait(event)
            except queue.Full:
                try:
                    channel.get_nowait()
                    channel.put_nowait(event)
                except (queue.Empty, queue.Full):
                    pass

    def _publish_status(self) -> None:
        self._publish({"type": "status", "data": self.snapshot()})

    def _append_log(self, text: str) -> None:
        with self._lock:
            self._sequence += 1
            entry = {
                "id": self._sequence,
                "time": time.strftime("%H:%M:%S"),
                "text": text,
            }
            self._logs.append(entry)
        self._publish({"type": "log", "data": entry})

    def start(self, task: str, command: list[str], cwd: Path | str, env: dict | None = None) -> dict:
        if task not in self.PHASES:
            raise TaskError(f"未知任务：{task}")
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                raise TaskBusy("已有任务在运行，请先停止")
            child_env = os.environ.copy()
            child_env["PYTHONUNBUFFERED"] = "1"
            if env:
                child_env.update(env)
            try:
                proc = subprocess.Popen(
                    command,
                    cwd=str(cwd),
                    env=child_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    start_new_session=True,
                )
            except (OSError, ValueError) as exc:
                self._task = task
                self._phase = "error"
                self._message = f"启动失败：{exc}"
                self._exit_code = -1
                self._publish_status()
                raise TaskError(self._message) from exc

            self._proc = proc
            self._task = task
            self._phase = self.PHASES[task]
            self._message = {
                "login": "请在 Chrome 中完成扫码登录",
                "check": "正在检查运行环境",
                "grab": "PC 抢票进程运行中",
                "android": "安卓抢票进程运行中",
            }[task]
            self._exit_code = None
            self._started_at = time.strftime("%Y-%m-%d %H:%M:%S")
            self._stop_requested = False

        self._append_log(f"── 启动{ {'login': '扫码登录', 'check': '环境自检', 'grab': 'PC 抢票', 'android': '安卓抢票'}[task] } ──")
        self._publish_status()
        threading.Thread(target=self._read_output, args=(proc,), daemon=True).start()
        return self.snapshot()

    def _read_output(self, proc) -> None:
        try:
            if proc.stdout is not None:
                for line in proc.stdout:
                    self._append_log(line.rstrip())
        finally:
            if proc.stdout is not None:
                proc.stdout.close()
            code = proc.wait()
            with self._lock:
                if proc is not self._proc:
                    return
                stopped = self._stop_requested
                self._exit_code = code
                if stopped:
                    self._phase = "stopped"
                    self._message = "任务已停止"
                elif code == 0:
                    self._phase = "idle"
                    self._message = {
                        "login": "登录流程结束，请检查登录状态",
                        "check": "环境自检通过",
                        "grab": "PC 抢票进程已结束，请在大麦确认订单状态",
                        "android": "安卓抢票进程已结束，请在大麦 App 确认订单状态",
                    }.get(self._task, "任务已结束")
                else:
                    self._phase = "error"
                    self._message = f"任务异常退出（代码 {code}）"
                self._proc = None
            self._append_log(f"── 进程退出，代码 {code} ──")
            self._publish_status()

    def stop(self) -> dict:
        with self._lock:
            proc = self._proc
            if proc is None or proc.poll() is not None:
                raise TaskNotRunning("当前没有运行中的任务")
            self._stop_requested = True
            self._phase = "stopping"
            self._message = "正在停止任务"
        self._append_log("── 正在停止进程组 ──")
        self._publish_status()
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=2)
        return self.snapshot()

    def complete_login(self, signal_path: Path | str) -> dict:
        with self._lock:
            if self._task != "login" or self._proc is None or self._proc.poll() is not None:
                raise TaskNotRunning("当前没有等待确认的扫码登录")
        path = Path(signal_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("done\n", encoding="utf-8")
        self._append_log("已确认登录，正在保存 Cookie")
        return self.snapshot()

    def shutdown(self) -> None:
        try:
            self.stop()
        except (TaskNotRunning, OSError, subprocess.TimeoutExpired):
            pass
