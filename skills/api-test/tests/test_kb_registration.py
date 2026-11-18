"""知识库根路径登记契约测试（纯文档级，不依赖本机知识库是否实际存在）。

锁三件事：
1. 登记路径单一出处（只在 doc_review.md，asset_inventory 只引用不复制）；
2. 使用纪律齐全（存在性检查、不得凭记忆补契约、检索为空如实留缺口）；
3. 非契约类领域事实指向 wjx-knowledge-base skill，不在本包复制。
路径变更时本测试失败——这是故意的：挪库必须同步改登记并知晓影响面。
"""

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
DOC_REVIEW = SKILL / "references" / "doc_review.md"
ASSET_INVENTORY = SKILL / "references" / "asset_inventory.md"

REGISTERED_PATH = r"D:\TestCase\MySharedSkills\qa-req2testcase-generator\user_knowledge"


def read(p):
    return p.read_text(encoding="utf-8")


class KbRootRegistration(unittest.TestCase):
    def test_registered_path_exists_and_is_single_source(self):
        doc = read(DOC_REVIEW)
        self.assertIn(REGISTERED_PATH, doc, "doc_review.md 必须登记本机知识库根路径")
        self.assertEqual(
            doc.count(REGISTERED_PATH), 1,
            "登记路径应只出现一次（单一出处），重复即漂移")
        asset = read(ASSET_INVENTORY)
        self.assertNotIn(REGISTERED_PATH, asset,
                         "asset_inventory 只引用登记，不复制路径")

    def test_registration_section_is_declared(self):
        doc = read(DOC_REVIEW)
        self.assertIn("本机知识库根路径", doc)
        # 必须说明脚本自动扫 project/ 子目录
        self.assertIn("project/", doc)

    def test_usage_discipline_is_complete(self):
        doc = read(DOC_REVIEW)
        for phrase in ("存在性检查", "不得凭记忆补契约", "知识库无记录", "降级"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, doc)

    def test_non_contract_facts_delegate_to_wjx_skill(self):
        """题型/帮助中心/历史需求等领域事实指向 wjx-knowledge-base，不复制内容。"""
        doc = read(DOC_REVIEW)
        self.assertIn("wjx-knowledge-base", doc)

    def test_asset_inventory_references_registration(self):
        asset = read(ASSET_INVENTORY)
        self.assertIn("kb_load_contract.py", asset)
        self.assertIn("doc_review.md", asset)
        self.assertIn("KB_PATH", asset)


class StageWiring(unittest.TestCase):
    """三阶段衔接契约：api-test（登记+纪律）、requirement-review（历史一致）、case-generator（约束来源）。"""

    PACKAGE = SKILL.parents[1]

    def test_distilled_is_index_only_discipline(self):
        """蒸馏摘要偏差教训（R91 曾把响应字段当必填）必须固化为纪律，不能只在会话里。"""
        doc = read(DOC_REVIEW)
        self.assertIn("原文为准，蒸馏只作索引", doc)
        self.assertIn("showdoc_api_docs/", doc)

    def test_requirement_review_uses_unified_var_and_delegates(self):
        rr = read(self.PACKAGE / "skills" / "requirement-review" / "SKILL.md")
        self.assertIn("KB_PATH", rr, "评审侧应使用统一的 KB_PATH 变量名")
        self.assertNotIn("WJX_KB_PATH", rr, "旧变量名已废弃，双名并存即漂移")
        self.assertIn("wjx-knowledge-base", rr, "非契约领域事实必须委派给检索 skill")
        self.assertIn("知识库无记录", rr)

    def test_case_generator_binds_constraint_source_to_contract(self):
        cg = read(self.PACKAGE / "skills" / "case-generator" / "SKILL.md")
        self.assertIn("kb_load_contract.py", cg)
        self.assertIn("禁止凭记忆编造约束", cg)
        self.assertIn("蒸馏", cg)
        self.assertIn("skills/api-test/references/doc_review.md", cg)
        # 被引用的登记文件必须真实存在
        self.assertTrue((self.PACKAGE / "skills" / "api-test" / "references" / "doc_review.md").is_file())

    def test_prd_historical_dimension_stays_disciplined(self):
        """历史一致维度依赖知识库对照材料，出处纪律必须仍在。"""
        prd = read(self.PACKAGE / "skills" / "requirement-review" / "references" / "prd-dimensions.md")
        self.assertIn("historical_consistency", prd)
        self.assertIn("不得凭记忆", prd)




