"""IMP-02 证据等级门禁：台账 result.json 可选字段 evidence_grade 的机械强制。

规则（与 scripts/blind_eval.py 的 grade != "E1" 不计检出口径对齐）：
- 未提供 evidence_grade 的旧 result.json 完全兼容（不校验、不报错）。
- status=PASS 且 evidence_grade 为 E2/E3 时拒收（ValueError，报错信息说明处置）。
- E2/E3 + 非 PASS 状态正常收。
"""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "execution_ledger.py"


class EvidenceGradeGateTests(unittest.TestCase):
    def setUp(self):
        if not SCRIPT.exists():
            self.fail("Missing feature: evidence grade gate on finish results")
        spec = importlib.util.spec_from_file_location("execution_ledger", SCRIPT)
        self.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tool)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.workspace = self.folder / "run"
        self.design = json.loads((ROOT / "examples" / "cases-example.json").read_text(encoding="utf-8"))
        self.context = {"target_url": "https://example.invalid/test", "environment": "test", "application_version": "build-1", "feature_flags": {"rename": True}, "role_scope": ["editor"], "dependency_mode": "sandbox"}
        self.evidence = self.folder / "observations.txt"
        self.evidence.write_text("Teaching fixture, not actual Web evidence", encoding="utf-8")
        self.tool.initialize(self.workspace, self.design, self.context, "ROUND-1", [])

    def begin(self, ident="ATT-1", case_id="TC-NAME-001"):
        return self.tool.begin(self.workspace, self.design, self.context, ident, case_id, "Create isolated project and save its name")

    def finish(self, ident="ATT-1", status="PASS", cause="none", evidence_grade=None):
        return self.tool.finish(self.workspace, self.design, self.context, ident, status, "Observed fixture result", [str(self.evidence)], [{"check": "项目重新打开后的名称", "actual": "新名称", "matched": status == "PASS"}], cause, [], "No real remote data created", evidence_grade=evidence_grade)

    def test_legacy_result_without_grade_is_accepted(self):
        """旧格式无 evidence_grade 字段：完全兼容，不校验不报错。"""
        self.begin()
        event = self.finish()
        self.assertEqual(event["status"], "PASS")
        self.assertNotIn("evidence_grade", event)
        self.assertEqual(self.tool.summarize(self.workspace, self.design, self.context)["counts"]["PASS"], 1)

    def test_pass_with_e1_is_accepted(self):
        """PASS + E1：正常收，并把等级写入 FINISH 事件。"""
        self.begin()
        event = self.finish(evidence_grade="E1")
        self.assertEqual(event["status"], "PASS")
        self.assertEqual(event["evidence_grade"], "E1")
        self.assertEqual(self.tool.summarize(self.workspace, self.design, self.context)["counts"]["PASS"], 1)

    def test_pass_with_e2_is_rejected_with_guidance(self):
        """PASS + E2：拒收，报错信息说明 PASS 须 E1 证据及处置方式。"""
        self.begin()
        with self.assertRaises(ValueError) as ctx:
            self.finish(evidence_grade="E2")
        message = str(ctx.exception)
        self.assertIn("PASS 须 E1 证据", message)
        self.assertIn("E2/E3 请记 BLOCKED 或降级", message)
        self.assertEqual(self.tool.summarize(self.workspace, self.design, self.context)["counts"]["PASS"], 0)

    def test_pass_with_e3_is_rejected(self):
        """PASS + E3（未执行）同样拒收。"""
        self.begin()
        with self.assertRaises(ValueError):
            self.finish(evidence_grade="E3")

    def test_blocked_with_e2_is_accepted(self):
        """BLOCKED + E2：非 PASS 状态正常收。"""
        self.begin()
        event = self.finish(status="BLOCKED", cause="environment", evidence_grade="E2")
        self.assertEqual(event["status"], "BLOCKED")
        self.assertEqual(event["evidence_grade"], "E2")
        self.assertEqual(self.tool.summarize(self.workspace, self.design, self.context)["counts"]["BLOCKED"], 1)

    def test_invalid_grade_value_is_rejected(self):
        """提供了 evidence_grade 但取值非法：显式报错（不静默忽略）。"""
        self.begin()
        with self.assertRaises(ValueError):
            self.finish(evidence_grade="E9")

    def test_grade_gate_rejects_before_journal_write(self):
        """拒收发生在写台账之前：日志里不留下被拒记录。"""
        self.begin()
        with self.assertRaises(ValueError):
            self.finish(evidence_grade="E2")
        self.assertEqual(len(self.tool.read_events(self.workspace)), 1)  # 仅 BEGIN


if __name__ == "__main__":
    unittest.main()
