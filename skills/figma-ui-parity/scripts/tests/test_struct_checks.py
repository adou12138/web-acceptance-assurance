#!/usr/bin/env python3
"""脚本 E（alignment_modal_checks.js）产物必须真正落成缺陷或证据。

锁定的不变式：
1. `alignments` 里 pass=false 的项 → confirmed 缺陷，维度 7.13；
2. pass=true → **pass 证据**（"检查跑了且通过"不能退化成 notRun）；
3. `clippedControls` → confirmed，维度 7.15；
4. `suspiciousText` 只是候选 → pending，**绝不 confirmed**（防把正常文案判成缺陷）；
5. 段缺失 / 类型异常不崩，也不凭空产条目；
6. 产物能被 assemble_report.collect_findings 真实消费（端到端形状，而不是键名手抄）。
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))


def run_struct_checks(payload, tmp: Path) -> dict:
    src = tmp / "alignment.json"
    out = tmp / "alignment_findings.json"
    src.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    r = subprocess.run([sys.executable, str(SCRIPTS / "struct_checks.py"),
                        "--alignment", str(src), "--output", str(out)],
                       capture_output=True, text=True)
    if r.returncode != 0 or not out.exists():
        raise AssertionError("struct_checks 未产出（rc=%s）%s" % (r.returncode, r.stderr[-500:]))
    return json.loads(out.read_text(encoding="utf-8"))


def items(payload):
    return payload.get("findings") or []


class StructChecksTests(unittest.TestCase):
    def test_failed_alignment_becomes_confirmed_defect(self):
        payload = {"alignments": [{"kind": "required-mark", "sel": "label.x", "anchor": "手机号",
                                   "deltaCenterY": 4.5, "pass": False,
                                   "box": {"x": 10, "y": 20, "w": 60, "h": 14}}]}
        with tempfile.TemporaryDirectory() as td:
            got = run_struct_checks(payload, Path(td))
        rows = items(got)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["dimension"], "7.13")
        self.assertEqual(row["status"], "confirmed")
        self.assertIn("required-mark", row["category"])
        self.assertIn("4.5", json.dumps(row, ensure_ascii=False))

    def test_passed_alignment_yields_pass_evidence_not_silence(self):
        payload = {"alignments": [{"kind": "hint-left-align", "sel": ".hint", "anchor": "必填",
                                   "deltaLeft": 0.5, "pass": True}]}
        with tempfile.TemporaryDirectory() as td:
            got = run_struct_checks(payload, Path(td))
        rows = items(got)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "pass")
        self.assertEqual(rows[0]["dimension"], "7.13")

    def test_icon_text_center_kind_is_supported(self):
        payload = {"alignments": [{"kind": "icon-text-center", "sel": "button.add",
                                   "anchor": "新增", "deltaCenterY": -3.0, "pass": False}]}
        with tempfile.TemporaryDirectory() as td:
            got = run_struct_checks(payload, Path(td))
        row = items(got)[0]
        self.assertEqual(row["dimension"], "7.13")
        self.assertEqual(row["status"], "confirmed")
        self.assertIn("icon-text-center", row["category"])

    def test_clipped_control_is_confirmed(self):
        payload = {"clippedControls": [{"sel": "select.x", "anchor": "全部成员",
                                        "scrollW": 160, "clientW": 120}]}
        with tempfile.TemporaryDirectory() as td:
            got = run_struct_checks(payload, Path(td))
        row = items(got)[0]
        self.assertEqual(row["dimension"], "7.15")
        self.assertEqual(row["status"], "confirmed")
        self.assertIn("160", json.dumps(row, ensure_ascii=False))

    def test_suspicious_text_is_pending_never_confirmed(self):
        payload = {"suspiciousText": [{"sel": "p.t", "text": "aaaaaaaa"}]}
        with tempfile.TemporaryDirectory() as td:
            got = run_struct_checks(payload, Path(td))
        row = items(got)[0]
        self.assertEqual(row["dimension"], "7.15")
        self.assertEqual(row["status"], "pending")
        self.assertNotEqual(row["status"], "confirmed")

    def test_missing_or_malformed_sections_do_not_crash_or_invent(self):
        for payload in ({}, {"alignments": None}, {"alignments": ["not-a-dict"]},
                        {"clippedControls": {}}, {"suspiciousText": "x"}):
            with tempfile.TemporaryDirectory() as td:
                got = run_struct_checks(payload, Path(td))
            self.assertEqual(items(got), [], payload)

    def test_output_is_consumed_by_assemble_report(self):
        """端到端形状：真实调用 collect_findings，不用手抄键名。"""
        import assemble_report
        payload = {"alignments": [{"kind": "required-mark", "sel": "label.x", "anchor": "姓名",
                                   "deltaCenterY": 5.0, "pass": False},
                                  {"kind": "hint-left-align", "sel": ".hint", "anchor": "姓名",
                                   "deltaLeft": 1.0, "pass": True}]}
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "alignment_findings.json"
            run_struct_checks(payload, Path(td))
            evidence = {}
            assemble_report.collect_findings(out, evidence)
        rows = evidence.get("7.13") or []
        statuses = sorted(str(r.get("status")) for r in rows)
        self.assertIn("fail", statuses)
        self.assertIn("pass", statuses)


class AlignmentScriptWiringTests(unittest.TestCase):
    """脚本 E 必须真的算出图标与文字垂直居中，而不是只写在文档里。"""

    def test_icon_text_center_check_exists_in_script(self):
        src = (SCRIPTS / "alignment_modal_checks.js").read_text(encoding="utf-8")
        self.assertIn("icon-text-center", src,
                      "脚本 E 未产出 icon-text-center：7.13 的「图标与文字垂直居中」无检测通道")


if __name__ == "__main__":
    unittest.main()
