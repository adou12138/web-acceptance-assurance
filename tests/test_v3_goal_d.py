"""v3 目标审计 D 批回归：证据/缺口逐跳贯通、图片闭环、回链守卫、原生测试点。"""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CG = ROOT / "skills" / "case-generator"


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BridgePropagation(unittest.TestCase):
    def setUp(self):
        self.rb = load("rb_d", "skills/case-generator/scripts/review_bridge.py")
        self.review = json.loads((CG / "tests" / "fixtures" / "review-sample-pass.json").read_text(encoding="utf-8-sig"))

    def test_bridge_preserves_gaps_evidence_visual_pending(self):
        review = copy.deepcopy(self.review)
        gap = {"area": "图中角色规则", "reason": "图中文字无法识读"}
        review["coverage_gaps"] = [gap]
        review["visual_evidence"] = [{"file": "media/x.png", "sha256": "a" * 64, "type": "ui_mockup",
                                      "extracted_rules": [], "confidence": 0, "boundary": "未识读", "image_understood": False}]
        review["not_reviewed"] = [{"dimension": "state_coverage", "reason": "缺状态图"}]
        review["confirmed_rules"] = []
        ac = review["acceptance"][0]
        ac["evidence_refs"] = [{"source": "需求第1页", "quote": "原文"}]
        ac["confirmation"] = "pending"
        skel, scope = self.rb.build_skeleton(review, "simple")
        self.assertTrue(skel.get("coverage_gaps"));self.assertEqual(skel["coverage_gaps"][0]["reason"], gap["reason"])
        self.assertEqual(skel.get("visual_evidence"), review["visual_evidence"])
        self.assertEqual(skel.get("not_reviewed"), review["not_reviewed"])
        mapped_ac = next(a for a in skel["acceptance"] if a["id"] == ac["acceptance_id"])
        self.assertEqual(mapped_ac.get("evidence_refs"), ac["evidence_refs"])
        self.assertEqual(mapped_ac.get("confirmation"), "provisional", "待确认 AC 须降级不得冒充已确认")
        self.assertTrue(mapped_ac.get("needs_confirmation"))


class ReviewRenderVisual(unittest.TestCase):
    def setUp(self):
        self.rr = load("rr_d", "skills/requirement-review/scripts/render_review.py")

    def test_render_shows_unread_image(self):
        review = json.loads((CG / "tests" / "fixtures" / "review-sample-pass.json").read_text(encoding="utf-8-sig"))
        review["visual_evidence"] = [{"file": "media/unread.png", "sha256": "b" * 64,
                                      "type": "ui_mockup", "extracted_rules": [], "confidence": 0,
                                      "boundary": "字号不清", "image_understood": False}]
        out = self.rr.render(review)
        self.assertIn("media/unread.png", out, "评审摘要必须列出未识读图片")
        self.assertIn("未识读", out)


class VisualEvidenceHardening(unittest.TestCase):
    def setUp(self):
        self.rc = load("rc_d", "skills/requirement-review/scripts/review_check.py")
        self.review = json.loads((CG / "tests" / "fixtures" / "review-sample-pass.json").read_text(encoding="utf-8-sig"))

    def test_unread_image_with_rules_rejected(self):
        review = copy.deepcopy(self.review)
        review["visual_evidence"] = [{"file": "missing.png", "sha256": "b" * 64,
                                      "type": "ui_mockup", "extracted_rules": ["名称长度1至20"],
                                      "confidence": 0, "boundary": "未识读", "image_understood": False}]
        errors, _, _ = self.rc.check(review)
        self.assertTrue(any("image_understood=false" in e for e in errors), "未识读却带规则必须报错")

    def test_unread_image_without_rules_accepted(self):
        review = copy.deepcopy(self.review)
        review["visual_evidence"] = [{"file": "missing.png", "sha256": "b" * 64,
                                      "type": "ui_mockup", "extracted_rules": [],
                                      "confidence": 0, "boundary": "字号不清", "image_understood": False}]
        errors, _, _ = self.rc.check(review)
        self.assertFalse(any("image_understood=false" in e for e in errors))


class SkillReferenceGuard(unittest.TestCase):
    def test_guard_enumerates_skill_references(self):
        mod = load("ref_d", "tests/test_reference_links.py")
        all_files = mod.all_reference_files()
        skill_refs = [p for p in all_files if "skills" in p.parts]
        self.assertGreater(len(skill_refs), 0, "回链守卫必须覆盖 skill 级 references 树")


class NativeTestpoints(unittest.TestCase):
    def setUp(self):
        self.rtp = load("rtp_d", "skills/case-generator/scripts/render_testpoints.py")

    def test_testpoints_from_obligations_without_testcases(self):
        cases = {
            "meta": {"schema_version": "1.0", "id": "REQ-01", "title": "仅测试点", "flow_kind": "simple"},
            "requirements": [{"id": "REQ-01", "name": "需求", "source": "评审", "description": "描述", "confirmation": "confirmed"}],
            "features": [{"id": "M01", "children": [{"id": "FP-01", "name": "功能点", "description": "d", "requirement_ids": ["REQ-01"]}]}],
            "acceptance": [{"id": "AC-01", "requirement_id": "REQ-01", "description": "d", "reason": "r",
                            "required": True, "confirmation": "confirmed", "oracle": "o", "elements": {}, "case_mode": "OR"}],
            "coverage_obligations": [
                {"id": "TP-01", "layer": "L2", "kind": "field_boundary", "target": "FP-01.名称", "source": "AC-01", "required": True, "tc_ids": []},
            ],
            "testcases": [],
        }
        rows = self.rtp.build_rows(cases)
        self.assertTrue(any("TP-01" in str(r) for r in rows), "无 testcases 时应能从义务输出测试点")


if __name__ == "__main__":
    unittest.main()
