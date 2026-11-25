import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "phase_gate.py"


class PhaseGateTestBase(unittest.TestCase):
    def setUp(self):
        if not SCRIPT.exists():
            self.fail("Missing three-phase execution gate: scripts/phase_gate.py")
        spec = importlib.util.spec_from_file_location("phase_gate", SCRIPT)
        self.pg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.pg)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.handoff_dir = self.folder / "handoff"
        self.handoff_dir.mkdir()
        self.state_path = self.folder / "phase-state.json"
        self.state = self.pg.init_state(self.state_path, "TASK-1", "SESS-1",
                                        self.pg.ROUTING["plan"]["model_class"],
                                        self.pg.ROUTING["plan"]["model_hint"])
        self.artifacts = self._write_artifacts()

    def _write_artifacts(self):
        """plan→execute 交接的三件真实产物：评审结论、用例集、对抗 plan。"""
        made = {}
        for name in ("review-result.json", "cases-demo.json", "adversarial-plan.json"):
            path = self.folder / name
            path.write_text("{}", encoding="utf-8")
            made[name.split("-")[0]] = str(path)
        return [made["review"], made["cases"], made["adversarial"]]

    def submit_phase(self, phase, session="SESS-1", **kwargs):
        kwargs.setdefault("artifacts", self.artifacts if phase == "execute" else None)
        kwargs.setdefault("model_class", self.pg.ROUTING[phase]["model_class"])
        return self.pg.submit(self.state, phase, session=session, **kwargs)

    def clean_collection_file(self):
        """新契约：完整 happy path 的 execute 收尾包含合规采集记录。"""
        folder = Path(self.tmp.name) / "collection"
        folder.mkdir(exist_ok=True)
        record = {"id": "OBS-1", "tc_ref": "TC-1", "collection_state": "collected",
                  "action_sequence": [{"step": 1, "action": "click", "target": "提交"}],
                  "evidence_paths": ["evidence/step1.png"],
                  "observed": {"page": "提示已保存"}}
        path = folder / "clean.jsonl"
        if not path.exists():
            path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def check(self, **kwargs):
        """带齐风险假设与回归影响面的裁决前置检查（缺任一项都该被压上限）。"""
        kwargs.setdefault("assumptions", {"reviewed": True, "items": []})
        kwargs.setdefault("regression", {"changed_areas": ["AI 主页整页替换"],
                                         "candidates": [{"id": "RG-01", "basis": "旧会话列表渲染"}]})
        return self.pg.check_verdict(self.state, **kwargs)

    def session_for(self, phase):
        return f"SESS-{self.pg.PHASES.index(phase) + 1}"

    def resume(self, phase, model_class=None):
        """模拟用户在新会话里粘回确认文本。"""
        prior = self.pg.PHASES[self.pg.PHASES.index(phase) - 1]
        token = self.state["phases"][prior]["handoff"]["token"]
        return self.pg.resume(self.state, phase, self.session_for(phase),
                              f"续跑任务 TASK-1 的 {phase} 阶段，确认码 {token}",
                              model_class or self.pg.ROUTING[phase]["model_class"],
                              self.pg.ROUTING[phase]["model_hint"])

    def finish_phase(self, phase, model_classes=None):
        """跨会话走完一个阶段：resume → start → complete → submit 下一阶段 → checkpoint。"""
        model_classes = model_classes or {}
        session = self.session_for(phase)
        if phase == "plan":
            # 规划阶段的模型来自起始会话：用会话记录模拟“这一阶段实际用的模型”
            if model_classes.get("plan"):
                self.state["sessions"][0]["model_class"] = model_classes["plan"]
        else:
            self.resume(phase, model_classes.get(phase))
        self.pg.start(self.state, phase, session)
        # 执行阶段收尾必须交攻击覆盖结论，否则“铺得全”会当成“打得深”过门
        coverage = ({"verdict_ceiling": "通过", "gaps": [], "attack_density": 0.4}
                    if phase == "execute" else None)
        # 探索清单 gate 与攻击覆盖同级：SKILL 把它写成批次结论硬前置
        exploration = ({
            "verdict_ceiling": "通过",
            "ready_pending": 0,
            "missing_views": [],
            "failed_items": [],
            "traversal_recorded": True,
            "mandatory_attributes": {"security": "covered", "compatibility": "covered",
                                     "copy": "covered"},
            "message": "无未消费候选、必查视角均有记录且含控件遍历条目",
        } if phase == "execute" else None)
        self.pg.complete(self.state, phase, session=session, attack_coverage=coverage,
                         exploration=exploration,
                         collection=self.clean_collection_file() if phase == "execute" else None)
        nxt = self.pg.NEXT_PHASE[phase]
        if nxt:
            self.pg.submit(self.state, nxt, session=session,
                           artifacts=self.artifacts if nxt == "execute" else None,
                           model_class=model_classes.get(nxt, self.pg.ROUTING[nxt]["model_class"]),
                           model_name=self.pg.ROUTING[nxt]["model_hint"])
        self.pg.checkpoint(self.state, phase, self.handoff_dir, session=session)
        return self.state

    def finish_all(self, model_classes=None):
        for phase in self.pg.PHASES:
            self.finish_phase(phase, model_classes)


