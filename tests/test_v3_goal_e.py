"""v3 目标审计 E 批回归：广覆盖进入执行的机器可检部分（离线夹具）。"""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MandatoryQualityAttributes(unittest.TestCase):
    def setUp(self):
        self.tool = load("exp_e", "scripts/exploration_state.py")
        self.base = {
            "id": "EXP-TRAV-01", "requirement_ids": ["REQ-1"],
            "page_url": "https://example.invalid/app", "role": "编辑者", "viewport": "1440x900",
            "state": "打开", "action": "走查", "risk": "high", "status": "PASS",
            "observation": "已观察", "evidence_paths": [str((ROOT / "README.md").resolve())],
            "blocked_reason": "", "side_effect_pending": False,
            "attributes": ["functional", "visual", "usability", "accessibility", "performance"],
        }
        self.data = {"schema_version": "1.0",
                     "context": {"target_url": "https://example.invalid/app", "application_version": "b", "environment": "test"},
                     "items": [dict(self.base)]}

    def gate(self, extra):
        self.data["items"] = [dict(self.base), extra]
        return self.tool.summarize(self.data)["gate"]

    def test_content_attribute_is_supported(self):
        row = dict(self.base, id="EXP-CONTENT-01", attributes=["content"], status="PASS")
        self.data["items"] = [dict(self.base), row]
        # 能被合法接受即证明 content 属性进入词汇表（items 按风险/ID 排序，按集合判断）
        ids = {item["id"] for item in self.tool.summarize(self.data)["items"]}
        self.assertIn("EXP-CONTENT-01", ids)

    def test_blocked_content_copywriting_lowers_ceiling(self):
        row = dict(self.base, id="EXP-CONTENT-01", status="BLOCKED", attributes=["content"],
                   blocked_reason="术语表未提供")
        g = self.gate(row)
        self.assertNotEqual(g["verdict_ceiling"], "通过", "文案必选属性被阻塞不得放行结论上限")

    def test_compat_blocked_lowers_ceiling(self):
        row = dict(self.base, id="EXP-COMPAT-01", status="BLOCKED", attributes=["compatibility"],
                   blocked_reason="缺 Firefox 环境")
        g = self.gate(row)
        self.assertNotEqual(g["verdict_ceiling"], "通过")


if __name__ == "__main__":
    unittest.main()
