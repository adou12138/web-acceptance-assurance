#!/usr/bin/env python3
"""第 8 轮整体审查修复的锁定测试（F50 / F52 / F53）。

F50  findings 类产物（state_probes / extra_checks / journeys / runtime_observability）
      的条目不带 fingerprint、element 为 null——旧版 assemble_report 原样并入
      report.defects，validate_defects --strict 必挂（rc=6），管线门禁短路后
      三件套报告零产出。现由 assemble_report 收口补齐必填形状。
F52  start_test 的 URL 配对启动通道不看指令内容——「上个月用 Figma 比过这个
      页面」+ 双 URL 旧版直接 started=true（纯历史回顾启动完整走查）。
F53  run_parity_pipeline 的 --patterns 与 --no-patterns 同时给时后者静默胜出，
      用户指定的池文件被无声吞掉——改为显式互斥报错。
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[2]
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))

import start_test  # noqa: E402


class TestF50FindingsShapeBackfill(unittest.TestCase):
    """F50：findings 类条目经 assemble_report 后必须满足 defect.schema.json 必填形状。"""

    def test_findings_without_fingerprint_and_element_pass_strict(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            # state_probes 的真实产物形状：无 fingerprint、element=null、severity 来自 severityHint
            (tmpdir / "state_scenarios.json").write_text(json.dumps({
                "results": [{
                    "dimension": "7.25", "status": "fail",
                    "findings": [{
                        "dimension": "7.25",
                        "symptom": "深色模式下白底白字，文字不可读",
                        "expected": "文字与背景对比度不低于 4.5",
                        "actual": "对比度 1.1",
                        "severityHint": "P1", "severity": "P1",
                        "confidence": 0.9, "status": "confirmed",
                        "element": None,
                        "evidence": [{"type": "state-probe", "result": "mismatch"}],
                        "sources": ["state-probes"],
                        "property": "dark-contrast",
                    }],
                }],
            }, ensure_ascii=False), encoding="utf-8")
            report = tmpdir / "report.json"
            report.write_text(json.dumps({"meta": {"checks": {}}}), encoding="utf-8")

            a = subprocess.run(
                [sys.executable, str(SCRIPTS / "assemble_report.py"),
                 "--report", str(report), "--state-scenarios", str(tmpdir / "state_scenarios.json"),
                 "--write"], capture_output=True, text=True, timeout=120)
            self.assertEqual(a.returncode, 0, a.stderr)

            doc = json.loads(report.read_text(encoding="utf-8"))
            defects = doc.get("defects") or []
            self.assertEqual(len(defects), 1, "confirmed finding 必须进 defects")
            d = defects[0]
            # schema 必填三件套齐备
            self.assertTrue(d.get("fingerprint") and len(d["fingerprint"]) >= 8)
            self.assertIsInstance(d.get("element"), dict)
            self.assertIn("selector", d["element"])
            self.assertIsInstance(d["element"].get("bbox"), dict)
            self.assertIn(d.get("severity"), ("P0", "P1", "P2", "P3"))

            v = subprocess.run(
                [sys.executable, str(SCRIPTS / "validate_defects.py"),
                 "--report", str(report), "--strict"], capture_output=True, text=True, timeout=120)
            self.assertEqual(v.returncode, 0,
                            "补齐后必须过严格校验（旧版此处 rc=6，管线门禁短路、报告零产出）: "
                            + v.stdout + v.stderr)

    def test_real_element_and_fingerprint_not_overwritten(self):
        """已有真定位/指纹的条目不得被补齐逻辑改写。"""
        import assemble_report as AR
        item = {
            "dimension": "7.24", "status": "confirmed", "severity": "P2",
            "fingerprint": "abcdef1234567890",
            "element": {"selector": "img.hero", "nth": 2,
                        "bbox": {"x": 1, "y": 2, "w": 3, "h": 4}, "anchor": "banner"},
        }
        out = AR._ensure_defect_shape(item)
        self.assertEqual(out["fingerprint"], "abcdef1234567890")
        self.assertEqual(out["element"]["selector"], "img.hero")
        self.assertEqual(out["element"]["nth"], 2)
        self.assertEqual(out["element"]["bbox"], {"x": 1, "y": 2, "w": 3, "h": 4})


class TestF52UrlPairHistoricalGuard(unittest.TestCase):
    """F52：纯历史陈述指令 + URL 配对不得启动。"""

    def test_past_statement_with_urls_does_not_start(self):
        started, reason = start_test.should_start(
            "上个月用 Figma 比过这个页面",
            "https://www.figma.com/design/abc/x",
            ["https://devtest.example.test/page"])
        self.assertFalse(started, "纯历史回顾不得因 URL 配对误启动")
        self.assertEqual(reason, "not_a_test_start")

    def test_url_pair_without_instruction_still_starts(self):
        """无指令文本时 URL 配照常触发（SKILL.md 既定启动信号，不得回退）。"""
        started, reason = start_test.should_start(
            "", "https://www.figma.com/design/abc/x",
            ["https://devtest.example.test/page"])
        self.assertTrue(started)
        self.assertEqual(reason, "figma_and_page_urls_present")

    def test_positive_instruction_with_urls_still_starts(self):
        started, _ = start_test.should_start(
            "重新执行 UI Parity 走查",
            "https://www.figma.com/design/abc/x",
            ["https://devtest.example.test/page"])
        self.assertTrue(started)


class TestF53PatternsNoPatternsConflict(unittest.TestCase):
    """F53：--patterns 与 --no-patterns 互斥，同时给必须显式报错。"""

    def test_conflict_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [sys.executable, str(SCRIPTS / "run_parity_pipeline.py"),
                 "--workdir", tmp, "--patterns", "x.json", "--no-patterns"],
                capture_output=True, text=True, timeout=60)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("互斥", proc.stderr)


if __name__ == "__main__":
    unittest.main()
