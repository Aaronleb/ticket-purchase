from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import signal
import sys
import threading
from pathlib import Path

from app import create_server
from appium_service import AppiumService


APP_VERSION = "1.0.0"


def default_resource_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent / "Resources"
    return Path(__file__).resolve().parent / "packaging" / "runtime"


def default_data_root() -> Path:
    return Path.home() / "Library" / "Application Support" / "DamaiAndroid"


def rewrite_appium_manifest(destination: Path) -> None:
    manifest = destination / "node_modules" / ".cache" / "appium" / "extensions.yaml"
    if not manifest.is_file():
        raise FileNotFoundError(f"未找到 Appium 扩展清单：{manifest}")
    lines = []
    package_name = None
    for line in manifest.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("pkgName:"):
            package_name = stripped.split(":", 1)[1].strip()
        if stripped.startswith("installPath:"):
            if not package_name:
                raise ValueError("Appium 扩展清单缺少 pkgName")
            install_path = destination / "node_modules" / package_name
            indent = line[: len(line) - len(line.lstrip())]
            line = f"{indent}installPath: {json.dumps(str(install_path), ensure_ascii=False)}"
        lines.append(line)
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")


def prepare_appium_home(template: Path, destination: Path) -> None:
    marker = destination / ".damai-app-version"
    if marker.is_file() and marker.read_text(encoding="utf-8").strip() == APP_VERSION:
        rewrite_appium_manifest(destination)
        return
    if not template.is_dir():
        raise FileNotFoundError(f"未找到内置 Appium 驱动：{template}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.parent / f".appium-home-{os.getpid()}"
    previous = destination.parent / f".appium-home-previous-{os.getpid()}"
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(previous, ignore_errors=True)
    shutil.copytree(template, staging)
    (staging / ".damai-app-version").write_text(f"{APP_VERSION}\n", encoding="utf-8")
    try:
        if destination.exists():
            os.replace(destination, previous)
        os.replace(staging, destination)
        rewrite_appium_manifest(destination)
        shutil.rmtree(previous, ignore_errors=True)
    except Exception:
        if previous.exists():
            if destination.exists():
                shutil.rmtree(destination)
            os.replace(previous, destination)
        raise


def configure_runtime_environment(node_path: Path, adb_path: Path) -> None:
    sdk_root = str(adb_path.parent)
    os.environ["ANDROID_HOME"] = sdk_root
    os.environ["ANDROID_SDK_ROOT"] = sdk_root
    os.environ["PATH"] = os.pathsep.join(
        [str(node_path.parent), str(adb_path.parent), os.environ.get("PATH", "")]
    )


def android_worker_command(resources: Path) -> list[str]:
    command = [sys.executable]
    if not getattr(sys, "frozen", False):
        command.extend(
            [
                str(Path(__file__).resolve()),
                "--android-worker",
                "--resources",
                str(resources),
            ]
        )
    else:
        command.append("--android-worker")
    return command


def run_android_worker() -> int:
    from damai_appium.damai_app_v2 import DamaiBot

    bot = DamaiBot()
    return 0 if bot.run_with_retry(max_retries=3) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="大麦安卓抢票 Mac 应用")
    parser.add_argument("--android-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-browser", "--no-window", dest="no_window", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--resources", type=Path, default=None)
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--startup-timeout", type=float, default=30)
    return parser


def request_window_close(window) -> None:
    if window.events.loaded.is_set():
        window.destroy()
        return
    if getattr(window, "_damai_close_pending", False):
        return
    window._damai_close_pending = True

    def close_after_load():
        window.events.loaded.wait()
        window.destroy()

    logging.info("等待页面初始化完成后退出")
    threading.Thread(target=close_after_load, name="damai-close-after-load", daemon=True).start()


def install_native_quit_handler(window):
    from AppKit import NSApplication, NSObject, NSTerminateCancel

    if not window.events.shown.wait(10):
        raise RuntimeError("应用窗口初始化超时")

    class ApplicationDelegate(NSObject):
        def applicationShouldTerminate_(self, app):
            logging.info("收到原生退出请求")
            request_window_close(window)
            return NSTerminateCancel

        def applicationSupportsSecureRestorableState_(self, app):
            return True

    delegate = ApplicationDelegate.alloc().init()
    window._damai_native_delegate = delegate
    NSApplication.sharedApplication().setDelegate_(delegate)
    return delegate


def install_window_close_guard(window) -> None:
    def close_when_loaded():
        if window.events.loaded.is_set():
            return True
        request_window_close(window)
        return False

    window.events.closing += close_when_loaded


def run_console(server, url: str, show_window: bool, webview_module=None) -> None:
    server_thread = None
    window = None
    stopping = False
    if show_window:
        server_thread = threading.Thread(
            target=server.serve_forever,
            name="damai-console",
            daemon=True,
        )
        server_thread.start()

    def request_shutdown(signum, frame):
        nonlocal stopping
        logging.info("收到退出信号：%s", signum)
        stopping = True
        if window is not None:
            request_window_close(window)
        else:
            threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    try:
        if not show_window:
            server.serve_forever()
            return
        if webview_module is None:
            import webview as webview_module
        window = webview_module.create_window(
            "大麦安卓抢票",
            url,
            width=1200,
            height=820,
            min_size=(960, 680),
        )
        install_window_close_guard(window)
        if not stopping:
            logging.info("正在显示应用窗口")
            webview_module.start(install_native_quit_handler, (window,))
            logging.info("窗口事件循环已结束")
    except Exception:
        logging.exception("应用窗口运行失败")
        raise
    finally:
        if show_window:
            logging.info("正在停止控制台服务")
            server.shutdown()
            server_thread.join(timeout=5)
            logging.info("控制台服务已停止")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    resources = (args.resources or default_resource_root()).resolve()
    if args.android_worker:
        if not getattr(sys, "frozen", False):
            sys.path.insert(0, str(resources / "runtime" / "ticket-purchase"))
        return run_android_worker()

    data_root = (args.data_dir or default_data_root()).resolve()
    logs = data_root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=logs / "console.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        encoding="utf-8",
    )

    runtime = resources / "runtime"
    node_path = runtime / "node" / "bin" / "node"
    adb_path = runtime / "android-platform-tools" / "adb"
    configure_runtime_environment(node_path, adb_path)

    try:
        prepare_appium_home(runtime / "appium-cli", data_root / "appium-home")
    except OSError as exc:
        logging.exception("无法准备 Appium 驱动")
        print(f"无法准备内置 Appium 驱动：{exc}", file=sys.stderr)
        return 1

    appium_entry = data_root / "appium-home" / "node_modules" / "appium" / "index.js"
    service = AppiumService(
        node_path,
        appium_entry,
        data_root / "appium-home",
        logs / "appium.log",
        startup_timeout=args.startup_timeout,
    )
    service.start()
    try:
        server = create_server(
            runtime / "ticket-purchase",
            args.port,
            android_only=True,
            android_config_path=data_root / "config.jsonc",
            android_command=android_worker_command(resources),
            managed_server_url=service.server_url,
            runtime_status=service.snapshot,
        )
    except OSError as exc:
        service.stop()
        logging.exception("无法启动控制台")
        print(f"无法启动控制台：{exc}", file=sys.stderr)
        return 1

    url = f"http://127.0.0.1:{server.server_address[1]}"
    logging.info("控制台地址：%s", url)
    print(f"大麦安卓控制台：{url}", flush=True)
    try:
        run_console(server, url, show_window=not args.no_window)
    finally:
        logging.info("正在停止抢票任务")
        server.application.manager.shutdown()
        logging.info("正在停止内置 Appium")
        service.stop()
        logging.info("正在关闭控制台监听")
        server.server_close()
        logging.info("所有后台服务已停止；存活线程：%s", [thread.name for thread in threading.enumerate()])
    logging.info("应用主函数完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
