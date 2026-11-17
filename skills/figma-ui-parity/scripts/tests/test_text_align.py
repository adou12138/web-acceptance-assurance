#!/usr/bin/env python3
"""7.13「对齐方式与设计稿一致」的判定：文本对齐必须真比，且口径要能对上。

为什么需要这组测试：设计侧字段叫 `textAlignHorizontal`（LEFT/CENTER/RIGHT/JUSTIFIED），
页面侧是 CSS `text-align`（left/start/center/right/end/justify），**两套枚举不同名**。
此前两侧都采集了、却没有任何比对路径 —— 采集了不消费等于没测，且文档已声明这条断言。

锁定的不变式：
1. 两侧都有效且语义不同 → confirmed，维度 7.13；
2. 语义相同但字面不同（LEFT vs start / RIGHT vs end）→ **不得报**（防假缺陷）；
3. 设计侧未声明 → 不比、不猜；
4. 视口比例不一致时仍可比（样式类口径，不受几何归一影响）。
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]


def run_compare(design_align, page_align, frame_w=1440, viewport_w=1440, tmp=None,
                design_case=None, page_transform=None) -> list:
    text = {"characters": "OK", "fontSize": 14, "fontWeight": 400}
    if design_align is not None:
        text["textAlignHorizontal"] = design_align
    if design_case is not None:
        text["textCase"] = design_case
    btn = {"id": "n1", "name": "btn", "type": "FRAME", "visible": True,
           "rect": {"x": 10, "y": 10, "w": 100, "h": 40}, "children": [
               {"id": "t1", "name": "label", "type": "TEXT", "visible": True,
                "rect": {"x": 20, "y": 18, "w": 30, "h": 16}, "text": text}]}
    design = {"tree": {"id": "root", "name": "root", "type": "FRAME",
                       "rect": {"x": 0, "y": 0, "w": frame_w, "h": 900}, "children": [btn]},
              "flat": [btn, btn["children"][0]]}
    item = {"sel": ".btn", "text": "OK", "rect": {"x": 10, "y": 10, "w": 100, "h": 40},
            "fontSize": "14px", "fontWeight": "400"}
    if page_align is not None:
        item["textAlign"] = page_align
    if page_transform is not None:
        item["textTransform"] = page_transform
    page = {"viewport": {"w": viewport_w}, "items": [item]}
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        (tmpdir / "design.json").write_text(json.dumps(design), encoding="utf-8")
        (tmpdir / "page.json").write_text(json.dumps(page), encoding="utf-8")
        out = tmpdir / "out.json"
        r = subprocess.run([sys.executable, str(SCRIPTS / "compare_fields.py"),
                            "--design", str(tmpdir / "design.json"),
                            "--page", str(tmpdir / "page.json"),
                            "--output", str(out), "--page-url", "https://p.test/"],
                           capture_output=True, text=True)
        if r.returncode != 0 or not out.exists():
            raise AssertionError("compare_fields 未产出（rc=%s）%s" % (r.returncode, r.stderr[-500:]))
        return json.loads(out.read_text(encoding="utf-8")).get("styleDiffs") or []


def align_rows(diffs):
    return [d for d in diffs if str(d.get("property")) == "textAlign"]


def case_rows(diffs):
    return [d for d in diffs if str(d.get("property")) == "textCase"]


class TextAlignTests(unittest.TestCase):
    def test_real_mismatch_is_confirmed(self):
        rows = align_rows(run_compare("LEFT", "right"))
        self.assertEqual(len(rows), 1, "设计 LEFT / 页面 right 未产出 textAlign 差异")
        self.assertEqual(rows[0]["status"], "confirmed")
        self.assertEqual(rows[0]["dimension"], "7.13")

    def test_semantically_equal_enum_must_not_be_reported(self):
        for design_val, page_val in (("LEFT", "start"), ("RIGHT", "end"),
                                     ("CENTER", "center"), ("JUSTIFIED", "justify")):
            self.assertEqual(align_rows(run_compare(design_val, page_val)), [],
                             "%s vs %s 语义相同，不应报差异" % (design_val, page_val))

    def test_missing_design_alignment_is_not_guessed(self):
        self.assertEqual(align_rows(run_compare(None, "right")), [],
                         "设计侧未声明对齐方式时不得凭空比较")
        self.assertEqual(align_rows(run_compare("LEFT", None)), [],
                         "页面侧拿不到 text-align 时不得凭空比较")

    def test_still_comparable_when_viewport_scaled(self):
        rows = align_rows(run_compare("LEFT", "center", frame_w=1440, viewport_w=375))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "confirmed",
                         "对齐方式是样式类口径，不应因视口比例不同被降为 notComparable")


class TextCaseTests(unittest.TestCase):
    """大小写变换同样是"两侧都采集、无人比对"的字段。"""

    def test_real_mismatch_is_confirmed(self):
        rows = case_rows(run_compare(None, None, design_case="UPPER", page_transform="none"))
        self.assertEqual(len(rows), 1, "设计 UPPER / 页面 none 未产出 textCase 差异")
        self.assertEqual(rows[0]["status"], "confirmed")
        self.assertEqual(rows[0]["dimension"], "7.1")

    def test_equivalent_mapping_must_not_be_reported(self):
        self.assertEqual(case_rows(run_compare(None, None, design_case="UPPER",
                                               page_transform="uppercase")), [])
        self.assertEqual(case_rows(run_compare(None, None, design_case="ORIGINAL",
                                               page_transform="none")), [])

    def test_missing_side_is_not_guessed(self):
        self.assertEqual(case_rows(run_compare(None, None, design_case=None,
                                               page_transform="uppercase")), [])
        self.assertEqual(case_rows(run_compare(None, None, design_case="UPPER",
                                               page_transform=None)), [])


if __name__ == "__main__":
    unittest.main()
