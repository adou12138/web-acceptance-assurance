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

import design_fingerprint as fp
import exploration_runner as er
import exploration_state as _EXP


def chain(rows):
    """给测试事件补 seq/digest/previous，模拟 execution_ledger 的追加链。"""
    events, previous = [], None
    for index, row in enumerate(rows, 1):
        content = dict(row)
        content["seq"] = index
        content["previous"] = previous
        content["digest"] = fp.fingerprint(content)
        events.append(content)
        previous = content["digest"]
    return events

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


class BudgetKeyAndRecordCliTests(unittest.TestCase):
    """S0.5：预算键名对齐 + 删占位批 + record 入口从台账派生消耗。"""

    def test_within_budget_accepts_consumed_key_names(self):
        """CLI 传的是 probes_consumed，声明的是 probes：必须仍然能拦住。"""
        self.assertFalse(er.within_budget({"read_only_budget": {"probes": 1}},
                                          {"probes_consumed": 1}))
        self.assertTrue(er.within_budget({"read_only_budget": {"probes": 2}},
                                         {"probes_consumed": 1}))

    def test_zero_write_limit_allows_zero_writes(self):
        """writes:0 表示不允许写；一次都没写时不得判成超预算（否则只读批次永远需人批）。"""
        self.assertTrue(er.within_budget({"read_only_budget": {"writes": 0}},
                                         {"writes_consumed": 0}))
        self.assertFalse(er.within_budget({"read_only_budget": {"writes": 0}},
                                          {"writes_consumed": 1}))

    def test_plan_cli_over_budget_requires_human(self):
        """真实 CLI 路径：账本累计已达上限 → requires_human=true（修复前恒为 auto）。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            exp = tmp / "exploration.json"
            exp.write_text(json.dumps(make_data([make_item("EXP-1")])), encoding="utf-8")
            caps = tmp / "capabilities.json"
            caps.write_text(json.dumps(CAPS_AVAILABLE), encoding="utf-8")
            decl = tmp / "declaration.json"
            decl.write_text(json.dumps(valid_declaration()), encoding="utf-8")
            manifest = tmp / "manifest.json"
            manifest.write_text(json.dumps({"read_only_budget": {"probes": 1}}),
                                encoding="utf-8")
            ledger = tmp / "budget-consumption.json"
            ledger.write_text(json.dumps({
                **er.new_budget_ledger({"read_only_budget": {"probes": 1}}),
                "probes_consumed": 999}), encoding="utf-8")
            out = tmp / "batch-plan.json"
            rc = er.main(["--exploration", str(exp), "--capabilities", str(caps),
                          "--declaration", str(decl), "--runtime-manifest", str(manifest),
                          "--budget-ledger", str(ledger), "--out", str(out)])
            self.assertEqual(rc, 0)
            plan = json.loads(out.read_text(encoding="utf-8"))
            self.assertTrue(plan["auto_continue"]["requires_human"])
            self.assertFalse(plan["auto_continue"]["auto"])
            self.assertTrue(plan["auto_continue"]["over_budget"])

    def test_plan_cli_does_not_append_placeholder_batch(self):
        """编排不产生消耗：不得追加 0 消耗占位批（会让 batch_count/比率分母虚增）。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            exp = tmp / "exploration.json"
            exp.write_text(json.dumps(make_data([make_item("EXP-1")])), encoding="utf-8")
            caps = tmp / "capabilities.json"
            caps.write_text(json.dumps(CAPS_AVAILABLE), encoding="utf-8")
            out = tmp / "batch-plan.json"
            ledger = tmp / "budget-consumption.json"
            for _ in range(2):
                self.assertEqual(er.main(["--exploration", str(exp), "--capabilities",
                                          str(caps), "--budget-ledger", str(ledger),
                                          "--out", str(out)]), 0)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(data["batches"], [])

    def test_record_cli_derives_consumption_from_ledger(self):
        """record：探针求和、时长按 BEGIN→FINISH 派生、无写台账时不写 0 冒充。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            events = tmp / "events.jsonl"
            rows = [
                {"kind": "BEGIN", "attempt_id": "ATT-1", "case_id": "TC-1",
                 "timestamp": "2026-11-26T00:00:00+00:00"},
                {"kind": "FINISH", "attempt_id": "ATT-1", "case_id": "TC-1",
                 "timestamp": "2026-11-26T00:05:00+00:00", "status": "PASS",
                 "probes": 4, "evidence": [{"path": "/e/a.png", "sha256": "a" * 64}]},
                {"kind": "BEGIN", "attempt_id": "ATT-2", "case_id": "TC-2",
                 "timestamp": "2026-11-26T00:06:00+00:00"},
                {"kind": "FINISH", "attempt_id": "ATT-2", "case_id": "TC-2",
                 "timestamp": "2026-11-26T00:08:00+00:00", "status": "FAIL",
                 "evidence": [{"path": "/e/b.png", "sha256": "b" * 64}]},
            ]
            events.write_text("\n".join(json.dumps(r, ensure_ascii=False)
                                       for r in chain(rows)), encoding="utf-8")
            ledger = tmp / "budget-consumption.json"
            rc = er.main(["record", "--ledger", str(ledger), "--events", str(events),
                          "--batch-id", "B1"])
            self.assertEqual(rc, 0)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(data["probes_consumed"], 4)
            self.assertEqual(data["minutes_consumed"], 7.0)
            self.assertEqual(data["writes_missing_batches"], 1)
            self.assertEqual(data["batches"][0]["detail"]["probes_undeclared_attempts"], 1)
            self.assertEqual(len(data["batches"]), 1)


class StopConditionTests(unittest.TestCase):
    """S1：三条收敛条件 + 一条安全联锁；gate 通过不参与停机。"""

    def test_three_convergence_plus_safety_and_no_quota(self):
        plan = er.build_batch_plan(
            _EXP.summarize(make_data([make_item("EXP-1")])), CAPS_AVAILABLE)
        codes = {item["code"]: item for item in plan["stop_conditions"]}
        self.assertEqual(set(codes), {"queue_empty", "increment_zero",
                                      "budget_exhausted", "side_effect_pending"})
        self.assertNotIn("quota_triggered", codes)
        for code in ("queue_empty", "increment_zero", "budget_exhausted"):
            self.assertEqual(codes[code]["class"], "convergence")
            self.assertTrue(codes[code]["mechanical"])
        self.assertEqual(codes["side_effect_pending"]["class"], "safety")

    def test_queue_state_counts_acknowledged_and_blocked(self):
        items = [make_item("EXP-R"),
                 make_item("EXP-A", acknowledged_unexplored={
                     "next_action": "补探针", "owner": "甲"}),
                 make_item("EXP-B", blocked_reason="等接口")]
        state = er.queue_state(_EXP.summarize(make_data(items)))
        self.assertEqual(state["ready"], 1)
        self.assertEqual(state["acknowledged"], 1)
        self.assertEqual(state["blocked"], 1)

    def test_safety_preempts_other_stops(self):
        stop = er.evaluate_stop({"ready": 0}, [5], 2, {"exhausted": True}, ["EXP-F"])
        self.assertEqual(stop["code"], "side_effect_pending")
        self.assertFalse(stop["convergence"])

    def test_queue_empty_convergence_requires_no_ack_or_block(self):
        dirty = er.evaluate_stop({"ready": 0, "acknowledged": 1, "blocked": 0,
                                  "acknowledged_incomplete": 0}, [], 2)
        self.assertEqual(dirty["code"], "queue_empty")
        self.assertFalse(dirty["convergence"])
        clean = er.evaluate_stop({"ready": 0, "acknowledged": 0, "blocked": 0,
                                  "acknowledged_incomplete": 0}, [], 2)
        self.assertTrue(clean["convergence"])

    def test_increment_zero_keeps_ready_remaining(self):
        stop = er.evaluate_stop({"ready": 3, "acknowledged": 0, "blocked": 0,
                                 "acknowledged_incomplete": 0}, [0, 0], 2)
        self.assertEqual(stop["code"], "increment_zero")
        self.assertEqual(stop["ready_remaining"], 3)
        self.assertFalse(stop["convergence"])

    def test_budget_exhausted(self):
        stop = er.evaluate_stop({"ready": 2}, [1], 2, {"exhausted": True})
        self.assertEqual(stop["code"], "budget_exhausted")

    def test_n_zero_rounds_must_be_positive(self):
        with self.assertRaises(ValueError):
            er.evaluate_stop({"ready": 1}, [], 0)


class RecordWatermarkTests(unittest.TestCase):
    """S4 前置：record 只累计水位线之后的新 FINISH，重复调用不重复计费。"""

    def _write_events(self, path, rows):
        path.write_text("\n".join(json.dumps(row, ensure_ascii=False)
                                  for row in chain(rows)), encoding="utf-8")

    def test_repeated_record_does_not_double_count(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            events = tmp / "events.jsonl"
            self._write_events(events, [
                {"kind": "BEGIN", "attempt_id": "ATT-1", "case_id": "TC-1",
                 "timestamp": "2026-11-26T00:00:00+00:00", "seq": 1},
                {"kind": "FINISH", "attempt_id": "ATT-1", "case_id": "TC-1",
                 "timestamp": "2026-11-26T00:05:00+00:00", "status": "PASS",
                 "probes": 4, "seq": 2,
                 "evidence": [{"path": "/e/a.png", "sha256": "a" * 64}]},
            ])
            ledger = tmp / "budget-consumption.json"
            self.assertEqual(er.main(["record", "--ledger", str(ledger),
                                      "--events", str(events),
                                      "--batch-id", "B1"]), 0)
            self.assertEqual(er.main(["record", "--ledger", str(ledger),
                                      "--events", str(events),
                                      "--batch-id", "B2"]), 0)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(data["probes_consumed"], 4)
            self.assertEqual(data["batches"][1]["probes"], 0)
            self.assertEqual(data["recorded_through_seq"], 2)

    def test_record_counts_only_events_after_watermark(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            events = tmp / "events.jsonl"
            rows = [
                {"kind": "BEGIN", "attempt_id": "ATT-1", "case_id": "TC-1",
                 "timestamp": "2026-11-26T00:00:00+00:00", "seq": 1},
                {"kind": "FINISH", "attempt_id": "ATT-1", "case_id": "TC-1",
                 "timestamp": "2026-11-26T00:05:00+00:00", "status": "PASS",
                 "probes": 4, "seq": 2,
                 "evidence": [{"path": "/e/a.png", "sha256": "a" * 64}]},
            ]
            self._write_events(events, rows)
            ledger = tmp / "budget-consumption.json"
            self.assertEqual(er.main(["record", "--ledger", str(ledger),
                                      "--events", str(events),
                                      "--batch-id", "B1"]), 0)
            rows += [
                {"kind": "BEGIN", "attempt_id": "ATT-2", "case_id": "TC-2",
                 "timestamp": "2026-11-26T00:06:00+00:00", "seq": 3},
                {"kind": "FINISH", "attempt_id": "ATT-2", "case_id": "TC-2",
                 "timestamp": "2026-11-26T00:08:00+00:00", "status": "FAIL",
                 "probes": 2, "seq": 4,
                 "evidence": [{"path": "/e/b.png", "sha256": "b" * 64}]},
            ]
            self._write_events(events, rows)
            self.assertEqual(er.main(["record", "--ledger", str(ledger),
                                      "--events", str(events),
                                      "--batch-id", "B2"]), 0)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(data["probes_consumed"], 6)
            self.assertEqual(data["batches"][1]["probes"], 2)


class ProbesFloorTests(unittest.TestCase):
    def test_undeclared_probes_count_as_floor(self):
        """不填 probes 时预算不能恒为 0：每条未声明 FINISH 按至少 1 次计。"""
        ledger = {"probes_consumed": 0,
                  "batches": [{"detail": {"probes_undeclared_attempts": 2}}]}
        self.assertFalse(er.within_budget({"read_only_budget": {"probes": 2}}, ledger))
        self.assertTrue(er.within_budget({"read_only_budget": {"probes": 3}}, ledger))


class LoopCliTests(unittest.TestCase):
    """S4：有界循环的状态机与日志；只验停机机制，不判检出。"""

    def _fixture(self, tmp, events_rows, surfaces=("SURF-01",), candidates=None):
        exp = tmp / "exploration.json"
        exp.write_text(json.dumps(make_data([make_item("EXP-1")])), encoding="utf-8")
        caps = tmp / "capabilities.json"
        caps.write_text(json.dumps(CAPS_AVAILABLE), encoding="utf-8")
        plan = tmp / "adversarial-plan.json"
        plan.write_text(json.dumps({"surfaces": [{"id": s} for s in surfaces]}),
                        encoding="utf-8")
        events = tmp / "events.jsonl"
        events.write_text("\n".join(json.dumps(row, ensure_ascii=False)
                                   for row in chain(events_rows)), encoding="utf-8")
        state = tmp / "loop-state.json"
        log = tmp / "loop-log.jsonl"
        ledger = tmp / "budget-consumption.json"
        args = ["loop", "--exploration", str(exp), "--capabilities", str(caps),
                "--plan", str(plan), "--events", str(events), "--ledger", str(ledger),
                "--state", str(state), "--log", str(log)]
        if candidates is not None:
            path = tmp / "candidates.json"
            path.write_text(json.dumps(candidates), encoding="utf-8")
            args += ["--candidates", str(path)]
        return args, state, log

    def _events(self):
        return [
            {"kind": "BEGIN", "attempt_id": "ATT-1", "case_id": "TC-1",
             "intended_action": "点保存", "seq": 1},
            {"kind": "FINISH", "attempt_id": "ATT-1", "case_id": "TC-1",
             "status": "PASS", "seq": 2, "surf_id": "SURF-01",
             "evidence": [{"path": "/e/a.png", "sha256": "a" * 64}]},
        ]

    def test_increment_zero_stops_after_n_rounds(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            args, state_path, log_path = self._fixture(Path(tmp), self._events())
            self.assertEqual(er.main(args + ["--n-zero-rounds", "2"]), 0)
            self.assertEqual(er.main(args + ["--n-zero-rounds", "2"]), 0)
            self.assertEqual(er.main(args + ["--n-zero-rounds", "2"]), 0)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertTrue(state["stopped"])
            self.assertEqual(state["stop"]["code"], "increment_zero")
            self.assertFalse(state["stop"]["convergence"])
            rounds = [json.loads(line) for line in
                      log_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["new_executions"] for row in rounds], [1, 0, 0])
            self.assertEqual(rounds[-1]["queue"]["ready"], 1)

    def test_n_cannot_change_mid_loop(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            args, _, _ = self._fixture(Path(tmp), self._events())
            self.assertEqual(er.main(args + ["--n-zero-rounds", "2"]), 0)
            self.assertEqual(er.main(args + ["--n-zero-rounds", "3"]), 2)

    def test_products_with_verdict_invalidate_round(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            args, _, log_path = self._fixture(
                Path(tmp), self._events(),
                candidates=[{"id": "C1", "observation": "候选", "verdict": "PASS"}])
            self.assertEqual(er.main(args), 0)
            row = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertTrue(row["products_invalid"])
            self.assertEqual(row["new_executions"], 0)
            self.assertTrue(row["product_violations"])

    def test_unregistered_surface_flagged_but_counted(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            events = [dict(row) for row in self._events()]
            events[1]["surf_id"] = "SURF-99"
            args, _, log_path = self._fixture(Path(tmp), events)
            self.assertEqual(er.main(args), 0)
            row = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["new_executions"], 1)
            self.assertEqual(row["unregistered_expansion"], ["ATT-1"])


class EventChainTests(unittest.TestCase):
    """H1/M4：台账必须验链；同一 batch_id 不得重复登记。"""

    def _rows(self):
        return [
            {"kind": "BEGIN", "attempt_id": "ATT-1", "case_id": "TC-1",
             "timestamp": "2026-11-26T00:00:00+00:00"},
            {"kind": "FINISH", "attempt_id": "ATT-1", "case_id": "TC-1",
             "timestamp": "2026-11-26T00:05:00+00:00", "status": "PASS",
             "probes": 4, "evidence": [{"path": "/e/a.png", "sha256": "a" * 64}]},
        ]

    def test_record_rejects_tampered_events(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            rows = chain(self._rows())
            rows[1]["status"] = "FAIL"  # 改内容但不改 digest
            events = tmp / "events.jsonl"
            events.write_text("\n".join(json.dumps(row, ensure_ascii=False)
                                       for row in rows), encoding="utf-8")
            rc = er.main(["record", "--ledger", str(tmp / "ledger.json"),
                          "--events", str(events), "--batch-id", "B1"])
            self.assertEqual(rc, 2)

    def test_duplicate_batch_id_rejected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            events = tmp / "events.jsonl"
            events.write_text("\n".join(json.dumps(row, ensure_ascii=False)
                                       for row in chain(self._rows())),
                              encoding="utf-8")
            ledger = tmp / "ledger.json"
            self.assertEqual(er.main(["record", "--ledger", str(ledger),
                                      "--events", str(events),
                                      "--batch-id", "B1"]), 0)
            self.assertEqual(er.main(["record", "--ledger", str(ledger),
                                      "--events", str(events),
                                      "--batch-id", "B1"]), 2)


class QueueCapabilityTests(unittest.TestCase):
    def test_capability_missing_moves_ready_to_blocked(self):
        report = _EXP.summarize(make_data([make_item("EXP-1"), make_item("EXP-2")]))
        state = er.queue_state(report, CAPS_PAGE_MISSING)
        self.assertEqual(state["ready"], 0)
        self.assertEqual(state["blocked"], 2)
        self.assertEqual(state["blocked_by_capability"], 2)
        stop = er.evaluate_stop(state, [], 2)
        self.assertEqual(stop["code"], "queue_empty")
        self.assertFalse(stop["convergence"])


class LoopCoverageAndGovernanceTests(unittest.TestCase):
    def _fixture(self, tmp, events_rows, surfaces=("SURF-01",), candidates=None):
        exp = tmp / "exploration.json"
        exp.write_text(json.dumps(make_data([make_item("EXP-1")])), encoding="utf-8")
        caps = tmp / "capabilities.json"
        caps.write_text(json.dumps(CAPS_AVAILABLE), encoding="utf-8")
        plan = tmp / "adversarial-plan.json"
        plan.write_text(json.dumps({"surfaces": [{"id": s} for s in surfaces]}),
                        encoding="utf-8")
        events = tmp / "events.jsonl"
        events.write_text("\n".join(json.dumps(row, ensure_ascii=False)
                                   for row in chain(events_rows)), encoding="utf-8")
        state, log = tmp / "loop-state.json", tmp / "loop-log.jsonl"
        ledger = tmp / "budget-consumption.json"
        args = ["loop", "--exploration", str(exp), "--capabilities", str(caps),
                "--plan", str(plan), "--events", str(events), "--ledger", str(ledger),
                "--state", str(state), "--log", str(log)]
        if candidates is not None:
            path = tmp / "candidates.json"
            path.write_text(json.dumps(candidates), encoding="utf-8")
            args += ["--candidates", str(path)]
        return args, state, log, ledger

    def _events(self):
        return [
            {"kind": "BEGIN", "attempt_id": "ATT-1", "case_id": "TC-1",
             "intended_action": "点保存"},
            {"kind": "FINISH", "attempt_id": "ATT-1", "case_id": "TC-1",
             "status": "PASS", "surf_id": "SURF-01", "method": "boundary",
             "probes": 4,
             "evidence": [{"path": "/e/a.png", "sha256": "a" * 64}]},
        ]

    def test_log_discloses_coverage_expansion(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            args, _, log, _ = self._fixture(Path(tmp), self._events())
            self.assertEqual(er.main(args), 0)
            row = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["new_case_ids"], ["TC-1"])
            self.assertEqual(row["new_surf_ids"], ["SURF-01"])
            self.assertEqual(row["method_counts"], {"boundary": 1})

    def test_loop_records_budget_from_events(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            args, _, _, ledger = self._fixture(Path(tmp), self._events())
            self.assertEqual(er.main(args), 0)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(data["probes_consumed"], 4)
            self.assertEqual(data["recorded_through_seq"], 2)

    def test_invalid_products_require_human_and_do_not_stop(self):
        """G-A：作废轮下一轮必须人工放行；放行后仍不参与增量归零停机。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            args, state, log, _ = self._fixture(
                Path(tmp), self._events(),
                candidates=[{"id": "C1", "observation": "候选", "verdict": "PASS"}])
            self.assertEqual(er.main(args), 0)
            self.assertEqual(er.main(args), 2)  # 未放行即拒绝推进
            self.assertEqual(er.main(args + ["--resume-after-human"]), 0)
            # 第 2 轮候选仍含判定字段 → 再次停住等人核
            self.assertEqual(er.main(args), 2)
            state_data = json.loads(state.read_text(encoding="utf-8"))
            self.assertFalse(state_data["stopped"])
            rows = [json.loads(line) for line in
                    log.read_text(encoding="utf-8").splitlines()]
            self.assertTrue(all(row["products_invalid"] for row in rows))
            self.assertTrue(all(row["requires_human"] for row in rows))

    def test_plan_required_for_loop(self):
        """G-B：--plan 必填——缺省即静默关闭自扩边界检查，不允许。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            args, _, _, _ = self._fixture(Path(tmp), self._events())
            index = args.index("--plan")
            without = args[:index] + args[index + 2:]
            with self.assertRaises(SystemExit) as ctx:
                er.main(without)
            self.assertEqual(ctx.exception.code, 2)

    def test_surfaces_missing_disclosed(self):
        """plan 无 surfaces 字段时显式披露，unregistered_expansion 不得静默为空。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            args, _, log, _ = self._fixture(tmp, self._events())
            (tmp / "adversarial-plan.json").write_text(
                json.dumps({"attack_hypotheses": []}), encoding="utf-8")
            self.assertEqual(er.main(args), 0)
            row = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
            self.assertTrue(row["surfaces_missing"])

    def test_new_fail_disclosed_as_detection(self):
        """G-C：检出披露——每轮新增 FAIL 必须可见（不作门禁）。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            events = [dict(row) for row in self._events()]
            events[1] = dict(events[1], status="FAIL")
            args, _, log, _ = self._fixture(Path(tmp), events)
            self.assertEqual(er.main(args), 0)
            row = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["new_fail_count"], 1)
            self.assertEqual(row["fail_attempt_ids"], ["ATT-1"])

    def test_plan_change_requires_new_state(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            args, _, _, _ = self._fixture(tmp, self._events())
            self.assertEqual(er.main(args), 0)
            (tmp / "adversarial-plan.json").write_text(
                json.dumps({"surfaces": [{"id": "SURF-02"}]}), encoding="utf-8")
            self.assertEqual(er.main(args), 2)


class AutoJudgementTests(unittest.TestCase):
    def _over_budget_setup(self, tmp):
        """预算已耗尽的编排环境：每次判定都记（含 auto=false）。"""
        exp = tmp / "exploration.json"
        exp.write_text(json.dumps(make_data([make_item("EXP-1")])), encoding="utf-8")
        caps = tmp / "capabilities.json"
        caps.write_text(json.dumps(CAPS_AVAILABLE), encoding="utf-8")
        decl = tmp / "declaration.json"
        decl.write_text(json.dumps(valid_declaration()), encoding="utf-8")
        manifest = tmp / "manifest.json"
        manifest.write_text(json.dumps({"read_only_budget": {"probes": 1}}),
                            encoding="utf-8")
        ledger = tmp / "budget-consumption.json"
        ledger.write_text(json.dumps({
            **er.new_budget_ledger({"read_only_budget": {"probes": 1}}),
            "probes_consumed": 9}), encoding="utf-8")
        return ["--exploration", str(exp), "--capabilities", str(caps),
                "--declaration", str(decl), "--runtime-manifest", str(manifest),
                "--budget-ledger", str(ledger), "--out", str(tmp / "plan.json")], ledger

    def test_non_auto_judgement_is_recorded_for_denominator(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            args, ledger = self._over_budget_setup(tmp)
            self.assertEqual(er.main(args), 0)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(len(data["auto_judgements"]), 1)
            self.assertFalse(data["auto_judgements"][0]["auto"])
            self.assertEqual(data["auto_approval"], [])

    def test_repeated_replan_does_not_inflate_ratio(self):
        """G-D：反复重排同一计划只保留一次编排期判定，比率分母不虚增。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            args, ledger = self._over_budget_setup(tmp)
            for _ in range(3):
                self.assertEqual(er.main(args), 0)
            data = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(len(data["auto_judgements"]), 1)


if __name__ == "__main__":
    unittest.main()