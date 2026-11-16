# -*- coding: UTF-8 -*-
"""Canvas 自绘底栏的实时模板感知。

Scrcpy 持续视频帧只保留最新帧并裁剪购买栏；区域未变化时不执行匹配，
变化后只接受“立即预订／立即购票／立即购买”正面模板命中。极速模式不调用
OCR，也不把像素变化、“已预约”或未知画面当作放行信号。文件末尾的 u2 OCR
实现仅保留给旧调用方，不参与极速模式。
"""
from __future__ import annotations

import subprocess
import sys
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
            [sys.executable, str(ocr_tool), "--daemon"],
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
            [sys.executable, str(ocr_tool), crop_path],
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


SCRCPY_DIFF_THRESHOLD = 4.0
SCRCPY_TEMPLATE_THRESHOLD = 0.86
SCRCPY_TEMPLATE_DIR = Path(__file__).resolve().parent / "resources" / "flip-templates"


def _scrcpy_crop(frame, focus_point=None, screen_size=None):
    height, width = frame.shape[:2]
    if focus_point and screen_size and all(screen_size):
        scale_x = width / screen_size[0]
        scale_y = height / screen_size[1]
        center_x = int(focus_point[0] * scale_x)
        center_y = int(focus_point[1] * scale_y)
        half_width = max(120, int(width * 0.24))
        left = max(0, center_x - half_width)
        right = min(width, center_x + half_width)
        top = max(0, center_y - int(height * 0.075))
        bottom = min(height, center_y + int(height * 0.04))
        if right > left and bottom > top:
            return frame[top:bottom, left:right]
    top = int(height * BOTTOM_STRIP_RATIO)
    return frame[top:, :]


def _small_gray(crop, cv2):
    return cv2.resize(
        cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY),
        (160, 64),
        interpolation=cv2.INTER_AREA,
    )


def _load_scrcpy_templates(cv2, template_dir=None):
    directory = Path(template_dir) if template_dir else SCRCPY_TEMPLATE_DIR
    if not directory.is_dir():
        return []
    templates = []
    for path in sorted(directory.glob("*.png")):
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is not None and image.size:
            templates.append((path.stem, image))
    return templates


def _scrcpy_template_hit(crop, templates, cv2):
    if not templates:
        return False
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    for _name, template in templates:
        for scale in (0.5, 0.59, 0.67, 0.8, 1.0, 1.25, 1.5, 2.0):
            width = max(1, int(template.shape[1] * scale))
            height = max(1, int(template.shape[0] * scale))
            if width > gray.shape[1] or height > gray.shape[0]:
                continue
            resized = cv2.resize(template, (width, height), interpolation=cv2.INTER_AREA)
            scores = cv2.matchTemplate(gray, resized, cv2.TM_CCOEFF_NORMED)
            if float(scores.max()) >= SCRCPY_TEMPLATE_THRESHOLD:
                return True
    return False


def make_scrcpy_watcher(
    device: str,
    focus_point=None,
    screen_size=None,
    template_dir=None,
):
    """持续接收 Scrcpy 最新帧，只接受购买按钮正面模板命中。"""
    try:
        import cv2
        from scrcpy_stream import ScrcpyError, ScrcpyStream
    except ImportError as exc:
        raise FlipWatchError(f"Scrcpy 运行依赖不可用：{exc}") from exc

    templates = _load_scrcpy_templates(cv2, template_dir)
    if not templates:
        directory = Path(template_dir) if template_dir else SCRCPY_TEMPLATE_DIR
        raise FlipWatchError(
            f"未找到可购买按钮模板：{directory}；请先校准可购买详情页"
        )

    stream = ScrcpyStream(device)
    try:
        stream.start()
    except (ScrcpyError, OSError) as exc:
        stream.stop()
        raise FlipWatchError(str(exc)) from exc

    def watch(remaining: float) -> bool:
        deadline = time.monotonic() + max(remaining, 0.0)
        previous = None
        try:
            while time.monotonic() < deadline:
                frame = stream.read_latest()
                if frame is None:
                    time.sleep(0.005)
                    continue
                crop = _scrcpy_crop(frame, focus_point, screen_size)
                small = _small_gray(crop, cv2)
                changed = previous is None or float(
                    cv2.absdiff(small, previous).mean()
                ) >= SCRCPY_DIFF_THRESHOLD
                previous = small
                if changed and _scrcpy_template_hit(crop, templates, cv2):
                    return True
            return False
        except Exception as exc:
            if isinstance(exc, FlipWatchError):
                raise
            raise FlipWatchError(
                f"Scrcpy 视频感知失败（{type(exc).__name__}: {exc}）"
            ) from exc
        finally:
            stream.stop()

    return "Scrcpy 实时帧+按钮模板（无 OCR）", watch


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