class KbPathBehaviorResolution(unittest.TestCase):
    """P0 回归（行为级，离线夹具）：--kb > KB_PATH > 同级候选 的解析顺序。

    docstring 声明"实际仅 KB_PATH 生效"，此前 flow.py / business_flow.py
    的调用路径漏掉了这一层（审核意见 P0-1/P0-2）；本类锁定修复后的行为。
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        base = Path(cls._tmp.name)
        cls.kb = base / "user_knowledge"
        docs = cls.kb / "project" / "showdoc_api_docs" / "demo"
        docs.mkdir(parents=True)
        (docs / "033_2.1.14 创建AI主页[1000107].md").write_text(
            "# 2.1.14 创建AI主页[1000107]\n\n|参数|类型|说明|必需|备注|\n"
            "|---|---|---|---|---|\n|html_content|string|AI主页HTML内容|是|最长200000个字符|\n",
            encoding="utf-8")
        cls.flows = cls.kb / "project" / "business_flows"
        cls.flows.mkdir(parents=True)
        cls.scripts = str(SKILL / "scripts")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _run_py(self, code, kb_path):
        env = dict(os.environ)
        env.pop("KB_PATH", None)
        if kb_path is not None:
            env["KB_PATH"] = str(kb_path)
        return subprocess.run([sys.executable, "-c", code], capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              env=env, timeout=120, cwd=str(SKILL))

    def _run_cli(self, script, args, kb_path):
        env = dict(os.environ)
        env.pop("KB_PATH", None)
        if kb_path is not None:
            env["KB_PATH"] = str(kb_path)
        return subprocess.run([sys.executable, str(SKILL / "scripts" / script), *args],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=env, timeout=120, cwd=str(SKILL))

    def test_flow_resolves_via_kb_path_env(self):
        code = (f"import sys; sys.path.insert(0, r'{self.scripts}'); import flow; "
                f"print(flow.resolve_contract_from_kb('1000107'))")
        r = self._run_py(code, self.kb)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1000107", r.stdout)
        self.assertIn(str(self.kb), r.stdout)

    def test_flow_fails_closed_without_env_or_candidates(self):
        code = (f"import sys; sys.path.insert(0, r'{self.scripts}'); import flow; "
                f"print(flow.resolve_contract_from_kb('1000107'))")
        r = self._run_py(code, None)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("None", r.stdout.strip())

    def test_explicit_kb_beats_env(self):
        """显式 --kb 优先于 KB_PATH：指向另一个夹具时解析结果跟随显式值。"""
        other = Path(self._tmp.name) / "other_kb"
        docs = other / "project" / "showdoc_api_docs" / "demo"
        docs.mkdir(parents=True)
        (docs / "other_1000107.md").write_text("x", encoding="utf-8")
        code = (f"import sys; sys.path.insert(0, r'{self.scripts}'); import flow; "
                f"print(flow.resolve_contract_from_kb('1000107', r'{other}'))")
        r = self._run_py(code, self.kb)
        self.assertIn(str(other), r.stdout)

    def test_kb_load_contract_cli_uses_kb_path_env(self):
        r = self._run_cli("kb_load_contract.py", ["--action", "1000107", "--no-print"], self.kb)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("KB_PATH 环境变量", r.stdout)
        self.assertIn("1000107", r.stdout)

    def test_business_flow_accepts_kb_path_env_fallback(self):
        r = self._run_cli("business_flow.py", ["--query", "x"], self.kb)
        # 业务流目录为空 → 走到"未找到"分支（returncode 1），而不是 argparse 错误
        self.assertEqual(r.returncode, 1, r.stderr or r.stdout)
        self.assertIn("未找到业务流程定义", r.stdout)

    def test_business_flow_still_errors_without_any_kb(self):
        r = self._run_cli("business_flow.py", ["--query", "x"], None)
        self.assertEqual(r.returncode, 2)
        self.assertIn("必须二选一", r.stderr)

    def test_business_flow_two_explicit_args_still_error(self):
        r = self._run_cli("business_flow.py", ["--flow", "x.json", "--kb", "y"], self.kb)
        self.assertEqual(r.returncode, 2)
        self.assertIn("必须二选一", r.stderr)


if __name__ == "__main__":
    unittest.main()


class AiPageDistilledCorrections(unittest.TestCase):
    """锁定 2026-11-18 的 KB 纠错：AI 主页三条规则的请求/响应分离与双登记。"""

    KB = Path("D:/TestCase/MySharedSkills/qa-req2testcase-generator/user_knowledge/project/distilled")

    def test_r91_r92_request_response_separated(self):
        for stem in ("M20_R091_2.1.14 创建AI主页[1000107].md",
                     "M20_R092_2.1.15 更新AI主页[1000108].md"):
            with self.subTest(rule=stem):
                text = (self.KB / "rules" / stem).read_text(encoding="utf-8")
                self.assertIn("参数要点（请求）", text)
                self.assertIn("参数要点（响应，非请求）", text)
                self.assertNotIn("必填参数：vid、html_content、vid", text)

    def test_r136_uses_real_contract_names(self):
        text = (self.KB / "rules" / "M20_R136_AI主页接口测试设计.md").read_text(encoding="utf-8")
        self.assertIn("html_content", text)
        self.assertIn("1000102", text)
        self.assertIn("state=2", text)
        self.assertNotIn("homepage_id(1000108必填", text)

    def test_r239_has_source_lines(self):
        text = (self.KB / "rules" / "M20_R239_AI主页实现侧补充规则.md").read_text(encoding="utf-8")
        self.assertIn("wjx-docs/tasks/ai-page-api-integration.md:45", text)
        self.assertIn("api-client.ts", text)
        self.assertIn("实现侧佐证", text)

    def test_manifest_and_index_registration(self):
        import json
        d = json.loads((self.KB / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(d["total_rules"], len(d["rules"]))
        self.assertIn("M20_R239", [r["id"] for r in d["rules"]])
        self.assertEqual(len(d["rules"]), 2002)
        idx = (self.KB / "INDEX.md").read_text(encoding="utf-8")
        self.assertIn("| M20 | 开放接口与集成 | 74 | 15 | 3 | 92 | 48 |", idx)
