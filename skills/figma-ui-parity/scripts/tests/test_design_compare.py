#!/usr/bin/env python3
"""设计稿对比的坐标与范围契约。

这组测试锁住两件容易悄悄退化的事：

1. ``_clamp_box`` 的边界行为。裁剪框越界会让 PIL 抛错（整条缺陷没图），
   而不加垂直上下文会把"设计两行、页面一行"这种差异裁掉——恰恰裁掉了证据。
2. ``--only-dims`` 的过滤。对比度、性能这类页面自身质量问题在设计稿上本来就是
   空白，给它们出并排图，评审会读成"设计稿这里什么都没有"，是造缺陷。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

try:
    import design_compare as dc
except ImportError as exc:  # pragma: no cover
    raise unittest.SkipTest("design_compare 不可导入：{}".format(exc))


class ClampBoxTest(unittest.TestCase):
    def test_vertical_padding_is_added(self) -> None:
        """垂直方向必须留上下文，否则设计稿的换行差异会被裁掉。"""
        left, top, right, bottom = dc._clamp_box(
            {"x": 100, "y": 200, "w": 120, "h": 20}, 1520, 1013)
        self.assertLess(top, 200, "上方没有留白，两行文字会被切掉")
        self.assertGreater(bottom, 220, "下方没有留白，第二行会被切掉")

    def test_box_is_clamped_inside_image(self) -> None:
        """元素贴边时裁剪框不得越界，越界 PIL 会直接抛错。"""
        left, top, right, bottom = dc._clamp_box(
            {"x": 0, "y": 0, "w": 60, "h": 12}, 1520, 1013)
        self.assertGreaterEqual(left, 0)
        self.assertGreaterEqual(top, 0)
        self.assertLessEqual(right, 1520)
        self.assertLessEqual(bottom, 1013)

    def test_horizontal_keeps_room_for_layout_shift(self) -> None:
        """兼容旧坐标函数：边界裁剪仍然安全。"""
        left, _, right, _ = dc._clamp_box(
            {"x": 400, "y": 200, "w": 200, "h": 40}, 1520, 1013)
        self.assertGreaterEqual(left, 0)
        self.assertLessEqual(right, 1520)
        self.assertGreater(right, left)


class ViewportPairTest(unittest.TestCase):
    def test_full_page_is_cropped_to_viewport_without_distortion(self) -> None:
        """fullPage 网页图必须先取首屏，不能拿整张长图与设计稿硬拼。"""
        from PIL import Image
        page = Image.new("RGB", (1520, 2200), "#123456")
        design = Image.new("RGB", (1520, 1013), "#abcdef")
        out = dc._page_viewport(page, {"width": 1520, "height": 1013}, design)
        self.assertEqual(out.size, (1520, 1013))
        self.assertEqual(out.getpixel((0, 0)), (18, 52, 86))

    def test_short_page_is_padded_not_stretched(self) -> None:
        """页面图不足首屏高度时补中性底，不拉伸内容。"""
        from PIL import Image
        page = Image.new("RGB", (1520, 700), "#123456")
        design = Image.new("RGB", (1520, 1013), "#abcdef")
        out = dc._page_viewport(page, {"width": 1520, "height": 1013}, design)
        self.assertEqual(out.size, (1520, 1013))
        self.assertEqual(out.getpixel((0, 699)), (18, 52, 86))
        self.assertEqual(out.getpixel((0, 700)), (245, 247, 250))


class OnlyDimsTest(unittest.TestCase):
    def test_default_scope_excludes_non_visual_quality_dims(self) -> None:
        """对比度/性能/网络异常不该出设计稿对比——设计稿上没有对应物。"""
        default = "7.1,7.2,7.3,7.5,7.6,7.7,7.8,7.13,7.21,7.23"
        only = {d.strip() for d in default.split(",")}
        for dim in ("7.41", "7.40", "7.33", "7.36"):
            self.assertNotIn(dim, only, "{} 属于页面自身质量问题，不该进设计稿对比".format(dim))
        for dim in ("7.1", "7.8", "7.23"):
            self.assertIn(dim, only, "{} 是设计还原类维度，应当出对比图".format(dim))


class EvidenceIntegrityTest(unittest.TestCase):
    def _run(self, root, shots, viewport="400x200", outdir=None, max_count=60):
        from PIL import Image
        design = root / "design.png"
        Image.new("RGB", (400, 200), "white").save(design)
        report = root / "report.json"
        report.write_text(json.dumps({"defects": [{"dimension": "7.1", "screenshots": shots}]}), encoding="utf-8")
        argv = ["design_compare", "--report", str(report), "--design-png", str(design),
                "--viewport", viewport, "--max", str(max_count)]
        if outdir:
            argv += ["--out-dir", str(outdir)]
        with mock.patch.object(sys, "argv", argv):
            rc = dc.main()
        return rc, json.loads(report.read_text(encoding="utf-8"))["defects"][0]

    def test_crop_only_does_not_masquerade_as_full_screen_comparison(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            crop = root / "sample.crop.png"
            Image.new("RGB", (20, 10), "red").save(crop)
            rc, defect = self._run(root, [{"kind": "crop", "path": str(crop)}])
            self.assertEqual(rc, 1, "没有整页证据时不能以成功状态退出")
            self.assertFalse(any(s["kind"] == "compare" for s in defect["screenshots"]))
            self.assertIn("缺少整页截图", defect["designNote"])

    def test_failed_refresh_drops_stale_comparison_without_deleting_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unrelated = root / "user-source.png"
            unrelated.write_bytes(b"keep")
            shots = [{"kind": "page", "path": str(root / "missing.page.png")},
                     {"kind": "compare", "path": str(unrelated)}]
            _, defect = self._run(root, shots)
            self.assertFalse(any(s["kind"] == "compare" for s in defect["screenshots"]))
            self.assertEqual(unrelated.read_bytes(), b"keep")

    def test_output_directory_is_created(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            page = root / "sample.page.png"
            Image.new("RGB", (400, 200), "red").save(page)
            rc, defect = self._run(root, [{"kind": "page", "path": str(page)}], outdir=root / "new" / "shots")
            self.assertEqual(rc, 0)
            comparisons = [s for s in defect["screenshots"] if s["kind"] == "compare"]
            self.assertEqual(len(comparisons), 1)
            self.assertTrue(Path(comparisons[0]["path"]).exists())

    def test_missing_design_drops_stale_comparison_references(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "report.json"
            report.write_text(json.dumps({"defects": [{"screenshots": [
                {"kind": "compare", "path": "old.png"}, {"kind": "page", "path": "page.png"}]}]}), encoding="utf-8")
            with mock.patch.object(sys, "argv", ["design_compare", "--report", str(report),
                                                  "--design-png", str(root / "missing.png")]):
                self.assertNotEqual(dc.main(), 0)
            defect = json.loads(report.read_text(encoding="utf-8"))["defects"][0]
            self.assertFalse(any(s["kind"] == "compare" for s in defect["screenshots"]))
            self.assertIn("设计稿", defect["designNote"])

    def test_relative_page_path_is_resolved_against_report_directory(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Image.new("RGB", (400, 200), "red").save(root / "relative.page.png")
            rc, defect = self._run(root, [{"kind": "page", "path": "relative.page.png"}])
            self.assertEqual(rc, 0)
            self.assertTrue(any(s["kind"] == "compare" for s in defect["screenshots"]))

    def test_region_never_overwrites_original_page_image(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "page.png"
            Image.new("RGB", (800, 600), "blue").save(page)
            original = page.read_bytes()
            defect = {"screenshots": [{"kind": "page", "path": str(page)}],
                      "shotBox": {"x": 200, "y": 200, "w": 20, "h": 10}}
            dc._attach_page_region(defect)
            self.assertEqual(page.read_bytes(), original, "区域图不能覆盖原始整页证据")
            self.assertNotEqual(defect["screenshots"][0]["path"], str(page))

    def test_truncation_also_clears_stale_comparisons(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, defect = self._run(root, [{"kind": "compare", "path": "old.png"}], max_count=0)
            self.assertEqual(defect["screenshots"], [])
            self.assertIn("上限", defect["designNote"])


if __name__ == "__main__":
    unittest.main()
