"""报告真实入口的截图来源、跨盘路径与证据可读性回归。"""
import base64
import contextlib
import builtins
import importlib.util
import csv
import html
from html.parser import HTMLParser
import io
import json
import ntpath
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote

from PIL import Image


def png_bytes(label):
    output = io.BytesIO()
    color = sum(label.encode()) % 256
    Image.new("RGB", (2, 2), (color, 100, 200)).save(output, format="PNG")
    return output.getvalue()

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))
import generate_reports as report


class EvidenceParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.images = []
        self.captions = []
        self.in_caption = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            self.links.append(attrs)
        elif tag == "img":
            self.images.append(attrs)
        elif tag == "figcaption":
            self.in_caption = True

    def handle_endtag(self, tag):
        if tag == "figcaption":
            self.in_caption = False

    def handle_data(self, data):
        if self.in_caption:
            self.captions.append(data)


class ReportShotPathsTests(unittest.TestCase):
    def render(self, root, shots, extra=None, cross_drive=False):
        source = root / "input"
        out = root / "output"
        source.mkdir(exist_ok=True)
        out.mkdir(exist_ok=True)
        defect = {"fingerprint": "report-shot", "severity": "P2", "status": "confirmed",
                  "symptom": "截图证据测试", "screenshots": shots, **(extra or {})}
        input_path = source / "upstream.json"
        input_path.write_text(json.dumps({"defects": [defect]}, ensure_ascii=False), encoding="utf-8")
        argv = ["generate_reports.py", "--input", str(input_path), "--outdir", str(out)]
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(sys, "argv", argv))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            if cross_drive:
                windows_relpath = ntpath.relpath
                def different_drives(*_args):
                    return windows_relpath("D:/shots/证据 #1.png", "C:/reports")
                stack.enter_context(patch.object(report.os.path, "relpath", side_effect=different_drives))
            try:
                code = report.main()
            except ValueError as exc:
                self.fail("跨盘截图路径不应使报告入口崩溃：{}".format(exc))
        self.assertEqual(code, 0)
        text = (out / "report.html").read_text(encoding="utf-8")
        parser = EvidenceParser()
        parser.feed(text)
        with (out / "defects.csv").open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(len(rows[0]), 10, "保持CSV十列契约（含问题来源、复现步骤）")
        self.assertEqual(len(rows[1]), 10)
        return text, parser, rows[1][8]

    def test_relative_shot_reads_input_parent_not_output_stale_image(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for folder, content in (("input", png_bytes("current-input-image")), ("output", png_bytes("old-output-image"))):
                shot = root / folder / "shots" / "crop.png"
                shot.parent.mkdir(parents=True)
                shot.write_bytes(content)
            text, _, csv_path = self.render(root, [{"kind": "crop", "path": "shots/crop.png"}])
            self.assertIn(base64.b64encode(png_bytes("current-input-image")).decode(), text)
            self.assertNotIn(base64.b64encode(png_bytes("old-output-image")).decode(), text)
            self.assertEqual(csv_path, "../input/shots/crop.png")

    def test_missing_input_shot_does_not_use_same_named_output_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            stale = root / "output" / "shots" / "crop.png"
            stale.parent.mkdir(parents=True)
            stale.write_bytes(png_bytes("old-output-image"))
            text, parser, csv_path = self.render(root, [{"kind": "crop", "path": "shots/crop.png"}])
            self.assertIn("图片缺失", text)
            self.assertFalse(parser.images)
            self.assertIn("(缺失)crop.png", csv_path)

    def test_link_encodes_hash_spaces_percent_and_unicode_but_csv_stays_relative(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            shot = root / "input" / "证据 #50% &.png"
            shot.parent.mkdir()
            shot.write_bytes(png_bytes("page"))
            _, parser, csv_path = self.render(root, [{"kind": "page", "path": str(shot)}])
            expected = "../input/" + quote(shot.name)
            self.assertIn(expected, [link.get("href") for link in parser.links])
            self.assertEqual(csv_path, "../input/" + shot.name)

    def test_cross_drive_page_and_large_crop_fall_back_to_file_uri(self):
        for kind in ("page", "crop"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                shot = root / "证据 #1.png"
                shot.write_bytes(png_bytes("large") + b"x" * (report.SHOT_INLINE_LIMIT + 1))
                _, parser, csv_path = self.render(root, [{"kind": kind, "path": str(shot)}], cross_drive=True)
                self.assertIn(shot.as_uri(), [link.get("href") for link in parser.links])
                self.assertEqual(csv_path, shot.as_uri())

    def test_shot_and_design_evidence_limits_are_escaped_in_captions(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            shot = root / "crop.png"
            shot.write_bytes(png_bytes("crop"))
            note = '仅页面局部证据 <script>alert("x")</script>'
            design_note = '缺少整页截图 <img src=x onerror="bad()">'
            text, parser, _ = self.render(root, [{"kind": "crop", "path": str(shot), "note": note}],
                                          {"designNote": design_note})
            captions = " ".join(parser.captions)
            self.assertIn(note, captions)
            self.assertIn(design_note, captions)
            self.assertIn(html.escape(note), text)
            self.assertNotIn("<script>", text)
            self.assertNotIn("<img src=x", text)

    def test_linked_and_missing_evidence_keep_limit_captions(self):
        for exists in (True, False):
            with self.subTest(exists=exists), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                shot = root / "page.png"
                if exists:
                    shot.write_bytes(png_bytes("page"))
                _, parser, _ = self.render(root, [{"kind": "page", "path": str(shot), "note": "整页缩放，仅供定位"}],
                                          {"designNote": "未生成设计侧对比"})
                self.assertIn("整页缩放，仅供定位", " ".join(parser.captions))
                self.assertIn("未生成设计侧对比", " ".join(parser.captions))

    def test_missing_pillow_keeps_report_with_explicit_unverified_reason(self):
        original_import = builtins.__import__
        def no_pillow(name, *args, **kwargs):
            if name == "PIL" or name.startswith("PIL."):
                raise ImportError("测试模拟缺少Pillow")
            return original_import(name, *args, **kwargs)
        spec = importlib.util.spec_from_file_location("report_without_pillow", SCRIPTS / "generate_reports.py")
        module = importlib.util.module_from_spec(spec)
        with patch.object(builtins, "__import__", no_pillow):
            try:
                spec.loader.exec_module(module)
            except ImportError as exc:
                self.fail("缺少Pillow不能使整个报告模块导入失败：{}".format(exc))
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            shot = root / "crop.png"
            shot.write_bytes(png_bytes("crop"))
            with patch.dict(self.render.__func__.__globals__, {"report": module}):
                text, parser, csv_path = self.render(root, [{"kind": "crop", "path": str(shot), "note": "仅局部证据"}],
                                                      {"designNote": "设计侧待确认"})
            self.assertIn("缺少 Pillow，无法验证截图", text)
            self.assertIn("缺少 Pillow，无法验证截图", csv_path)
            self.assertIn("仅局部证据", " ".join(parser.captions))
            self.assertIn("设计侧待确认", " ".join(parser.captions))
            self.assertFalse(parser.images)
            self.assertFalse(parser.links)

    def test_supported_image_mime_matches_verified_format(self):
        for image_format, mime in (("JPEG", "image/jpeg"), ("WEBP", "image/webp")):
            with self.subTest(image_format=image_format), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                shot = root / "actual-image.png"
                Image.new("RGB", (2, 2), (20, 40, 60)).save(shot, format=image_format)
                _, parser, _ = self.render(root, [{"kind": "crop", "path": str(shot)}])
                self.assertTrue(all(image["src"].startswith("data:" + mime + ";base64,") for image in parser.images))
                self.assertTrue(parser.images)

    def test_non_image_secret_is_not_inlined_or_linked(self):
        for kind, suffix in (("crop", ".txt"), ("compare", ".png"), ("page", ".txt")):
            with self.subTest(kind=kind, suffix=suffix), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                secret = root / ("private-config" + suffix)
                secret.write_text("MOCK_SECRET", encoding="utf-8")
                original_read = Path.read_bytes
                def guarded_read(path):
                    self.assertNotEqual(path.resolve(), secret.resolve(), "非图片不得执行read_bytes内联")
                    return original_read(path)
                with patch.object(Path, "read_bytes", guarded_read):
                    text, parser, csv_path = self.render(root, [{"kind": kind, "path": str(secret), "note": "未核验证据"}],
                                                          {"designNote": "设计侧也未确认"})
                self.assertNotIn("MOCK_SECRET", text)
                self.assertNotIn(base64.b64encode(b"MOCK_SECRET").decode(), text)
                self.assertFalse(parser.images)
                self.assertFalse(parser.links)
                self.assertIn("图片无效或不可读", text)
                self.assertIn("未核验证据", " ".join(parser.captions))
                self.assertIn("设计侧也未确认", " ".join(parser.captions))
                self.assertIn("(无效)", csv_path)

    def test_unavailable_shots_keep_design_evidence_limit(self):
        for shots in ([], [{"kind": "none", "note": "元素未找到"}]):
            with self.subTest(shots=shots), tempfile.TemporaryDirectory() as td:
                text, _, _ = self.render(Path(td), shots,
                                         {"shotError": "页面证据缺失", "designNote": "设计证据缺失 <b>待确认</b>"})
                self.assertIn("设计证据缺失 &lt;b&gt;待确认&lt;/b&gt;", text)
                self.assertNotIn("<b>待确认</b>", text)

    def test_compare_keeps_crop_and_offers_clickable_full_resolution_inline_images(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            shots = []
            for kind in ("compare", "crop", "page", "design"):
                shot = root / (kind + ".png")
                shot.write_bytes(png_bytes(kind))
                shots.append({"kind": kind, "path": str(shot)})
            text, parser, _ = self.render(root, shots)
            sources = [image["src"] for image in parser.images]
            for content in (png_bytes("compare"), png_bytes("crop")):
                uri = "data:image/png;base64," + base64.b64encode(content).decode()
                self.assertIn(uri, sources)
                self.assertTrue(any(link.get("href") == uri and "download" in link for link in parser.links))
            self.assertIn("shot-compare", text)
            wide_rule = re.search(r"\.shot-compare\s+img\s*\{([^}]+)\}", text)
            self.assertIsNotNone(wide_rule, "对比图需有独立宽屏样式")
            width = re.search(r"max-width\s*:\s*(\d+)px", wide_rule.group(1))
            self.assertIsNotNone(width)
            self.assertGreaterEqual(int(width.group(1)), 640)


if __name__ == "__main__":
    unittest.main()
