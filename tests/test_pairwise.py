"""pairwise.py 组合覆盖与独立验真测试（生成器不得自证）。

契约以脚本实际输出为准：generate → {parameters, constraints, combinations, missing_pairs(list), complete, input_hash}；
verify(parameters, constraints, combinations) → {verified, required_pairs, covered_pairs, missing_pairs(list)}。
"""

import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "scripts" / "pairwise.py"

PARAMS = [
    {"name": "角色", "values": ["管理员", "普通用户", "访客"]},
    {"name": "权限", "values": ["查看", "编辑"]},
]
CONSTRAINTS = [[{"name": "角色", "value": "访客"}, {"name": "权限", "value": "编辑"}]]


def load_module():
    spec = importlib.util.spec_from_file_location("pairwise", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PairwiseTests(unittest.TestCase):
    def setUp(self):
        if not MODULE.exists():
            self.fail("缺少 scripts/pairwise.py")
        self.pw = load_module()
        self.generated = self.pw.generate(PARAMS, CONSTRAINTS)

    def test_generation_covers_all_pairs(self):
        self.assertTrue(self.generated["complete"])
        self.assertEqual(self.generated["missing_pairs"], [])
        self.assertLessEqual(self.generated["combinations"].__len__(), 3 * 2)

    def test_constraint_rows_excluded(self):
        for row in self.generated["combinations"]:
            self.assertFalse(row.get("角色") == "访客" and row.get("权限") == "编辑",
                             f"非法组合未被排除：{row}")

    def test_independent_verify_passes(self):
        verdict = self.pw.verify(self.generated["parameters"], self.generated["constraints"],
                                 self.generated["combinations"])
        self.assertTrue(verdict["verified"])

    def test_verify_detects_under_coverage(self):
        trimmed = self.generated["combinations"][:-2]
        verdict = self.pw.verify(self.generated["parameters"], self.generated["constraints"], trimmed)
        self.assertFalse(verdict["verified"])
        self.assertGreater(len(verdict["missing_pairs"]), 0)

    def test_generate_and_verify_keeps_both_parts(self):
        result = self.pw.generate_and_verify(PARAMS, CONSTRAINTS)
        self.assertIn("verification", result)
        self.assertTrue(result["verification"]["verified"])
        self.assertIn("input_hash", result)

    def test_invalid_parameters_rejected(self):
        with self.assertRaises(self.pw.PairwiseError):
            self.pw.generate([], None)

    def test_generator_does_not_mutate_input(self):
        params = copy.deepcopy(PARAMS)
        self.pw.generate_and_verify(params, copy.deepcopy(CONSTRAINTS))
        self.assertEqual(params, PARAMS)


if __name__ == "__main__":
    unittest.main()
