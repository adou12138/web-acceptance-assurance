"""review_bridge.py 评审→骨架映射测试（含阶段 1 最小 cases 串链验收）。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _util import CASES_12, REVIEW_BLOCKING, REVIEW_PASS, load  # noqa: E402

import case_data as cd  # noqa: E402
import review_bridge as rb  # noqa: E402


class TestBridgeMapping(unittest.TestCase):
    def test_skeleton_shape(self):
        skeleton, scope = rb.build_skeleton(load(REVIEW_PASS), "simple")
        self.assertEqual(skeleton["meta"]["id"], "REQ-01")
        self.assertEqual(skeleton["meta"]["flow_kind"], "simple")
        self.assertEqual(len(skeleton["features"]), 1)
        top = skeleton["features"][0]
        self.assertEqual(top["id"], "M01")
        self.assertEqual([c["id"] for c in top["children"]], ["FP-01", "FP-02", "FP-03"])
        self.assertEqual(len(skeleton["acceptance"]), 3)
        for ac in skeleton["acceptance"]:
            self.assertEqual(ac["case_ids"], [])
            self.assertEqual(ac["case_mode"], "OR")
            self.assertTrue(ac["alternative_reason"])
        self.assertEqual(scope["blocked"], ["FP-04"])
        self.assertEqual(scope["go_ahead_effective"], ["FP-01", "FP-02", "FP-03"])

    def test_ac_bridged_fields(self):
        skeleton, _ = rb.build_skeleton(load(REVIEW_PASS), "simple")
        ac1 = next(a for a in skeleton["acceptance"] if a["id"] == "AC-01")
        self.assertEqual(ac1["description"], ac1["oracle"])
        self.assertEqual(ac1["reason"], "评审阶段确认（来源功能点 FP-01）")
        self.assertEqual(
            ac1["elements"],
            {"scene": "问卷管理→回收站列表页", "object": "类型筛选器与列表",
             "condition": "回收站内预置 2 份普通问卷与 1 份考试",
             "focus": "筛选后列表只显示匹配类型且条数正确"},
        )

    def test_all_blocked_fails(self):
        with self.assertRaises(SystemExit) as ctx:
            rb.build_skeleton(load(REVIEW_BLOCKING), "simple")
        self.assertEqual(ctx.exception.code, 1)

    def test_workflow_requires_scenarios(self):
        with self.assertRaises(SystemExit) as ctx:
            rb.build_skeleton(load(REVIEW_PASS), "workflow")
        self.assertEqual(ctx.exception.code, 1)

    def test_min_cases_from_review_passes_validate(self):
        """阶段 1 验收：评审输出拼一份最小 cases 样本后 case_data.validate 无 errors。"""
        skeleton, _ = rb.build_skeleton(load(REVIEW_PASS), "simple")
        cases12 = load(CASES_12)
        skeleton["testcases"] = cases12["testcases"]
        for ac in skeleton["acceptance"]:
            # AC.required=true 时 case_ids 只登记 required 用例（case_data 硬校验）
            ac["case_ids"] = [tc["id"] for tc in skeleton["testcases"]
                              if ac["id"] in tc.get("acceptance_ids", [])
                              and tc.get("required") is True]
        errors, _ = cd.validate(skeleton)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()