# -*- coding: utf-8 -*-
"""措辞门禁：禁止词 + 具体性信号的双条件判定。

单条件（只查禁止词）会误杀「提示『保存成功』」这类合格描述；
所以除「命中禁止词」外，还必须验证「没有任何可核对的具体性信号」。
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import wording_gate as WG  # noqa: E402


class TestCheckText(unittest.TestCase):
    def test_hollow_text_is_flagged(self):
        self.assertEqual(WG.check_text("弹窗关闭后页面正常展示"), "正常展示")

    def test_quoted_literal_is_exempt(self):
        """「保存成功」是字面量，可核对——命中禁止词也应放过。"""
        self.assertIsNone(WG.check_text("点击后提示「保存成功」，弹窗关闭"))

    def test_numeric_signal_is_exempt(self):
        self.assertIsNone(WG.check_text("列表从 3 条变为 4 条，操作成功"))

    def test_color_plus_element_is_exempt(self):
        self.assertIsNone(WG.check_text("输入框出现红色边框，校验失败"))

    def test_two_ui_words_is_exempt(self):
        self.assertIsNone(WG.check_text("按钮置灰，弹窗不再出现，操作成功"))

    def test_plain_text_without_banned_word_passes(self):
        self.assertIsNone(WG.check_text("首屏外出现保存按钮"))


class TestScan(unittest.TestCase):
    def test_scans_defects(self):
        report = {"defects": [{"dimension": "7.13", "status": "pending",
                               "symptom": "页面正常展示",
                               "element": {"selector": "label.x"}}]}
        got = WG.scan(report)
        self.assertFalse(got["pass"])
        self.assertEqual(got["flagged"][0]["dimension"], "7.13")
        self.assertEqual(got["flagged"][0]["selector"], "label.x")

    def test_clean_report_passes(self):
        report = {"defects": [{"dimension": "7.13", "status": "confirmed",
                               "symptom": "星号中心比标签低 5px",
                               "element": {"selector": "label.x"}}]}
        self.assertTrue(WG.scan(report)["pass"])

    def test_empty_report_is_not_error(self):
        self.assertEqual(WG.scan({}), {"checked": 0, "flagged": [], "pass": True})


class TestWiring(unittest.TestCase):
    def test_assemble_records_wording_gate(self):
        with tempfile.TemporaryDirectory() as td:
            rp = Path(td) / "report.json"
            rp.write_text(json.dumps({"url": "https://p.test/", "defects": [
                {"dimension": "7.13", "status": "pending", "symptom": "页面正常展示",
                 "element": {"selector": "label"}}]}), encoding="utf-8")
            r = subprocess.run([sys.executable, str(SCRIPTS / "assemble_report.py"),
                                "--report", str(rp), "--write"],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-300:])
            doc = json.loads(rp.read_text(encoding="utf-8"))
        gate = doc["meta"]["wording_gate"]
        self.assertFalse(gate["pass"])
        self.assertEqual(gate["flagged"][0]["pattern"], "正常展示")


class TestRestoreClaimGate(unittest.TestCase):
    """无设计稿时标题/结论不得写「还原度」（机械门禁，不靠 agent 自觉）。"""

    def test_no_design_with_restore_claim_is_flagged(self):
        report = {"title": "首页还原度对比报告",
                  "meta": {"designProvided": False}}
        got = WG.scan_restore_claims(report)
        self.assertFalse(got["pass"])
        self.assertEqual(got["flagged"][0]["field"], "title")

    def test_design_present_allows_restore_claim(self):
        report = {"title": "首页还原度对比报告",
                  "meta": {"designProvided": True}}
        self.assertTrue(WG.scan_restore_claims(report)["pass"])

    def test_missing_design_flag_is_not_guessed(self):
        got = WG.scan_restore_claims({"title": "还原度"})
        self.assertEqual(got["checked"], 0)
        self.assertTrue(got["pass"])

    def test_assemble_accepts_design_provided(self):
        with tempfile.TemporaryDirectory() as td:
            rp = Path(td) / "report.json"
            rp.write_text(json.dumps({"title": "首页还原度对比报告", "defects": []}),
                          encoding="utf-8")
            r = subprocess.run([sys.executable, str(SCRIPTS / "assemble_report.py"),
                                "--report", str(rp), "--design-provided", "false", "--write"],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-300:])
            doc = json.loads(rp.read_text(encoding="utf-8"))
        self.assertIs(doc["meta"]["designProvided"], False)
        self.assertFalse(doc["meta"]["restoreClaimGate"]["pass"])


if __name__ == "__main__":
    unittest.main()
