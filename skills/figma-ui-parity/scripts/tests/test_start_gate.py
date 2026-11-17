import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "start_test.py"


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)


class StartGateTests(unittest.TestCase):
    def test_consultation_does_not_start_environment_check(self):
        proc = run("--instruction", "解释 playwright-core 和 MCP 的区别")
        self.assertEqual(proc.returncode, 0)
        result = json.loads(proc.stdout)
        self.assertFalse(result["started"])
        self.assertEqual(result["trigger"], "not_a_test_start")

    def test_explicit_test_start_requires_safety_policy_before_environment_check(self):
        proc = run("--instruction", "开始执行 Figma UI parity 测试", "--figma-url", "https://www.figma.com/design/abc/x")
        self.assertEqual(proc.returncode, 2)
        result = json.loads(proc.stdout)
        self.assertTrue(result["started"])
        self.assertEqual(result["reason"], "safety_policy_required")

    def test_responsive_check_counts_as_test_start(self):
        proc = run("--instruction", "帮我看看这个页面的响应式有没有问题")
        self.assertEqual(proc.returncode, 2)
        result = json.loads(proc.stdout)
        self.assertTrue(result["started"])
        self.assertEqual(result["trigger"], "explicit_test_instruction")
        self.assertEqual(result["reason"], "safety_policy_required")

    def test_breakpoint_check_counts_as_test_start(self):
        proc = run("--instruction", "检查一下各断点下的适配")
        self.assertEqual(proc.returncode, 2)
        result = json.loads(proc.stdout)
        self.assertTrue(result["started"])

    def test_bare_topic_mention_does_not_start(self):
        proc = run("--instruction", "响应式是怎么回事")
        self.assertEqual(proc.returncode, 0)
        result = json.loads(proc.stdout)
        self.assertFalse(result["started"])
        self.assertEqual(result["trigger"], "not_a_test_start")

    def test_ui_walkthrough_counts_as_test_start(self):
        proc = run("--instruction", "帮我走查一下这个页面有没有 bug")
        self.assertEqual(proc.returncode, 2)
        result = json.loads(proc.stdout)
        self.assertTrue(result["started"])
        self.assertEqual(result["trigger"], "explicit_test_instruction")

    def test_url_pair_start_writes_manifest_after_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run(
                "--figma-url", "https://www.figma.com/design/abc/x",
                "--page-url", "https://devtest.example.test/page",
                "--safety-policy", "staticOnly",
                "--user-safety-selection", "仅静态检查",
                "--root", tmp,
                "--workdir", "parity-test",
            )
            # 【审查修复·环境耦合】依赖不齐的环境（如未装 playwright/node 的机器、
            # 低版本 Python）上 start_test 走 unavailable 分支返回 3（F49：阻断但
            # manifest 必须照写）——旧版无条件断言 rc==0，在这种环境必红。
            # rc∈{0,3} 都是正常门控行为，分别锁定各自分支的完整语义。
            self.assertIn(proc.returncode, (0, 3), proc.stderr)
            result = json.loads(proc.stdout)
            self.assertTrue(result["started"])
            manifest = json.loads((Path(tmp) / "parity-test" / "run-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["safetyPolicy"], "staticOnly")
            self.assertFalse(manifest["testEnvironmentConfirmed"])
            self.assertEqual(manifest["writeInterception"], "abort")
            if proc.returncode == 0:
                # 【剥离适配·三态】needs_setup/partial 环境下 start_test 返回
                # degraded（start_test.py:293-298：仅 unavailable 才 blocked）——
                # 源环境 playwright-core 常备只见过 ready；剥离后 manifest 照写、
                # 策略落盘的断言在 degraded 分支同样成立。
                self.assertIn(result["status"], ("ready", "degraded"))
            else:
                self.assertEqual(result["status"], "blocked")


class TestRound2HistoricalClause(unittest.TestCase):
    """【审查修复·P2-17】历史陈述分句不得充当启动触发。

    实证：「上个版本走查过了，这次不用再走查」——第一分句无否定词，旧版
    positive_clause 原样保留，TRIGGERS 裸词「走查」命中 ⇒ started=true。
    """

    @classmethod
    def setUpClass(cls):
        if str(SCRIPT.parent) not in sys.path:
            sys.path.insert(0, str(SCRIPT.parent))
        import start_test
        cls.st = start_test

    def test_past_statement_plus_refusal_rejected(self):
        started, reason = self.st.should_start("上个版本走查过了，这次不用再走查", None, [])
        self.assertFalse(started, "拒绝重测的指令被误判为启动")
        self.assertEqual(reason, "negated_test_instruction")

    def test_resume_with_past_word_still_starts(self):
        self.assertTrue(self.st.should_start("继续上次的走查", None, [])[0])
        self.assertTrue(self.st.should_start("已经走查过一半了，继续走查", None, [])[0])

    def test_past_consult_not_a_start(self):
        started, reason = self.st.should_start("上个版本的走查报告在哪", None, [])
        self.assertFalse(started)
        self.assertEqual(reason, "not_a_test_start")

    def test_negation_then_start_no_regression(self):
        """第一轮 F43a 语义不回退：否定分句 + 启动分句仍放行。"""
        self.assertTrue(self.st.should_start("停止响应式适配检查，现在开始 UI Parity", None, [])[0])


class TestPromptContract(unittest.TestCase):
    """test-prompts.json 的三条典型指令必须与启动门禁一致。

    这些 prompt 是 skill 的对外契约；此前第 2 条（单页面 URL + 响应式/断点）
    在实现里被判 not_a_test_start，契约与代码背离却无人发现。
    """

    @classmethod
    def setUpClass(cls):
        if str(SCRIPT.parent) not in sys.path:
            sys.path.insert(0, str(SCRIPT.parent))
        import start_test
        cls.st = start_test
        cls.prompts = {str(p["id"]): p["prompt"] for p in
                       json.loads((ROOT / "test-prompts.json").read_text(encoding="utf-8"))}

    def test_p1_figma_vs_page_starts_by_url_pair(self):
        started, _ = self.st.should_start(
            self.prompts["1"], "https://www.figma.com/design/abc/x",
            ["https://devtest.example.test/page"])
        self.assertTrue(started, "设计稿+页面场景未启动")

    def test_p2_page_only_responsive_starts(self):
        """目标词在前、疑问语气在后（「不同断点下的响应式有没有问题」）。"""
        started, reason = self.st.should_start(self.prompts["2"], None,
                                               ["https://devtest.example.test/page"])
        self.assertTrue(started, "单页面 URL + 响应式/断点诉求被判成咨询")
        self.assertEqual(reason, "explicit_test_instruction")

    def test_p3_walkthrough_without_design_starts(self):
        started, reason = self.st.should_start(self.prompts["3"], None,
                                               ["https://devtest.example.test/page"])
        self.assertTrue(started, "无设计稿走查未启动")
        self.assertEqual(reason, "explicit_test_instruction")


if __name__ == "__main__":
    unittest.main()
