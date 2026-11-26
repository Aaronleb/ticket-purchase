# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path.cwd()
PROJECT_ROOT = ROOT

ocr_datas, ocr_binaries, ocr_hiddenimports = collect_all("rapidocr_onnxruntime")
hiddenimports = [
    "damai_appium.config",
    "damai_appium.damai_app_v2",
    "activity_watch",
    "flip_watch",
    "scrcpy_stream",
    "ocr_tool",
    "av",
    "cv2",
    "adbutils",
] + ocr_hiddenimports

if sys.platform == "win32":
    # pywebview 的 Windows 后端在运行时动态导入，静态分析看不到
    hiddenimports += [
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
    ]

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

if sys.platform == "win32":
    # Windows 免安装版：onedir + 无控制台窗口，运行目录由构建脚本补 runtime/
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
else:
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
        argv_emulation=False,
        target_arch="arm64",
        codesign_identity=None,
        entitlements_file=None,
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
    app = BUNDLE(
        collection,
        name="DamaiAndroid.app",
        icon=None,
        bundle_identifier="cn.damai.androidcontroller",
        info_plist={
            "CFBundleDisplayName": "大麦安卓抢票",
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1",
            "NSHighResolutionCapable": True,
            "LSUIElement": False,
        },
    )
