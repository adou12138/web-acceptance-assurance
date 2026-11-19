"""审核 B1 回归：豁免不得自授即通过（堵住对抗强制力的自助后门）。

对应《审核结论_角色定义与对抗性_v1.35.0》B1：任意理由字符串即可跳过全部对抗
并直接判"通过"。本测试锁定三项机械约束：
1. 豁免永远不能把结论上限抬到"通过"；
2. 含写操作（dependency_mode=real）时豁免不成立；
3. 用例数超阈时豁免不成立。
"""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load():
    spec = importlib.util.spec_from_file_location("ledger", ROOT / "scripts/execution_ledger.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WaiverBackdoorTests(unittest.TestCase):
    def setUp(self):
        self.mod = load()

    def test_waiver_never_reaches_pass(self):
        # 即使豁免成立，上限也只能是待核实，绝不能是通过
        info = self.mod._adversarial_precondition(None, "纯只读小任务", read_only=True, case_count=2)
        self.assertEqual(info["adversarial_state"], "waived")
        self.assertEqual(info["adversarial_ceiling"], "待核实")

    def test_waiver_rejected_when_writes_involved(self):
        info = self.mod._adversarial_precondition(None, "随便写个理由", read_only=False, case_count=1)
        self.assertEqual(info["adversarial_state"], "missing")
        self.assertEqual(info["adversarial_ceiling"], "待核实")
        self.assertTrue(any("含写操作" in r for r in info["adversarial_reasons"]))

    def test_waiver_rejected_when_case_count_exceeds(self):
        info = self.mod._adversarial_precondition(None, "理由", read_only=True,
                                                  case_count=self.mod.WAIVER_MAX_CASES + 1)
        self.assertEqual(info["adversarial_state"], "missing")
        self.assertTrue(any("超豁免阈" in r for r in info["adversarial_reasons"]))

    def test_waiver_rejected_without_reason(self):
        info = self.mod._adversarial_precondition(None, "", read_only=True, case_count=1)
        self.assertEqual(info["adversarial_state"], "missing")

    def test_empty_gate_with_valid_waiver_still_pending(self):
        empty = {"verdict_ceiling": "通过", "adversarial_enabled": False, "reasons": []}
        info = self.mod._adversarial_precondition(empty, "只读核对", read_only=True, case_count=1)
        self.assertEqual(info["adversarial_state"], "waived")
        self.assertEqual(info["adversarial_ceiling"], "待核实")

    def test_verified_gate_keeps_its_ceiling(self):
        ok = {"verdict_ceiling": "通过", "adversarial_enabled": True, "reasons": []}
        info = self.mod._adversarial_precondition(ok, None, read_only=False, case_count=99)
        self.assertEqual(info["adversarial_state"], "verified")
        self.assertEqual(info["adversarial_ceiling"], "通过")


if __name__ == "__main__":
    unittest.main()
