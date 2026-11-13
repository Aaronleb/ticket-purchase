#!/bin/sh
# 编译屏幕文字识别工具（macOS Vision），供安卓脚本定位自绘控件。
set -e
cd "$(dirname "$0")"
swiftc -O damai_ocr.swift -o damai_ocr
echo "已生成 $(pwd)/damai_ocr"
