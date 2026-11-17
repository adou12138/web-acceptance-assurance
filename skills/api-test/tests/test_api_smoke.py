"""api-test 核心链路冒烟测试（unittest + CLI 子进程；全部离线，不发真实请求）。

覆盖：CLI 可用性 / 契约解析 / R1 文档审查门禁 / 计划模式生成 / R7 地址开关 /
16 列问题单口径 / schemas 完整性 / SKILL.md 契约（判路+红线+两套边界）/ 引用面无裸引用。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"
ASSETS = SKILL / "assets"
SCHEMAS = SKILL / "schemas"
FIX = Path(__file__).resolve().parent / "fixtures"
CONTRACT = FIX / "minimal_contract.md"
REVIEW = FIX / "doc_review_report.md"


def run(script, *args, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run([sys.executable, str(script), *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=e, cwd=str(SKILL))


class TestFlowCli(unittest.TestCase):
    def test_flow_help(self):
        p = run(SCRIPTS / "flow.py", "--help")
        self.assertEqual(p.returncode, 0, p.stderr[-300:])
        for token in ("--contract", "--execute", "--adapter", "--plan-only", "--out-dir"):
            self.assertIn(token, p.stdout)

    def test_other_entrypoints_help(self):
        for name in ("api_test_runner.py", "smoke_runner.py", "case_runner.py", "artifact_readback.py"):
            p = run(SCRIPTS / name, "--help")
            self.assertEqual(p.returncode, 0, f"{name}: {p.stderr[-200:]}")


class TestContractParse(unittest.TestCase):
    def test_parse_showdoc_action(self):
        sys.path.insert(0, str(SCRIPTS))
        from contract_parser import parse_showdoc
        contract = parse_showdoc(CONTRACT.read_text(encoding="utf-8-sig"))
        self.assertEqual(contract.get("action"), "1000107")
        self.assertIn("example.test", json.dumps(contract, ensure_ascii=False, default=str))


class TestPlanPipeline(unittest.TestCase):
    """R1 门禁 + 计划模式生成（离线）。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.work = Path(cls._tmp.name)
        cls.contract_dir = cls.work / "contract"
        cls.contract_dir.mkdir(parents=True)
        shutil.copy(CONTRACT, cls.contract_dir / "minimal_contract.md")
        shutil.copy(REVIEW, cls.contract_dir / "api_test_doc_review_report.md")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_plan_only_no_llm_facts_generates(self):
        """机械链路：--no-llm-facts 显式跳过语义层 → 计划模式产出用例。"""
        out = self.work / "out_mech"
        p = run(SCRIPTS / "flow.py", "--contract", self.contract_dir / "minimal_contract.md",
                "--plan-only", "--out-dir", out, "--no-llm-facts")
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-500:])
        produced = [f for f in out.rglob("*") if f.is_file()]
        self.assertTrue(produced, "计划模式（机械链路）应产出用例/计划文件")

    def test_semantic_layer_requires_facts(self):
        """M11 默认严格：未交语义事实 → 只出提纲 exit 4，不降级。"""
        out = self.work / "out_sem"
        p = run(SCRIPTS / "flow.py", "--contract", self.contract_dir / "minimal_contract.md",
                "--plan-only", "--out-dir", out)
        self.assertEqual(p.returncode, 4, (p.stdout + p.stderr)[-400:])
        combined = p.stdout + p.stderr
        self.assertIn("交作业", combined)
        prep = list(self.contract_dir.rglob("prep_prompt_*.md"))
        self.assertTrue(prep, "应生成语义层提纲文件")

    def test_doc_review_gate_blocks_execution(self):
        clean = self.work / "clean"
        clean.mkdir(exist_ok=True)
        shutil.copy(CONTRACT, clean / "minimal_contract.md")
        p = run(SCRIPTS / "flow.py", "--contract", clean / "minimal_contract.md", "--execute",
                "--out-dir", self.work / "out2", env={"WJX_BASE_URL": "https://example.test"})
        self.assertEqual(p.returncode, 3, f"stdout={p.stdout[-300:]} stderr={p.stderr[-300:]}")


