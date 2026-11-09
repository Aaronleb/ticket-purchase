# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

ROOT = Path.cwd()
PROJECT_ROOT = ROOT

hiddenimports = [
    "damai_appium.config",
    "damai_appium.damai_app_v2",
    "damai_appium.ticket_selection",
    "rapidocr_onnxruntime.ch_ppocr_v3_det.text_detect",
    "rapidocr_onnxruntime.ch_ppocr_v3_rec.text_recognize",
    "rapidocr_onnxruntime.ch_ppocr_v2_cls.text_cls",
]

analysis = Analysis(
    [str(ROOT / "desktop_app.py")],
    pathex=[str(ROOT), str(PROJECT_ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "static"), "static"),
        *collect_data_files("rapidocr_onnxruntime"),
    ],
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