class PhaseMachineTests(PhaseGateTestBase):
    def test_init_approves_plan_and_leaves_others_pending(self):
        """用户下达任务即视为放行规划阶段，其余阶段仍须交接放行。"""
        self.assertEqual(list(self.state["phases"]), list(self.pg.PHASES))
        self.assertEqual(self.state["phases"]["plan"]["status"], "approved")
        self.assertEqual(self.state["phases"]["plan"]["approval"]["mode"], "task_start")
        for phase in ("execute", "adjudicate"):
            self.assertEqual(self.state["phases"][phase]["status"], "pending")
        check = self.check()
        self.assertFalse(check["verdict_allowed"])
        self.assertEqual(check["phase_ceiling"], "待核实")

    def test_execute_completion_without_attack_coverage_blocks_verdict(self):
        # 真实教训：154 项执行里 143 项走正向、风险面关联 0，却一路进到裁决
        self.pg.start(self.state, "plan", "SESS-1")
        self.pg.complete(self.state, "plan", session="SESS-1")
        self.submit_phase("execute")
        self.pg.checkpoint(self.state, "plan", self.handoff_dir, session="SESS-1")
        self.resume("execute")
        self.pg.start(self.state, "execute", self.session_for("execute"))
        self.pg.complete(self.state, "execute", session=self.session_for("execute"),
                         collection=self.clean_collection_file())
        entry = self.state["phases"]["execute"]["attack_coverage"]
        self.assertEqual(entry["state"], "missing")
        check = self.check()
        self.assertFalse(check["verdict_allowed"])
        self.assertTrue(any("attack_coverage" in r for r in check["reasons"]))

    def test_low_attack_density_blocks_verdict(self):
        self.pg.start(self.state, "plan", "SESS-1")
        self.pg.complete(self.state, "plan", session="SESS-1")
        self.submit_phase("execute")
        self.pg.checkpoint(self.state, "plan", self.handoff_dir, session="SESS-1")
        self.resume("execute")
        self.pg.start(self.state, "execute", self.session_for("execute"))
        self.pg.complete(self.state, "execute", session=self.session_for("execute"),
                         collection=self.clean_collection_file(),
                         attack_coverage={"verdict_ceiling": "待核实", "attack_density": 0.078,
                                          "gaps": [{"code": "attack_density_low",
                                                    "message": "密度 0.078 低于门槛 0.3"}]})
        check = self.check()
        self.assertFalse(check["verdict_allowed"])
        self.assertTrue(any("攻击覆盖不达标" in r for r in check["reasons"]))

    def test_execute_cannot_start_before_cross_session_resume(self):
        self.pg.start(self.state, "plan", "SESS-1")
        self.pg.complete(self.state, "plan", session="SESS-1")
        self.submit_phase("execute")
        # 只 submit 申请，不得开始——必须新会话 resume
        with self.assertRaises(ValueError):
            self.pg.start(self.state, "execute", "SESS-1")

    def test_execute_requires_plan_done(self):
        with self.assertRaises(ValueError):
            self.submit_phase("execute")

    def test_happy_path_across_three_sessions_unlocks_verdict(self):
        self.finish_all()
        check = self.check()
        self.assertTrue(check["verdict_allowed"], check["reasons"])
        self.assertIsNone(check["phase_ceiling"])
        ids = [item["id"] for item in self.state["sessions"]]
        self.assertEqual(ids, ["SESS-1", "SESS-2", "SESS-3"])

    def test_fail_ceiling_from_upstream_propagates_not_downgraded_to_pending(self):
        """上游已算出「不通过」时，phase_ceiling 必须保留「不通过」，不得恒压成待核实。

        B1 后该缺口属 downgrade 档：允许出具结论，但档位仍取上游更严的「不通过」。
        """
        self.finish_all()
        self.state["phases"]["execute"]["attack_coverage"]["ceiling"] = "不通过"
        check = self.check()
        self.assertTrue(check["verdict_allowed"])
        self.assertEqual(check["phase_ceiling"], "不通过")
        self.assertTrue(any(l["code"] == "attack_coverage_not_met"
                            for l in check["limitations"]))

    def test_explore_fail_ceiling_propagates_not_downgraded_to_pending(self):
        """探索清单上游判「不通过」时，档位同样保留，不被恒压成待核实。"""
        self.finish_all()
        self.state["phases"]["execute"]["exploration"]["ceiling"] = "不通过"
        check = self.check()
        self.assertTrue(check["verdict_allowed"])
        self.assertEqual(check["phase_ceiling"], "不通过")
        self.assertTrue(any(l["code"] == "exploration_not_met"
                            for l in check["limitations"]))

    def test_sessions_record_actual_models(self):
        self.finish_all()
        models = {item["id"]: item["model_name"] for item in self.state["sessions"]}
        self.assertEqual(models["SESS-1"], self.pg.ROUTING["plan"]["model_hint"])
        self.assertEqual(models["SESS-2"], self.pg.ROUTING["execute"]["model_hint"])
        self.assertEqual(models["SESS-3"], self.pg.ROUTING["adjudicate"]["model_hint"])

    def test_reject_then_resubmit(self):
        self.pg.start(self.state, "plan", "SESS-1")
        self.pg.complete(self.state, "plan", session="SESS-1")
        self.submit_phase("execute")
        self.pg.reject(self.state, "execute", "user", "缺少异常路径")
        self.assertEqual(self.state["phases"]["execute"]["status"], "rejected")
        self.assertEqual(self.state["phases"]["execute"]["rejections"][0]["reason"], "缺少异常路径")
        self.submit_phase("execute")
        self.assertEqual(self.state["phases"]["execute"]["status"], "awaiting_approval")

    def test_complete_requires_in_progress(self):
        with self.assertRaises(ValueError):
            self.pg.complete(self.state, "plan", session="SESS-1")

    def test_open_amendment_blocks_start_and_verdict(self):
        self.pg.start(self.state, "plan", "SESS-1")
        self.pg.request_amendment(self.state, "plan", "agent", "采集期发现新风险面需补设计",
                                  session="SESS-1")
        check = self.check()
        self.assertFalse(check["verdict_allowed"])
        self.assertTrue(any("修订" in reason for reason in check["reasons"]))
        self.pg.approve_amendment(self.state, "AMD-1", "user", "同意补充设计")
        self.assertEqual(self.state["amendments"][0]["status"], "approved")

    def test_routing_mismatch_blocks_until_acknowledged(self):
        """路由误配属 downgrade 档：允许出具结论但压待核实；ack 后缺口清空、上限解除。"""
        self.finish_all(model_classes={"plan": self.pg.ROUTING["execute"]["model_class"]})
        check = self.check()
        self.assertTrue(check["verdict_allowed"], "B1 后误配允许受限交付")
        self.assertEqual(check["phase_ceiling"], "待核实", "规划用快模型属路由误配，须压上限")
        self.assertTrue(any(l["code"] == "routing_mismatch" for l in check["limitations"]))
        self.assertTrue(any(w["code"] == "routing_mismatch" for w in check["warnings"]))
        self.pg.acknowledge_routing(self.state, "plan", "本会话仅有快模型可用，已在报告中披露")
        check = self.check()
        self.assertTrue(check["verdict_allowed"], check["reasons"])
        self.assertIsNone(check["phase_ceiling"])

    def test_state_persists_and_reloads(self):
        self.finish_all()
        self.pg.save(self.state_path, self.state)
        reloaded = self.pg.load(self.state_path)
        self.assertEqual(reloaded["phases"]["adjudicate"]["status"], "done")
        self.assertTrue(self.pg.check_verdict(reloaded, assumptions={"reviewed": True, "items": []}, regression={"changed_areas": [], "candidates": [], "no_impact_reason": "重载校验"})["verdict_allowed"])
        self.assertGreaterEqual(len(reloaded["history"]), len(self.pg.PHASES) * 4)

    def test_init_refuses_existing_state(self):
        with self.assertRaises(ValueError):
            self.pg.init_state(self.state_path, "TASK-2")


class CrossSessionHandoffTests(PhaseGateTestBase):
    """阶段之间必须停会话、新会话续跑——同会话推进一律不给过。"""

    def to_handoff(self):
        self.pg.start(self.state, "plan", "SESS-1")
        self.pg.complete(self.state, "plan", session="SESS-1")
        self.submit_phase("execute")
        self.pg.checkpoint(self.state, "plan", self.handoff_dir, session="SESS-1")
        return self.state["phases"]["plan"]["handoff"]

    def test_checkpoint_requires_next_phase_application(self):
        self.pg.start(self.state, "plan", "SESS-1")
        with self.assertRaises(ValueError) as ctx:
            self.pg.checkpoint(self.state, "plan", self.handoff_dir, session="SESS-1")
        self.assertIn("submit", str(ctx.exception))

    def test_checkpoint_requires_phase_started(self):
        with self.assertRaises(ValueError):
            self.pg.checkpoint(self.state, "plan", self.handoff_dir, session="SESS-1")

    def test_checkpoint_writes_handoff_with_confirm_text(self):
        handoff = self.to_handoff()
        md = Path(handoff["markdown"])
        self.assertTrue(md.is_file())
        text = md.read_text(encoding="utf-8")
        self.assertIn(handoff["token"], text)
        self.assertIn(self.pg.ROUTING["execute"]["model_hint"], text)
        self.assertEqual(self.state["awaiting_session_for"], "execute")
        self.assertFalse(handoff["consumed"])

    def test_old_session_cannot_advance_after_checkpoint(self):
        handoff = self.to_handoff()
        with self.assertRaises(ValueError):
            self.pg.start(self.state, "execute", "SESS-1")
        self.assertEqual(self.state["phases"]["execute"]["status"], "awaiting_approval")
        self.assertFalse(handoff["consumed"])

    def test_resume_requires_confirm_token(self):
        self.to_handoff()
        with self.assertRaises(ValueError) as ctx:
            self.pg.resume(self.state, "execute", "SESS-2", "继续", "fast")
        self.assertIn("确认码", str(ctx.exception))
        self.assertEqual(self.state["phases"]["execute"]["status"], "awaiting_approval")

    def test_resume_rejects_same_session(self):
        self.to_handoff()
        token = self.state["phases"]["plan"]["handoff"]["token"]
        with self.assertRaises(ValueError) as ctx:
            self.pg.resume(self.state, "execute", "SESS-1", f"确认码 {token}", "fast")
        self.assertIn("必须换会话", str(ctx.exception))

    def test_resume_rejects_reused_session_id(self):
        self.to_handoff()
        token = self.state["phases"]["plan"]["handoff"]["token"]
        with self.assertRaises(ValueError) as ctx:
            self.pg.resume(self.state, "execute", "SESS-1", f"确认码 {token}", "fast")
        self.assertIn("换会话", str(ctx.exception))
        # 已完成阶段用过的会话标识不得复用
        self.pg.resume(self.state, "execute", "SESS-2", f"确认码 {token}", "fast")
        self.pg.start(self.state, "execute", "SESS-2")
        self.pg.complete(self.state, "execute", session="SESS-2",
                         collection=self.clean_collection_file())
        self.submit_phase("adjudicate", session="SESS-2")
        self.pg.checkpoint(self.state, "execute", self.handoff_dir, session="SESS-2")
        token2 = self.state["phases"]["execute"]["handoff"]["token"]
        # 复用交接会话本身：直接拦
        with self.assertRaises(ValueError) as ctx:
            self.pg.resume(self.state, "adjudicate", "SESS-2", f"确认码 {token2}", "strong-reasoning")
        self.assertIn("换会话", str(ctx.exception))
        # 复用更早阶段用过的会话标识：同样拦（不同阶段必须各自新会话）
        with self.assertRaises(ValueError) as ctx:
            self.pg.resume(self.state, "adjudicate", "SESS-1", f"确认码 {token2}", "strong-reasoning")
        self.assertIn("已使用过", str(ctx.exception))

    def test_resume_cannot_double_consume_handoff(self):
        self.to_handoff()
        token = self.state["phases"]["plan"]["handoff"]["token"]
        self.pg.resume(self.state, "execute", "SESS-2", f"确认码 {token}", "fast")
        with self.assertRaises(ValueError):
            self.pg.resume(self.state, "execute", "SESS-3", f"确认码 {token}", "fast")

    def test_resume_records_cross_session_approval(self):
        self.to_handoff()
        token = self.state["phases"]["plan"]["handoff"]["token"]
        self.pg.resume(self.state, "execute", "SESS-2", f"确认码 {token}", "fast", "GLM-5.3-Flash high")
        approval = self.state["phases"]["execute"]["approval"]
        self.assertEqual(approval["mode"], "cross_session")
        self.assertEqual(approval["from_session"], "SESS-1")
        self.assertEqual(approval["to_session"], "SESS-2")
        self.assertEqual(approval["confirm_token"], token)
        self.assertEqual(self.state["active_session"], "SESS-2")
        self.assertIsNone(self.state["awaiting_session_for"])

    def test_foreign_session_cannot_operate_state(self):
        self.to_handoff()
        with self.assertRaises(ValueError) as ctx:
            self.submit_phase("adjudicate", session="SESS-9")
        self.assertIn("无权操作", str(ctx.exception))

    def test_handoff_reject_from_new_session(self):
        self.to_handoff()
        self.pg.reject(self.state, "execute", "user", "交接产物缺用例追溯", session="SESS-2")
        self.assertEqual(self.state["phases"]["execute"]["status"], "rejected")
        self.assertEqual(self.state["active_session"], "SESS-2")
        self.assertIsNone(self.state["awaiting_session_for"])

    def test_same_session_approve_caps_verdict(self):
        """同会话放行是兼容路径：能走完流程，但结论上限只能待核实（B1 downgrade 档）。"""
        for phase in self.pg.PHASES:
            if phase != "plan":
                self.submit_phase(phase)
                self.pg.approve(self.state, phase, "user", "同会话同意")
            self.pg.start(self.state, phase, self.session_for("plan"))
            self.pg.complete(self.state, phase, session=self.session_for("plan"))
        check = self.check()
        self.assertTrue(check["verdict_allowed"])
        self.assertEqual(check["phase_ceiling"], "待核实")
        self.assertTrue(any("同会话放行" in reason for reason in check["reasons"]))
        items = [l for l in check["limitations"] if l["code"] == "same_session_approval"]
        self.assertTrue(items)
        self.assertTrue(items[0]["needs_human"], "重走跨会话放行需要人，remedy 须指向人工动作")


