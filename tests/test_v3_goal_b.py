"""v3 目标审计 B 批回归：分支覆盖可信性（离线、只读夹具）。

G01 场景交接契约 / G02 义务反查 / G03 可信存量 / G04 方法见证 / G05 PATH 必选分支。
"""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
CG = ROOT / "skills" / "case-generator"


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture():
    return json.loads((CG / "tests" / "fixtures" / "cases-sample-12.json").read_text(encoding="utf-8-sig"))


class ReviewScenarioContract(unittest.TestCase):
    def setUp(self):
        self.rc = load("rc_b", "skills/requirement-review/scripts/review_check.py")
        self.rb = load("rb_b", "skills/case-generator/scripts/review_bridge.py")
        self.review = json.loads((CG / "tests" / "fixtures" / "review-sample-pass.json").read_text(encoding="utf-8-sig"))

    def test_review_with_scenarios_is_accepted(self):
        review = copy.deepcopy(self.review)
        review["scenarios"] = [{"id": "SCN-01", "name": "保存流程", "goal": "完成保存", "paths": {
            "positive": {"PATH-01": {"description": "成功"}},
            "exception": {"PATH-02": {"description": "失败"}},
        }}]
        errors, _, _ = self.rc.check(review)
        self.assertEqual([e for e in errors if "scenarios" in e], [], "评审校验必须允许合法 scenarios 字段")

    def test_workflow_bridge_uses_review_scenarios(self):
        review = copy.deepcopy(self.review)
        review["scenarios"] = [{"id": "SCN-01", "name": "保存流程", "goal": "完成保存", "paths": {
            "positive": {"PATH-01": {"description": "成功"}},
            "exception": {"PATH-02": {"description": "失败"}},
        }}]
        skeleton, _ = self.rb.build_skeleton(review, "workflow")
        self.assertEqual(skeleton["scenarios"], review["scenarios"])


class ObligationBasedCoverage(unittest.TestCase):
    def setUp(self):
        self.cm = load("cm_b", "scripts/coverage_matrix.py")
        self.gc = load("gc_b", "skills/case-generator/scripts/gate_checks.py")
        self.policy = json.loads((CG / "config" / "policy.json").read_text(encoding="utf-8-sig"))

    def test_flags_alone_cannot_claim_l1_l2_when_obligations_declared(self):
        cases = fixture()
        cases["coverage_obligations"] = [
            {"id": "OBL-1", "layer": "L1", "kind": "path", "target": "FP-01", "source": "AC-01", "required": True, "tc_ids": []},
            {"id": "OBL-2", "layer": "L2", "kind": "field_boundary", "target": "FP-01.名称长度", "source": "AC-01", "required": True, "tc_ids": []},
        ]
        matrix = self.cm.build_matrix(cases, self.policy)
        self.assertFalse(matrix["layer_matrix"]["L1"]["covered"])
        self.assertFalse(matrix["layer_matrix"]["L2"]["covered"])
        self.assertTrue(any(o.get("id") == "OBL-1" for o in matrix.get("unmet_obligations", [])))

    def test_method_placeholder_witness_rejected(self):
        cases = fixture()
        for tc in cases["testcases"]:
            tc["method_evidence"] = {"scenario": "x", "boundary": True}
        matrix = self.cm.build_matrix(cases, self.policy)
        self.assertFalse(matrix["layer_matrix"]["L3"]["covered"], "布尔/字符串伪见证不得计入 L3")


class LegacyAndApplicability(unittest.TestCase):
    def setUp(self):
        self.cm = load("cm_b2", "scripts/coverage_matrix.py")
        self.policy = json.loads((CG / "config" / "policy.json").read_text(encoding="utf-8-sig"))

    def test_insufficient_evidence_is_a_gap_not_not_required(self):
        cases = fixture()
        cases["applicability"]["functional"] = {"state": "insufficient_evidence", "reason": "缺字段规则原文"}
        matrix = self.cm.build_matrix(cases, self.policy)
        l2 = matrix["layer_matrix"]["L2"]
        self.assertFalse(l2.get("not_required"), "证据不足不得记为不要求覆盖")
        self.assertTrue(matrix.get("missing_layers") and "L2" in matrix["missing_layers"])


class PathObligations(unittest.TestCase):
    def setUp(self):
        self.gc = load("gc_b2", "skills/case-generator/scripts/gate_checks.py")
        self.policy = json.loads((CG / "config" / "policy.json").read_text(encoding="utf-8-sig"))

    def test_declared_path_without_tc_blocks_even_simple(self):
        cases = fixture()
        cases["meta"]["flow_kind"] = "workflow"
        cases["testcases"][0]["scenario_id"] = "SCN-01"
        cases["testcases"][0]["path_id"] = "PATH-01"
        cases["scenarios"] = [{
            "id": "SCN-01", "name": "保存流程", "goal": "完成保存",
            "feature_ids": ["FP-01"],
            "paths": {
                "positive": {"PATH-01": {"description": "成功保存"}},
                "exception": {"PATH-02": {"description": "校验失败"}},
                "permission": {"PATH-03": {"description": "越权访问"}},
            },
        }]
        runner = self.gc.GateRunner(copy.deepcopy(self.policy))
        runner._cov_path(cases)
        cov_path = next(c for c in runner.checks if c["id"] == "COV-PATH")
        self.assertEqual(cov_path["status"], "failed", "存在 PATH 但无 TC 不得直接放行")
        why = " ".join(i.get("why", "") for i in cov_path["issues"])
        self.assertIn("PATH-02", why)


if __name__ == "__main__":
    unittest.main()
