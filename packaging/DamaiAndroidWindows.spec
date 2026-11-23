# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path.cwd()
PROJECT_ROOT = ROOT

ocr_datas, ocr_binaries, ocr_hiddenimports = collect_all("rapidocr_onnxruntime")
hiddenimports = [
    "damai_appium.config",
    "damai_appium.damai_app_v2",
    "flip_watch",
    "scrcpy_stream",
    "av",
    "cv2",
    "adbutils",
] + ocr_hiddenimports

analysis = Analysis(
    [str(ROOT / "desktop_app.py")],
    pathex=[str(ROOT), str(PROJECT_ROOT)],
    binaries=ocr_binaries,
    datas=[
        (str(ROOT / "static"), "static"),
        (str(ROOT / "resources"), "resources"),
    ] + ocr_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["damai", "pytest", "unittest.mock"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="DamaiAndroid",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
collection = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DamaiAndroid",
)
