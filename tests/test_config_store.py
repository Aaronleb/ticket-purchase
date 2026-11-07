import json
import tempfile
import unittest
from pathlib import Path

from config_store import ConfigError, ConfigStore, validate_config


def valid_config():
    return {
        "index_url": "https://www.damai.cn/",
        "login_url": "https://passport.damai.cn/login",
        "target_url": "https://detail.damai.cn/item.htm?id=123",
        "users": ["测试观演人"],
        "city": "杭州",
        "dates": ["2026-04-11"],
        "prices": ["680"],
        "if_listen": True,
        "if_commit_order": False,
        "max_retries": 1000,
        "fast_mode": True,
        "page_load_delay": 2,
    }


class ConfigValidationTests(unittest.TestCase):
    def test_valid_config_is_normalized_and_deduplicated(self):
        payload = valid_config()
        payload["users"] = [" 测试观演人 ", "测试观演人"]
        config = validate_config(payload)
        self.assertEqual(config["users"], ["测试观演人"])
        self.assertEqual(config["target_url"], payload["target_url"])

    def test_rejects_non_damai_target(self):
        payload = valid_config()
        payload["target_url"] = "https://example.com/item/123"
        with self.assertRaisesRegex(ConfigError, "大麦网"):
            validate_config(payload)

    def test_requires_at_least_one_user(self):
        payload = valid_config()
        payload["users"] = []
        with self.assertRaisesRegex(ConfigError, "观演人"):
            validate_config(payload)

    def test_rejects_invalid_switch_type(self):
        payload = valid_config()
        payload["if_commit_order"] = "false"
        with self.assertRaisesRegex(ConfigError, "布尔值"):
            validate_config(payload)


class ConfigStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "damai").mkdir()
        (self.root / "damai" / "damai.py").write_text("print('test')\n", encoding="utf-8")
        (self.root / "damai" / "check_environment.py").write_text("print('test')\n", encoding="utf-8")
        self.store = ConfigStore(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def test_save_and_load_round_trip(self):
        expected = self.store.save(valid_config())
        actual = self.store.load()
        self.assertEqual(actual, expected)
        raw = json.loads((self.root / "damai" / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["users"], ["测试观演人"])

    def test_missing_project_has_actionable_error(self):
        store = ConfigStore(self.root / "missing")
        with self.assertRaisesRegex(ConfigError, "未找到有效的大麦项目"):
            store.ensure_project()


if __name__ == "__main__":
    unittest.main()
