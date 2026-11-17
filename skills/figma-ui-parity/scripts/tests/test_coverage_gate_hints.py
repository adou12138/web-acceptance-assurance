# -*- coding: utf-8 -*-
"""覆盖率门禁的修复提示：缺口要能直接照做，而不是只知道「缺什么」。"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]


def _run(report):
    with tempfile.TemporaryDirectory() as td:
        rp = Path(td) / "report.json"
        rp.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        r = subprocess.run([sys.executable, str(SCRIPTS / "coverage_gate.py"),
                            "--report", str(rp)], capture_output=True, text=True)
    if not r.stdout.strip().startswith("{"):
        raise AssertionError("rc=%s 没有输出覆盖结论：%s" % (r.returncode, r.stderr[-300:]))
    return json.loads(r.stdout)


class CoverageFixHintTests(unittest.TestCase):
    def test_missing_and_unexplained_carry_hints(self):
        """7.9 完全缺失 + 7.20 notRun 无原因：两类缺口各有各的补法。"""
        cov = _run({"url": "https://p.test/", "checks": {
            "7.20": {"status": "notRun"}}})
        hints = cov["fixHints"]
        self.assertEqual(len(hints), len(cov["missingDimensions"]) + len(cov["unexplained"]),
                         "每条缺口都要有且只有一条提示，不能漏也不能凭空多")
        reason_hint = [h for h in hints if h["dimension"] == "7.20"]
        self.assertTrue(reason_hint, "notRun 无原因的维度要能被索引到")
        self.assertIn("reason", reason_hint[0]["hint"])
        self.assertTrue(any("产物" in h["hint"] or "N/A" in h["hint"]
                            for h in hints if h["dimension"] != "7.20"),
                        "完全缺失的维度应提示补产物或显式标 N/A")

    def test_hints_do_not_change_verdict(self):
        """加提示不改判定：missingDimensions 仍是唯一判据。"""
        cov = _run({"url": "https://p.test/", "checks": {"7.20": {"status": "notRun"}}})
        self.assertIn("7.20", [h["dimension"] for h in cov["fixHints"]])
        self.assertTrue(cov["missingDimensions"], "缺口判定不受提示影响")


if __name__ == "__main__":
    unittest.main()
