"""用例资产基线契约（M10）：固定输入与版本、差异定位到条目。

三件事：
1. **输入与版本固定**：基线记下输入指纹与工具版本。换一份用例来比时，
   机械指标口径可能已变——此时必须标「不可比」，不得把「换题了」说成「退化���。
2. **旧基线保留可用**：`fixture-12.json` 是历史口径锚点，仍要能读。
3. **差异定位到条目**：指标级差异只说哪一项变差，定位要落到具体用例、
   具体结构错误、具体覆盖缺口、具体断言数变化。
"""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "eval_cases.py"
FIXTURE = ROOT / "skills" / "case-generator" / "tests" / "fixtures" / "cases-sample-12.json"
POLICY = ROOT / "skills" / "case-generator" / "config" / "policy.json"
LEGACY_BASELINE = ROOT / "docs" / "evals-baseline" / "fixture-12.json"
V2_BASELINE = ROOT / "docs" / "evals-baseline" / "fixture-12-v2.json"
SAMPLE = ROOT / "docs" / "evals-baseline" / "samples" / "requirement-sample-desensitized.json"


def load_module():
    spec = importlib.util.spec_from_file_location("eval_cases", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EvalBaselineContract(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()
        self.cases = json.loads(FIXTURE.read_text(encoding="utf-8-sig"))
        self.policy = json.loads(POLICY.read_text(encoding="utf-8-sig"))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                              capture_output=True, text=True, encoding="utf-8")

    # —— 固定输入与版本 ——

    def test_saved_baseline_records_input_fingerprint_and_version(self):
        out = self.folder / "baseline.json"
        result = self.run_cli("--cases", FIXTURE, "--save-baseline", out)
        self.assertEqual(result.returncode, 0, result.stderr)
        doc = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(doc["schema"], "case-eval-baseline/2")
        self.assertTrue(doc["tool_version"])
        self.assertTrue(doc["input"]["sha256"])
        self.assertIn("metrics", doc)
        self.assertIn("detail", doc)

    def test_different_input_is_reported_as_not_comparable(self):
        """换题了必须标不可比，不得直接当退化判定。"""
        out = self.folder / "baseline.json"
        self.run_cli("--cases", FIXTURE, "--save-baseline", out)
        result = self.run_cli("--cases", SAMPLE, "--baseline", out)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["comparable"])
        self.assertTrue(any("指纹不同" in note for note in payload["comparability_notes"]))

    def test_tool_version_mismatch_is_reported(self):
        doc = json.loads(V2_BASELINE.read_text(encoding="utf-8"))
        doc["tool_version"] = "0"
        out = self.folder / "baseline.json"
        out.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        result = self.run_cli("--cases", FIXTURE, "--baseline", out)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["comparable"])
        self.assertTrue(any("工具" in note or "v0" in note
                            for note in payload["comparability_notes"]))

    def test_same_input_is_comparable(self):
        result = self.run_cli("--cases", FIXTURE, "--baseline", V2_BASELINE)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["comparable"])
        self.assertEqual(payload["degraded"], {})

    # —— 旧基线兼容 ——

    def test_legacy_baseline_is_still_readable(self):
        """fixture-12.json 是历史锚点，仍要能读（旧格式只出指标，无条目级定位）。"""
        legacy = json.loads(LEGACY_BASELINE.read_text(encoding="utf-8-sig"))
        metrics, detail, doc = self.mod._read_baseline(legacy)
        self.assertEqual(metrics, legacy)
        self.assertIsNone(detail)
        self.assertIsNone(doc)
        result = self.run_cli("--cases", FIXTURE, "--baseline", LEGACY_BASELINE)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["detail"]["comparable"])
        self.assertIn("条目级", payload["detail"]["reason"])

    # —— 差异定位到条目 ——

    def test_thinned_assertion_is_located_to_case(self):
        baseline_detail = self.mod.detail(self.cases, self.policy)
        degraded = copy.deepcopy(self.cases)
        target = degraded["testcases"][0]
        target["assertions"] = list(target["assertions"])[:-1]
        current_detail = self.mod.detail(degraded, self.policy)
        located = self.mod.diff_detail(current_detail, baseline_detail)
        self.assertTrue(located["comparable"])
        thinner = located["assertions"]["thinner"]
        self.assertTrue(thinner)
        self.assertEqual(thinner[0]["id"], target["id"])
        self.assertEqual(thinner[0]["now"], len(target["assertions"]))

    def test_new_structural_error_is_located(self):
        baseline_detail = self.mod.detail(self.cases, self.policy)
        degraded = copy.deepcopy(self.cases)
        degraded["testcases"][0].pop("priority", None)
        current_detail = self.mod.detail(degraded, self.policy)
        located = self.mod.diff_detail(current_detail, baseline_detail)
        self.assertTrue(located["structural"]["added"])
        self.assertTrue(any("priority" in item for item in located["structural"]["added"]))

    def test_new_coverage_gap_is_located(self):
        baseline_detail = self.mod.detail(self.cases, self.policy)
        degraded = copy.deepcopy(self.cases)
        degraded["coverage_gaps"] = [{"id": "GAP-01", "target": "FP-01",
                                       "reason": "权限路径未覆盖"}]
        current_detail = self.mod.detail(degraded, self.policy)
        located = self.mod.diff_detail(current_detail, baseline_detail)
        added = located["coverage"]["gaps_added"]
        self.assertTrue(added)
        self.assertEqual(added[0]["id"], "GAP-01")

    def test_added_and_removed_cases_are_located(self):
        baseline_detail = self.mod.detail(self.cases, self.policy)
        degraded = copy.deepcopy(self.cases)
        removed = degraded["testcases"].pop()
        current_detail = self.mod.detail(degraded, self.policy)
        located = self.mod.diff_detail(current_detail, baseline_detail)
        self.assertIn(removed["id"], located["cases"]["removed"])

    def test_diff_csv_is_written_with_bom_and_entries(self):
        out = self.folder / "baseline.json"
        diff_csv = self.folder / "diff.csv"
        self.run_cli("--cases", FIXTURE, "--save-baseline", out)
        degraded = self.folder / "degraded.json"
        cases = copy.deepcopy(self.cases)
        cases["testcases"][0]["assertions"] = list(cases["testcases"][0]["assertions"])[:-1]
        degraded.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
        result = self.run_cli("--cases", degraded, "--baseline", out,
                              "--out-diff", diff_csv)
        # 退出码 1 = 检测到退化（断言变稀），这是正确行为，不是工具失败
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertTrue(diff_csv.is_file(), "退化时也必须产出条目级差异明细")
        raw = diff_csv.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        text = raw.decode("utf-8-sig")
        self.assertIn("断言", text)
        self.assertIn(cases["testcases"][0]["id"], text)

    # —— 样本可复跑 ——

    def test_desensitized_sample_scores_reproducibly(self):
        """脱敏样本存在且可复跑打分（两次结果一致）。"""
        self.assertTrue(SAMPLE.is_file(), "缺少脱敏样本")
        first = self.run_cli("--cases", SAMPLE)
        second = self.run_cli("--cases", SAMPLE)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(json.loads(first.stdout)["metrics"],
                         json.loads(second.stdout)["metrics"])

    def test_sample_baseline_is_published(self):
        baseline = SAMPLE.with_name("requirement-sample-desensitized-baseline.json")
        self.assertTrue(baseline.is_file(), "脱敏样本缺已发布基线")
        doc = json.loads(baseline.read_text(encoding="utf-8"))
        self.assertEqual(doc["schema"], "case-eval-baseline/2")
        result = self.run_cli("--cases", SAMPLE, "--baseline", baseline)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["comparable"])
        self.assertEqual(payload["degraded"], {})


if __name__ == "__main__":
    unittest.main()
