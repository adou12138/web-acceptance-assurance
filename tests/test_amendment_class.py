"""修订分类契约（M7）：readonly_lead 与 scope_change 的处置必须相反且机械可判。

契约的单一事实源是 `scripts/amendment_class.py`，本文件锁住两端消费
（`phase_gate` 与 `execution_ledger`）的行为一致。

三条硬规则：
1. **固定授权只读观察可继续**：凭据齐备的 `readonly_lead` 登记后不阻断阶段推进。
2. **范围变更守先批后动**：新增写入、角色或权限扩展、环境变更、超预算、
   属性未知或凭据不全——一律 `scope_change`，未批准不得执行。
3. **`path_type`、HTTP 方法或「未看到写操作」单独都不能作为只读凭据**：
   缺凭据的只读申报从严归 `scope_change`。

分类只认**登记事实**，不认自然语言自述。
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load(name, path=None):
    spec = importlib.util.spec_from_file_location(name, path or SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AmendmentBase(unittest.TestCase):
    def setUp(self):
        self.pg = load("phase_gate")
        self.ac = load("amendment_class")
        self.ledger = load("execution_ledger")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)

    def state(self, name="st"):
        path = self.folder / f"{name}.json"
        state = self.pg.init_state(path, "TASK-1", "SESS-1",
                                   self.pg.ROUTING["plan"]["model_class"],
                                   self.pg.ROUTING["plan"]["model_hint"])
        return state, path

    def readonly_declaration(self, **overrides):
        declaration = {
            "kind": "readonly_lead",
            "scope": "查看已授权项目的列表页",
            "environment": "sandbox",
            "role": "editor",
            "allowed_actions": ["view_list", "read_dom"],
            "no_state_change": True,
        }
        declaration.update(overrides)
        return declaration

    def handoff_artifacts(self):
        """plan → execute 交接要求的三件真实产物（只认真实存在的绝对路径）。"""
        names = ("review-result.json", "cases-suite.json", "adversarial-plan.json")
        paths = []
        for name in names:
            path = self.folder / name
            path.write_text("{}\n", encoding="utf-8")
            paths.append(str(path))
        return paths

    def run_plan_to_checkpoint(self, state):
        """走完 plan 阶段并停在交接点：plan done + execute 已 submit。"""
        self.pg.start(state, "plan", session="SESS-1")
        self.pg.complete(state, "plan", session="SESS-1")
        self.pg.submit(state, "execute", session="SESS-1",
                       artifacts=self.handoff_artifacts(),
                       model_class=self.pg.ROUTING["execute"]["model_class"],
                       model_name=self.pg.ROUTING["execute"]["model_hint"])

    def resume_execute(self, state):
        """凭交接包确认码在新会话 resume execute（确认文本取自真实交接包文件）。"""
        handoff = state["phases"]["plan"]["handoff"]
        handoff_json = json.loads(Path(handoff["json"]).read_text(encoding="utf-8"))
        confirm = handoff_json["confirm_text"]
        self.pg.resume(state, "execute", "SESS-2", confirm,
                       model_class=self.pg.ROUTING["execute"]["model_class"],
                       model_name=self.pg.ROUTING["execute"]["model_hint"])
        return confirm

    def settled_state(self):
        """三阶段全部走完的阶段状态：此时上限只可能被修订分类影响。"""
        state, _path = self.state()
        for phase in ("plan", "execute", "adjudicate"):
            entry = state["phases"][phase]
            entry["status"] = "done"
            entry["approval"] = {"mode": "cross_session"}
            entry["routing"] = {"declared_class": self.pg.ROUTING[phase]["model_class"],
                                "expected_class": self.pg.ROUTING[phase]["model_class"]}
        state["awaiting_session_for"] = None
        return state


class ClassificationContract(AmendmentBase):
    """分类判据本身。"""

    def test_complete_readonly_declaration_is_readonly(self):
        kind, reasons = self.ac.classify(self.readonly_declaration())
        self.assertEqual(kind, "readonly_lead")
        self.assertEqual(reasons, [])

    def test_missing_readonly_evidence_falls_back_to_scope_change(self):
        """缺只读凭据 → 从严归 scope_change（先批后动）。"""
        for field in ("scope", "environment", "role", "allowed_actions", "no_state_change"):
            declaration = self.readonly_declaration()
            declaration.pop(field)
            kind, reasons = self.ac.classify(declaration)
            self.assertEqual(kind, "scope_change", field)
            self.assertTrue(reasons)

    def test_write_signal_beats_readonly_evidence(self):
        """命中范围变更信号时，即使只读凭据齐备也归 scope_change。"""
        for signal in ("writes", "role_change", "environment_change",
                       "over_budget", "unknown_attributes", "incomplete_evidence"):
            kind, reasons = self.ac.classify(
                self.readonly_declaration(**{signal: True}))
            self.assertEqual(kind, "scope_change", signal)
            self.assertTrue(any(signal in reason for reason in reasons))

    def test_no_declaration_defaults_to_scope_change(self):
        """没有分类信息 → 从严按 scope_change 处理。"""
        for declaration in (None, {}, {"kind": "unknown_kind"}):
            kind, reasons = self.ac.classify(declaration)
            self.assertEqual(kind, "scope_change")

    def test_path_type_alone_cannot_justify_readonly(self):
        """path_type / HTTP 方法 / 「未看到写操作」单独都不是只读凭据。"""
        declaration = self.readonly_declaration()
        declaration.pop("scope")
        declaration["path_type"] = "positive"
        declaration["http_method"] = "GET"
        kind, _reasons = self.ac.classify(declaration)
        self.assertEqual(kind, "scope_change")

    def test_unknown_field_is_rejected(self):
        ok, message = self.ac.validate_declaration({"kind": "readonly_lead", "随便": 1})
        self.assertFalse(ok)
        self.assertIn("未知字段", message)

    def test_requires_agreement(self):
        self.assertFalse(self.ac.requires_approval("readonly_lead"))
        self.assertTrue(self.ac.requires_approval("scope_change"))

    def test_is_blocking_is_driven_by_kind_then_status(self):
        """类别决定"需不需要先批"，状态决定"批了没有"。"""
        # 只读：任何状态下都不阻断
        self.assertFalse(self.ac.is_blocking(
            {"kind": "readonly_lead", "status": "awaiting_approval"}))
        self.assertFalse(self.ac.is_blocking(
            {"kind": "readonly_lead", "status": "approved_readonly"}))
        # 范围变更：只在未批准时阻断
        self.assertTrue(self.ac.is_blocking(
            {"kind": "scope_change", "status": "awaiting_approval"}))
        self.assertFalse(self.ac.is_blocking(
            {"kind": "scope_change", "status": "approved"}))
        # 历史状态文件没有 kind → 从严按范围变更处理
        self.assertTrue(self.ac.is_blocking({"status": "awaiting_approval"}))
        self.assertFalse(self.ac.is_blocking({"status": "approved"}))


class PhaseGateBehaviour(AmendmentBase):
    """phase_gate 侧：分类决定阻断与否。"""

    def test_readonly_amendment_does_not_block_next_phase(self):
        """固定授权只读观察登记后可继续阶段推进（走到下一阶段真正开始）。"""
        state, _path = self.state()
        self.pg.start(state, "plan", session="SESS-1")
        self.pg.request_amendment(state, "plan", "tester", "计划外发现一个只读线索",
                                  session="SESS-1",
                                  declaration=self.readonly_declaration())
        self.assertEqual(state["amendments"][0]["kind"], "readonly_lead")
        self.assertEqual(state["amendments"][0]["status"], "approved_readonly")
        # 只读登记不阻断：plan 完成后可 submit 下一阶段
        self.pg.complete(state, "plan", session="SESS-1")
        self.pg.submit(state, "execute", session="SESS-1",
                       artifacts=self.handoff_artifacts(),
                       model_class=self.pg.ROUTING["execute"]["model_class"],
                       model_name=self.pg.ROUTING["execute"]["model_hint"])
        self.assertEqual(state["phases"]["execute"]["status"], "awaiting_approval")
        # 交接点也不被只读登记卡住
        self.pg.checkpoint(state, "plan", self.folder, session="SESS-1")
        self.assertEqual(state["awaiting_session_for"], "execute")
        # 新会话 resume 后可开始下一阶段
        self.resume_execute(state)
        self.pg.start(state, "execute", session="SESS-2")
        self.assertEqual(state["phases"]["execute"]["status"], "in_progress")

    def test_scope_change_blocks_until_approved(self):
        """范围变更未批准 → 阶段推进被阻（checkpoint / start / resume 三处）。"""
        state, _path = self.state()
        self.pg.start(state, "plan", session="SESS-1")
        self.pg.request_amendment(state, "plan", "tester", "需要写操作",
                                  session="SESS-1",
                                  declaration={"kind": "scope_change", "writes": True})
        self.assertEqual(state["amendments"][0]["status"], "awaiting_approval")
        self.pg.complete(state, "plan", session="SESS-1")
        self.pg.submit(state, "execute", session="SESS-1",
                       artifacts=self.handoff_artifacts(),
                       model_class=self.pg.ROUTING["execute"]["model_class"],
                       model_name=self.pg.ROUTING["execute"]["model_hint"])
        # 交接点被阻：先批后动
        with self.assertRaises(ValueError) as ctx:
            self.pg.checkpoint(state, "plan", self.folder, session="SESS-1")
        self.assertIn("范围变更", str(ctx.exception))
        # 批准后放行
        self.pg.approve_amendment(state, "AMD-1", "tester", "同意本次范围扩展")
        self.assertEqual(state["amendments"][0]["status"], "approved")
        self.pg.checkpoint(state, "plan", self.folder, session="SESS-1")
        self.resume_execute(state)
        self.pg.start(state, "execute", session="SESS-2")
        self.assertEqual(state["phases"]["execute"]["status"], "in_progress")

    def test_scope_change_blocks_resume(self):
        """范围变更未批准时，跨会话 resume 也不得绕过先批后动。"""
        state, _path = self.state()
        self.pg.start(state, "plan", session="SESS-1")
        self.pg.request_amendment(state, "plan", "tester", "需要写操作",
                                  session="SESS-1",
                                  declaration={"kind": "scope_change", "writes": True})
        self.pg.complete(state, "plan", session="SESS-1")
        self.pg.submit(state, "execute", session="SESS-1",
                       artifacts=self.handoff_artifacts(),
                       model_class=self.pg.ROUTING["execute"]["model_class"],
                       model_name=self.pg.ROUTING["execute"]["model_hint"])
        # 先放行修订，才能走到交接点；再重新制造一个未批准的修订
        self.pg.approve_amendment(state, "AMD-1", "tester", "同意")
        self.pg.checkpoint(state, "plan", self.folder, session="SESS-1")
        state["amendments"].append({"id": "AMD-2", "phase": "plan",
                                    "kind": "scope_change", "status": "awaiting_approval",
                                    "note": "第二个未批准的范围变更", "approval": None})
        with self.assertRaises(ValueError) as ctx:
            self.resume_execute(state)
        self.assertIn("范围变更", str(ctx.exception))

    def test_legacy_call_without_declaration_still_blocks(self):
        """旧调用不传声明 → 仍按先批后动（原行为不得被放宽）。"""
        state, _path = self.state()
        self.pg.request_amendment(state, "plan", "tester", "老式修订", session="SESS-1")
        self.assertEqual(state["amendments"][0]["kind"], "scope_change")
        self.assertEqual(state["amendments"][0]["status"], "awaiting_approval")

    def test_check_verdict_does_not_block_on_readonly(self):
        """只读观察不阻断裁决（check_verdict 不把它列为未批准修订）。"""
        state, _path = self.state()
        self.pg.request_amendment(state, "plan", "tester", "只读线索",
                                  session="SESS-1",
                                  declaration=self.readonly_declaration())
        reasons = self.pg.check_verdict(state)["reasons"]
        self.assertFalse(any("修订申请" in reason for reason in reasons))

    def test_check_verdict_blocks_on_unapproved_scope_change(self):
        """未批准的范围变更阻断裁决。"""
        state, _path = self.state()
        self.pg.request_amendment(state, "plan", "tester", "要写",
                                  session="SESS-1",
                                  declaration={"kind": "scope_change", "writes": True})
        reasons = self.pg.check_verdict(state)["reasons"]
        self.assertTrue(any("修订申请" in reason for reason in reasons))


class LedgerBehaviour(AmendmentBase):
    """execution_ledger 侧：与 phase_gate 同一口径。"""

    def test_readonly_amendment_does_not_lower_ceiling(self):
        """只读观察不压台账结论上限（三阶段已走完时上限仍为无限制）。"""
        state = self.settled_state()
        before = self.ledger._phase_precondition(state)
        self.assertIsNone(before["phase_ceiling"])
        self.pg.request_amendment(state, "plan", "tester", "只读线索",
                                  session="SESS-1",
                                  declaration=self.readonly_declaration())
        result = self.ledger._phase_precondition(state)
        self.assertIsNone(result["phase_ceiling"])
        self.assertEqual(result["phase_reasons"], before["phase_reasons"])

    def test_unapproved_scope_change_lowers_ceiling(self):
        """未批准的范围变更压上限——与 phase_gate 同口径。"""
        state = self.settled_state()
        self.pg.request_amendment(state, "plan", "tester", "要写",
                                  session="SESS-1",
                                  declaration={"kind": "scope_change", "writes": True})
        result = self.ledger._phase_precondition(state)
        self.assertEqual(result["phase_ceiling"], "待核实")
        self.assertTrue(any("范围变更" in reason or "scope_change" in reason
                            for reason in result["phase_reasons"]))

    def test_approved_scope_change_does_not_lower_ceiling(self):
        """范围变更获批后不再压上限——先批后动的闭环。"""
        state = self.settled_state()
        self.pg.request_amendment(state, "plan", "tester", "要写",
                                  session="SESS-1",
                                  declaration={"kind": "scope_change", "writes": True})
        self.pg.approve_amendment(state, "AMD-1", "tester", "同意")
        result = self.ledger._phase_precondition(state)
        self.assertIsNone(result["phase_ceiling"])

    def test_readonly_kind_wins_over_waiting_status(self):
        """类别优先于状态字段：只读观察即便落在待批准状态也不阻断、不压上限。

        这条锁的是「kind 不是死字段」——若判定只看 status，kind 就无人消费。
        """
        state = self.settled_state()
        state["amendments"].append({"id": "AMD-9", "phase": "plan",
                                    "kind": "readonly_lead", "status": "awaiting_approval",
                                    "note": "只读线索", "approval": None})
        # phase_gate 侧
        self.assertEqual(self.pg._pending_scope_changes(state), [])
        reasons = self.pg.check_verdict(state)["reasons"]
        self.assertFalse(any("修订申请" in reason for reason in reasons))
        # 台账侧
        result = self.ledger._phase_precondition(state)
        self.assertIsNone(result["phase_ceiling"])

    def test_legacy_amendment_without_kind_blocks(self):
        """历史状态文件里没有 kind 的待批准修订 → 从严阻断。"""
        state = self.settled_state()
        state["amendments"].append({"id": "AMD-0", "phase": "plan",
                                    "status": "awaiting_approval",
                                    "note": "老版本登记的修订", "approval": None})
        self.assertEqual(len(self.pg._pending_scope_changes(state)), 1)
        result = self.ledger._phase_precondition(state)
        self.assertEqual(result["phase_ceiling"], "待核实")


class CliContract(AmendmentBase):
    """CLI 入口：声明文件必须真的被消费。"""

    def test_amend_with_declaration_file(self):
        declaration = self.folder / "decl.json"
        declaration.write_text(json.dumps({
            "kind": "readonly_lead", "scope": "查看列表页", "environment": "sandbox",
            "role": "editor", "allowed_actions": ["view_list"], "no_state_change": True,
        }), encoding="utf-8")
        state_path = self.folder / "cli-state.json"
        out = subprocess.run(
            [sys.executable, str(SCRIPTS / "phase_gate.py"), "init", str(state_path),
             "--task", "TASK-1", "--session", "SESS-1",
             "--model-class", self.pg.ROUTING["plan"]["model_class"],
             "--model-name", self.pg.ROUTING["plan"]["model_hint"]],
            capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(out.returncode, 0, out.stderr)
        out = subprocess.run(
            [sys.executable, str(SCRIPTS / "phase_gate.py"), "amend", str(state_path),
             "--phase", "plan", "--by", "tester", "--note", "只读线索",
             "--session", "SESS-1", "--declaration", str(declaration)],
            capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(out.returncode, 0, out.stderr)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["amendments"][0]["kind"], "readonly_lead")
        self.assertEqual(state["amendments"][0]["status"], "approved_readonly")


if __name__ == "__main__":
    unittest.main()
