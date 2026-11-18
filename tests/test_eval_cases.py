"""eval_cases.py 质量基线测试：同输入可复现、退化可检出、边界如实。"""

import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "scripts" / "eval_cases.py"
FIXTURE = ROOT / "skills" / "case-generator" / "tests" / "fixtures" / "cases-sample-12.json"
POLICY = ROOT / "skills" / "case-generator" / "config" / "policy.json"
BASELINE = ROOT / "docs" / "evals-baseline" / "fixture-12.json"


def load_module():
    spec = importlib.util.spec_from_file_location("eval_cases", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EvalCasesTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()
        self.cases = json.loads(FIXTURE.read_text(encoding="utf-8-sig"))
        self.policy = json.loads(POLICY.read_text(encoding="utf-8-sig"))

    def test_metrics_reproducible(self):
        a = self.mod.metrics(self.cases, self.policy)
        b = self.mod.metrics(copy.deepcopy(self.cases), self.policy)
        self.assertEqual(a, b)

    def test_matches_saved_baseline(self):
        baseline = json.loads(BASELINE.read_text(encoding="utf-8-sig"))
        self.assertEqual(self.mod.metrics(self.cases, self.policy), baseline)

    def test_degradation_detected_when_l1_dropped(self):
        cases = copy.deepcopy(self.cases)
        for tc in cases["testcases"]:
            tc["coverage_flags"] = [f for f in (tc.get("coverage_flags") or []) if f != "L1"]
        now = self.mod.metrics(cases, self.policy)
        baseline = self.mod.metrics(self.cases, self.policy)
        worse = self.mod.compare(now, baseline)
        self.assertIn("coverage_layers_missing", worse)
        self.assertIn("feature_l1_ratio", worse)

    def test_method_drop_detected(self):
        cases = copy.deepcopy(self.cases)
        for tc in cases["testcases"]:
            tc["method_evidence"] = {"scenario": {"source": "主流程", "path": "进入→操作"}}
        now = self.mod.metrics(cases, self.policy)
        baseline = self.mod.metrics(self.cases, self.policy)
        self.assertIn("method_variety", self.mod.compare(now, baseline))

    def test_no_degradation_on_identical_input(self):
        current = self.mod.metrics(self.cases, self.policy)
        self.assertEqual(self.mod.compare(current, current), {})

    def test_empty_baseline_is_not_a_pass(self):
        current = self.mod.metrics(self.cases, self.policy)
        self.assertEqual(self.mod.compare(current, {}), {})


if __name__ == "__main__":
    unittest.main()
