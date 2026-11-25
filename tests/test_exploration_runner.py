"""D2 exploration_runner.py 专项测试（v1.49.0 方案 §D2 + X-02/X-13/P3 修订口径）。

验收基准：
- ready_queue 全部来自 exploration_state.summarize()["ready"]，不另建排序（顺序一致）；
- 页面交互通道均 missing → ready 项转 blocked_with_recovery，保留 recovery 原文，
  且不得借此免压档（X-03）；
- 自动续批（X-02）：仅 classify→readonly_lead + reasons==[] + not is_blocking +
  未超预算才 auto；三种反向凭据（no_state_change="false"/allowed_actions=[]/
  scope="   "）一律 requires_human=true；
- writeback_commands[] 是发射模板（P3）：脚本不自行执行回写、不代写证据；
- honest_exits（X-13）只登记条目 ID 与未填清单，字段由 Agent 填；
- 预算账本 auto_approval 事件（E2 指标数据源）。
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import exploration_runner as er
import exploration_state as _EXP

CAPS_AVAILABLE = {"schema": "runtime-capabilities/1", "channels": {
    "http_contract": {"state": "unprobed", "reason": "r", "recovery": ""},
    "browser_selfhost": {"state": "available", "reason": "r", "recovery": ""},
    "browser_mcp": {"state": "unprobed", "reason": "r", "recovery": ""},
    "auth_state": {"state": "unprobed", "reason": "r", "recovery": ""},
    "design_data": {"state": "missing", "reason": "r", "recovery": "rec-design"},
}}
CAPS_PAGE_MISSING = {"schema": "runtime-capabilities/1", "channels": dict(
    CAPS_AVAILABLE["channels"],
    browser_selfhost={"state": "missing", "reason": "r", "recovery": "宿主提供运行时或声明 PLAYWRIGHT_CORE_PATH"},
    browser_mcp={"state": "unprobed", "reason": "r", "recovery": "会话内确认 MCP 工具"},
)}


def make_item(ident, **changes):
    row = {"id": ident, "requirement_ids": ["REQ-1"],
           "page_url": "https://example.invalid/app/form", "role": "编辑者",
           "viewport": "1440x900", "state": "表单打开", "action": "核对保存按钮",
           "risk": "medium", "status": "NOT_RUN", "observation": "",
           "evidence_paths": [], "blocked_reason": "", "side_effect_pending": False,
           "attributes": ["functional"]}
    row.update(changes)
    return row


def make_data(items):
    return {"schema_version": "1.0",
            "context": {"target_url": "https://example.invalid/app",
                        "application_version": "build-1", "environment": "test"},
            "items": items}


def valid_declaration():
    return {"kind": "readonly_lead",
            "scope": "首页表单只读探针", "environment": "test",
            "role": "编辑者", "allowed_actions": ["navigate", "read_dom"],
            "no_state_change": True}


class ReadyQueueTests(unittest.TestCase):
    def test_ready_queue_matches_summarize_order(self):
        """ready_queue 全部来自 summarize()["ready"]，不另建排序（高风险先、ID 序）。"""
        items = [make_item("EXP-Z", risk="low"), make_item("EXP-B", risk="high"),
                 make_item("EXP-A", risk="high"), make_item("EXP-D", blocked_reason="等数据")]
        report = _EXP.summarize(make_data(items))
        plan = er.build_batch_plan(report, CAPS_AVAILABLE, None, 2)
        self.assertEqual(plan["ready_queue"], report["ready"])
        self.assertEqual(report["ready"], ["EXP-A", "EXP-B", "EXP-Z"])
        self.assertEqual(plan["batches"], [["EXP-A", "EXP-B"], ["EXP-Z"]])

    def test_page_channels_missing_blocks_with_recovery(self):
        """capabilities 页面通道均 missing → ready 转 blocked_with_recovery，
        recovery 原文保留（X-03：不借此免压档）。"""
        items = [make_item("EXP-1"), make_item("EXP-2")]
        report = _EXP.summarize(make_data(items))
        plan = er.build_batch_plan(report, CAPS_PAGE_MISSING, None, None)
        self.assertEqual(plan["ready_queue"], [])
        self.assertEqual({b["id"] for b in plan["blocked_with_recovery"]},
                         {"EXP-1", "EXP-2"})
        for blocked in plan["blocked_with_recovery"]:
            self.assertIn("宿主提供运行时或声明 PLAYWRIGHT_CORE_PATH", blocked["recovery"])
            self.assertIn("会话内确认 MCP 工具", blocked["recovery"])

    def test_reconcile_and_reproduction_passthrough(self):
        """needs_reconcile（副作用待核）与 reproduction_candidates（FAIL）原样透传；
        FAIL 条目的证据须真实存在（exploration_state 证据校验），脚本不代写证据。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp) / "观察.txt"
            evidence.write_text("测试夹具证据，不代表真实业务验收", encoding="utf-8")
            items = [make_item("EXP-F", status="FAIL", observation="实测观察",
                               evidence_paths=[str(evidence)], side_effect_pending=True)]
            report = _EXP.summarize(make_data(items))
        plan = er.build_batch_plan(report, CAPS_AVAILABLE)
        self.assertEqual(plan["reconcile_items"]["needs_reconcile"], ["EXP-F"])
        self.assertEqual(plan["reconcile_items"]["reproduction_candidates"], ["EXP-F"])
        self.assertEqual(plan["ready_queue"], [])


