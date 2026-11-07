import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import desktop_app


class DesktopAppTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.template = self.root / "template"
        self.template.mkdir()
        (self.template / "driver.txt").write_text("bundled", encoding="utf-8")
        self.manifest = self.template / "node_modules" / ".cache" / "appium" / "extensions.yaml"
        self.manifest.parent.mkdir(parents=True)
        self.manifest.write_text(
            "drivers:\n  uiautomator2:\n    pkgName: appium-uiautomator2-driver\n"
            "    installPath: /build/runtime/node_modules/appium-uiautomator2-driver\n",
            encoding="utf-8",
        )
        self.destination = self.root / "data" / "appium-home"

    def tearDown(self):
        self.temporary.cleanup()

    def test_prepare_appium_home_copies_writable_template(self):
        desktop_app.prepare_appium_home(self.template, self.destination)
        self.assertEqual((self.destination / "driver.txt").read_text(encoding="utf-8"), "bundled")
        self.assertEqual(
            (self.destination / ".damai-app-version").read_text(encoding="utf-8").strip(),
            desktop_app.APP_VERSION,
        )
        manifest = (self.destination / "node_modules" / ".cache" / "appium" / "extensions.yaml").read_text(encoding="utf-8")
        self.assertIn(str(self.destination), manifest)
        self.assertNotIn("/build/runtime", manifest)

    def test_prepare_appium_home_reuses_matching_version(self):
        desktop_app.prepare_appium_home(self.template, self.destination)
        marker = self.destination / "local-write"
        marker.write_text("keep", encoding="utf-8")
        desktop_app.prepare_appium_home(self.template, self.destination)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_runtime_environment_points_appium_to_bundled_adb(self):
        node = self.root / "runtime" / "node" / "bin" / "node"
        adb = self.root / "runtime" / "android-platform-tools" / "adb"
        with patch.dict(desktop_app.os.environ, {"PATH": "/usr/bin"}, clear=True):
            desktop_app.configure_runtime_environment(node, adb)
            self.assertEqual(desktop_app.os.environ["ANDROID_HOME"], str(adb.parent))
            self.assertEqual(desktop_app.os.environ["ANDROID_SDK_ROOT"], str(adb.parent))
            self.assertEqual(
                desktop_app.os.environ["PATH"],
                f"{node.parent}:{adb.parent}:/usr/bin",
            )

    def test_web_mode_worker_command_includes_entry_script_and_resources(self):
        with (
            patch.object(desktop_app.sys, "executable", "/python"),
            patch.object(desktop_app.sys, "frozen", False, create=True),
        ):
            self.assertEqual(
                desktop_app.android_worker_command(self.root),
                [
                    "/python",
                    str(Path(desktop_app.__file__).resolve()),
                    "--android-worker",
                    "--resources",
                    str(self.root),
                ],
            )

    def test_web_worker_adds_bundled_project_to_import_path(self):
        with (
            patch.object(desktop_app.sys, "path", []),
            patch.object(desktop_app.sys, "frozen", False, create=True),
            patch("desktop_app.run_android_worker", return_value=0),
        ):
            result = desktop_app.main(["--android-worker", "--resources", str(self.root)])
            self.assertEqual(result, 0)
            self.assertEqual(desktop_app.sys.path, [str(self.root.resolve() / "runtime" / "ticket-purchase")])

    def test_packaged_worker_command_uses_application_executable(self):
        with (
            patch.object(desktop_app.sys, "executable", "/Applications/DamaiAndroid"),
            patch.object(desktop_app.sys, "frozen", True, create=True),
        ):
            self.assertEqual(
                desktop_app.android_worker_command(self.root),
                ["/Applications/DamaiAndroid", "--android-worker"],
            )

    def test_worker_returns_nonzero_when_grabbing_fails(self):
        package = ModuleType("damai_appium")
        module = ModuleType("damai_appium.damai_app_v2")
        bot = MagicMock()
        bot.run_with_retry.return_value = False
        module.DamaiBot = MagicMock(return_value=bot)
        with patch.dict(sys.modules, {"damai_appium": package, "damai_appium.damai_app_v2": module}):
            self.assertEqual(desktop_app.run_android_worker(), 1)
        bot.run_with_retry.assert_called_once_with(max_retries=3)

    def test_window_mode_displays_console_and_stops_server_on_close(self):
        server = MagicMock()
        window = MagicMock()
        webview = ModuleType("webview")
        webview.create_window = MagicMock(return_value=window)
        webview.start = MagicMock()
        with patch("desktop_app.signal.signal"), patch("desktop_app.install_native_quit_handler") as quit_handler, patch("desktop_app.install_window_close_guard") as close_guard:
            desktop_app.run_console(server, "http://127.0.0.1:8765", True, webview)
        webview.create_window.assert_called_once_with(
            "大麦安卓抢票",
            "http://127.0.0.1:8765",
            width=1200,
            height=820,
            min_size=(960, 680),
        )
        close_guard.assert_called_once_with(window)
        webview.start.assert_called_once_with(quit_handler, (window,))
        server.serve_forever.assert_called_once_with()
        server.shutdown.assert_called_once_with()

    def test_window_start_failure_stops_server(self):
        server = MagicMock()
        webview = ModuleType("webview")
        webview.create_window = MagicMock(return_value=MagicMock())
        webview.start = MagicMock(side_effect=RuntimeError("window failed"))
        with patch("desktop_app.signal.signal"), patch("desktop_app.install_native_quit_handler"), patch("desktop_app.install_window_close_guard"):
            with self.assertRaisesRegex(RuntimeError, "window failed"):
                desktop_app.run_console(server, "http://127.0.0.1:8765", True, webview)
        server.shutdown.assert_called_once_with()

    def test_close_guard_defers_exit_until_page_is_loaded(self):
        class ClosingEvent:
            def __iadd__(self, callback):
                self.callback = callback
                return self

        closing = ClosingEvent()
        window = MagicMock()
        window.events.closing = closing
        window.events.loaded.is_set.return_value = False
        window._damai_close_pending = False
        thread = MagicMock()
        with patch("desktop_app.threading.Thread", return_value=thread) as thread_type:
            desktop_app.install_window_close_guard(window)
            self.assertFalse(closing.callback())
            self.assertFalse(closing.callback())
        thread_type.assert_called_once()
        self.assertTrue(thread_type.call_args.kwargs["daemon"])
        thread.start.assert_called_once_with()
        thread_type.call_args.kwargs["target"]()
        window.events.loaded.wait.assert_called_once_with()
        window.destroy.assert_called_once_with()
        window.events.loaded.is_set.return_value = True
        self.assertTrue(closing.callback())

    def test_native_quit_closes_window_before_termination(self):
        window = MagicMock()
        application = MagicMock()
        appkit = ModuleType("AppKit")
        appkit.NSTerminateCancel = 0
        appkit.NSApplication = MagicMock()
        appkit.NSApplication.sharedApplication.return_value = application

        class Delegate:
            @classmethod
            def alloc(cls):
                return cls()

            def init(self):
                return self

        appkit.NSObject = Delegate
        with patch.dict(sys.modules, {"AppKit": appkit}):
            delegate = desktop_app.install_native_quit_handler(window)
        with patch("desktop_app.request_window_close") as close_window:
            self.assertEqual(delegate.applicationShouldTerminate_(application), 0)
        close_window.assert_called_once_with(window)
        application.setDelegate_.assert_called_once_with(delegate)

    def test_headless_mode_does_not_create_window(self):
        server = MagicMock()
        webview = ModuleType("webview")
        webview.create_window = MagicMock()
        with patch("desktop_app.signal.signal"):
            desktop_app.run_console(server, "http://127.0.0.1:8765", False, webview)
        server.serve_forever.assert_called_once_with()
        webview.create_window.assert_not_called()

    def test_no_window_alias_is_supported(self):
        args = desktop_app.build_parser().parse_args(["--no-window"])
        self.assertTrue(args.no_window)


if __name__ == "__main__":
    unittest.main()
