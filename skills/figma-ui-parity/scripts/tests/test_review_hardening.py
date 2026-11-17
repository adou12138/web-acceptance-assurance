# -*- coding: utf-8 -*-
"""2026-11-02 整体审查修复的回归锁：安全策略短路、凭据脱敏、warn 严重度。"""
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))


class TestProbePolicyContract(unittest.TestCase):
    """run_page_probes 是管线默认必跑驱动，staticOnly 下不得点击或 hover。

    该脚本是页面内注入型 IIFE，无法离线实例化；用源码契约锁住两个短路点。
    """

    def setUp(self):
        self.text = (SCRIPTS / "node" / "run_page_probes.cjs").read_text(encoding="utf-8")

    def test_click_short_circuits_static_only(self):
        self.assertIn("if (SAFETY_POLICY === 'staticOnly') return [];", self.text,
                      "staticOnly 下 clickSafeButtons 必须直接返回空")

    def test_hover_is_guarded_by_policy(self):
        self.assertIn("if (SAFETY_POLICY !== 'staticOnly') {", self.text,
                      "hover 可能打开浮层，staticOnly 下必须跳过")


class TestCredentialRedaction(unittest.TestCase):
    def test_start_test_redacts_url_credentials(self):
        import start_test
        red = start_test._redact("https://p.test/a?token=abc123&theme=dark")
        self.assertIn("token=[REDACTED]", red)
        self.assertIn("theme=dark", red)

    def test_pipeline_redacts_command_credentials(self):
        import run_parity_pipeline as RPP
        out = RPP.redact_text("--url https://p.test/a?sessionid=zzz --x 1")
        self.assertIn("sessionid=[REDACTED]", out)
        self.assertIn("--x 1", out)

    def test_bearer_header_is_redacted(self):
        import start_test
        self.assertIn("[REDACTED]", start_test._redact("Authorization: Bearer abc.def"))


class TestWarnSeverity(unittest.TestCase):
    def test_warn_ranks_above_not_run(self):
        import assemble_report
        order = assemble_report.SEVERITY_ORDER
        self.assertIn("warn", order)
        self.assertLess(order.index("warn"), order.index("notRun"),
                        "已执行的 warn 不得被 notRun 覆盖")


if __name__ == "__main__":
    unittest.main()