class AutoContinueTests(unittest.TestCase):
    """X-02：自动续批必须过 classify()+is_blocking() 双层凭据校验。"""

    def test_happy_path_auto(self):
        result = er.auto_continue(valid_declaration(),
                                  {"read_only_budget": {"probes": 20}},
                                  {"probes": 3})
        self.assertTrue(result["auto"])
        self.assertFalse(result["requires_human"])
        self.assertEqual(result["basis"], "readonly batch within declared budget")

    def test_reverse_no_state_change_string_false(self):
        """反向用例 1：no_state_change 写成字符串 "false" → requires_human=true。"""
        decl = dict(valid_declaration(), no_state_change="false")
        result = er.auto_continue(decl)
        self.assertFalse(result["auto"])
        self.assertTrue(result["requires_human"])
        self.assertTrue(result["classify_reasons"], "classify reasons 原文必须附在输出")

    def test_reverse_allowed_actions_empty(self):
        """反向用例 2：allowed_actions=[]（空数组=没有已授权动作）→ requires_human=true。"""
        decl = dict(valid_declaration(), allowed_actions=[])
        result = er.auto_continue(decl)
        self.assertFalse(result["auto"])
        self.assertTrue(result["requires_human"])

    def test_reverse_scope_blank(self):
        """反向用例 3：scope 填空白 → requires_human=true。"""
        decl = dict(valid_declaration(), scope="   ")
        result = er.auto_continue(decl)
        self.assertFalse(result["auto"])
        self.assertTrue(result["requires_human"])

    def test_reverse_scope_change_signal_writes(self):
        """SCOPE_CHANGE_SIGNALS 命中（writes=true）→ 归 scope_change → requires_human。"""
        decl = dict(valid_declaration(), writes=True)
        result = er.auto_continue(decl)
        self.assertFalse(result["auto"])
        self.assertEqual(result["kind"], "scope_change")

    def test_over_budget_requires_human(self):
        """未超预算才 auto：consumed 达上限 → requires_human=true 且 over_budget 标记。"""
        result = er.auto_continue(valid_declaration(),
                                  {"read_only_budget": {"probes": 20}},
                                  {"probes": 20})
        self.assertFalse(result["auto"])
        self.assertTrue(result["requires_human"])
        self.assertTrue(result["over_budget"])

    def test_missing_budget_declaration_does_not_block(self):
        """无预算声明：不拦截（预算纪律由 stop_conditions 提示），凭据合格仍 auto。"""
        result = er.auto_continue(valid_declaration(), None, None)
        self.assertTrue(result["auto"])


