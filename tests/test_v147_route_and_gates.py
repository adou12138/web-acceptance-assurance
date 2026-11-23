"""v1.47.0 实测整改回归：Agent 专项路由、字段诊断可定位、视角深度口径、裁决前置登记。

背景：2026-11-23 问卷星星仔全能力测试后的专家包自审。实测暴露 7 处摩擦，其中
4 处是「按文档写会失败或看不到」，1 处是知识在包里但没被路由到执行路径，1 处是
裁决前置清单只在终检索要导致跨会话补登，1 处是报告把「清点过控件」表述成「已验收
该视角」。本文件锁住这些整改，防止后续改动把它们退回去。
"""
import csv
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_phase_gate import PhaseGateTestBase  # noqa: E402  复用阶段状态机夹具

AGENT_MD = ROOT / "agents" / "web-acceptance-assurance.md"
OUTPUT_TEMPLATES = ROOT / "references" / "output-templates.md"
ROUTE_PROMPTS = ROOT / "references" / "route-prompts.json"
EXPLORATION_SCRIPT = ROOT / "scripts" / "exploration_state.py"
ATTACK_SCRIPT = ROOT / "scripts" / "attack_coverage.py"
RENDER_SCRIPT = ROOT / "scripts" / "render_report_html.py"

COVERAGE = {"verdict_ceiling": "通过", "gaps": [], "attack_density": 0.4}
EXPLORATION = {"verdict_ceiling": "通过", "ready_pending": 0, "missing_views": [],
               "failed_items": [], "traversal_recorded": True,
               "mandatory_attributes": {"security": "covered"}, "message": "无未消费候选"}

LEDGER_COLUMNS = ["view", "id", "requirement_ids", "case_id", "target_url",
                   "application_version", "environment", "page_url", "role", "viewport",
                   "state", "action", "risk", "status", "attributes", "observation",
                   "evidence_paths", "evidence", "blocked_reason", "side_effect_pending"]


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def minimal_exploration():
    """一份字段齐备的最小清单；测试按需删改单个字段。"""
    return {
        "schema_version": "1.0",
        "context": {"target_url": "https://example.invalid", "application_version": "v1",
                    "environment": "本地受控"},
        "items": [{
            "id": "EXP-001", "requirement_ids": ["REQ-1"],
            "page_url": "https://example.invalid/app", "role": "访客",
            "viewport": "1280x800", "state": "初始态", "action": "点击主按钮",
            "risk": "low", "status": "NOT_RUN", "observation": "",
            "evidence_paths": [], "side_effect_pending": False,
            "attributes": ["functional"], "blocked_reason": "",
        }],
    }


class TestAgentSubjectRouting(unittest.TestCase):
    """E07：被测对象本身是对话式 Agent 时，方法论必须被路由到执行路径。"""

    def test_agent_methodology_is_in_reading_contract(self):
        text = AGENT_MD.read_text(encoding="utf-8")
        self.assertIn("skills/case-generator/references/methodology/ai_feature_testing.md", text)
        self.assertIn("第 8 节", text)

    def test_route_table_has_agent_subject_row(self):
        text = AGENT_MD.read_text(encoding="utf-8")
        self.assertIn("被测对象本身是对话式 Agent / AI 助手", text)
        self.assertIn("ai_feature_testing.md", text)

    def test_route_regression_baseline_has_agent_case(self):
        data = json.loads(ROUTE_PROMPTS.read_text(encoding="utf-8-sig"))
        prompts = data["prompts"]
        ids = [p["id"] for p in prompts]
        self.assertEqual(len(ids), len(set(ids)), "路由基线 ID 必须唯一")
        agent_rows = [p for p in prompts if p.get("expected_skill") == "main"
                      and "ai_feature_testing" in (p.get("expected_path") or "")]
        self.assertTrue(agent_rows, "路由基线须覆盖「被测对象是对话式 Agent」这一路")
        for row in agent_rows:
            self.assertIn("ai_feature_testing", row["expected_path"])


class TestFieldDiagnostics(unittest.TestCase):
    """E01：字段校验报错必须点名是哪个字段，不能只说「缺必填或含未知字段」。"""

    def setUp(self):
        self.tool = load_module(EXPLORATION_SCRIPT, "exploration_state_e01")

    def test_missing_field_is_named(self):
        data = minimal_exploration()
        del data["items"][0]["blocked_reason"]
        with self.assertRaises(ValueError) as ctx:
            self.tool.summarize(data)
        message = str(ctx.exception)
        self.assertIn("缺字段", message)
        self.assertIn("blocked_reason", message)
        self.assertNotIn("包含未知字段", message)

    def test_unknown_field_is_named(self):
        data = minimal_exploration()
        data["items"][0]["oops"] = 1
        with self.assertRaises(ValueError) as ctx:
            self.tool.summarize(data)
        message = str(ctx.exception)
        self.assertIn("未知字段", message)
        self.assertIn("oops", message)

    def test_missing_and_unknown_are_listed_separately(self):
        data = minimal_exploration()
        del data["items"][0]["blocked_reason"]
        data["items"][0]["oops"] = 1
        with self.assertRaises(ValueError) as ctx:
            self.tool.summarize(data)
        message = str(ctx.exception)
        self.assertIn("缺字段", message)
        self.assertIn("未知字段", message)


