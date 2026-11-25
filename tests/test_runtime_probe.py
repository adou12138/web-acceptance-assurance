"""D1 runtime_probe.py 专项测试（v1.49.0 方案 §D1 + X-03/X-07 修订口径）。

验收基准：
- 无 playwright-core 环境返回结构化三态（不抛异常、不安装、不启动）；
- http_contract 运行时依赖恒具备、available 取决于 manifest 登记（X-07）；
- auth_state 只查 profile 存在/可写、有效性恒 unprobed（X-07：不做读回）；
- manifest 校验：declared_at 必填、声明字段空串非法（runtime-manifest/1）；
- 单通道探测异常记 missing，脚本不崩（纯探测不因环境怪异而崩）。
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.runtime_probe as rp


class RuntimeProbeStructuralTests(unittest.TestCase):
    def test_probe_without_manifest_is_structured_and_raises_nothing(self):
        result = rp.probe(None)
        self.assertEqual(result["schema"], "runtime-capabilities/1")
        self.assertFalse(result["manifest_present"])
        for name in rp.CHANNELS:
            entry = result["channels"][name]
            self.assertIn(entry["state"], rp.STATES, name)
            self.assertTrue(isinstance(entry["reason"], str) and entry["reason"], name)
            self.assertIn("recovery", entry, name)
        self.assertIn("page_interaction", result["degradation_chain"])

    def test_all_channel_keys_present(self):
        result = rp.probe(None)
        self.assertEqual(set(result["channels"]), set(rp.CHANNELS))

    def test_channel_exception_becomes_missing_not_crash(self):
        """通道内部探测异常记 missing 并留痕——纯探测脚本不因环境怪异而崩。"""
        with mock.patch.object(rp, "_playwright_require_ok",
                               return_value=(True, "chromium")), \
             mock.patch.object(rp, "_find_chrome", side_effect=RuntimeError("boom")):
            result = rp.probe({"declared_at": "2026-11-25T00:00:00Z"})
        entry = result["channels"]["browser_selfhost"]
        self.assertEqual(entry["state"], "missing")
        self.assertIn("boom", entry["reason"])


class RuntimeProbeChannelTests(unittest.TestCase):
    def test_http_contract_available_depends_on_manifest(self):
        """X-07：http_contract 运行时依赖恒具备，available 取决于 manifest 登记。"""
        unprobed = rp.probe({"declared_at": "t"})["channels"]["http_contract"]
        self.assertEqual(unprobed["state"], "unprobed")
        self.assertIn("target_origin", unprobed["recovery"])
        available = rp.probe({"declared_at": "t",
                              "target_origin": "https://example.com"})["channels"]["http_contract"]
        self.assertEqual(available["state"], "available")
        self.assertIn("https://example.com", available["reason"])

    def test_auth_state_only_checks_profile_dir_writability(self):
        """X-07：auth_state 只查 profile 存在/可写；available 仅表示「可尝试登录」，
        有效性保持 unprobed——不做任何登录态读回。"""
        with tempfile.TemporaryDirectory() as tmp:
            entry = rp.probe({"declared_at": "t", "profile_dir": tmp})["channels"]["auth_state"]
            self.assertEqual(entry["state"], "available")
            self.assertIn("有效性", entry["reason"])
            self.assertIn("unprobed", entry["reason"])
        entry = rp.probe({"declared_at": "t",
                          "profile_dir": "Z:/no/such/dir"})["channels"]["auth_state"]
        self.assertEqual(entry["state"], "missing")
        self.assertTrue(entry["recovery"])

    def test_auth_state_unprobed_without_profile(self):
        entry = rp.probe({"declared_at": "t"})["channels"]["auth_state"]
        self.assertEqual(entry["state"], "unprobed")
        self.assertIn("profile_dir", entry["recovery"])

    def test_browser_selfhost_missing_without_playwright(self):
        """无 playwright-core：missing 且 recovery 指向宿主运行时——不安装不启动。"""
        with mock.patch.object(rp, "_playwright_require_ok",
                               return_value=(False, "simulated: not installed")):
            entry = rp.probe({"declared_at": "t"})["channels"]["browser_selfhost"]
        self.assertEqual(entry["state"], "missing")
        self.assertIn("playwright-core", entry["reason"])
        self.assertIn("不自动安装", entry["recovery"])

    def test_browser_selfhost_missing_without_chrome(self):
        """playwright 可 require 但 Chrome 未命中：missing + 声明 chrome_path 的 recovery。"""
        with mock.patch.object(rp, "_playwright_require_ok",
                               return_value=(True, "chromium")), \
             mock.patch.object(rp, "_find_chrome", return_value=None):
            entry = rp.probe({"declared_at": "t"})["channels"]["browser_selfhost"]
        self.assertEqual(entry["state"], "missing")
        self.assertIn("chrome_path", entry["recovery"])

    def test_browser_selfhost_missing_without_profile(self):
        """playwright+Chrome 齐但无 profile 声明：missing（登录态无处落盘）。"""
        with mock.patch.object(rp, "_playwright_require_ok",
                               return_value=(True, "chromium")), \
             mock.patch.object(rp, "_find_chrome", return_value="C:/fake/chrome.exe"):
            entry = rp.probe({"declared_at": "t"})["channels"]["browser_selfhost"]
        self.assertEqual(entry["state"], "missing")
        self.assertIn("profile_dir", entry["recovery"])

    def test_browser_mcp_is_always_unprobed(self):
        """MCP 工具存在于会话内，脚本层恒 unprobed——由会话按 §一判定。"""
        entry = rp.probe({"declared_at": "t"})["channels"]["browser_mcp"]
        self.assertEqual(entry["state"], "unprobed")
        self.assertIn("browser-connection.md", entry["reason"])

    def test_design_data_missing_yields_datamissing_recovery(self):
        entry = rp.probe({"declared_at": "t"})["channels"]["design_data"]
        self.assertEqual(entry["state"], "missing")
        self.assertIn("dataMissing", entry["recovery"])

    def test_design_data_available_via_token_env(self):
        with mock.patch.dict("os.environ", {"FIGMA_TOKEN": "tok"}):
            entry = rp.probe({"declared_at": "t"})["channels"]["design_data"]
        self.assertEqual(entry["state"], "available")


class RuntimeManifestValidationTests(unittest.TestCase):
    def test_rejects_non_object(self):
        with self.assertRaises(ValueError):
            rp.validate_manifest([])

    def test_rejects_missing_declared_at(self):
        with self.assertRaises(ValueError):
            rp.validate_manifest({"target_origin": "https://x"})

    def test_rejects_empty_declared_string_fields(self):
        with self.assertRaises(ValueError):
            rp.validate_manifest({"declared_at": "t", "profile_dir": "  "})

    def test_rejects_bad_authorized_roles(self):
        with self.assertRaises(ValueError):
            rp.validate_manifest({"declared_at": "t", "authorized_roles": [1, 2]})

    def test_rejects_bad_read_only_budget(self):
        with self.assertRaises(ValueError):
            rp.validate_manifest({"declared_at": "t", "read_only_budget": 30})

    def test_accepts_minimal_valid_manifest(self):
        payload = rp.validate_manifest({"declared_at": "2026-11-25T00:00:00Z"})
        self.assertEqual(payload["declared_at"], "2026-11-25T00:00:00Z")

    def test_accepts_full_manifest(self):
        payload = rp.validate_manifest({
            "declared_at": "2026-11-25T00:00:00Z",
            "target_origin": "https://example.com",
            "profile_dir": "C:/profiles/test",
            "chrome_path": "C:/chrome.exe",
            "playwright_core_path": "C:/pw",
            "authorized_roles": ["editor"],
            "read_only_budget": {"probes": 20, "minutes": 30, "writes": 0},
        })
        self.assertEqual(payload["target_origin"], "https://example.com")


if __name__ == "__main__":
    unittest.main()