class TestContractAssets(unittest.TestCase):
    def test_issue_template_columns(self):
        import csv
        with (ASSETS / "issue_template.csv").open(encoding="utf-8-sig", newline="") as stream:
            headers = next(csv.reader(stream))
        self.assertEqual(len(headers), 16, f"问题单应为 16 列，当前 {len(headers)}：{headers}")

    def test_finding_columns_json(self):
        data = json.loads((ASSETS / "finding_columns.json").read_text(encoding="utf-8-sig"))
        self.assertTrue(data)

    def test_schemas_valid(self):
        schemas = sorted(SCHEMAS.glob("*.json"))
        self.assertGreaterEqual(len(schemas), 7)
        for f in schemas:
            json.loads(f.read_text(encoding="utf-8-sig"))

    def test_sojump_adapter_absent(self):
        self.assertFalse((SKILL / "adapters" / "sojump.py").exists(), "站点适配器不应随包分发")
        self.assertFalse((ASSETS / "personal_config.json").exists(), "真实个人配置不应随包分发")
        self.assertTrue((ASSETS / "personal_config.example.json").exists())


class TestSkillContract(unittest.TestCase):
    """SKILL.md 契约：判路表 / 红线 / 冷启动 / 两套质量体系边界。"""

    @classmethod
    def setUpClass(cls):
        cls.text = (SKILL / "SKILL.md").read_text(encoding="utf-8")

    def test_judgement_table_and_redlines(self):
        self.assertIn("## 2. 判路", self.text)
        for i in range(1, 9):
            self.assertIn(f"**R{i}**", self.text)
        self.assertIn("## 4. 执行前决策点矩阵", self.text)
        self.assertIn("不得沿用上次运行", self.text)

    def test_two_quality_system_boundary(self):
        self.assertIn("不套用砺衡的 S0/G/V/COV/COV-PATH 门禁", self.text)
        self.assertIn("不与砺衡 cases JSON、执行台账合并", self.text)

    def test_cold_start_and_knowledge_base(self):
        self.assertIn("## 1. 冷启动", self.text)
        self.assertIn("personal_config.example.json", self.text)
        self.assertIn("本包不内置知识库路径", self.text)

    def test_no_bare_excluded_asset_refs(self):
        rules = [("smoke_cases_wjx.json", "smoke_cases_wjx.json（站点专属资产，未随本包分发）"),
                 ("smoke_config_wjx.json", "smoke_config_wjx.json（站点专属配置"),
                 ("business_flow_executor.py", "business_flow_executor.py（站点绑定入口，未随本包分发）")]
        texts = {p: p.read_text(encoding="utf-8") for p in (SKILL / "references").rglob("*.md")}
        for old, marked in rules:
            for p, text in texts.items():
                stripped = text.replace(marked, "")
                self.assertNotIn(old, stripped, f"{p.name} 存在裸引用 {old}")


class TestSmokeAndDependency(unittest.TestCase):
    """判路 4（冒烟离线链路）/ 判路 7（依赖静态扫描）/ 回读错误路径。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.work = Path(cls._tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_smoke_dry_run_with_examples(self):
        out = self.work / "reports"
        p = run(SCRIPTS / "smoke_runner.py", "--config", ASSETS / "smoke_config.example.json",
                "--cases", ASSETS / "smoke_cases.example.json", "--dry-run", "--out-dir", out)
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-400:])
        self.assertIn("dry-run 未发起调用", p.stdout)
        self.assertIn("前置条件不满足", p.stdout)  # 依赖链 skip_if 生效
        produced = [f.name for f in out.rglob("*") if f.is_file()]
        for kind in ("smoke_results_", "smoke_issues_", "smoke_report_", "smoke_summary_"):
            self.assertTrue(any(name.startswith(kind) for name in produced), f"缺产物 {kind}: {produced}")

    def test_dependency_scan_on_fixture(self):
        out = self.work / "deps.json"
        p = run(SCRIPTS / "dependency_scan.py", "--contract", CONTRACT, "--out", out)
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-300:])
        data = json.loads(out.read_text(encoding="utf-8-sig"))
        self.assertEqual(data.get("self_action"), "1000107")
        self.assertEqual(data.get("dependencies"), [])

    def test_readback_without_artifacts_is_error(self):
        p = run(SCRIPTS / "artifact_readback.py", "--dir", self.work / "empty")
        self.assertEqual(p.returncode, 1)
        self.assertIn("NO_ARTIFACTS", p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()