class TestDepthWording(unittest.TestCase):
    """E03/E05/E06：报告与提示必须区分「有记录」与「覆盖到下限」，并说清参数形态。"""

    def test_replay_note_hint_states_file_path(self):
        text = ATTACK_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("--replay-note 传的是 JSON 文件路径", text)

    def test_render_help_states_gate_source(self):
        result = subprocess.run([sys.executable, str(RENDER_SCRIPT), "--help"],
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace")
        self.assertIn("exploration_state", result.stdout)
        self.assertIn("整份报告", result.stdout)

    def test_report_marks_thin_views(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            ledger = folder / "ledger.csv"
            with ledger.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=LEDGER_COLUMNS)
                writer.writeheader()
                writer.writerow({
                    "view": "界面样式", "id": "EXP-001", "requirement_ids": '["REQ-1"]',
                    "case_id": "", "target_url": "https://example.invalid",
                    "application_version": "v1", "environment": "本地受控",
                    "page_url": "https://example.invalid/app", "role": "访客",
                    "viewport": "1280x800", "state": "初始态", "action": "清点控件",
                    "risk": "low", "status": "PASS", "attributes": '["visual"]',
                    "observation": "控件可交互", "evidence_paths": "[]", "evidence": "[]",
                    "blocked_reason": "", "side_effect_pending": "false"})
            gate = folder / "gate.json"
            gate.write_text(json.dumps({
                "schema_version": "1.0", "planned": 1, "counts": {"PASS": 1},
                "gate": {"ready_pending": 0, "ready_pending_ids": [], "ready_high_risk": 0,
                         "missing_views": [], "thin_views": ["界面样式"],
                         "view_cells": {"界面样式": ["https://example.invalid/app|初始态"]},
                         "failed_items": [], "traversal_recorded": True,
                         "mandatory_attributes": {"visual": "covered"},
                         "verdict_ceiling": "待核实", "message": "覆盖偏薄"}}, ensure_ascii=False),
                encoding="utf-8")
            out = folder / "report.html"
            result = subprocess.run(
                [sys.executable, str(RENDER_SCRIPT), "--output", str(out),
                 "--dataset", f"样本::{ledger}::{gate}"],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(result.returncode, 0, result.stderr[-400:])
            html = out.read_text(encoding="utf-8")
            self.assertIn("记录偏薄", html)
            self.assertNotIn("有台账记录</span>", html.split("记录偏薄")[0][-400:])


class TestArtifactContractTable(unittest.TestCase):
    """E08：产物与消费脚本的对照表必须存在且覆盖全部阶段产物。"""

    def test_table_exists(self):
        text = OUTPUT_TEMPLATES.read_text(encoding="utf-8")
        self.assertIn("## 0.4 产物与消费脚本对照", text)

    def test_table_lists_consumers(self):
        text = OUTPUT_TEMPLATES.read_text(encoding="utf-8")
        for token in ("check-adjudication", "finding_summary.py", "scan-collection",
                      "attack_coverage.py --ledger", "check-verdict --assumptions",
                      "check-verdict --regression", "exploration_state.py"):
            self.assertIn(token, text)

    def test_agent_md_points_to_table(self):
        text = AGENT_MD.read_text(encoding="utf-8")
        self.assertIn("0.4 节", text)


class TestVerdictInputRegistration(PhaseGateTestBase):
    """E04：裁决前置清单要在检查点就可见，且登记不等于终检放行。"""

    def _reach_execute(self):
        self.pg.start(self.state, "plan", "SESS-1")
        self.pg.complete(self.state, "plan", session="SESS-1")
        self.submit_phase("execute")
        self.pg.checkpoint(self.state, "plan", self.handoff_dir, session="SESS-1")
        self.resume("execute")
        self.pg.start(self.state, "execute", self.session_for("execute"))

    def _write_inputs(self, assumptions=None, regression=None):
        paths = {}
        assumptions = assumptions if assumptions is not None else {
            "reviewed": True,
            "items": [{"id": "ASM-01", "assumption": "a", "basis": "b",
                       "falsify_action": "c", "result": "confirmed"}]}
        regression = regression if regression is not None else {
            "changed_areas": ["题目写入"], "candidates": [{"id": "RG-01", "basis": "题序顺延"}]}
        for name, payload in (("assumptions", assumptions), ("regression", regression)):
            path = self.folder / f"{name}.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            paths[name] = str(path)
        return paths

    def test_handoff_lists_unregistered_verdict_inputs(self):
        self._reach_execute()
        self.pg.complete(self.state, "execute", session=self.session_for("execute"),
                         attack_coverage=COVERAGE, exploration=EXPLORATION)
        self.pg.submit(self.state, "adjudicate", session=self.session_for("execute"),
                       model_class=self.pg.ROUTING["adjudicate"]["model_class"],
                       model_name=self.pg.ROUTING["adjudicate"]["model_hint"])
        self.pg.checkpoint(self.state, "execute", self.handoff_dir,
                           session=self.session_for("execute"))
        text = (self.handoff_dir / "handoff-execute.md").read_text(encoding="utf-8")
        self.assertIn("下一阶段需提交的前置清单", text)
        self.assertIn("风险假设清算", text)
        self.assertIn("回归影响面", text)

    def test_registration_clears_pending_list(self):
        self._reach_execute()
        paths = self._write_inputs()
        self.pg.complete(self.state, "execute", session=self.session_for("execute"),
                         attack_coverage=COVERAGE, exploration=EXPLORATION,
                         assumptions=paths["assumptions"], regression=paths["regression"])
        entry = self.state["phases"]["execute"]
        self.assertTrue(entry["assumptions"]["registered"])
        self.assertTrue(entry["regression"]["registered"])
        self.pg.submit(self.state, "adjudicate", session=self.session_for("execute"),
                       model_class=self.pg.ROUTING["adjudicate"]["model_class"],
                       model_name=self.pg.ROUTING["adjudicate"]["model_hint"])
        self.pg.checkpoint(self.state, "execute", self.handoff_dir,
                           session=self.session_for("execute"))
        text = (self.handoff_dir / "handoff-execute.md").read_text(encoding="utf-8")
        self.assertNotIn("下一阶段需提交的前置清单", text)

    def test_registration_rejects_malformed_input(self):
        self._reach_execute()
        paths = self._write_inputs(assumptions={"items": []})  # 缺 reviewed
        with self.assertRaises(ValueError) as ctx:
            self.pg.complete(self.state, "execute", session=self.session_for("execute"),
                             attack_coverage=COVERAGE, exploration=EXPLORATION,
                             assumptions=paths["assumptions"])
        self.assertIn("reviewed", str(ctx.exception))
        self.assertNotIn("assumptions", self.state["phases"]["execute"])

    def test_registration_does_not_weaken_final_check(self):
        """登记过 ≠ 终检放行：终检仍以自身入参判定，不看阶段里登记过没有。"""
        self._reach_execute()
        paths = self._write_inputs()
        self.pg.complete(self.state, "execute", session=self.session_for("execute"),
                         attack_coverage=COVERAGE, exploration=EXPLORATION,
                         assumptions=paths["assumptions"], regression=paths["regression"])
        check = self.pg.check_verdict(self.state)  # 不传前置清单
        self.assertFalse(check["verdict_allowed"])
        joined = "；".join(check["reasons"])
        self.assertIn("风险假设清算", joined)
        self.assertIn("回归影响面", joined)

    def test_open_assumption_may_be_registered_but_still_holds_ceiling(self):
        """result=open 是内容状态不是格式错误：允许登记，但终检照旧压上限。"""
        self._reach_execute()
        paths = self._write_inputs(assumptions={
            "reviewed": True,
            "items": [{"id": "ASM-01", "assumption": "尚未证伪", "basis": "b",
                       "falsify_action": "c", "result": "open"}]})
        self.pg.complete(self.state, "execute", session=self.session_for("execute"),
                         attack_coverage=COVERAGE, exploration=EXPLORATION,
                         assumptions=paths["assumptions"])
        self.assertTrue(self.state["phases"]["execute"]["assumptions"]["registered"])
        payload = json.loads(Path(paths["assumptions"]).read_text(encoding="utf-8"))
        reasons = self.pg._assumption_reasons(payload)
        self.assertTrue(any("仍未证伪" in item for item in reasons))
        self.assertEqual(self.pg._assumption_reasons(payload, structure_only=True), [])

    def test_registration_rejects_missing_file(self):
        self._reach_execute()
        with self.assertRaises(ValueError) as ctx:
            self.pg.complete(self.state, "execute", session=self.session_for("execute"),
                             attack_coverage=COVERAGE, exploration=EXPLORATION,
                             assumptions=str(self.folder / "not-there.json"))
        self.assertIn("不存在", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
