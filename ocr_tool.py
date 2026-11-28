# -*- coding: UTF-8 -*-
"""截图 OCR：RapidOCR 的进程内封装与命令行接口。

开发模式下 ocr_tool 由 ``tools/damai_ocr`` 以子进程方式调用；PyInstaller
冻结后 ``sys.executable`` 是应用本体，不能再把脚本路径当参数传给它，所以
打包版直接在 worker 进程内调用 :func:`recognize`，同时复用已加载的模型。
"""
from __future__ import annotations

import sys
import threading

_lock = threading.Lock()
_engine = None

USAGE = "用法：damai_ocr <图片路径> | damai_ocr --daemon"


def _get_engine():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _engine = RapidOCR()
    return _engine


def recognize(path) -> list[tuple[int, int, float, str]]:
    """识别图片，返回 [(中心 x, 中心 y, 置信度, 文本)]；失败返回空列表。"""
    try:
        with _lock:
            result, _elapse = _get_engine()(str(path))
    except Exception:  # noqa: BLE001 - OCR 失败不应中断抢票流程
        return []
    rows: list[tuple[int, int, float, str]] = []
    for box, text, score in result or []:
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        if not xs or not ys:
            continue
        cx = int(sum(xs) / len(xs))
        cy = int(sum(ys) / len(ys))
        text = " ".join(str(text).split())
        if text:
            rows.append((cx, cy, float(score), text))
    return rows


def _iter_result_lines(rows) -> list[str]:
    return [f"{cx}\t{cy}\t{score:.3f}\t{text}" for cx, cy, score, text in rows]


def daemon() -> int:
    """常驻模式：从 stdin 逐行读图片路径，识别完输出结果 + ===END===。"""
    print("READY", flush=True)
    for line in sys.stdin:
        path = line.strip()
        if not path:
            continue
        for output in _iter_result_lines(recognize(path)):
            print(output, flush=True)
        print("===END===", flush=True)
    return 0


def main(argv=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        print(USAGE, file=sys.stderr)
        return 2
    if arguments[0] == "--daemon":
        return daemon()
    for output in _iter_result_lines(recognize(arguments[0])):
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
