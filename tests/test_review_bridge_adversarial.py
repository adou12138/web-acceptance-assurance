"""IMP-07 回归：评审到生成的对抗交接（review_bridge.build_adversarial_handoff）。"""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REVIEW = {
    "schema_version": "1.0",
    "coverage_gaps": [
        {"area": "导出失败提示", "reason": "失败分支无覆盖证据"},
        {"area": "重复提交幂等", "reason": "并发场景未评审"},
    ],
}

NEGATIVE_OK = [{
    "text": "未授权角色不得删除他人订单",
    "side_effect": "需核对删除接口未产生写入、无残留任务",
    "authorization": "仅使用获授权测试账号与自有数据",
}]


class ReviewBridgeAdversarialTests(unittest.TestCase):
    def setUp(self):
        self.bridge = load("review_bridge_adv", "skills/case-generator/scripts/review_bridge.py")

    def test_normal_mapping_produces_gap_and_negative_drafts(self):
        handoff = self.bridge.build_adversarial_handoff(REVIEW, NEGATIVE_OK)
        drafts = handoff["adv_candidates"]
        self.assertEqual(len(drafts), 3)
        self.assertEqual(drafts[0]["adv_id"], "ADV-DRAFT-01")
        self.assertEqual(drafts[0]["origin"], "coverage_gaps")
        self.assertEqual(drafts[0]["target"], "导出失败提示")
        self.assertEqual(drafts[0]["probe_type"], "challenge")
        neg = drafts[-1]
        self.assertEqual(neg["origin"], "negative_assertions")
        self.assertEqual(neg["side_effect"], NEGATIVE_OK[0]["side_effect"])
        self.assertEqual(neg["authorization"], NEGATIVE_OK[0]["authorization"])
        self.assertIn("oracle-contract", handoff["reminder"])

    def test_missing_coverage_gaps_yields_empty_adv_with_note(self):
        handoff = self.bridge.build_adversarial_handoff({"schema_version": "1.0"}, None)
        self.assertEqual(handoff["adv_candidates"], [])
        self.assertTrue(any("空 ADV 草稿" in note for note in handoff["notes"]))

    def test_negative_assertion_without_side_effect_rejected(self):
        bad = [{"text": "必须拒绝越权访问", "side_effect": "", "authorization": "授权测试账号"}]
        with self.assertRaises(ValueError):
            self.bridge.build_adversarial_handoff(REVIEW, bad)

    def test_negative_assertion_without_authorization_rejected(self):
        bad = [{"text": "必须拒绝越权访问", "side_effect": "核对无写入", "authorization": ""}]
        with self.assertRaises(ValueError):
            self.bridge.build_adversarial_handoff(REVIEW, bad)

    def test_non_list_coverage_gaps_rejected(self):
        with self.assertRaises(ValueError):
            self.bridge.build_adversarial_handoff({"coverage_gaps": "bad"}, None)


if __name__ == "__main__":
    unittest.main()


REQ_ISSUES_OK = [
    {"id": "RI-01", "type": "conflict", "area": "字段必填规则",
     "description": "规则 A 要求必填，规则 B 允许为空，实现无论按哪条都有一方违背"},
    {"type": "missing", "area": "保存失败恢复",
     "description": "未定义保存失败时用户输入是否保留"},
]

REQ_ISSUES_BAD_TYPE = [{"type": "whatever", "area": "x", "description": "y"}]
REQ_ISSUES_NO_DESC = [{"type": "missing", "area": "x"}]


class RequirementIssueHandoffTests(unittest.TestCase):
    """需求批判维度：requirement_issues → origin=requirement_issues 的 ADV 草稿。"""

    def setUp(self):
        self.bridge = load("review_bridge_adv", "skills/case-generator/scripts/review_bridge.py")

    def test_requirement_issues_mapped_with_req_issue_basis(self):
        out = self.bridge.build_adversarial_handoff(REVIEW, NEGATIVE_OK, REQ_ISSUES_OK)
        issues = [d for d in out["adv_candidates"] if d["origin"] == "requirement_issues"]
        self.assertEqual(len(issues), 2)
        self.assertIn("req_issue", issues[0]["expectation_basis"])
        self.assertIn("需求冲突", issues[0]["hypothesis"])
        self.assertIn("RI-01", issues[0]["note"])
        # 草稿 id 连续不冲突
        ids = [d["adv_id"] for d in out["adv_candidates"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_missing_area_rejected(self):
        with self.assertRaises(ValueError):
            self.bridge.build_adversarial_handoff(REVIEW, None,
                                                  [{"type": "missing", "description": "y"}])

    def test_invalid_type_rejected(self):
        with self.assertRaises(ValueError):
            self.bridge.build_adversarial_handoff(REVIEW, None, REQ_ISSUES_BAD_TYPE)

    def test_missing_description_rejected(self):
        with self.assertRaises(ValueError):
            self.bridge.build_adversarial_handoff(REVIEW, None, REQ_ISSUES_NO_DESC)

    def test_absent_issues_is_backward_compatible(self):
        out = self.bridge.build_adversarial_handoff(REVIEW, NEGATIVE_OK)
        self.assertFalse(any(d["origin"] == "requirement_issues" for d in out["adv_candidates"]))
