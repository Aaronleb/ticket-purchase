# -*- coding: UTF-8 -*-
"""Canvas 自绘底栏的实时模板感知。

Scrcpy 持续视频帧只保留最新帧并裁剪购买栏；区域未变化时不执行匹配，
变化后只接受“立即预订／立即购票／立即购买”正面模板命中。极速模式不调用
OCR，也不把像素变化、“已预约”或未知画面当作放行信号。开售时刻之后如果
模板全部未命中，而底栏相对开售前发生了稳定的大幅翻转，则自动补采当前帧
为新模板并立即放行；落盘在后台线程，不占用点击路径。文件末尾的 u2 OCR
实现仅保留给旧调用方，不参与极速模式。
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
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
        if getattr(sys, "frozen", False):
            # 打包后不能再启动 tools/damai_ocr 子进程，改用进程内识别
            return _oneshot_texts(crop_path, ocr_tool)
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
    if getattr(sys, "frozen", False):
        import ocr_tool

        return [text for _x, _y, _score, text in ocr_tool.recognize(crop_path)]
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

# 票档页「确定」按钮的固定区域模板。底部栏左侧价格块宽度会随金额变化，
# 只裁右侧固定的按钮框，价格从 480 变成 1280 也不影响模板。
SKU_CONFIRM_PREFIX = "sku-confirm"
SUBMIT_TEMPLATE_PREFIX = "sku-submit"
# 采集模板时的裁剪半宽/半高（相对屏幕），只含按钮本体
SKU_TEMPLATE_HALF_WIDTH = 0.19
SKU_TEMPLATE_HALF_HEIGHT = 0.030
# 运行时在该区域里搜模板，比模板本身略大，保证模板能被完整包含
SKU_SEARCH_HALF_WIDTH = 0.22
SKU_SEARCH_HALF_HEIGHT = 0.05

# 可购买状态的底栏通常是有颜色的（大麦是橙红渐变），但部分页面“已预约”
# 底栏也是橙色，所以着色只作辅助判断，不能单独放行。
# 灰度模板只能证明“文字还在”，开售前后文字相同，所以再加一道着色判断：
# 只有按钮中心这块区域被着色（饱和度高）时才认为按钮真的翻转了。
ENABLED_PATCH_HALF_W = 0.10  # 相对屏宽的取样半宽
ENABLED_PATCH_HALF_H = 0.02  # 相对屏高的取样半高
ENABLED_MIN_SATURATION = 90  # HSV 饱和度阈值（0~255）
ENABLED_PATCH_RATIO = 0.35  # 取样区里高饱和像素的最低占比

# 模板全部未命中时的自动补采：开售时刻之后，底栏相对开售前发生稳定的大幅
# 变化才放行，并把这一帧保存为新模板。补采落盘放在后台线程，不占点击路径。
AUTO_CAPTURE_DIFF_THRESHOLD = 7.0  # 与开售前底栏的最小灰度差
AUTO_CAPTURE_STABLE_DIFF = 2.0  # 连续两帧之间允许的抖动
AUTO_TEMPLATE_KEEP = 4  # 最多保留几份自动补采模板


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


def _button_is_enabled(frame, focus_point, screen_size, cv2):
    """按钮中心区域是否已着色（大麦可购买态是橙红，未开售是灰暗色）。

    没有按钮坐标时退回模板判断，不做额外拦截。
    """
    if not focus_point or not screen_size or not all(screen_size):
        return True
    height, width = frame.shape[:2]
    center_x = int(focus_point[0] * width / screen_size[0])
    center_y = int(focus_point[1] * height / screen_size[1])
    half_width = max(30, int(width * ENABLED_PATCH_HALF_W))
    half_height = max(20, int(height * ENABLED_PATCH_HALF_H))
    left = max(0, center_x - half_width)
    right = min(width, center_x + half_width)
    top = max(0, center_y - half_height)
    bottom = min(height, center_y + half_height)
    patch = frame[top:bottom, left:right]
    if patch.size == 0:
        return False
    saturation = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)[:, :, 1]
    return float((saturation >= ENABLED_MIN_SATURATION).mean()) >= ENABLED_PATCH_RATIO


def _buy_button_crop(frame, focus_point, screen_size, cv2):
    """按校准模板的比例裁出按钮灰度图；没有坐标时返回 None。"""
    if not focus_point or not screen_size or not all(screen_size):
        return None
    height, width = frame.shape[:2]
    center_x = int(focus_point[0] * width / screen_size[0])
    center_y = int(focus_point[1] * height / screen_size[1])
    half_width = max(60, int(width * 0.18))
    half_height = max(20, int(height * 0.035))
    left = max(0, center_x - half_width)
    right = min(width, center_x + half_width)
    top = max(0, center_y - half_height)
    bottom = min(height, center_y + half_height)
    if right <= left or bottom <= top:
        return None
    return cv2.cvtColor(frame[top:bottom, left:right], cv2.COLOR_BGR2GRAY)


def _save_auto_template_async(image, directory, cv2):
    """后台落盘自动补采的模板，避免阻塞放行后的第一次点击。"""

    def _write():
        try:
            directory.mkdir(parents=True, exist_ok=True)
            destination = directory / f"auto-{int(time.time() * 1000)}.png"
            cv2.imwrite(str(destination), image)
            existing = sorted(
                directory.glob("auto-*.png"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            for stale in existing[AUTO_TEMPLATE_KEEP:]:
                stale.unlink(missing_ok=True)
        except Exception:
            # 补采只是为下次运行留模板，失败不能影响本次放行
            pass

    threading.Thread(target=_write, daemon=True).start()


def _save_live_template_async(image, directory, cv2):
    """保存开售态「立即购买」按钮模板（只存一次），下次开抢匹配更快。"""

    def _write():
        try:
            directory.mkdir(parents=True, exist_ok=True)
            destination = directory / "buy-live.png"
            if destination.exists():
                return
            cv2.imwrite(str(destination), image)
            print(f"已保存开售态底栏模板：{destination.name}")
        except Exception:
            # 模板只是下次提速，失败不影响本次放行
            pass

    threading.Thread(target=_write, daemon=True).start()


NEGATIVE_TEMPLATE_PREFIX = "negative-"


def _load_scrcpy_templates(cv2, template_dir=None):
    """正面模板：只加载详情页底栏模板，排除负向与票档页「确定」模板。"""
    directory = Path(template_dir) if template_dir else SCRCPY_TEMPLATE_DIR
    if not directory.is_dir():
        return []
    templates = []
    for path in sorted(directory.glob("*.png")):
        if path.stem.startswith(NEGATIVE_TEMPLATE_PREFIX):
            continue
        if path.stem.startswith("sku-"):
            continue
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is not None and image.size:
            templates.append((path.stem, image))
    return templates


def _load_named_templates(cv2, prefix, template_dir=None):
    directory = Path(template_dir) if template_dir else SCRCPY_TEMPLATE_DIR
    if not directory.is_dir():
        return []
    templates = []
    for path in sorted(directory.glob(f"{prefix}*.png")):
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is not None and image.size:
            templates.append((path.stem, image))
    return templates


def _load_sku_confirm_templates(cv2, template_dir=None):
    """票档页「确定」按钮模板：只加载 sku-confirm 前缀。"""
    return _load_named_templates(cv2, SKU_CONFIRM_PREFIX, template_dir)


def _load_submit_templates(cv2, template_dir=None):
    """确认页「立即提交」按钮模板：只加载 sku-submit 前缀。"""
    return _load_named_templates(cv2, SUBMIT_TEMPLATE_PREFIX, template_dir)


def _load_scrcpy_negatives(cv2, template_dir=None):
    """负向模板：命中“已预约/未开售”时拒绝放行。"""
    directory = Path(template_dir) if template_dir else SCRCPY_TEMPLATE_DIR
    if not directory.is_dir():
        return []
    negatives = []
    for path in sorted(
        directory.glob(f"{NEGATIVE_TEMPLATE_PREFIX}*.png")
    ):
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is not None and image.size:
            negatives.append((path.stem, image))
    return negatives


def _scrcpy_template_hit(crop, templates, cv2, stats=None):
    if not templates:
        return False
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    best = -1.0
    for name, template in templates:
        for scale in (0.5, 0.59, 0.67, 0.8, 1.0, 1.25, 1.5, 2.0):
            width = max(1, int(template.shape[1] * scale))
            height = max(1, int(template.shape[0] * scale))
            if width > gray.shape[1] or height > gray.shape[0]:
                continue
            resized = cv2.resize(template, (width, height), interpolation=cv2.INTER_AREA)
            scores = cv2.matchTemplate(gray, resized, cv2.TM_CCOEFF_NORMED)
            score = float(scores.max())
            if stats is not None and score > best:
                best = score
                stats.update({"score": score, "template": name, "scale": scale})
            if score >= SCRCPY_TEMPLATE_THRESHOLD:
                return True
    return False


def _sku_search_crop(frame, focus_point, screen_size, cv2):
    """裁票档页底部「确定」按钮附近区域，完全不含左侧价格块。"""
    if not focus_point or not screen_size or not all(screen_size):
        return None
    height, width = frame.shape[:2]
    center_x = int(focus_point[0] * width / screen_size[0])
    center_y = int(focus_point[1] * height / screen_size[1])
    half_width = max(60, int(width * SKU_SEARCH_HALF_WIDTH))
    half_height = max(20, int(height * SKU_SEARCH_HALF_HEIGHT))
    left = max(0, center_x - half_width)
    right = min(width, center_x + half_width)
    top = max(0, center_y - half_height)
    bottom = min(height, center_y + half_height)
    if right <= left or bottom <= top:
        return None
    return frame[top:bottom, left:right]


def _watch_fixed_button(
    stream,
    focus_point,
    screen_size,
    timeout: float,
    templates,
) -> bool:
    """固定按钮区域是否已出现且可用；超时返回 False。"""
    import cv2

    if not templates:
        return False
    deadline = time.monotonic() + max(timeout, 0.0)
    while time.monotonic() < deadline:
        frame = stream.read_latest()
        if frame is None:
            time.sleep(0.002)
            continue
        crop = _sku_search_crop(frame, focus_point, screen_size, cv2)
        if crop is None:
            continue
        if _scrcpy_template_hit(crop, templates, cv2) and _button_is_enabled(
            frame, focus_point, screen_size, cv2
        ):
            return True
    return False


def watch_sku_confirm(
    stream,
    focus_point,
    screen_size,
    timeout: float,
    template_dir=None,
) -> bool:
    """票档页「确定」按钮是否已出现且可用；模板缺失时立即返回 False。"""
    import cv2

    return _watch_fixed_button(
        stream,
        focus_point,
        screen_size,
        timeout,
        _load_sku_confirm_templates(cv2, template_dir),
    )


def watch_submit_ready(
    stream,
    focus_point,
    screen_size,
    timeout: float,
    template_dir=None,
) -> bool:
    """确认页「立即提交」按钮是否已出现且可用；模板缺失时立即返回 False。"""
    import cv2

    return _watch_fixed_button(
        stream,
        focus_point,
        screen_size,
        timeout,
        _load_submit_templates(cv2, template_dir),
    )


def make_scrcpy_watcher(
    device: str,
    focus_point=None,
    screen_size=None,
    template_dir=None,
    keep_alive: bool = False,
):
    """接收 Scrcpy 最新帧；模板命中即放行，开售时刻后模板未命中时自动补采放行。"""
    try:
        import cv2
        from scrcpy_stream import ScrcpyError, ScrcpyStream
    except ImportError as exc:
        raise FlipWatchError(f"Scrcpy 运行依赖不可用：{exc}") from exc

    template_root = Path(template_dir) if template_dir else SCRCPY_TEMPLATE_DIR
    templates = _load_scrcpy_templates(cv2, template_dir)
    negatives = _load_scrcpy_negatives(cv2, template_dir)
    if not templates:
        raise FlipWatchError(
            f"未找到可购买按钮模板：{template_root}；请先校准可购买详情页"
        )

    stream = ScrcpyStream(device)
    try:
        stream.start()
    except (ScrcpyError, OSError) as exc:
        stream.stop()
        raise FlipWatchError(str(exc)) from exc

    def watch(remaining: float, post_sale_timeout: float = 0.0) -> bool:
        started = time.monotonic()
        target_at = started + max(remaining, 0.0)
        deadline = target_at + max(post_sale_timeout, 0.0)
        previous = None
        baseline = None
        last_change_at = None
        dim_warned = False
        negative_warned = False
        best_after_target = -1.0
        best_after_scale = None
        # 没有按钮坐标时无法确认翻转区域，不启用自动补采放行
        auto_capture = bool(focus_point and screen_size and all(screen_size))
        try:
            while True:
                now = time.monotonic()
                if now >= deadline:
                    if best_after_target >= 0:
                        print(
                            f"底栏模板最高分 {best_after_target:.3f}"
                            f"（阈值 {SCRCPY_TEMPLATE_THRESHOLD}，"
                            f"scale={best_after_scale}），未放行"
                        )
                    return False
                frame = stream.read_latest()
                if frame is None:
                    time.sleep(0.005)
                    continue
                crop = _scrcpy_crop(frame, focus_point, screen_size)
                small = _small_gray(crop, cv2)
                frame_diff = (
                    0.0
                    if previous is None
                    else float(cv2.absdiff(small, previous).mean())
                )
                changed = previous is None or frame_diff >= SCRCPY_DIFF_THRESHOLD
                if baseline is None or (
                    now < target_at and frame_diff <= AUTO_CAPTURE_STABLE_DIFF
                ):
                    # 基线取开售前最后一个稳定画面，避免开售前的页面刷新影响判断
                    baseline = small
                previous = small
                if changed:
                    last_change_at = now
                if changed and negatives and _scrcpy_template_hit(
                    crop, negatives, cv2
                ):
                    if not negative_warned:
                        print(
                            "模板命中，但底栏是“已预约/未开售”，"
                            "继续等待翻转为“立即购买/立即购票”"
                        )
                        negative_warned = True
                    continue
                stats = {}
                hit = changed and _scrcpy_template_hit(
                    crop, templates, cv2, stats=stats
                )
                if changed and now >= target_at and stats.get("score", -1) > best_after_target:
                    best_after_target = stats["score"]
                    best_after_scale = stats.get("scale")
                if hit:
                    if not _button_is_enabled(frame, focus_point, screen_size, cv2):
                        if not dim_warned:
                            print("模板命中，但底栏仍是灰暗色（未开售/已预约），继续等待翻转")
                            dim_warned = True
                        continue
                    if now >= target_at:
                        print(
                            f"底栏模板命中：score={stats.get('score', 0):.3f} "
                            f"scale={stats.get('scale')}，"
                            f"开售后 {(now - target_at) * 1000:.0f} 毫秒"
                        )
                        live_image = _buy_button_crop(
                            frame, focus_point, screen_size, cv2
                        )
                        if auto_capture and live_image is not None:
                            _save_live_template_async(
                                live_image, template_root, cv2
                            )
                    return True
                if (
                    not auto_capture
                    or now < target_at
                    or last_change_at is None
                    or last_change_at < target_at
                ):
                    continue
                if not _button_is_enabled(frame, focus_point, screen_size, cv2):
                    continue
                delta = float(cv2.absdiff(small, baseline).mean())
                if (
                    delta < AUTO_CAPTURE_DIFF_THRESHOLD
                    or frame_diff > AUTO_CAPTURE_STABLE_DIFF
                ):
                    continue
                template_image = _buy_button_crop(
                    frame, focus_point, screen_size, cv2
                )
                if template_image is not None:
                    _save_auto_template_async(template_image, template_root, cv2)
                print(
                    "模板未命中，开售后底栏已稳定翻转："
                    "自动补采当前按钮为模板并立即放行"
                )
                return True
            return False
        except Exception as exc:
            if isinstance(exc, FlipWatchError):
                raise
            raise FlipWatchError(
                f"Scrcpy 视频感知失败（{type(exc).__name__}: {exc}）"
            ) from exc
        finally:
            if not keep_alive:
                stream.stop()

    # 保持视频流存活时，把句柄交给调用方，由抢票流程在提交/超时/异常时统一关闭
    watch.stream = stream
    watch.close = stream.stop
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
