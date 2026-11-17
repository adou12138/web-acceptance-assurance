"""render_review.py 摘要渲染测试（派生视图纪律）。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _util import REVIEW_INSUFFICIENT, REVIEW_PASS, load  # noqa: E402

import render_review as rr  # noqa: E402


class TestRenderReview(unittest.TestCase):
    def test_section_counts_match_json(self):
        data = load(REVIEW_PASS)
        text = rr.render(data)
        self.assertIn("## 阻塞项（1）", text)
        self.assertIn("## 待确认项（2）", text)
        self.assertIn("## 可直接推进（3）", text)
        self.assertIn("## 覆盖缺口（1）", text)
        self.assertIn("## 未评审维度（1）", text)

    def test_no_invented_items(self):
        data = load(REVIEW_PASS)
        text = rr.render(data)
        self.assertIn("B-01", text)
        self.assertIn("P-01", text)
        self.assertIn("P-02", text)
        self.assertNotIn("B-99", text)
        self.assertNotIn("P-99", text)

    def test_blocking_grouped_by_feature(self):
        data = load(REVIEW_PASS)
        text = rr.render(data)
        self.assertIn("### FP-04", text)
        self.assertIn("自动清理的保留期", text)

    def test_reconciliation_line(self):
        data = load(REVIEW_PASS)
        text = rr.render(data)
        self.assertIn("对账：", text)
        self.assertIn("阻塞 1==blocking[1]", text)
        self.assertNotIn("confirmed_rules[", text)

    def test_fixed_six_sections_only(self):
        """方案 §5：摘要章节固定六节，不得增删。"""
        data = load(REVIEW_PASS)
        text = rr.render(data)
        for section in ("需求信息", "阻塞项", "待确认项", "可直接推进", "覆盖缺口", "未评审维度"):
            self.assertIn(f"## {section}", text)
        self.assertNotIn("已确认规则备查", text)

    def test_insufficient_not_reviewed_listed(self):
        data = load(REVIEW_INSUFFICIENT)
        text = rr.render(data)
        self.assertIn("historical_consistency", text)
        self.assertIn("performance_security", text)
        self.assertIn("state_coverage", text)

    def test_cli_writes_file(self):
        import subprocess
        import sys as _sys
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "review-summary.md"
            p = subprocess.run(
                [_sys.executable, str(Path(rr.__file__)),
                 "--input", str(REVIEW_PASS), "--out", str(out)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertTrue(out.is_file())
            content = out.read_text(encoding="utf-8")
            self.assertIn("对账：", content)


if __name__ == "__main__":
    unittest.main()