"""台账侧攻击覆盖前置：attack_coverage 结论必须能机械压制 verdict_ceiling。

对齐已有 phase_state / adversarial_gate 的接法：只认脚本产出的 verdict_ceiling 与
gaps，不接受人工改写的"覆盖已充分"声明；未传时 baseline 结构保持旧样。
"""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "execution_ledger.py"


class AttackPreconditionTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("execution_ledger", SCRIPT)
        self.ledger = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.ledger)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.design = json.loads((ROOT / "examples" / "cases-example.json").read_text(encoding="utf-8"))
        self.context = {"target_url": "https://example.invalid/test", "environment": "test",
                        "application_version": "b1", "feature_flags": {}, "role_scope": ["editor"],
                        "dependency_mode": "sandbox"}

    def init(self, name, attack=None):
        workspace = Path(self.tmp.name) / name
        baseline = self.ledger.initialize(workspace, self.design, self.context, "R1", [],
                                          attack_coverage=attack)
        return workspace, baseline

    def coverage(self, ceiling="通过", gaps=None, density=0.4):
        return {"verdict_ceiling": ceiling, "gaps": gaps or [], "attack_density": density}

    def test_missing_argument_keeps_backward_compatible_baseline(self):
        _, baseline = self.init("run-a")
        self.assertNotIn("attack_coverage_ceiling", baseline["gate_precondition"])

    def test_passed_coverage_is_recorded(self):
        _, baseline = self.init("run-b", self.coverage())
        gate = baseline["gate_precondition"]
        self.assertEqual(gate["attack_coverage_ceiling"], "通过")
        self.assertEqual(gate["attack_density"], 0.4)
        self.assertEqual(gate["attack_gaps"], [])

    def test_low_density_pushes_ceiling_to_pending(self):
        workspace, _ = self.init("run-c", self.coverage(
            "待核实", [{"code": "attack_density_low", "message": "密度 0.08 < 0.3"}]))
        report = self.ledger.summarize(workspace, self.design, self.context, None)
        self.assertEqual(report["verdict_ceiling"], "待核实")

    def test_gaps_are_surfaced_verbatim(self):
        gaps = [{"code": "oracle_self_attested", "message": "同源自证 27 条"}]
        _, baseline = self.init("run-d", self.coverage("待核实", gaps))
        self.assertEqual(baseline["gate_precondition"]["attack_gaps"], gaps)

    def test_invalid_coverage_payload_rejected(self):
        with self.assertRaises(ValueError):
            self.init("run-e", {"gaps": []})              # 缺 verdict_ceiling
        with self.assertRaises(ValueError):
            self.init("run-f", {"verdict_ceiling": "通过"})  # 缺 gaps
        with self.assertRaises(ValueError):
            self.init("run-g", ["not", "an", "object"])


if __name__ == "__main__":
    unittest.main()
