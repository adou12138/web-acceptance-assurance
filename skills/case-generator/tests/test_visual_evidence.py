"""原型图证据：extract_docx_media 解包 + review_check 的 visual_evidence 校验。"""

import copy
import json
import sys
import tempfile
import zipfile
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _util import REVIEW_PASS, load  # noqa: E402

import review_check as rc  # noqa: E402
import extract_docx_media as edm  # noqa: E402

HASH = "a" * 64


def make_docx(path, images=("word/media/image1.png", "word/media/image2.png")):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        for name in images:
            archive.writestr(name, b"\x89PNG\r\n\x1a\n" + name.encode())
    return path


class ExtractMediaTests(unittest.TestCase):
    def test_extracts_and_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            docx = make_docx(Path(tmp) / "req.docx")
            manifest = edm.extract(docx, Path(tmp) / "work")
            self.assertEqual(manifest["count"], 2)
            for item in manifest["images"]:
                self.assertEqual(len(item["sha256"]), 64)
                self.assertTrue(Path(item["file"]).is_file())
            self.assertTrue((Path(tmp) / "work" / "media_manifest.json").is_file())

    def test_manifest_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            docx = make_docx(Path(tmp) / "req.docx")
            first = edm.extract(docx, Path(tmp) / "a")
            second = edm.extract(docx, Path(tmp) / "b")
            self.assertEqual([i["sha256"] for i in first["images"]],
                             [i["sha256"] for i in second["images"]])

    def test_non_docx_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            txt = Path(tmp) / "req.txt"
            txt.write_text("需求正文", encoding="utf-8")
            with self.assertRaises(ValueError):
                edm.extract(txt, Path(tmp) / "work")

    def test_broken_zip_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "fake.docx"
            bad.write_bytes(b"not a zip")
            with self.assertRaises(ValueError):
                edm.extract(bad, Path(tmp) / "work")


class VisualEvidenceCheckTests(unittest.TestCase):
    def setUp(self):
        self.base = load(REVIEW_PASS)

    def with_visual(self, entry):
        cases = copy.deepcopy(self.base)
        cases["visual_evidence"] = [entry]
        return rc.check(cases)

    def valid_entry(self, **overrides):
        entry = {
            "file": "media/img01.png",
            "sha256": HASH,
            "type": "ui_mockup",
            "extracted_rules": ["回收站列表需显示类型标签"],
            "confidence": 0.8,
            "boundary": "截图仅覆盖列表首屏，未含分页器",
            "image_understood": True,
        }
        entry.update(overrides)
        return entry

    def test_valid_visual_evidence_passes(self):
        errors, warnings, _ = self.with_visual(self.valid_entry())
        self.assertEqual(errors, [])

    def test_bad_sha256_rejected(self):
        errors, _, _ = self.with_visual(self.valid_entry(sha256="xyz"))
        self.assertTrue(any("sha256" in e for e in errors))

    def test_unknown_type_rejected(self):
        errors, _, _ = self.with_visual(self.valid_entry(type="photo"))
        self.assertTrue(any("type" in e for e in errors))

    def test_confidence_out_of_range_rejected(self):
        errors, _, _ = self.with_visual(self.valid_entry(confidence=1.5))
        self.assertTrue(any("confidence" in e for e in errors))

    def test_understood_without_rules_warns(self):
        errors, warnings, _ = self.with_visual(self.valid_entry(extracted_rules=[]))
        self.assertEqual(errors, [])
        self.assertTrue(any("未提取任何规则" in w for w in warnings))

    def test_not_understood_keeps_gap_without_error(self):
        errors, warnings, _ = self.with_visual(
            self.valid_entry(image_understood=False, extracted_rules=[],
                             boundary="Word 中该图为 EMF 矢量图，当前无法解码"))
        self.assertEqual(errors, [])

    def test_missing_visual_evidence_is_allowed(self):
        errors, _, _ = rc.check(copy.deepcopy(self.base))
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
