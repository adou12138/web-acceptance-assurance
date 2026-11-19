"""裁决依据来源的断言守卫。

来源：2026-11-19 源码变异实测中 CHL-SM-06 存活——把 phase_gate.FORBIDDEN_SOURCES
里的 `fabricated` 删掉后，test_phase_gate.py 全部用例仍然通过。即"四类禁止的
依据来源"这条硬规则此前没有任何断言守着，改坏了 CI 不会报警。

本文件按集合逐项参数化：只要 FORBIDDEN_SOURCES 中任何一项被从校验逻辑里漏掉，
对应子用例即失败；同时保留一条合法来源的对照，避免把校验写成"一律拒绝"。
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import phase_gate as pg  # noqa: E402


def _finding(**overrides):
    finding = {
        "id": "F-1",
        "classification": "not_a_defect",
        "evidence_refs": ["evidence/shot-01.png"],
        "basis": ["observed_ui"],
        "page_observed": True,
        "evidence_grade": "E1",
    }
    finding.update(overrides)
    return finding


class ForbiddenSourceGuard(unittest.TestCase):
    def codes(self, finding):
        return [v["code"] for v in pg.check_adjudications([finding])]

    def test_every_forbidden_source_is_rejected(self):
        # 断言的目标是"集合内容被校验逻辑真正消费"，而非某几个具体值
        expected = {"assumed", "inferred_from_design", "script_inferred", "fabricated",
                    "script_completed", "expected_only"}
        self.assertTrue(expected.issubset(set(pg.FORBIDDEN_SOURCES)),
                        f"禁用来源集合被改动：缺 {expected - set(pg.FORBIDDEN_SOURCES)}")
        for source in sorted(pg.FORBIDDEN_SOURCES):
            with self.subTest(source=source):
                self.assertIn("forbidden_source", self.codes(_finding(source=source)))

    def test_forbidden_source_is_case_insensitive(self):
        for source in ("FABRICATED", "Script_Inferred", "assumed"):
            with self.subTest(source=source):
                self.assertIn("forbidden_source", self.codes(_finding(source=source)))

    def test_legit_source_not_flagged(self):
        self.assertNotIn("forbidden_source", self.codes(_finding(source="manual_observation")))
        self.assertNotIn("forbidden_source", self.codes(_finding()))

    def test_script_completed_page_behavior_rejected(self):
        # 与来源无关的独立条款：脚本补全页面行为后判定，同样必须拦
        self.assertIn("script_completed_behavior",
                      self.codes(_finding(page_behavior_completed_by_script=True)))

    def test_product_defect_requires_observed_page_and_e1(self):
        codes = self.codes(_finding(classification="product_defect", page_observed=False,
                                    evidence_grade="E2"))
        self.assertIn("defect_not_observed", codes)
        self.assertIn("defect_grade_too_low", codes)

    def test_clean_finding_has_no_violations(self):
        self.assertEqual(self.codes(_finding()), [])


if __name__ == "__main__":
    unittest.main()
