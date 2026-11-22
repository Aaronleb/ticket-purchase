#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读探测当前手机画面：把「我的预约」这类页面上的文本读出来。

用途：确认大麦 App 的预约信息（演出名/场次/票档/金额）是否出现在无障碍树里，
从而判断能不能不靠 OCR 直接读，以及金额字段长什么样。

只读：不点击、不滑动、不登录、不下单。读不到时可用 --ocr 退回截图识别。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OCR_TOOL = PROJECT_ROOT / "tools" / "damai_ocr"
AMOUNT_RE = re.compile(r"[¥￥]\s*\d[\d,]*(?:\.\d+)?")


def _adb(device: str, *args: str, timeout: float = 20.0) -> str:
    result = subprocess.run(
        ["adb", "-s", device, *args],
        capture_output=True, text=True, timeout=timeout,
    )
    return result.stdout


def foreground_activity(device: str) -> str:
    output = _adb(device, "shell", "dumpsys", "activity", "activities")
    match = re.search(r"topResumedActivity=.*?/([\w.$]+)", output)
    return match.group(1) if match else ""


def ui_dump(device: str) -> ET.Element | None:
    """拿无障碍树；/dev/tty 失败时退回写文件再 cat。"""
    for _ in range(2):
        output = _adb(device, "shell", "uiautomator", "dump", "--compressed", "/dev/tty")
        start, end = output.find("<?xml"), output.find("</hierarchy>")
        if start >= 0:
            xml = output[start:end + len("</hierarchy>")] if end > start else output[start:]
            try:
                return ET.fromstring(xml)
            except ET.ParseError:
                pass
        _adb(device, "shell", "uiautomator", "dump", "--compressed", "/sdcard/probe_ui.xml")
        output = _adb(device, "shell", "cat", "/sdcard/probe_ui.xml")
        start, end = output.find("<?xml"), output.find("</hierarchy>")
        if start >= 0:
            xml = output[start:end + len("</hierarchy>")] if end > start else output[start:]
            try:
                return ET.fromstring(xml)
            except ET.ParseError:
                pass
    return None


def dump_texts(device: str, root: ET.Element | None) -> dict:
    if root is None:
        return {"texts": [], "descriptions": [], "resource_ids": []}
    texts, descs, ids = [], [], []
    for node in root.iter():
        text = (node.get("text") or "").strip()
        desc = (node.get("content-desc") or "").strip()
        rid = (node.get("resource-id") or "").strip()
        if text:
            texts.append(text)
        if desc:
            descs.append(desc)
        if rid:
            ids.append(rid)
    def _dedup(items):
        seen, out = set(), []
        for item in items:
            if item not in seen:
                seen.add(item)
                out.append(item)
        return out
    return {
        "texts": _dedup(texts),
        "descriptions": _dedup(descs),
        "resource_ids": _dedup(ids),
    }


COUNT_RE = re.compile(r"^(\d+)\s*张$")
TIME_HINTS = ("明天", "后天", "今天", "月", "日", "开抢", ":")
TITLE_HINTS = ("演唱会", "音乐节", "巡演", "见面会", "话剧", "脱口秀")


def _text_rows(root: ET.Element) -> list[dict]:
    rows = []
    for node in root.iter():
        text = (node.get("text") or "").strip() or (node.get("content-desc") or "").strip()
        bounds = parse_bounds(node.get("bounds"))
        if text and bounds:
            rows.append({"text": text, "bounds": bounds, "y": bounds[1], "x": bounds[0]})
    rows.sort(key=lambda row: (row["y"], row["x"]))
    return rows


def parse_bounds(text: str | None):
    match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", text or "")
    return tuple(int(value) for value in match.groups()) if match else None


def _is_title(row: dict) -> bool:
    text = row["text"]
    return len(text) > 8 and (
        ("·" in text and row["x"] < 500) or any(hint in text for hint in TITLE_HINTS)
    )


