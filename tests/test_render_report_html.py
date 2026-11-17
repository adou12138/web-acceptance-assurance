import base64
import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "render_report_html.py"

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8AAAwAB/AL+2QAAAABJRU5ErkJggg=="
)

COLUMNS = ["view", "id", "requirement_ids", "case_id", "target_url", "application_version",
           "environment", "page_url", "role", "viewport", "state", "action", "risk", "status",
           "attributes", "observation", "evidence_paths", "evidence", "blocked_reason",
           "side_effect_pending"]


def row(view, ident, action, risk="high", status="NOT_RUN", viewport="1280x800"):
    return {
        "view": view, "id": ident, "requirement_ids": '["REQ-1"]', "case_id": "",
        "target_url": "https://example.invalid/app", "application_version": "build-1",
        "environment": "本地受控", "page_url": "https://example.invalid/app/form", "role": "访客",
        "viewport": viewport, "state": "观察状态", "action": action, "risk": risk,
        "status": status, "attributes": '["visual"]', "observation": "", "evidence_paths": "[]",
        "evidence": "[]", "blocked_reason": "", "side_effect_pending": "false",
    }


def gate_payload(planned, pending, missing, ceiling="待核实"):
    return {
        "schema_version": "1.0",
        "planned": planned,
        "counts": {"NOT_RUN": planned},
        "gate": {
            "ready_pending": pending,
            "ready_pending_ids": [],
            "ready_high_risk": 1,
            "missing_views": missing,
            "verdict_ceiling": ceiling,
            "message": "仍有项未执行",
        },
    }


class RenderReportHtmlTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), "缺少功能：把台账与证据渲染成单文件 HTML 报告")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)

    def write_csv(self, name, rows):
        path = self.folder / name
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def write_json(self, name, payload):
        path = self.folder / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                              capture_output=True, text=True, encoding="utf-8")

    def standard(self, output, *extra):
        return ["--output", str(output), "--title", "验收报告", "--subject", "受控站", *extra]

    def test_renders_single_file_with_inlined_screenshot(self):
        ledger = self.write_csv("ledger.csv", [
            row("界面样式", "OBS-a", "复核截断文本"),
            row("基础无障碍", "OBS-a", "复核截断文本"),
            row("界面样式", "OBS-b", "复核图片失效", risk="medium"),
        ])
        gate = self.write_json("gate.json", gate_payload(2, 2, ["功能", "性能"]))
        shot = self.folder / "shot.png"
        shot.write_bytes(PNG_1PX)
        output = self.folder / "report.html"
        result = self.run_cli(*self.standard(
            output,
            "--dataset", f"桌面::" + str(ledger) + "::" + str(gate),
            "--screenshot", f"{shot}::桌面全页::核对断图与截断",
        ))
        self.assertEqual(result.returncode, 0, result.stderr)
        page = output.read_text(encoding="utf-8")
        self.assertIn("验收报告", page)
        self.assertIn("data:image/png;base64,", page, "截图必须内联，否则单文件搬移后图片会裂")
        self.assertIn("OBS-a", page)
        self.assertIn("OBS-b", page)
        self.assertIn("待核实", page)
        self.assertIn("功能", page)
        self.assertIn("界面样式、基础无障碍", page, "同一记录多视角应合并展示而不是重复成两行")

    def test_counts_and_view_totals_come_from_data(self):
        ledger = self.write_csv("ledger.csv", [
            row("界面样式", "OBS-a", "动作A"),
            row("界面样式", "OBS-b", "动作B"),
            row("交互易用性", "OBS-b", "动作B"),
        ])
        gate = self.write_json("gate.json", gate_payload(2, 2, ["功能"]))
        output = self.folder / "report.html"
        result = self.run_cli(*self.standard(
            output, "--dataset", f"桌面::{ledger}::{gate}"))
        self.assertEqual(result.returncode, 0, result.stderr)
        page = output.read_text(encoding="utf-8")
        self.assertIn(">2<", page)
        self.assertIn("观察候选（去重）", page)

    def test_html_escapes_fields_instead_of_injecting_markup(self):
        ledger = self.write_csv("ledger.csv", [
            row("界面样式", "OBS-x", "<script>alert(1)</script>"),
        ])
        gate = self.write_json("gate.json", gate_payload(1, 1, []))
        output = self.folder / "report.html"
        result = self.run_cli(*self.standard(
            output, "--dataset", f"桌面::{ledger}::{gate}"))
        self.assertEqual(result.returncode, 0, result.stderr)
        page = output.read_text(encoding="utf-8")
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_refuses_overwrite_and_missing_inputs(self):
        ledger = self.write_csv("ledger.csv", [row("界面样式", "OBS-a", "动作A")])
        gate = self.write_json("gate.json", gate_payload(1, 1, []))
        existing = self.folder / "exists.html"
        existing.write_text("keep", encoding="utf-8")
        result = self.run_cli(*self.standard(existing, "--dataset", f"桌面::{ledger}::{gate}"))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(existing.read_text(encoding="utf-8"), "keep")
        missing = self.folder / "new.html"
        bad = self.run_cli(*self.standard(
            missing, "--dataset", f"桌面::{self.folder / 'nope.csv'}::{gate}"))
        self.assertEqual(bad.returncode, 2)
        self.assertFalse(missing.exists())
        self.assertTrue(bad.stderr.strip())

    def test_lists_evidence_fingerprints_when_directory_given(self):
        ledger = self.write_csv("ledger.csv", [row("界面样式", "OBS-a", "动作A")])
        gate = self.write_json("gate.json", gate_payload(1, 1, []))
        evidence = self.folder / "evidence"
        evidence.mkdir()
        artifact = evidence / "trace.json.gz"
        artifact.write_bytes(b"trace-bytes")
        output = self.folder / "report.html"
        result = self.run_cli(*self.standard(
            output, "--dataset", f"桌面::{ledger}::{gate}", "--evidence-dir", evidence))
        self.assertEqual(result.returncode, 0, result.stderr)
        page = output.read_text(encoding="utf-8")
        self.assertIn("trace.json.gz", page)
        self.assertIn("证据文件", page)

    def test_performance_summary_is_rendered_when_provided(self):
        ledger = self.write_csv("ledger.csv", [row("性能", "OBS-performance", "补采未覆盖性能族")])
        gate = self.write_json("gate.json", gate_payload(1, 1, []))
        perf = self.write_json("perf.json", {
            "source": "DevTools 性能追踪",
            "environment": "本地",
            "runs": [{"kind": "加载型", "metrics": [{"name": "LCP", "value": "61 ms", "note": "首屏"}]}],
            "notAvailable": ["真实用户分位数"],
        })
        output = self.folder / "report.html"
        result = self.run_cli(*self.standard(
            output, "--dataset", f"桌面::{ledger}::{gate}", "--performance", perf))
        self.assertEqual(result.returncode, 0, result.stderr)
        page = output.read_text(encoding="utf-8")
        self.assertIn("LCP", page)
        self.assertIn("61 ms", page)
        self.assertIn("真实用户分位数", page)


if __name__ == "__main__":
    unittest.main()
