import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import perf_matrix  # noqa: E402


def cell(ident, **overrides):
    value = {
        "id": ident,
        "viewport": {"width": 375, "height": 812, "mobile": True},
        "network": "Slow 4G",
        "cpu_throttling": 4,
        "load_type": "cold",
        "metrics": {"lcp_ms": 799, "ttfb_ms": 565, "render_delay_ms": 234, "cls": 0.0},
        "notes": "",
    }
    value.update(overrides)
    return value


def payload(cells, thresholds=None):
    return {
        "schema_version": "1.0",
        "context": {
            "target_url": "https://example.invalid/app",
            "application_version": "v1",
            "environment": "受控环境",
            "measured_at": "2026-11-14T20:00:00+08:00",
        },
        "thresholds": thresholds or {
            "lcp_ms": 2500,
            "ttfb_ms": 800,
            "render_delay_ms": 1000,
            "cls": 0.1,
            "source": "Core Web Vitals 良好阈值，lab 单次结果仅作劣化对比",
        },
        "cells": cells,
    }


class PerfMatrixTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def trace(self, name="trace.json.gz", content=b"trace-bytes"):
        path = self.root / name
        path.write_bytes(content)
        return path, hashlib.sha256(content).hexdigest()

    def test_two_cells_export_csv_with_bom_and_uniform_columns(self):
        output = self.root / "matrix.csv"
        report = perf_matrix.export_csv(payload([cell("M-1"), cell("M-2", network="No throttling", cpu_throttling=1)]), output)
        self.assertEqual(report["cells_total"], 2)
        self.assertEqual(output.read_bytes()[:3], b"\xef\xbb\xbf")
        with output.open(encoding="utf-8-sig") as stream:
            rows = list(csv.reader(stream))
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(len(row) == len(rows[0]) for row in rows))
        self.assertIn("evidence_status", rows[0])

    def test_threshold_source_is_mandatory(self):
        bad = payload([cell("M-1")])
        del bad["thresholds"]["source"]
        with self.assertRaises(ValueError):
            perf_matrix.summarize(bad)

    def test_null_metric_is_not_collected_not_zero(self):
        item = cell("M-1", metrics={"lcp_ms": 799, "cls": None, "inp_ms": None})
        report = perf_matrix.summarize(payload([item]))
        row = report["cells"][0]
        self.assertEqual(row["verdicts"]["cls"], "not-collected")
        self.assertEqual(row["verdicts"]["inp_ms"], "not-collected")
        self.assertIsNone(row["values"]["cls"])
        self.assertEqual(row["overall"], "partial")

    def test_exceed_needs_verified_evidence(self):
        trace, digest = self.trace()
        item = cell("M-1", metrics={"lcp_ms": 4000, "cls": 0.0},
                    evidence={"trace_file": str(trace), "sha256": digest})
        report = perf_matrix.summarize(payload([item]))
        row = report["cells"][0]
        self.assertEqual(row["verdicts"]["lcp_ms"], "exceed")
        self.assertEqual(row["evidence_status"], "verified")
        self.assertTrue(row["measured"])
        self.assertEqual(row["overall"], "exceed")
        self.assertEqual(report["exceed_cells"], 1)

    def test_hash_mismatch_blocks_conclusion(self):
        trace, _ = self.trace()
        item = cell("M-1", metrics={"lcp_ms": 4000, "cls": 0.0},
                    evidence={"trace_file": str(trace), "sha256": "0" * 64})
        report = perf_matrix.summarize(payload([item]))
        row = report["cells"][0]
        self.assertEqual(row["evidence_status"], "mismatch")
        self.assertFalse(row["measured"])
        self.assertEqual(row["overall"], "exceed")
        self.assertEqual(row["conclusion"], "待核实")
        self.assertEqual(report["exceed_cells"], 0)

    def test_missing_evidence_stays_unverified(self):
        report = perf_matrix.summarize(payload([cell("M-1")]))
        row = report["cells"][0]
        self.assertEqual(row["evidence_status"], "no-evidence")
        self.assertFalse(row["measured"])
        self.assertEqual(report["unverified_cells"], 1)

    def test_missing_trace_file_is_flagged(self):
        item = cell("M-1", evidence={"trace_file": str(self.root / "nope.json.gz"), "sha256": "a" * 64})
        report = perf_matrix.summarize(payload([item]))
        self.assertEqual(report["cells"][0]["evidence_status"], "file-missing")

    def test_duplicate_id_rejected(self):
        with self.assertRaises(ValueError):
            perf_matrix.summarize(payload([cell("M-1"), cell("M-1", network="Fast 3G")]))

    def test_duplicate_condition_rejected(self):
        with self.assertRaises(ValueError):
            perf_matrix.summarize(payload([cell("M-1"), cell("M-2")]))

    def test_unknown_metric_key_rejected(self):
        with self.assertRaises(ValueError):
            perf_matrix.summarize(payload([cell("M-1", metrics={"lcp_ms": 100, "magic": 1})]))

    # —— 移动端设备身份（v1.53.0）：同视口不同 UA/DPR 是不同条件格 ——
    def test_same_viewport_different_ua_are_distinct_conditions(self):
        """照文档成套模拟 iOS 与 Android 两格，不得被判成重复条件。"""
        ios = cell("M-ios", device={"user_agent": "Mozilla/5.0 (iPhone; ...) Version/17.5 Mobile/15E148 Safari/604.1"})
        android = cell("M-android", device={"user_agent": "Mozilla/5.0 (Linux; Android 14; Pixel 7) Chrome/126.0.0.0 Mobile Safari/537.36"})
        report = perf_matrix.summarize(payload([ios, android]))
        self.assertEqual(report["cells_total"], 2)

    def test_same_viewport_different_dpr_are_distinct_conditions(self):
        report = perf_matrix.summarize(payload([cell("M-1", device={"dpr": 2}),
                                                cell("M-2", device={"dpr": 3})]))
        self.assertEqual(report["cells_total"], 2)

    def test_identical_device_still_rejected_as_duplicate(self):
        """设备身份相同才算重复：不能拿 UA 当"多跑一格"的免重复通行证。"""
        same = {"user_agent": "UA-1", "dpr": 3, "orientation": "portrait", "touch": True}
        with self.assertRaises(ValueError):
            perf_matrix.summarize(payload([cell("M-1", device=dict(same)),
                                           cell("M-2", device=dict(same))]))

    def test_platform_and_touch_points_also_participate_in_dedup(self):
        """审核修复：文档承诺「设备身份参与去重」，platform/max_touch_points 漏了就会
        把成套模拟的两格误判重复（同 UA/DPR 不同平台或触点数）。"""
        base = {"user_agent": "UA-1", "dpr": 3}
        report = perf_matrix.summarize(payload([
            cell("M-1", device=dict(base, platform="iPhone", max_touch_points=5)),
            cell("M-2", device=dict(base, platform="Linux armv8l", max_touch_points=5)),
            cell("M-3", device=dict(base, platform="iPhone", max_touch_points=10)),
        ]))
        self.assertEqual(report["cells_total"], 3)

    def test_legacy_cell_without_device_still_works(self):
        """旧矩阵无 device 字段：去重行为不变（两格同条件仍拒收）。"""
        with self.assertRaises(ValueError):
            perf_matrix.summarize(payload([cell("M-1"), cell("M-2")]))
        report = perf_matrix.summarize(payload([cell("M-1"),
                                                cell("M-2", network="Fast 3G")]))
        self.assertEqual(report["cells_total"], 2)
        self.assertEqual(report["cells"][0]["device"], {})

    def test_mobile_flag_participates_in_dedup(self):
        """审核修复：mobile 进键是有意修正——桌面/移动同视口不能被判成重复采集。"""
        report = perf_matrix.summarize(payload([
            cell("M-mobile"),
            cell("M-desktop", viewport={"width": 375, "height": 812, "mobile": False}),
        ]))
        self.assertEqual(report["cells_total"], 2)

    def test_device_field_values_are_validated(self):
        # 空串不得静默丢弃：显式给了就必须记下，否则两格不同配置会被并成一格。
        for bad in ({"orientation": "斜屏"}, {"orientation": ""}, {"user_agent": ""},
                    {"platform": ""}, {"dpr": 0}, {"touch": "yes"},
                    {"unknown_key": 1}, {"screen_w": 0}):
            with self.subTest(device=bad):
                with self.assertRaises(ValueError):
                    perf_matrix.summarize(payload([cell("M-1", device=bad)]))

    def test_device_identity_is_exported_to_csv(self):
        output = self.root / "matrix.csv"
        # screen 是 CSS 像素（window.screen.width/height），不是物理像素。
        item = cell("M-1", device={"user_agent": "UA-iOS", "dpr": 3, "screen_w": 390,
                                   "screen_h": 844, "orientation": "portrait",
                                   "touch": True, "max_touch_points": 5, "platform": "iPhone"})
        perf_matrix.export_csv(payload([item]), output)
        with output.open(encoding="utf-8-sig") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(rows[0]["user_agent"], "UA-iOS")
        self.assertEqual(rows[0]["dpr"], "3")
        self.assertEqual(rows[0]["screen_w"], "390")
        self.assertEqual(rows[0]["screen_h"], "844")
        self.assertEqual(rows[0]["orientation"], "portrait")
        self.assertEqual(rows[0]["max_touch_points"], "5")
        self.assertEqual(rows[0]["platform"], "iPhone")

    def test_negative_metric_rejected(self):
        with self.assertRaises(ValueError):
            perf_matrix.summarize(payload([cell("M-1", metrics={"lcp_ms": -1})]))

    def test_credentials_in_target_url_rejected(self):
        bad = payload([cell("M-1")])
        bad["context"]["target_url"] = "https://user:pw@example.invalid/app"
        with self.assertRaises(ValueError):
            perf_matrix.summarize(bad)

    def test_refuses_to_overwrite_existing_csv(self):
        output = self.root / "matrix.csv"
        output.write_text("existing", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            perf_matrix.export_csv(payload([cell("M-1")]), output)

    def test_csv_escapes_formula_like_text(self):
        output = self.root / "matrix.csv"
        perf_matrix.export_csv(payload([cell("M-1", notes="=cmd|calc")]), output)
        with output.open(encoding="utf-8-sig") as stream:
            rows = list(csv.DictReader(stream))
        self.assertTrue(rows[0]["notes"].startswith("'="))

    def test_cli_reports_summary(self):
        trace, digest = self.trace()
        data = payload([cell("M-1", evidence={"trace_file": str(trace), "sha256": digest})])
        source = self.root / "in.json"
        source.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        output = self.root / "out.csv"
        result = subprocess.run(
            [sys.executable, str(Path(perf_matrix.__file__)), str(source), "--output", str(output)],
            capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["cells_total"], 1)
        self.assertEqual(summary["measured_cells"], 1)
        self.assertTrue(output.exists())


if __name__ == "__main__":
    unittest.main()
