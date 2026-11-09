import json
import tempfile
import unittest
from pathlib import Path

import pytest

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
        "if_commit_order": False,
    }


class AndroidConfigValidationTests(unittest.TestCase):
    def test_valid_config_is_normalized(self):
        payload = valid_android_config()
        payload["users"] = [" 测试观演人 ", "测试观演人"]
        config = validate_android_config(payload)
        self.assertEqual(config["server_url"], "http://127.0.0.1:4723")
        self.assertEqual(config["users"], ["测试观演人"])

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

    def test_requires_user_and_city(self):
        payload = valid_android_config()
        payload["users"] = []
        with self.assertRaisesRegex(ConfigError, "观演人"):
            validate_android_config(payload)
        payload = valid_android_config()
        payload["city"] = " "
        with self.assertRaisesRegex(ConfigError, "城市"):
            validate_android_config(payload)

    def test_sale_time_is_normalized(self):
        payload = valid_android_config()
        payload["sale_time"] = "2026-12-01T20:30"
        config = validate_android_config(payload)
        self.assertEqual(config["sale_time"], "2026-12-01T20:30:00")

    def test_blank_sale_time_preserves_immediate_mode(self):
        config = validate_android_config(valid_android_config())
        self.assertEqual(config["sale_time"], "")


@pytest.mark.parametrize(
    "value",
    [123, "2026-02-30T20:00", "2026-12-01T20:00:00Z", "2026-12-01"],
)
def test_rejects_invalid_sale_time(value):
    payload = valid_android_config()
    payload["sale_time"] = value
    with pytest.raises(ConfigError, match="开售时间"):
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
        payload = valid_android_config()
        payload["sale_time"] = "2026-12-01T20:30"
        expected = self.store.save(payload)
        actual = self.store.load()
        self.assertEqual(actual, expected)
        self.assertEqual(actual["sale_time"], "2026-12-01T20:30:00")
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
