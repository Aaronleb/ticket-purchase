#!/usr/bin/env python3
"""把目录打包成带 UTF-8 文件名标记的 zip，避免 Windows 解压中文乱码。"""
from __future__ import annotations

import argparse
import os
import sys
import zipfile
from pathlib import Path


def build(source: Path, archive: Path, compresslevel: int = 6) -> None:
    source = source.resolve()
    archive = archive.resolve()
    if not source.is_dir():
        raise SystemExit(f"目录不存在：{source}")
    archive.parent.mkdir(parents=True, exist_ok=True)
    base = source.parent
    with zipfile.ZipFile(
        archive, "w", zipfile.ZIP_DEFLATED, compresslevel=compresslevel
    ) as bundle:
        for dirpath, dirnames, filenames in os.walk(source):
            dirnames.sort()
            for filename in sorted(filenames):
                full_path = Path(dirpath) / filename
                arcname = os.path.relpath(full_path, base).replace(os.sep, "/")
                bundle.write(full_path, arcname)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--compresslevel", type=int, default=6)
    args = parser.parse_args(argv)
    build(args.source, args.archive, args.compresslevel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
