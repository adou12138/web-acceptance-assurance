"""复审 H3 回归：需求可疑点必须显式声明（批判需求本身的机械门）。

对应《复审意见_v1.34.0》A3：requirement-issues.json 原本可选且无校验，
导致"质疑需求"这一最难动作默认不发生。本测试锁定显式声明语义。
"""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "skills/requirement-review/scripts/review_check.py"


def load():
    spec = importlib.util.spec_from_file_location("review_check", CHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RequirementIssueDeclaredTests(unittest.TestCase):
    def setUp(self):
        self.mod = load()

    def test_undeclared_fails(self):
        # 未提供 requirement-issues → 必须失败（不得默认"需求无问题"）
        errors, _ = self.mod.check_requirement_issues(None)
        self.assertTrue(errors)
        self.assertTrue(any("未声明需求可疑点" in e for e in errors))

    def test_empty_array_means_reviewed_none_found(self):
        errors, _ = self.mod.check_requirement_issues([])
        self.assertEqual(errors, [])

    def test_valid_issues_pass(self):
        errors, _ = self.mod.check_requirement_issues([
            {"type": "conflict", "area": "字段必填规则", "description": "两条规则矛盾"},
            {"type": "missing", "area": "保存失败恢复", "description": "未定义输入是否保留"},
        ])
        self.assertEqual(errors, [])

    def test_invalid_type_rejected(self):
        errors, _ = self.mod.check_requirement_issues([
            {"type": "whatever", "area": "x", "description": "y"},
        ])
        self.assertTrue(any(".type:" in e for e in errors))

    def test_missing_fields_rejected(self):
        errors, _ = self.mod.check_requirement_issues([{"type": "missing"}])
        self.assertTrue(any(".area:" in e for e in errors))
        self.assertTrue(any(".description:" in e for e in errors))

    def test_non_array_rejected(self):
        errors, _ = self.mod.check_requirement_issues({"type": "missing"})
        self.assertTrue(any("必须为数组" in e for e in errors))

    def test_all_four_types_accepted(self):
        issues = [{"type": t, "area": f"a-{t}", "description": "d"} for t in
                  ("missing", "conflict", "hidden_assumption", "business_sanity")]
        errors, _ = self.mod.check_requirement_issues(issues)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()