def find_entry(root: ET.Element, keyword: str) -> dict | None:
    """在「我的预约/待抢票」列表里按关键词找一条，解析金额、张数、开抢时间。"""
    rows = _text_rows(root)
    title = next((row for row in rows if keyword in row["text"] and _is_title(row)), None)
    if title is None:
        title = next((row for row in rows if keyword in row["text"]), None)
    if title is None:
        return None

    card = []
    for row in rows:
        if row["y"] <= title["y"] or row["text"] == title["text"]:
            continue
        # 下一条演出标题出现即视为本卡片结束
        if _is_title(row) and row["x"] < 500:
            break
        card.append(row)

    amount = None
    count = 1
    when = ""
    for row in card:
        text = row["text"]
        if text in {"¥", "￥"}:
            continue
        matched_count = COUNT_RE.match(text)
        if matched_count:
            count = int(matched_count.group(1))
            continue
        if any(hint in text for hint in TIME_HINTS):
            if not when:
                when = text
            continue
        matched_amount = re.fullmatch(r"(\d+(?:\.\d+)?)", text)
        if matched_amount and amount is None:
            amount = float(matched_amount.group(1))

    return {
        "title": title["text"],
        "unit_amount": amount,
        "count": count,
        "total_amount": amount * count if amount is not None else None,
        "sale_time_text": when,
        "card_texts": [row["text"] for row in card],
    }


def ocr_texts(device: str) -> list[str]:
    """截图后交给项目自带 OCR；仅在无障碍树读不到时用。"""
    screenshot = Path(tempfile.gettempdir()) / "damai_probe.png"
    with open(screenshot, "wb") as handle:
        subprocess.run(
            ["adb", "-s", device, "exec-out", "screencap", "-p"],
            stdout=handle, timeout=30,
        )
    result = subprocess.run(
        [sys.executable, str(OCR_TOOL), str(screenshot)],
        capture_output=True, text=True, timeout=120,
    )
    rows = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 4:
            rows.append(parts[3])
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="只读探测当前画面的文本")
    parser.add_argument("--device", default="", help="设备序列号，默认读 config.jsonc")
    parser.add_argument("--ocr", action="store_true", help="无障碍树为空时用 OCR 兜底")
    parser.add_argument("--find", default="", help="按关键词解析一条预约记录（金额/张数/时间）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    device = args.device.strip()
    if not device:
        config_path = PROJECT_ROOT / "damai_appium" / "config.jsonc"
        try:
            device = str(json.loads(config_path.read_text("utf-8")).get("device_name", "")).strip()
        except (OSError, ValueError):
            device = ""
    if not device:
        print("未指定设备，且 config.jsonc 里没有 device_name", file=sys.stderr)
        return 2

    activity = foreground_activity(device)
    root = ui_dump(device)

    if args.find:
        entry = find_entry(root, args.find) if root is not None else None
        if entry is None:
            print(f"没有在当前页面的无障碍树里找到包含「{args.find}」的记录", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(entry, ensure_ascii=False, indent=2))
            return 0
        unit = f"{entry['unit_amount']:g} 元" if entry["unit_amount"] is not None else "未读到"
        total = f"{entry['total_amount']:g} 元" if entry["total_amount"] is not None else "未读到"
        print(f"匹配：{entry['title']}")
        print(f"单价：{unit}；张数：{entry['count']}；合计：{total}")
        print(f"开抢：{entry['sale_time_text'] or '未读到'}")
        print(f"卡片文本：{' | '.join(entry['card_texts'])}")
        return 0

    data = dump_texts(device, root)
    texts = data["texts"] + data["descriptions"]
    amounts = [a for text in texts for a in AMOUNT_RE.findall(text)]
    if args.ocr and not texts:
        texts = ocr_texts(device)
        amounts = [a for text in texts for a in AMOUNT_RE.findall(text)]

    payload = {
        "device": device,
        "activity": activity,
        "node_count": len(list(root.iter())) if root is not None else 0,
        "texts": texts,
        "amounts": amounts,
        "ocr_used": bool(args.ocr and not data["texts"]),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"设备：{device}")
    print(f"前台：{activity or '未知'}")
    print(f"无障碍节点数：{payload['node_count']}")
    print(f"是否退回 OCR：{'是' if payload['ocr_used'] else '否'}")
    print(f"金额字段：{', '.join(amounts) if amounts else '无'}")
    print("文本：")
    if not texts:
        print("  （空：这个页面很可能是 Canvas/H5 自绘，只能用 OCR）")
    for text in texts:
        print(f"  - {text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
