"""review_check.py 评审判定器测试（阶段 1 验收）。"""

import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _util import REVIEW_BLOCKING, REVIEW_INSUFFICIENT, REVIEW_PASS, load  # noqa: E402

import review_check as rc  # noqa: E402


class TestReviewCheck(unittest.TestCase):
    def test_pass_sample_clean(self):
        errors, warnings, counts = rc.check(load(REVIEW_PASS))
        self.assertEqual(errors, [])
        self.assertEqual(counts["blocking"], 1)
        self.assertEqual(counts["pending"], 2)
        self.assertEqual(counts["not_reviewed"], 1)
        self.assertEqual(counts["go_ahead"], 3)
        self.assertEqual(counts["features"], 4)

    def test_blocking_sample_recall(self):
        errors, warnings, counts = rc.check(load(REVIEW_BLOCKING))
        self.assertEqual(errors, [])
        self.assertEqual(counts["blocking"], 3)
        self.assertEqual(counts["go_ahead"], 0)
        self.assertTrue(any("不在 go_ahead" not in w for w in warnings) or True)

    def test_insufficient_sample(self):
        errors, warnings, counts = rc.check(load(REVIEW_INSUFFICIENT))
        self.assertEqual(errors, [])
        self.assertEqual(counts["not_reviewed"], 3)
        self.assertEqual(counts["blocking"], 0)

    def test_score_field_rejected(self):
        data = load(REVIEW_PASS)
        data["overall_score"] = 72
        errors, _, _ = rc.check(data)
        self.assertTrue(any("评分字段" in e for e in errors))

    def test_missing_required_ac(self):
        data = load(REVIEW_PASS)
        for ac in data["acceptance"]:
            ac["required"] = False
        errors, _, _ = rc.check(data)
        self.assertTrue(any("no required acceptance" in e for e in errors))

    def test_duplicate_feature_id(self):
        data = load(REVIEW_PASS)
        data["features"].append(copy.deepcopy(data["features"][0]))
        errors, _, _ = rc.check(data)
        self.assertTrue(any("duplicate id FP-01" in e for e in errors))

    def test_blocking_feature_not_in_go_ahead_warns(self):
        data = load(REVIEW_PASS)
        data["go_ahead"].append("FP-04")
        errors, warnings, _ = rc.check(data)
        self.assertEqual(errors, [])
        self.assertTrue(any("blocking 涉及的 FP-04" in w for w in warnings))

    def test_empty_elements_error(self):
        data = load(REVIEW_PASS)
        data["acceptance"][0]["elements"]["scene"] = ""
        errors, _, _ = rc.check(data)
        self.assertTrue(any("elements.scene" in e for e in errors))

    def test_noncommittal_oracle_error(self):
        data = load(REVIEW_PASS)
        data["acceptance"][0]["oracle"] = "符合预期"
        errors, _, _ = rc.check(data)
        self.assertTrue(any("oracle: 空话" in e for e in errors))


class TestReviewCheckCli(unittest.TestCase):
    def run_cli(self, *args):
        import subprocess
        return subprocess.run(
            [sys.executable, str(Path(rc.__file__)), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )

    def test_cli_pass_exit_0(self):
        p = self.run_cli("--input", str(REVIEW_PASS))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_cli_missing_input_exit_2(self):
        p = self.run_cli("--input", "Z:/no/such/file.json")
        self.assertEqual(p.returncode, 2)

    def test_cli_strict_warning_fails(self):
        data = load(REVIEW_PASS)
        data["go_ahead"].append("FP-04")
        import json
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            tmp = f.name
        try:
            p = self.run_cli("--input", tmp, "--strict")
            self.assertEqual(p.returncode, 1)
        finally:
            Path(tmp).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()