import json
import tempfile
import unittest
from pathlib import Path

from android_config import AndroidConfigStore, validate_android_config
from config_store import ConfigError


def valid_android_config():
    return {
        "server_url": "http://127.0.0.1:4723/",
        "device_name": "emulator-5554",
        "platform_version": "16",
        "keyword": "",
        "users": ["测试观演人"],
        "city": "杭州",
        "date": "",
        "price": "",
        "price_index": 1,
        "ticket_count": 2,
        "sell_start_time": "2026-11-13T20:00:00",
        "if_commit_order": False,
    }


class AndroidConfigValidationTests(unittest.TestCase):
    def test_valid_config_is_normalized(self):
        payload = valid_android_config()
        payload["users"] = [" 测试观演人 ", "测试观演人"]
        config = validate_android_config(payload)
        self.assertEqual(config["server_url"], "http://127.0.0.1:4723")
        self.assertEqual(config["users"], ["测试观演人"])
        self.assertEqual(config["sell_start_time"], "2026-11-13T20:00:00+08:00")

    def test_rejects_invalid_server_url(self):
        payload = valid_android_config()
        payload["server_url"] = "file:///tmp/appium"
        with self.assertRaisesRegex(ConfigError, "Appium 地址"):
            validate_android_config(payload)

    def test_rejects_negative_price_index(self):
        payload = valid_android_config()
        payload["price_index"] = -1
        with self.assertRaisesRegex(ConfigError, "price_index"):
            validate_android_config(payload)

    def test_allows_empty_legacy_users_and_city_for_reservation(self):
        payload = valid_android_config()
        payload["users"] = []
        payload["city"] = " "
        config = validate_android_config(payload)
        self.assertEqual(config["users"], [])
        self.assertEqual(config["city"], "")
        self.assertEqual(config["ticket_count"], 2)

    def test_rejects_invalid_ticket_count(self):
        for value in (0, 7, 1.5, True, "2"):
            with self.subTest(value=value):
                payload = valid_android_config()
                payload["ticket_count"] = value
                with self.assertRaisesRegex(ConfigError, "ticket_count"):
                    validate_android_config(payload)

    def test_requires_valid_sale_time(self):
        for value in ("", None, "not-a-time", 123):
            with self.subTest(value=value):
                payload = valid_android_config()
                payload["sell_start_time"] = value
                with self.assertRaisesRegex(ConfigError, "抢购时间"):
                    validate_android_config(payload)


class AndroidConfigStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.appium_dir = self.root / "damai_appium"
        self.appium_dir.mkdir()
        (self.appium_dir / "damai_app_v2.py").write_text("print('test')\n", encoding="utf-8")
        self.store = AndroidConfigStore(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def test_save_and_load_round_trip(self):
        expected = self.store.save(valid_android_config())
        actual = self.store.load()
        self.assertEqual(actual, expected)
        raw = json.loads((self.appium_dir / "config.jsonc").read_text(encoding="utf-8"))
        self.assertEqual(raw["device_name"], "emulator-5554")

    def test_missing_android_project_has_actionable_error(self):
        store = AndroidConfigStore(self.root / "missing")
        with self.assertRaisesRegex(ConfigError, "安卓 Appium 项目"):
            store.ensure_project()

    def test_packaged_store_uses_writable_path_and_managed_server(self):
        config_path = self.root / "user-data" / "config.jsonc"
        store = AndroidConfigStore(
            self.root,
            config_path=config_path,
            managed_server_url="http://127.0.0.1:49152/",
        )
        payload = valid_android_config()
        payload["server_url"] = "https://external.example.com"
        saved = store.save(payload)
        self.assertEqual(saved["server_url"], "http://127.0.0.1:49152")
        self.assertTrue(config_path.is_file())
        self.assertEqual(store.load()["server_url"], "http://127.0.0.1:49152")


if __name__ == "__main__":
    unittest.main()