class CollectionRecordTests(PhaseGateTestBase):
    def clean(self, **overrides):
        record = {"id": "OBS-1", "tc_ref": "TC-1", "collection_state": "collected",
                  "action_sequence": [{"step": 1, "action": "click", "target": "提交"}],
                  "evidence_paths": ["evidence/step1.png"],
                  "observed": {"page": "提示已保存", "console": [], "network": []}}
        record.update(overrides)
        return record

    def test_clean_record_passes(self):
        self.assertEqual(self.pg.scan_collection_records([self.clean()]), [])

    def test_verdict_field_in_collection_is_rejected(self):
        violations = self.pg.scan_collection_records([self.clean(verdict="PASS")])
        self.assertEqual(violations[0]["code"], "judgement_key")

    def test_severity_and_classification_rejected(self):
        for key in ("severity", "classification", "conclusion"):
            with self.subTest(key=key):
                violations = self.pg.scan_collection_records([self.clean(**{key: "major"})])
                self.assertTrue(any(v["code"] == "judgement_key" for v in violations))

    def test_pass_fail_state_rejected(self):
        for value in ("PASS", "FAIL", "通过"):
            with self.subTest(value=value):
                violations = self.pg.scan_collection_records(
                    [self.clean(collection_state=value)])
                self.assertTrue(any(v["code"] == "judgement_state" for v in violations))

    def test_missing_state_and_missing_evidence(self):
        record = self.clean()
        record.pop("collection_state")
        self.assertTrue(any(v["code"] == "missing_state"
                            for v in self.pg.scan_collection_records([record])))
        record = self.clean(evidence_paths=[])
        self.assertTrue(any(v["code"] == "no_evidence"
                            for v in self.pg.scan_collection_records([record])))

    def test_blocked_record_needs_no_evidence(self):
        self.assertEqual(self.pg.scan_collection_records(
            [self.clean(collection_state="blocked", evidence_paths=[])]), [])

    def test_status_is_treated_like_verdict(self):
        """实测漏洞：verdict 被拦、status 不拦——想绕的绕得过去，老实写的被拦。"""
        for key in ("status", "result", "outcome", "assessment", "score"):
            with self.subTest(key=key):
                violations = self.pg.scan_collection_records([self.clean(**{key: "PASS"})])
                self.assertTrue(any(v["code"] == "judgement_key" for v in violations))

    def test_lead_channel_records_suspicion_without_verdict(self):
        record = self.clean(lead={"suspicion": "审批卡片按钮顺序可疑",
                                  "basis": "与同页其他弹窗的按钮顺序相反",
                                  "where": "审批卡片"})
        self.assertEqual(self.pg.scan_collection_records([record]), [])

    def test_lead_without_basis_is_rejected(self):
        record = self.clean(lead={"suspicion": "看着不对"})
        codes = [v["code"] for v in self.pg.scan_collection_records([record])]
        self.assertIn("lead_missing_basis", codes)

    def test_lead_cannot_smuggle_a_verdict(self):
        record = self.clean(lead={"suspicion": "x", "basis": "y", "verdict": "FAIL"})
        codes = [v["code"] for v in self.pg.scan_collection_records([record])]
        self.assertIn("lead_unknown_field", codes)


