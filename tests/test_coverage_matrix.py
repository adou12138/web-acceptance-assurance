"""coverage_matrix.py 四层覆盖矩阵测试（口径以 methodology §5 + policy.coverage_layers 为准）。"""

import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "scripts" / "coverage_matrix.py"
FIXTURE = ROOT / "skills" / "case-generator" / "tests" / "fixtures" / "cases-sample-12.json"
POLICY = ROOT / "skills" / "case-generator" / "config" / "policy.json"


def load_module():
    spec = importlib.util.spec_from_file_location("coverage_matrix", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CoverageMatrixTests(unittest.TestCase):
    def setUp(self):
        if not MODULE.exists():
            self.fail("缺少 scripts/coverage_matrix.py")
        self.cm = load_module()
        self.cases = json.loads(FIXTURE.read_text(encoding="utf-8-sig"))
        self.policy = json.loads(POLICY.read_text(encoding="utf-8-sig"))

    def matrix(self, cases=None):
        return self.cm.build_matrix(cases or self.cases, self.policy)

    def test_fixture_covers_l1_l2_l3(self):
        matrix = self.matrix()
        self.assertEqual(matrix["missing_layers"], [])
        for layer in ("L1", "L2", "L3"):
            self.assertTrue(matrix["layer_matrix"][layer]["covered"], layer)
        self.assertFalse(matrix["layer_matrix"]["L4"]["counts_as_covered"])
        self.assertEqual(matrix["counts"]["features"], 3)

    def test_l1_gap_when_feature_has_no_l1_case(self):
        cases = copy.deepcopy(self.cases)
        for tc in cases["testcases"]:
            if tc["id"] == "TC-M01-001":
                tc["coverage_flags"] = ["L2"]
        matrix = self.matrix(cases)
        self.assertIn("L1", matrix["missing_layers"])
        self.assertIn("FP-01", matrix["layer_matrix"]["L1"]["detail"]["uncovered_features"])
        self.assertTrue(any(g["kind"] == "feature_uncovered" and g["target"] == "FP-01" for g in matrix["gaps"]))

    def test_l2_not_required_when_functional_not_applicable(self):
        cases = copy.deepcopy(self.cases)
        cases["applicability"]["functional"] = {"state": "not_applicable",
                                                "reason": "本次仅改文案，无功能行为变化"}
        matrix = self.matrix(cases)
        self.assertTrue(matrix["layer_matrix"]["L2"].get("not_required"))
        self.assertNotIn("L2", matrix["missing_layers"])
        self.assertFalse(matrix["layer_matrix"]["L2"]["covered"])

    def test_declared_method_without_evidence_is_gap(self):
        cases = copy.deepcopy(self.cases)
        for tc in cases["testcases"]:
            if tc["id"] == "TC-M01-002":
                tc["method_evidence"] = {"boundary": {}}
        matrix = self.matrix(cases)
        self.assertIn("L3", matrix["missing_layers"])
        self.assertTrue(matrix["layer_matrix"]["L3"]["detail"]["declared_without_evidence"])
        self.assertTrue(any(g["kind"] == "method_unwitnessed" for g in matrix["gaps"]))

    def test_unregistered_feature_is_reported(self):
        cases = copy.deepcopy(self.cases)
        clone = copy.deepcopy(cases["testcases"][0])
        clone["id"] = "TC-M01-099"
        clone["feature_ids"] = ["FP-99"]
        clone["coverage_flags"] = ["L2"]
        cases["testcases"].append(clone)
        matrix = self.matrix(cases)
        self.assertIn("FP-99", matrix["layer_matrix"]["L1"]["detail"]["unregistered_features"])
        self.assertIn("FP-99", matrix["layer_matrix"]["L1"]["detail"]["uncovered_features"])

    def test_l4_ratio_warn_only(self):
        cases = copy.deepcopy(self.cases)
        for tc in cases["testcases"][:4]:
            tc["suites"] = list(set(tc.get("suites") or []) | {"exploratory"})
        matrix = self.matrix(cases)
        self.assertTrue(matrix["layer_matrix"]["L4"]["warn"])
        self.assertNotIn("L4", matrix["missing_layers"])

    def test_matrix_is_read_only(self):
        before = json.dumps(self.cases, ensure_ascii=False, sort_keys=True)
        self.matrix()
        self.assertEqual(before, json.dumps(self.cases, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
