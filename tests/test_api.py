import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from app import create_server
from test_config_store import valid_config


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "damai").mkdir()
        (self.root / "damai" / "damai.py").write_text("print('fake grab started', flush=True)\n", encoding="utf-8")
        (self.root / "damai" / "check_environment.py").write_text("print('check ok', flush=True)\n", encoding="utf-8")
        (self.root / "tools").mkdir()
        (self.root / "tools" / "account_login.py").write_text("print('login fake', flush=True)\n", encoding="utf-8")
        self.server = create_server(self.root, port=0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.application.manager.shutdown()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def request(self, method, path, payload=None, same_origin=True):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {}
        body = None
        if payload is not None:
            body = json.dumps(payload)
            headers["Content-Type"] = "application/json"
        if same_origin:
            headers["X-Requested-With"] = "fetch"
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        content_type = response.getheader("Content-Type", "")
        data = json.loads(raw) if "application/json" in content_type else raw.decode("utf-8")
        status = response.status
        connection.close()
        return status, data

    def test_static_page_and_loopback_binding(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        status, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("开始抢票", page)
        self.assertIn("/app.js", page)

    def test_config_round_trip(self):
        status, saved = self.request("PUT", "/api/config", valid_config())
        self.assertEqual(status, 200)
        self.assertEqual(saved["city"], "杭州")
        status, loaded = self.request("GET", "/api/config")
        self.assertEqual(status, 200)
        self.assertEqual(loaded["users"], ["测试观演人"])

    def test_mutation_requires_page_request_marker(self):
        status, error = self.request("PUT", "/api/config", valid_config(), same_origin=False)
        self.assertEqual(status, 400)
        self.assertIn("请求标识", error["error"])

    def test_one_click_start_executes_project_entry_and_reports_real_exit(self):
        status, started = self.request("POST", "/api/start", valid_config())
        self.assertEqual(status, 200)
        self.assertEqual(started["phase"], "grabbing")
        deadline = time.time() + 5
        while time.time() < deadline:
            _, current = self.request("GET", "/api/status")
            if current["phase"] == "idle":
                break
            time.sleep(0.03)
        self.assertEqual(current["phase"], "idle")
        self.assertEqual(current["exit_code"], 0)
        _, logs = self.request("GET", "/api/logs")
        self.assertIn("fake grab started", [entry["text"] for entry in logs["logs"]])
        self.assertIn("请在大麦确认订单状态", current["message"])


if __name__ == "__main__":
    unittest.main()
