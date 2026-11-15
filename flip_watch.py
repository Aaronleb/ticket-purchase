# -*- coding: UTF-8 -*-
"""u2 加速的底栏翻转感知。

真机验证结论：大麦详情页底栏（已预约/立即预订）是自绘控件，
无障碍树里没有任何文本节点，``d(textContains=...)`` 永远等不到。
但 u2 走本地 agent 截图只要 ~200ms（adb screencap 的一半以上），
所以这里用 u2 截图 + 裁剪底栏条带 + 轻量 OCR 只做关键词匹配：
一轮约 0.5 秒，比全屏 adb 截图 OCR（约 1.8 秒/轮）快 3 倍。

设备需要先执行过一次 ``python -m uiautomator2 init``（装 atx-agent）。
agent 不可用时报 FlipWatchError，调用方退回全屏 OCR 轮询。
"""
from __future__ import annotations

import subprocess
import tempfile
import time
from pathlib import Path


FLIP_KEYWORDS = ("立即预订", "立即购票", "立即购买")
# 裁剪屏幕底部条带感知翻转（底栏固定在底部区域）
BOTTOM_STRIP_RATIO = 0.82
# 每轮之间的间隔
POLL_GAP = 0.15
# OCR 单轮超时
OCR_TIMEOUT = 15.0

_DEFAULT_OCR_TOOL = Path(__file__).resolve().parent / "tools" / "damai_ocr"


class FlipWatchError(RuntimeError):
    """u2 agent 不可用，无法启用翻转感知。"""


def _spawn_daemon(ocr_tool: Path):
    """启动 OCR 常驻进程，返回 (进程, 首行) 或失败时 None。"""
    try:
        proc = subprocess.Popen(
            [str(ocr_tool), "--daemon"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )
        first = proc.stdout.readline().strip()
        if proc.poll() is not None or first != "READY":
            proc.kill()
            return None
        return proc
    except OSError:
        return None


class _DaemonOcr:
    """复用常驻 OCR 进程；进程死掉则退回一次性调用。"""

    _proc = None
    _tool = None

    @classmethod
    def texts(cls, crop_path: str, ocr_tool: Path) -> list[str]:
        if cls._tool != ocr_tool:
            cls._proc = None  # 工具路径变了，旧进程不用了
            cls._tool = ocr_tool
        lines = cls._ask_daemon(crop_path, ocr_tool)
        if lines is None:
            return _oneshot_texts(crop_path, ocr_tool)
        return lines

    @classmethod
    def _ask_daemon(cls, crop_path: str, ocr_tool: Path):
        if cls._proc is None:
            cls._proc = _spawn_daemon(ocr_tool)
        proc = cls._proc
        if proc is None:
            return None
        try:
            proc.stdin.write(crop_path + "\n")
            proc.stdin.flush()
            lines = []
            while True:
                line = proc.stdout.readline()
                if not line:  # 进程退出
                    cls._proc = None
                    return None
                line = line.strip()
                if line == "===END===":
                    return lines
                if line.startswith("ERROR\t"):
                    continue
                lines.append(line)
        except (OSError, ValueError):
            cls._proc = None
            return None


def _oneshot_texts(crop_path: str, ocr_tool: Path) -> list[str]:
    try:
        result = subprocess.run(
            [str(ocr_tool), crop_path],
            capture_output=True, text=True, timeout=OCR_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [
        parts[3]
        for line in result.stdout.splitlines()
        if len(parts := line.split("\t")) == 4
    ]


STRIP_STABLE_RATIO = 0.88  # 与按钮坐标区（约 0.945 屏高）对齐，留余量
STABLE_THRESHOLD = 5.0  # 相邻两帧底栏平均像素差（0~255）低于此值算稳定
STABLE_ROUNDS = 2  # 连续几轮稳定才认为渲染完成


def make_strip_stable_waiter(device: str):
    """连接设备端 u2 agent，返回 (名称, wait)。

    wait(timeout_seconds) -> bool：轮询截图比对底栏区域，连续 STABLE_ROUNDS
    轮像素基本不变返回 True（渲染完成，可盲点坐标）；超时返回 False。
    截图异常（agent 掉线）抛 FlipWatchError。
    """
    try:
        import uiautomator2 as u2
    except ImportError as exc:
        raise FlipWatchError(f"uiautomator2 未安装：{exc}") from exc
    try:
        d = u2.connect(device)
        d.info  # 探活
    except Exception as exc:
        raise FlipWatchError(
            f"无法连接设备端 u2 agent（{type(exc).__name__}: {exc}）；"
            "手机连上后执行 .venv/bin/python -m uiautomator2 init 安装"
        ) from exc

    from PIL import ImageChops, ImageStat

    def _strip():
        image = d.screenshot()
        top = int(image.height * STRIP_STABLE_RATIO)
        return image.crop((0, top, image.width, image.height)).convert("L")

    def wait(timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        prev = None
        stable = 0
        while time.monotonic() < deadline:
            try:
                cur = _strip()
            except Exception as exc:
                raise FlipWatchError(
                    f"u2 截图失败（{type(exc).__name__}: {exc}）"
                ) from exc
            if prev is not None:
                diff = ImageChops.difference(cur, prev)
                mean = sum(ImageStat.Stat(diff).mean) / len(diff.getbands())
                stable = stable + 1 if mean <= STABLE_THRESHOLD else 0
                if stable >= STABLE_ROUNDS:
                    return True
            prev = cur
        return False

    return "u2 底栏渲染稳定检测", wait


def make_flip_watcher(device: str, ocr_tool=None):
    """连接设备端 u2 agent，返回 (名称, watch)。

    watch(remaining_seconds) -> bool：在 remaining 秒内一旦底栏条带
    OCR 命中可购买文案返回 True，超时返回 False。
    截图异常（agent 掉线）抛 FlipWatchError，由调用方计数退回。
    """
    try:
        import uiautomator2 as u2
    except ImportError as exc:
        raise FlipWatchError(f"uiautomator2 未安装：{exc}") from exc
    try:
        d = u2.connect(device)
        d.info  # 探活：agent 不在会在这里抛异常
    except Exception as exc:
        raise FlipWatchError(
            f"无法连接设备端 u2 agent（{type(exc).__name__}: {exc}）；"
            "手机连上后执行 .venv/bin/python -m uiautomator2 init 安装"
        ) from exc

    tool = Path(ocr_tool) if ocr_tool else _DEFAULT_OCR_TOOL
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    crop_path = tmp.name
    tmp.close()

    def watch(remaining: float) -> bool:
        deadline = time.monotonic() + remaining
        while True:
            left = deadline - time.monotonic()
            if left <= 0.05:
                return False
            try:
                image = d.screenshot()
            except Exception as exc:
                raise FlipWatchError(
                    f"u2 截图失败（{type(exc).__name__}: {exc}）"
                ) from exc
            strip_top = int(image.height * BOTTOM_STRIP_RATIO)
            image.crop((0, strip_top, image.width, image.height)).save(
                crop_path
            )
            texts = _DaemonOcr.texts(crop_path, tool)
            if any(keyword in text for keyword in FLIP_KEYWORDS
                   for text in texts):
                return True
            gap = min(POLL_GAP, left)
            if gap > 0:
                time.sleep(gap)

    return "u2 截图+底栏 OCR", watch
