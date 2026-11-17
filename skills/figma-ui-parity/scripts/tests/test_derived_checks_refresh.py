#!/usr/bin/env python3
"""报告必须吸收新证据：机器派生的 checks 重跑时重算，人工结论永久保留。

背景（这组测试锁住的真实故障）：
``report.json`` 里的 ``meta.checks`` 一旦由 ``assemble_report.py`` 写过一次，
第二轮就会把整份 checks 当成"人工结论"原样保留。于是补齐驱动产物、重跑管线后，
35 条 ``notRun`` 一条都不更新，覆盖率高永远停在第一轮的水平——报告呈现
"再怎么跑也覆盖不了"的假象，而每个阶段的 ``pipeline-log`` 都显示 rc=0。

判据只有一条：**这条结论是机器派生的，还是人写的**。前者必须重算，后者必须保留。
"""
from __future__ import annotations
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
ASSEMBLE = SCRIPTS / "assemble_report.py"


def run_assemble(report_path: Path, findings_paths: list[Path]) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(ASSEMBLE), "--report", str(report_path), "--write"]
    for f in findings_paths:
        cmd += ["--findings", str(f)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120)


class DerivedChecksRefreshTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.report = self.dir / "report.json"
        self.findings = self.dir / "findings.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_report(self, checks: dict) -> None:
        self.report.write_text(json.dumps({
            "url": "https://example.test/",
            "defects": [],
            "meta": {"checks": checks},
        }, ensure_ascii=False), encoding="utf-8")

    def _write_findings(self, dimension: str, status: str, symptom: str) -> None:
        self.findings.write_text(json.dumps({
            "findings": [{
                "dimension": dimension,
                "status": status,
                "symptom": symptom,
                "expected": "符合设计",
                "actual": "与期望不符",
                "severity": "P2",
                "confidence": 0.8,
                "element": {"selector": "#x", "nth": 0,
                            "bbox": {"x": 1, "y": 2, "w": 3, "h": 4}},
            }],
        }, ensure_ascii=False), encoding="utf-8")

    def _checks(self) -> dict:
        return json.loads(self.report.read_text(encoding="utf-8"))["meta"]["checks"]

    def test_derived_notrun_is_recomputed_when_evidence_arrives(self) -> None:
        """上一轮的机器 notRun 不能冻结：本轮有产物就必须改判。"""
        self._write_report({
            "7.4": {
                "status": "notRun",
                "reason": "无产物覆盖该维度（未提供对应驱动产物，或该维度无适用场景）",
                "evidence": {"sources": [], "count": 0},
            },
        })
        self._write_findings("7.4", "confirmed", "768px 下文档横向溢出")

        proc = run_assemble(self.report, [self.findings])
        self.assertEqual(proc.returncode, 0, proc.stderr)

        check = self._checks()["7.4"]
        self.assertEqual(check["status"], "fail",
                         "机器派生的 notRun 没有被本轮证据覆盖，报告仍在吃旧结论")

    def test_derived_profile_reason_is_recomputed(self) -> None:
        """档位改写的 notRun 也属于机器派生，换档位后原因必须跟着变。"""
        self._write_report({
            "7.4": {
                "status": "notRun",
                "reason": "档位 standard 要求该维度，但本轮未执行：无产物覆盖该维度",
                "profileRequiredButMissing": True,
                "evidence": {"sources": [], "count": 0},
            },
        })
        # 本轮仍无证据，但档位换成 smoke（不含 7.4）：reason 应改写成"档位未纳入"。
        proc = subprocess.run(
            [sys.executable, str(ASSEMBLE), "--report", str(self.report),
             "--write", "--profile", "smoke"],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)

        check = self._checks()["7.4"]
        self.assertIn("smoke", check["reason"],
                      "档位改写的派生态没有重算，上一轮的档位文案被冻结")
        self.assertTrue(check.get("profileExcluded"),
                        "smoke 不含 7.4，应标 profileExcluded")

    def test_manual_conclusion_is_preserved(self) -> None:
        """人工结论不得被机器证据顶掉——这是与上一条相反方向的保护。"""
        self._write_report({
            "7.1": {
                "status": "fail",
                "reason": "人工比对设计稿：主标题行高 46.94px，设计 56.33px",
                "note": "评审会已确认按缺陷处理",
            },
        })
        # 产物里 7.1 是 pass，机器不该覆盖人的判断。
        self._write_findings("7.1", "pass", "自动比对未发现差异")

        proc = run_assemble(self.report, [self.findings])
        self.assertEqual(proc.returncode, 0, proc.stderr)

        check = self._checks()["7.1"]
        self.assertEqual(check["status"], "fail", "人工结论被机器证据覆盖了")
        self.assertIn("人工比对", check["reason"])

    def test_derived_confidence_and_severity_are_normalized(self) -> None:
        """producer 用中文严重度且不带 confidence 时，收口必须补齐到 schema 词表。"""
        self._write_report({})
        self.findings.write_text(json.dumps({
            "findings": [{
                "dimension": "7.13",
                "status": "confirmed",
                "symptom": "提示文案左边缘未对齐",
                "expected": "左边缘差 ≤2px",
                "actual": "deltaLeft=6",
                "severity": "中",
                "element": {"selector": "span", "nth": 0,
                            "bbox": {"x": 1, "y": 2, "w": 3, "h": 4}},
            }],
        }, ensure_ascii=False), encoding="utf-8")

        proc = run_assemble(self.report, [self.findings])
        self.assertEqual(proc.returncode, 0, proc.stderr)

        defects = json.loads(self.report.read_text(encoding="utf-8"))["defects"]
        self.assertEqual(len(defects), 1)
        self.assertEqual(defects[0]["severity"], "P2",
                         "中文严重度没有归一到 P0-P3，validate_defects 会整轮拒绝出报告")
        self.assertIsInstance(defects[0]["confidence"], (int, float),
                              "confidence 是 schema 必填项，缺失会让门禁 rc=6")


if __name__ == "__main__":
    unittest.main()