class WritebackAndHonestExitsTests(unittest.TestCase):
    def test_writeback_commands_are_emitted_templates(self):
        """P3：writeback_commands 是发射模板（占位符 + begin/finish 成对），
        脚本不自行执行——命令字符串存在即发射，无执行副作用。"""
        plan = er.build_batch_plan(_EXP.summarize(make_data([make_item("EXP-1")])),
                                   CAPS_AVAILABLE)
        cmds = plan["writeback_commands"]
        self.assertEqual(len(cmds), 2)  # begin + finish 成对
        self.assertIn("begin", cmds[0])
        self.assertIn("finish", cmds[1])
        self.assertIn("<workspace>", cmds[0])  # 占位符由 Agent 填实
        self.assertIn("EXP-1", cmds[0])

    def test_honest_exits_only_ids_not_content(self):
        """X-13：honest_exits 只登记条目 ID 与未填清单；不代写字段内容。"""
        items = [
            make_item("EXP-FULL", acknowledged_unexplored={
                "next_action": "补审批卡片文案探针", "owner": "砺衡"},
                falsify_action="重放双写核对覆盖"),
            make_item("EXP-NOACK"),
            make_item("EXP-NOOWNER", acknowledged_unexplored={
                "next_action": "补探针", "owner": ""}, falsify_action="x"),
        ]
        exits = er._honest_exits(items)
        self.assertEqual(exits["filled_ids"], ["EXP-FULL"])
        self.assertEqual(exits["unfilled_ids"], ["EXP-NOACK", "EXP-NOOWNER"])


class BudgetLedgerTests(unittest.TestCase):
    def test_record_batch_accumulates_and_records_auto_approval(self):
        ledger = er.new_budget_ledger({"read_only_budget": {"probes": 20}})
        er.record_batch(ledger, "B1", probes=5, minutes=10, writes=0)
        auto = {"auto": True, "basis": "readonly batch within declared budget"}
        er.record_batch(ledger, "B2", probes=3, minutes=5, writes=0, auto=auto)
        self.assertEqual(ledger["probes_consumed"], 8)
        self.assertEqual(ledger["minutes_consumed"], 15)
        self.assertEqual(len(ledger["auto_approval"]), 1)
        self.assertEqual(ledger["auto_approval"][0]["batch_id"], "B2")
        self.assertEqual(ledger["schema"], "budget-consumption/1")

    def test_cli_full_flow(self):
        """CLI 全流程：exploration + capabilities + declaration + budget ledger → batch-plan。"""
        import tempfile
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            exp = tmp / "exploration.json"
            exp.write_text(json.dumps(make_data([make_item("EXP-1")])), encoding="utf-8")
            caps = tmp / "capabilities.json"
            caps.write_text(json.dumps(CAPS_AVAILABLE), encoding="utf-8")
            decl = tmp / "declaration.json"
            decl.write_text(json.dumps(valid_declaration()), encoding="utf-8")
            out = tmp / "batch-plan.json"
            ledger = tmp / "budget-consumption.json"
            done = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "exploration_runner.py"),
                 "--exploration", str(exp), "--capabilities", str(caps),
                 "--declaration", str(decl), "--budget-ledger", str(ledger),
                 "--out", str(out)], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(done.returncode, 0, done.stderr)
            plan = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(plan["schema"], "batch-plan/1")
            self.assertEqual(plan["ready_queue"], ["EXP-1"])
            self.assertTrue(plan["auto_continue"]["auto"])
            ledger_data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(ledger_data["schema"], "budget-consumption/1")
            self.assertEqual(len(ledger_data["auto_approval"]), 1)


if __name__ == "__main__":
    unittest.main()