class CollectionClosureTests(PhaseGateTestBase):
    """v1.48.4：complete(execute) 消费采集记录——脏数据拒收尾、缺失/空清单压待核实。"""

    def _reach_execute(self):
        self.pg.start(self.state, "plan", "SESS-1")
        self.pg.complete(self.state, "plan", session="SESS-1")
        self.submit_phase("execute")
        self.pg.checkpoint(self.state, "plan", self.handoff_dir, session="SESS-1")
        self.resume("execute")
        self.pg.start(self.state, "execute", self.session_for("execute"))

    def _finish_execute(self, collection="__clean__"):
        """复刻 finish_phase("execute") 但 collection 可控（"__clean__"/None/路径）。"""
        session = self.session_for("execute")
        self.resume("execute")
        self.pg.start(self.state, "execute", session)
        coverage = {"verdict_ceiling": "通过", "gaps": [], "attack_density": 0.4}
        exploration = {"verdict_ceiling": "通过", "ready_pending": 0,
                       "missing_views": [], "failed_items": [],
                       "traversal_recorded": True,
                       "mandatory_attributes": {"security": "covered",
                                                "compatibility": "covered",
                                                "copy": "covered"},
                       "message": "夹具"}
        kwargs = {}
        if collection != "__clean__":
            kwargs["collection"] = collection
        else:
            kwargs["collection"] = self.clean_collection_file()
        self.pg.complete(self.state, "execute", session=session,
                         attack_coverage=coverage, exploration=exploration, **kwargs)
        self.pg.submit(self.state, "adjudicate", session=session,
                       model_class=self.pg.ROUTING["adjudicate"]["model_class"],
                       model_name=self.pg.ROUTING["adjudicate"]["model_hint"])
        self.pg.checkpoint(self.state, "execute", self.handoff_dir, session=session)

    def _close_all_after_execute(self, collection="__clean__"):
        self.pg.start(self.state, "plan", "SESS-1")
        self.pg.complete(self.state, "plan", session="SESS-1")
        self.submit_phase("execute")
        self.pg.checkpoint(self.state, "plan", self.handoff_dir, session="SESS-1")
        self._finish_execute(collection)
        self.resume("adjudicate")
        self.pg.start(self.state, "adjudicate", self.session_for("adjudicate"))
        self.pg.complete(self.state, "adjudicate", session=self.session_for("adjudicate"))
        return self.check()

    def _dirty_file(self):
        path = self.folder / "dirty.jsonl"
        path.write_text(json.dumps({"id": "OBS-1", "collection_state": "collected",
                                    "verdict": "PASS",
                                    "evidence_paths": ["a.png"]}), encoding="utf-8")
        return str(path)

    def test_dirty_collection_blocks_complete(self):
        """判定字段混进采集 = 采集事实不成立：必须先修正，不能带病收尾。"""
        self._reach_execute()
        with self.assertRaises(ValueError) as ctx:
            self.pg.complete(self.state, "execute", session=self.session_for("execute"),
                             collection=self._dirty_file())
        self.assertIn("结构违规", str(ctx.exception))
        self.assertEqual(self.state["phases"]["execute"]["status"], "in_progress")

    def test_clean_collection_recorded_as_provided(self):
        self._reach_execute()
        self.pg.complete(self.state, "execute", session=self.session_for("execute"),
                         collection=self.clean_collection_file())
        self.assertEqual(self.state["phases"]["execute"]["collection"]["state"],
                         "provided")

    def test_missing_collection_closes_and_presses_ceiling(self):
        """收尾不交采集 = 受限交付（B1 downgrade 档）：可出具结论，压待核实并给补正入口。"""
        check = self._close_all_after_execute(collection=None)
        self.assertEqual(self.state["phases"]["execute"]["collection"]["state"],
                         "not_provided")
        self.assertTrue(check["verdict_allowed"])
        self.assertEqual(check["phase_ceiling"], "待核实")
        self.assertTrue(any("未提供采集记录" in r for r in check["reasons"]))
        items = [l for l in check["limitations"] if l["code"] == "collection_not_provided"]
        self.assertTrue(items)
        self.assertIn("revise execute --collection", items[0]["remedy"])

    def test_empty_collection_file_not_disguised_as_provided(self):
        """空清单登记 empty：拿 0 条记录冒充"已校验通过"必须可区分。"""
        empty = self.folder / "empty.jsonl"
        empty.write_text("", encoding="utf-8")
        check = self._close_all_after_execute(collection=str(empty))
        entry = self.state["phases"]["execute"]["collection"]
        self.assertEqual(entry["state"], "empty")
        self.assertEqual(entry["records"], 0)
        self.assertTrue(check["verdict_allowed"])
        self.assertEqual(check["phase_ceiling"], "待核实")
        self.assertTrue(any("采集记录为空" in r for r in check["reasons"]))
        self.assertTrue(any(l["code"] == "collection_empty"
                            for l in check["limitations"]))

    def test_legacy_state_without_collection_field_disclosed(self):
        """旧批次状态文件没有 collection 字段：不拒绝，但按原契约披露留痕。"""
        check = self._close_all_after_execute(collection=self.clean_collection_file())
        del self.state["phases"]["execute"]["collection"]
        legacy = self.check()
        self.assertTrue(any("旧批次状态文件" in r for r in legacy["reasons"]))
        self.assertNotIn("旧批次状态文件", " ".join(check["reasons"]))

    def test_collection_on_other_phase_rejected(self):
        """--collection 只对 execute 有意义：传给 plan 收尾必须显式报错而非静默丢弃。"""
        self.pg.start(self.state, "plan", "SESS-1")
        with self.assertRaises(ValueError) as ctx:
            self.pg.complete(self.state, "plan", session="SESS-1",
                             collection=self.clean_collection_file())
        self.assertIn("--collection", str(ctx.exception))

    def test_revise_collection_unblocks_verdict(self):
        """漏交采集是死路：complete 只能调一次，必须有 revise --collection 补正入口。

        复审实测坐实：旧版 revise 只认 attack_coverage/exploration，execute 收尾漏交采集后
        check_verdict 因 collection!=provided 无条件 allowed=False，永久无法补正。
        """
        self._close_all_after_execute(collection=None)
        blocked = self.check()
        self.assertTrue(blocked["verdict_allowed"], "B1 后漏交采集允许受限交付")
        self.assertEqual(blocked["phase_ceiling"], "待核实")
        self.assertTrue(any("未提供采集记录" in r for r in blocked["reasons"]))
        self.assertTrue(any(l["code"] == "collection_not_provided"
                            for l in blocked["limitations"]))
        adjudicate_session = self.session_for("adjudicate")
        self.pg.revise(self.state, "execute",
                       collection=self.clean_collection_file(),
                       note="补齐上一会话漏交的采集记录", session=adjudicate_session)
        self.assertEqual(self.state["phases"]["execute"]["collection"]["state"], "provided")
        unblocked = self.check()
        self.assertNotIn("未提供采集记录", " ".join(unblocked["reasons"]))

    def test_revise_collection_rejects_dirty_records(self):
        """补正不是后门：脏采集照样拒收。"""
        self._close_all_after_execute(collection=None)
        with self.assertRaises(ValueError) as ctx:
            self.pg.revise(self.state, "execute", collection=self._dirty_file(),
                           note="补正", session=self.session_for("adjudicate"))
        self.assertIn("结构违规", str(ctx.exception))

    def test_revise_collection_empty_not_disguised(self):
        """空文件补正同样登记 empty，不得洗成 provided。"""
        empty = self.folder / "empty2.jsonl"
        empty.write_text("", encoding="utf-8")
        self._close_all_after_execute(collection=None)
        self.pg.revise(self.state, "execute", collection=str(empty),
                       note="补正空采集", session=self.session_for("adjudicate"))
        self.assertEqual(self.state["phases"]["execute"]["collection"]["state"], "empty")
        self.assertTrue(any("采集记录为空" in r for r in self.check()["reasons"]))

    def test_revise_collection_requires_note(self):
        """B3/F-07：--collection 补正无需人工放行即可解除缺口，必须 --note 留痕。

        note 校验先于记录校验（缺 note 连脏记录都进不了补正流程）；
        脏记录 + 带 note 的拒绝理由属结构违规，由 test_revise_collection_rejects_dirty_records 覆盖。
        """
        self._close_all_after_execute(collection=None)
        with self.assertRaises(ValueError) as ctx:
            self.pg.revise(self.state, "execute",
                           collection=self.clean_collection_file(),
                           session=self.session_for("adjudicate"))
        self.assertIn("--note", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx2:
            self.pg.revise(self.state, "execute",
                           collection=self._dirty_file(), note=None,
                           session=self.session_for("adjudicate"))
        self.assertIn("--note", str(ctx2.exception),
                      "note 缺失的拦截先于记录内容校验，一视同仁")

    def test_revise_collection_records_auto_basis(self):
        """B3：collection 补正事件记 auto=true + basis=note 原文，供人工核对识别自动解除。"""
        self._close_all_after_execute(collection=None)
        note = "补齐上一会话漏交的采集记录（依据：交接包 pending_inputs 点名）"
        self.pg.revise(self.state, "execute", collection=self.clean_collection_file(),
                       note=note, session=self.session_for("adjudicate"))
        events = [h for h in self.state["history"] if h.get("action") == "revise"]
        self.assertEqual(len(events), 1)
        detail = events[0]["detail"]
        self.assertTrue(detail["auto"])
        self.assertEqual(detail["basis"], note)
        self.assertIn("collection", detail["fields"])

    def test_handoff_lists_collection_gap_as_pending_input(self):
        """交接包必须点名采集缺口，否则下一会话开工即撞墙。"""
        self.pg.start(self.state, "plan", "SESS-1")
        self.pg.complete(self.state, "plan", session="SESS-1")
        self.submit_phase("execute")
        self.pg.checkpoint(self.state, "plan", self.handoff_dir, session="SESS-1")
        self._finish_execute(collection=None)
        handoff = json.loads(
            (self.handoff_dir / "handoff-execute.json").read_text(encoding="utf-8"))
        self.assertTrue(any("采集" in item for item in handoff["pending_inputs"]))

    def test_handoff_omits_collection_gap_when_provided(self):
        self.pg.start(self.state, "plan", "SESS-1")
        self.pg.complete(self.state, "plan", session="SESS-1")
        self.submit_phase("execute")
        self.pg.checkpoint(self.state, "plan", self.handoff_dir, session="SESS-1")
        self._finish_execute(collection=self.clean_collection_file())
        handoff = json.loads(
            (self.handoff_dir / "handoff-execute.json").read_text(encoding="utf-8"))
        self.assertFalse(any("采集" in item for item in handoff["pending_inputs"]))

    def test_pending_inputs_cover_legacy_state_without_collection_key(self):
        """B2/F-06：旧批次状态文件没有 collection 键时，交接包同样点名并给 revise 补正命令。

        v1.48.6 的 `is not None` 判断让旧状态文件逃过点名——下一会话开工即撞墙。
        B2 改为「字段缺失或 state != provided」一视同仁；remedy 取 REASON_POLICY 单一来源。
        """
        self.pg.start(self.state, "plan", "SESS-1")
        self.pg.complete(self.state, "plan", session="SESS-1")
        self.submit_phase("execute")
        self.pg.checkpoint(self.state, "plan", self.handoff_dir, session="SESS-1")
        self._finish_execute(collection=self.clean_collection_file())
        del self.state["phases"]["execute"]["collection"]
        pending = self.pg._pending_verdict_inputs(self.state)
        self.assertTrue(any("采集" in item for item in pending), pending)
        self.assertTrue(any("revise execute --collection" in item for item in pending))
        # 补正后采集条目消失（assumptions/regression 本场景未登记，按既有语义保留）
        self.pg.revise(self.state, "execute", collection=self.clean_collection_file(),
                       note="补齐旧批次缺口", session=self.session_for("execute"))
        pending = self.pg._pending_verdict_inputs(self.state)
        self.assertFalse(any("采集" in item for item in pending), pending)


class AdjudicationHonestyTests(PhaseGateTestBase):
    def valid(self, **overrides):
        finding = {"id": "BUG-1", "classification": "product_defect",
                   "evidence_refs": ["evidence/step1.png"],
                   "basis": ["observed_ui", "observed_network"],
                   "source": "observed", "evidence_grade": "E1",
                   "page_observed": True}
        finding.update(overrides)
        return finding

    def test_valid_finding_passes(self):
        self.assertEqual(self.pg.check_adjudications([self.valid()]), [])

    def test_missing_evidence_refs_rejected(self):
        violations = self.pg.check_adjudications([self.valid(evidence_refs=[])])
        self.assertTrue(any(v["code"] == "no_evidence_refs" for v in violations))

    def test_inferred_basis_rejected(self):
        violations = self.pg.check_adjudications(
            [self.valid(basis=["assumed"], source="assumed")])
        codes = {v["code"] for v in violations}
        self.assertIn("no_observed_basis", codes)
        self.assertIn("forbidden_source", codes)

    def test_script_completed_behavior_rejected(self):
        violations = self.pg.check_adjudications(
            [self.valid(page_behavior_completed_by_script=True)])
        self.assertTrue(any(v["code"] == "script_completed_behavior" for v in violations))

    def test_defect_requires_e1_and_page_observed(self):
        violations = self.pg.check_adjudications(
            [self.valid(evidence_grade="E2", page_observed=False)])
        codes = {v["code"] for v in violations}
        self.assertIn("defect_grade_too_low", codes)
        self.assertIn("defect_not_observed", codes)

    def test_environment_failure_may_be_simulated(self):
        self.assertEqual(self.pg.check_adjudications(
            [self.valid(classification="environment_failure", evidence_grade="E2",
                        page_observed=False)]), [])

    def test_bad_classification_rejected(self):
        violations = self.pg.check_adjudications([self.valid(classification="looks_wrong")])
        self.assertTrue(any(v["code"] == "bad_classification" for v in violations))


class PhaseGateCliTests(PhaseGateTestBase):
    def cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              capture_output=True, text=True, encoding="utf-8")

    def test_cli_cross_session_flow(self):
        state = self.folder / "cli-state.json"
        handoff = self.folder / "cli-handoff"
        handoff.mkdir()
        self.assertEqual(self.cli("init", str(state), "--task", "TASK-CLI",
                                  "--session", "SESS-1",
                                  "--model-class", "strong-reasoning").returncode, 0)
        blocked = self.cli("check-verdict", str(state))
        self.assertEqual(blocked.returncode, 1)
        self.assertEqual(json.loads(blocked.stdout)["phase_ceiling"], "待核实")

        # 规划阶段由 init 放行；execute 未交接前不得开始
        self.assertEqual(self.cli("start", str(state), "--phase", "plan",
                                  "--session", "SESS-1").returncode, 0)
        self.assertEqual(self.cli("complete", str(state), "--phase", "plan",
                                  "--session", "SESS-1").returncode, 0)
        cmd = ["submit", str(state), "--phase", "execute", "--session", "SESS-1"]
        for artifact in self.artifacts:
            cmd += ["--artifact", artifact]
        self.assertEqual(self.cli(*cmd).returncode, 0)
        premature = self.cli("start", str(state), "--phase", "execute", "--session", "SESS-1")
        self.assertEqual(premature.returncode, 2, "未交接不得开始")

        self.assertEqual(self.cli("checkpoint", str(state), "--phase", "plan",
                                  "--handoff-dir", str(handoff), "--session", "SESS-1").returncode, 0)
        token = json.loads((handoff / "handoff-plan.json").read_text(encoding="utf-8"))["token"]
        # 同会话续跑必须被拒
        same = self.cli("resume", str(state), "--phase", "execute", "--session", "SESS-1",
                        "--user-confirm", f"确认码 {token}", "--model-class", "fast")
        self.assertEqual(same.returncode, 2)
        ok = self.cli("resume", str(state), "--phase", "execute", "--session", "SESS-2",
                      "--user-confirm", f"确认码 {token}", "--model-class", "fast")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        status = json.loads(self.cli("status", str(state)).stdout)
        self.assertEqual(status["phases"]["execute"]["status"], "approved")
        self.assertEqual(status["active_session"], "SESS-2")

    def test_cli_scan_collection_rc(self):
        bad = self.folder / "records.jsonl"
        bad.write_text(json.dumps({"id": "OBS-1", "collection_state": "collected",
                                   "verdict": "PASS", "evidence_paths": ["a.png"]}) + "\n",
                       encoding="utf-8")
        run = self.cli("scan-collection", str(bad))
        self.assertEqual(run.returncode, 1)
        self.assertFalse(json.loads(run.stdout)["ok"])

    def test_cli_check_adjudication_rc(self):
        bad = self.folder / "findings.json"
        bad.write_text(json.dumps([{"id": "BUG-1", "classification": "product_defect",
                                    "basis": ["assumed"], "source": "assumed"}]), encoding="utf-8")
        run = self.cli("check-adjudication", str(bad))
        self.assertEqual(run.returncode, 1)


class TamperResistanceTests(PhaseGateTestBase):
    """自攻验证：自我放行、手改状态文件、伪造会话都必须被拦。"""

    def test_agent_cannot_self_approve(self):
        for ident in ("agent", "assistant", "system", "砺衡"):
            with self.subTest(identity=ident), self.assertRaises(ValueError):
                self.pg.approve(self.state, "plan", ident, "我自己同意")

    def test_agent_cannot_approve_amendment(self):
        self.pg.start(self.state, "plan", "SESS-1")
        self.pg.request_amendment(self.state, "plan", "agent", "补充风险面", session="SESS-1")
        with self.assertRaises(ValueError):
            self.pg.approve_amendment(self.state, "AMD-1", "agent", "自行批准")

    def test_hand_edited_phase_status_is_detected(self):
        self.pg.save(self.state_path, self.state)
        raw = self.state_path.read_text(encoding="utf-8")
        tampered = raw.replace('"status": "pending"', '"status": "done"', 1)
        self.assertNotEqual(raw, tampered)
        self.state_path.write_text(tampered, encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            self.pg.load(self.state_path)
        self.assertIn("不可信", str(ctx.exception))

    def test_hand_edited_history_entry_is_detected(self):
        self.pg.save(self.state_path, self.state)
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        data["history"][0]["action"] = "resume"
        self.state_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.pg.load(self.state_path)

    def test_hand_edited_active_session_is_detected(self):
        self.pg.save(self.state_path, self.state)
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        data["active_session"] = "SESS-9"
        self.state_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.pg.load(self.state_path)

    def test_untampered_state_round_trips(self):
        self.finish_all()
        self.pg.save(self.state_path, self.state)
        reloaded = self.pg.load(self.state_path)
        self.assertTrue(self.pg.check_verdict(reloaded, assumptions={"reviewed": True, "items": []}, regression={"changed_areas": [], "candidates": [], "no_impact_reason": "重载校验"})["verdict_allowed"])


class PhaseLedgerIntegrationTests(PhaseGateTestBase):
    """阶段闸门必须真的压住台账结论上限——只造开关不接线等于没做。"""

    def setUp(self):
        super().setUp()
        spec = importlib.util.spec_from_file_location("execution_ledger",
                                                      ROOT / "scripts" / "execution_ledger.py")
        self.ledger = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.ledger)
        self.design = json.loads((ROOT / "examples" / "cases-example.json").read_text(encoding="utf-8"))
        self.context = {"target_url": "https://example.invalid/test", "environment": "test",
                        "application_version": "build-1", "feature_flags": {}, "role_scope": ["editor"],
                        "dependency_mode": "sandbox"}
        self.evidence = self.folder / "observations.txt"
        self.evidence.write_text("Teaching fixture, not actual Web evidence", encoding="utf-8")
        self.adversarial = {"adversarial_enabled": True, "verdict_ceiling": "通过", "reasons": []}

    def run_all_cases(self, workspace):
        for index, case in enumerate(self.design["testcases"]):
            attempt = f"ATT-{index}"
            self.ledger.begin(workspace, self.design, self.context, attempt, case["id"], "动作")
            checks = [{"check": item["check"], "actual": "观察值", "matched": True}
                      for item in case["assertions"]]
            self.ledger.finish(workspace, self.design, self.context, attempt, "PASS",
                               "观察结果", [str(self.evidence)], checks, "none", [], "无真实远端数据")

    def test_without_phase_state_baseline_shape_unchanged(self):
        workspace = self.folder / "no-phase"
        baseline = self.ledger.initialize(workspace, self.design, self.context, "R1",
                                          [], None, None, self.adversarial, None)
        self.assertNotIn("phase_ceiling", baseline["gate_precondition"])

    def test_incomplete_phase_state_caps_verdict_to_pending(self):
        self.pg.start(self.state, "plan", "SESS-1")
        workspace = self.folder / "incomplete"
        baseline = self.ledger.initialize(workspace, self.design, self.context, "R1",
                                          [], None, None, self.adversarial, None, self.state)
        self.assertEqual(baseline["gate_precondition"]["phase_state"], "incomplete")
        self.assertEqual(baseline["gate_precondition"]["phase_ceiling"], "待核实")
        self.run_all_cases(workspace)
        summary = self.ledger.summarize(workspace, self.design, self.context, [])
        self.assertEqual(summary["counts"]["PASS"], len(self.design["testcases"]))
        self.assertEqual(summary["verdict_ceiling"], "待核实",
                         "阶段未走完时即使全 PASS 也不得给通过")

    def test_complete_phase_state_allows_pass(self):
        self.finish_all()
        workspace = self.folder / "complete"
        baseline = self.ledger.initialize(workspace, self.design, self.context, "R1",
                                          [], None, None, self.adversarial, None, self.state)
        self.assertEqual(baseline["gate_precondition"]["phase_state"], "complete")
        self.assertIsNone(baseline["gate_precondition"]["phase_ceiling"])
        self.run_all_cases(workspace)
        summary = self.ledger.summarize(workspace, self.design, self.context, [])
        self.assertEqual(summary["verdict_ceiling"], "通过")

    def test_same_session_approval_caps_ledger_verdict(self):
        """同会话放行虽走完流程，台账侧也必须压住结论。"""
        for phase in self.pg.PHASES:
            if phase != "plan":
                self.submit_phase(phase)
                self.pg.approve(self.state, phase, "user", "同会话同意")
            self.pg.start(self.state, phase, "SESS-1")
            self.pg.complete(self.state, phase, session="SESS-1")
        workspace = self.folder / "same-session"
        baseline = self.ledger.initialize(workspace, self.design, self.context, "R1",
                                          [], None, None, self.adversarial, None, self.state)
        self.assertEqual(baseline["gate_precondition"]["phase_ceiling"], "待核实")
        self.assertTrue(any("同会话" in reason
                            for reason in baseline["gate_precondition"]["phase_reasons"]))
        self.run_all_cases(workspace)
        self.assertEqual(self.ledger.summarize(workspace, self.design, self.context, [])["verdict_ceiling"],
                         "待核实")

    def test_unacknowledged_routing_mismatch_caps_verdict(self):
        self.finish_all(model_classes={"adjudicate": self.pg.ROUTING["execute"]["model_class"]})
        workspace = self.folder / "routing"
        baseline = self.ledger.initialize(workspace, self.design, self.context, "R1",
                                          [], None, None, self.adversarial, None, self.state)
        reasons = baseline["gate_precondition"]["phase_reasons"]
        self.assertTrue(any("路由误配" in reason for reason in reasons), reasons)
        self.run_all_cases(workspace)
        self.assertEqual(self.ledger.summarize(workspace, self.design, self.context, [])["verdict_ceiling"],
                         "待核实")

    def test_cli_init_accepts_phase_state(self):
        state_file = self.folder / "cli-phase.json"
        self.pg.save(state_file, self.state)
        design_file = self.folder / "cases.json"
        context_file = self.folder / "context.json"
        design_file.write_text(json.dumps(self.design, ensure_ascii=False), encoding="utf-8")
        context_file.write_text(json.dumps(self.context, ensure_ascii=False), encoding="utf-8")
        workspace = self.folder / "cli-ledger"
        run = subprocess.run([sys.executable, str(ROOT / "scripts" / "execution_ledger.py"),
                              "init", str(workspace), str(design_file), str(context_file),
                              "R-CLI", "--phase-state", str(state_file)],
                             capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(run.returncode, 0, run.stderr)
        baseline = json.loads((workspace / "baseline.json").read_text(encoding="utf-8"))
        self.assertEqual(baseline["gate_precondition"]["phase_state"], "incomplete")


class HandoffArtifactValidation(PhaseGateTestBase):
    """交接产物必须是真实文件：状态流转不能代替评审/用例/对抗 plan。"""

    def complete_plan(self):
        self.pg.start(self.state, "plan", "SESS-1")
        self.pg.complete(self.state, "plan", session="SESS-1")

    def test_nonexistent_artifact_rejected(self):
        self.complete_plan()
        ghost = self.folder / "ghost"
        with self.assertRaises(ValueError) as ctx:
            self.pg.submit(self.state, "execute", session="SESS-1",
                           artifacts=[str(ghost / "review-result.json"),
                                      str(ghost / "cases-demo.json"),
                                      str(ghost / "adversarial-plan.json")])
        self.assertIn("不存在", str(ctx.exception))

    def test_relative_artifact_rejected(self):
        self.complete_plan()
        with self.assertRaises(ValueError):
            self.pg.submit(self.state, "execute", session="SESS-1",
                           artifacts=["review-result.json", "cases-demo.json",
                                      "adversarial-plan.json"])

    def test_plan_handoff_requires_three_artifacts(self):
        self.complete_plan()
        for artifacts in (None, [], [self.artifacts[0]],
                          self.artifacts[:2], [self.artifacts[0], self.artifacts[2]]):
            with self.subTest(count=0 if not artifacts else len(artifacts)):
                with self.assertRaises(ValueError):
                    self.pg.submit(self.state, "execute", session="SESS-1",
                                   artifacts=artifacts)

    def test_complete_plan_with_three_real_artifacts_passes(self):
        self.complete_plan()
        self.pg.submit(self.state, "execute", session="SESS-1", artifacts=self.artifacts)
        self.assertEqual(self.state["phases"]["execute"]["status"], "awaiting_approval")
        self.assertEqual(len(self.state["phases"]["execute"]["artifacts"]), 3)


class AssumptionAndRegressionGate(PhaseGateTestBase):
    """风险假设必须逐条证伪，回归影响面必须被评估——两者缺一即压上限。

    B1（phase-verdict-check/2）：以下缺口均属 downgrade 档——允许出具结论
    （verdict_allowed=True），但 phase_ceiling 压「待核实」，limitations[].code
    逐条可定位且必须带 remedy 补正入口。
    """

    def finished(self):
        self.finish_all()
        return self.check()

    def assert_downgraded(self, check, code):
        self.assertTrue(check["verdict_allowed"])
        self.assertEqual(check["phase_ceiling"], "待核实")
        items = [l for l in check["limitations"] if l["code"] == code]
        self.assertTrue(items, check["limitations"])
        self.assertTrue(all(l["remedy"] for l in items))

    def test_missing_assumptions_blocks(self):
        self.finish_all()
        check = self.pg.check_verdict(
            self.state, assumptions=None,
            regression={"changed_areas": [], "candidates": [], "no_impact_reason": "夹具"})
        self.assert_downgraded(check, "assumptions_missing")
        self.assertTrue(any("风险假设" in reason for reason in check["reasons"]))

    def test_empty_assumptions_need_reviewed_flag(self):
        self.finish_all()
        check = self.pg.check_verdict(
            self.state, assumptions={"items": []},
            regression={"changed_areas": [], "candidates": [], "no_impact_reason": "夹具"})
        self.assert_downgraded(check, "assumptions_not_reviewed")
        self.assertTrue(any("reviewed" in reason for reason in check["reasons"]))

    def test_open_assumption_blocks(self):
        self.finish_all()
        check = self.pg.check_verdict(
            self.state,
            assumptions={"reviewed": True, "items": [{
                "id": "ASM-01", "assumption": "发布后立即可见",
                "basis": "产品口头说明", "falsify_action": "发布后刷新列表核对",
                "result": "open"}]},
            regression={"changed_areas": [], "candidates": [], "no_impact_reason": "夹具"})
        self.assert_downgraded(check, "assumptions_gap")
        self.assertTrue(any("ASM-01" in reason for reason in check["reasons"]))

    def test_missing_regression_blocks(self):
        self.finish_all()
        check = self.pg.check_verdict(
            self.state, assumptions={"reviewed": True, "items": []}, regression=None)
        self.assert_downgraded(check, "regression_missing")
        self.assertTrue(any("回归影响面" in reason for reason in check["reasons"]))

    def test_changed_area_without_candidates_blocks(self):
        self.finish_all()
        check = self.pg.check_verdict(
            self.state, assumptions={"reviewed": True, "items": []},
            regression={"changed_areas": ["主页整页替换"], "candidates": []})
        self.assert_downgraded(check, "regression_gap")
        self.assertTrue(any("回归候选" in reason for reason in check["reasons"]))

    def test_candidate_without_basis_blocks(self):
        self.finish_all()
        check = self.pg.check_verdict(
            self.state, assumptions={"reviewed": True, "items": []},
            regression={"changed_areas": ["主页"], "candidates": [{"id": "RG-01"}]})
        self.assert_downgraded(check, "regression_gap")
        self.assertTrue(any("basis" in reason for reason in check["reasons"]))

    def test_full_declaration_allows_verdict(self):
        self.finish_all()
        check = self.pg.check_verdict(
            self.state,
            assumptions={"reviewed": True, "items": [{
                "id": "ASM-01", "assumption": "发布后立即可见",
                "basis": "产品说明", "falsify_action": "发布后刷新列表核对",
                "result": "refuted"}]},
            regression={"changed_areas": ["主页整页替换"],
                        "candidates": [{"id": "RG-01", "basis": "旧会话列表渲染依赖主页配置"}]})
        self.assertTrue(check["verdict_allowed"], check["reasons"])
        self.assertEqual(check["schema"], "phase-verdict-check/2")
        self.assertIsNone(check["phase_ceiling"])
        self.assertEqual(check["blocking_reasons"], [])
        self.assertEqual(check["limitations"], [])


class ExplorationGateWiringTests(PhaseGateTestBase):
    """DEV-1119-08：探索清单 gate 曾只写在文档里，phase_gate 里 grep 不到 exploration。

    实测代价：一轮完整验收从未生成 exploration.json，complete/submit/checkpoint
    三步全过零告警，missing_views / mandatory_attributes / EXP-TRAV- 从未被核对。
    """

    GATE_OK = {"verdict_ceiling": "通过", "ready_pending": 0, "missing_views": [],
               "failed_items": [], "traversal_recorded": True,
               "mandatory_attributes": {"security": "covered", "compatibility": "covered",
                                        "copy": "covered"},
               "message": "无未消费候选"}

    def _run_to_execute_done(self, exploration):
        self.finish_phase("plan")
        self.resume("execute")
        self.pg.start(self.state, "execute", self.session_for("execute"))
        self.pg.complete(self.state, "execute", session=self.session_for("execute"),
                         collection=self.clean_collection_file(),
                         attack_coverage={"verdict_ceiling": "通过", "gaps": []},
                         exploration=exploration)

    def test_missing_exploration_blocks_verdict(self):
        """未交探索清单 → 结论上限压待核实（缺失与「做了且通过」必须可区分）。"""
        self._run_to_execute_done(None)
        entry = self.state["phases"]["execute"]["exploration"]
        self.assertEqual(entry["state"], "missing")
        self.assertEqual(entry["ceiling"], "待核实")
        check = self.pg.check_verdict(self.state)
        self.assertTrue(any("探索清单" in r for r in check["reasons"]), check["reasons"])

    def test_provided_gate_clears_reason(self):
        """交出达标的 gate → 不再因探索清单压上限。"""
        self._run_to_execute_done(self.GATE_OK)
        entry = self.state["phases"]["execute"]["exploration"]
        self.assertEqual(entry["state"], "provided")
        self.assertEqual(entry["ceiling"], "通过")
        check = self.pg.check_verdict(self.state)
        self.assertFalse([r for r in check["reasons"] if "探索清单" in r], check["reasons"])

    def test_gate_gaps_are_surfaced(self):
        """gate 不达标时，具体缺口要原样透出，不能只说一句「不达标」。"""
        gate = {"verdict_ceiling": "待核实", "ready_pending": 3,
                "missing_views": ["usability"], "thin_views": ["performance"],
                "failed_items": ["EXP-1"], "traversal_recorded": False,
                "mandatory_attributes": {"security": "undeclared"},
                "message": "仍有3项可执行未执行"}
        self._run_to_execute_done(gate)
        entry = self.state["phases"]["execute"]["exploration"]
        joined = "；".join(entry["gaps"])
        # failed_items 透出条目 ID 而非条数：条数只说"有几条"，ID 才能顺着清单回查是哪条
        for token in ("ready_pending=3", "missing_views=usability", "thin_views=performance",
                      "failed_items=EXP-1", "EXP-TRAV-", "mandatory_attributes 未闭合=security"):
            self.assertIn(token, joined)

    def test_bad_ceiling_rejected(self):
        with self.assertRaises(ValueError):
            self._run_to_execute_done({"verdict_ceiling": "大概没问题"})


class CompleteArtifactTests(PhaseGateTestBase):
    """DEV-1119-07：complete 不接受 --artifact，plan.artifacts 恒空，交接包输出
    「（未登记产物路径）」——交接包让下一会话看不到上一阶段交了什么。"""

    def test_artifacts_recorded_and_reach_handoff(self):
        self.finish_phase("plan")
        # finish_phase 已 complete 过 plan；这里验证 complete 阶段能把产物写进 entry
        self.resume("execute")
        self.pg.start(self.state, "execute", self.session_for("execute"))
        extra = [self.folder / "a.json", self.folder / "b.jsonl"]
        for path in extra:
            path.write_text("{}", encoding="utf-8")
        self.pg.complete(self.state, "execute", session=self.session_for("execute"),
                         collection=self.clean_collection_file(),
                         attack_coverage={"verdict_ceiling": "通过", "gaps": []},
                         exploration=ExplorationGateWiringTests.GATE_OK,
                         artifacts=[str(p) for p in extra])
        arts = self.state["phases"]["execute"]["artifacts"]
        # complete 是**追加**：submit 阶段登记的产物不应被覆盖掉
        for path in extra:
            self.assertIn(str(path), arts)
        self.assertGreaterEqual(len(arts), 2)

    def test_complete_rejects_placeholder_artifact(self):
        """收尾产物与交接产物同一强度：只认真实存在的绝对路径。

        允许占位路径会让交接包「本阶段产物」列出一堆不存在的文件，比空着更误导。
        """
        self.finish_phase("plan")
        self.resume("execute")
        self.pg.start(self.state, "execute", self.session_for("execute"))
        for bad in (str(self.folder / "ghost.json"), "relative.json"):
            with self.subTest(artifact=bad):
                with self.assertRaises(ValueError):
                    self.pg.complete(self.state, "execute", session=self.session_for("execute"),
                         collection=self.clean_collection_file(),
                                     attack_coverage={"verdict_ceiling": "通过", "gaps": []},
                                     exploration=ExplorationGateWiringTests.GATE_OK,
                                     artifacts=[bad])

    def test_no_artifact_keeps_previous(self):
        """不传 --artifact 时不把已有登记清空（向后兼容）。"""
        self.finish_phase("plan")
        self.resume("execute")
        self.pg.start(self.state, "execute", self.session_for("execute"))
        before = list(self.state["phases"]["execute"]["artifacts"])
        self.pg.complete(self.state, "execute", session=self.session_for("execute"),
                         collection=self.clean_collection_file(),
                         attack_coverage={"verdict_ceiling": "通过", "gaps": []},
                         exploration=ExplorationGateWiringTests.GATE_OK)
        self.assertEqual(self.state["phases"]["execute"]["artifacts"], before)


class ReviseTests(PhaseGateTestBase):
    """DEV-1119-13：execute 在 S2 冻结快照，S3 补齐后重跑得「通过」却无法回写——
    check-verdict 永远读过期快照，报「不达标」而实际已达标。"""

    def _execute_done_with_gap(self):
        self.finish_phase("plan")
        self.resume("execute")
        self.pg.start(self.state, "execute", self.session_for("execute"))
        self.pg.complete(
            self.state, "execute", session=self.session_for("execute"),
            attack_coverage={"verdict_ceiling": "待核实", "gaps": [{"code": "x"}]},
            exploration=ExplorationGateWiringTests.GATE_OK)

    def test_revise_updates_ceiling_without_changing_status(self):
        self._execute_done_with_gap()
        self.assertEqual(self.state["phases"]["execute"]["status"], "done")
        self.pg.revise(self.state, "execute",
                       attack_coverage={"verdict_ceiling": "通过", "gaps": []},
                       note="S3 补齐后回写", session=self.session_for("execute"),
                       allow_relax=True)   # 待核实→通过属解锁验收，须显式放行
        entry = self.state["phases"]["execute"]
        self.assertEqual(entry["status"], "done")                 # 状态不动
        self.assertEqual(entry["attack_coverage"]["ceiling"], "通过")
        self.assertTrue(entry["attack_coverage"]["revised"])       # 留痕
        self.assertEqual(self.state["history"][-1]["action"], "revise")
        # 迁移本身也要留痕，事后能查到"谁把待核实改成了通过"
        self.assertEqual(
            self.state["history"][-1]["detail"]["transitions"]["attack_coverage"],
            "待核实 -> 通过")

    def test_revise_refuses_to_unlock_pass_without_allow_relax(self):
        """补正不得自行把未通过结论改成通过——那是给门禁开后门。"""
        self._execute_done_with_gap()
        with self.assertRaises(ValueError) as ctx:
            self.pg.revise(self.state, "execute",
                           attack_coverage={"verdict_ceiling": "通过", "gaps": []},
                           note="悄悄变绿", session=self.session_for("execute"))
        self.assertIn("升级为「通过」", str(ctx.exception))
        self.assertEqual(self.state["phases"]["execute"]["attack_coverage"]["ceiling"], "待核实")

    def test_revise_allows_tightening_and_sideways(self):
        """收紧（待核实→不通过）与平移（不通过→待核实）都是重跑门禁的正常产物，不该被拦。"""
        self._execute_done_with_gap()
        self.pg.revise(self.state, "execute",
                       attack_coverage={"verdict_ceiling": "不通过", "gaps": [{"code": "y"}]},
                       note="复测仍失败", session=self.session_for("execute"))
        self.assertEqual(self.state["phases"]["execute"]["attack_coverage"]["ceiling"], "不通过")
        self.pg.revise(self.state, "execute",
                       attack_coverage={"verdict_ceiling": "待核实", "gaps": []},
                       note="缺陷归因到环境", session=self.session_for("execute"))
        self.assertEqual(self.state["phases"]["execute"]["attack_coverage"]["ceiling"], "待核实")

    def test_revise_rejects_non_execute_phase(self):
        """check_verdict 只读 execute：写别的阶段是门禁看不见的死数据。"""
        self.finish_phase("plan")
        with self.assertRaises(ValueError) as ctx:
            self.pg.revise(self.state, "plan",
                           attack_coverage={"verdict_ceiling": "待核实", "gaps": []},
                           session=self.session_for("plan"))
        self.assertIn("只支持 execute", str(ctx.exception))
        self.assertNotIn("attack_coverage", self.state["phases"]["plan"])

    def test_revise_requires_done_phase(self):
        self.finish_phase("plan")
        with self.assertRaises(ValueError):
            self.pg.revise(self.state, "execute", attack_coverage={"verdict_ceiling": "通过"},
                           session=self.session_for("plan"))

    def test_revise_requires_at_least_one_field(self):
        self._execute_done_with_gap()
        with self.assertRaises(ValueError):
            self.pg.revise(self.state, "execute", session=self.session_for("execute"))


class ContradictoryCeilingTests(PhaseGateTestBase):
    """上游自相矛盾（ceiling=通过 却带非空 gaps）时按缺口从严。

    「通过」只是一个词，gaps 才是机械可核对的东西。若信任那个词，缺口就会被一个
    字符串抹掉——这正是本装置反复要防的「用自述代替证据」。
    """

    def test_attack_coverage_downgrades_on_contradiction(self):
        entry = self.pg._attack_check({"verdict_ceiling": "通过",
                                       "gaps": [{"code": "surface_without_hypothesis"}],
                                       "attack_density": 0.1})
        self.assertEqual(entry["ceiling"], "待核实")
        self.assertTrue(entry["reasons"], "降级后必须给出可核对理由")

    def test_exploration_downgrades_on_contradiction(self):
        entry = self.pg._exploration_check({
            "verdict_ceiling": "通过", "ready_pending": 0, "missing_views": [],
            "failed_items": [], "traversal_recorded": True,
            "mandatory_attributes": {"security": "undeclared"}})
        self.assertEqual(entry["ceiling"], "待核实")
        self.assertIn("security", "；".join(entry["gaps"]))

    def test_consistent_pass_is_not_downgraded(self):
        """没有缺口时不许误降——过严的门禁会让人绕过门禁。"""
        entry = self.pg._exploration_check({
            "verdict_ceiling": "通过", "ready_pending": 0, "missing_views": [],
            "thin_views": [], "failed_items": [], "traversal_recorded": True,
            "mandatory_attributes": {"security": "covered", "compatibility": "not_applicable",
                                     "copy": "covered"}})
        self.assertEqual(entry["ceiling"], "通过")
        self.assertEqual(entry["gaps"], [])


class ResultFieldScopeTests(PhaseGateTestBase):
    """attack_coverage / exploration 只对 execute 有意义，check_verdict 也只读它。"""

    def test_complete_rejects_conclusion_fields_on_plan(self):
        """静默丢弃会让调用方以为"交了、被记录了"，实际没进任何门禁。"""
        self.pg.start(self.state, "plan", "SESS-1")
        for kwargs in ({"attack_coverage": {"verdict_ceiling": "待核实", "gaps": []}},
                       {"exploration": {"verdict_ceiling": "通过"}}):
            with self.subTest(**{k: v for k, v in kwargs.items()}):
                with self.assertRaises(ValueError) as ctx:
                    self.pg.complete(self.state, "plan", session="SESS-1", **kwargs)
                self.assertIn("只对 execute 阶段生效", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
