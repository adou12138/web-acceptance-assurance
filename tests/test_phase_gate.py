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
        self.state_path = self.folder / "phase-state.json"
        self.state = self.pg.init_state(self.state_path, "TASK-1")

    def run_phase(self, phase, model_class=None):
        """完整走一个阶段：提交 → 放行 → 开始 → 完成。"""
        model_class = model_class or self.pg.ROUTING[phase]["model_class"]
        self.pg.submit(self.state, phase, model_class=model_class, model_name="test-model")
        self.pg.approve(self.state, phase, "user", f"同意进入{phase}")
        self.pg.start(self.state, phase)
        self.pg.complete(self.state, phase)
        return self.state

    def finish_all(self, model_classes=None):
        model_classes = model_classes or {}
        for phase in self.pg.PHASES:
            self.run_phase(phase, model_classes.get(phase))


class PhaseMachineTests(PhaseGateTestBase):
    def test_init_creates_three_pending_phases_and_blocks_verdict(self):
        self.assertEqual(list(self.state["phases"]), list(self.pg.PHASES))
        for phase in self.pg.PHASES:
            self.assertEqual(self.state["phases"][phase]["status"], "pending")
        check = self.pg.check_verdict(self.state)
        self.assertFalse(check["verdict_allowed"])
        self.assertEqual(check["phase_ceiling"], "待核实")

    def test_cannot_start_phase_without_user_approval(self):
        with self.assertRaises(ValueError):
            self.pg.start(self.state, "plan")
        self.pg.submit(self.state, "plan", model_class="strong-reasoning")
        # 仅提交（等待放行）仍不得开始——用户同意是独立一步
        with self.assertRaises(ValueError):
            self.pg.start(self.state, "plan")

    def test_execute_requires_plan_done(self):
        self.pg.submit(self.state, "plan", model_class="strong-reasoning")
        self.pg.approve(self.state, "plan", "user", "同意")
        self.pg.start(self.state, "plan")
        with self.assertRaises(ValueError):
            self.pg.submit(self.state, "execute", model_class="fast")
        self.pg.complete(self.state, "plan")
        self.pg.submit(self.state, "execute", model_class="fast")
        self.assertEqual(self.state["phases"]["execute"]["status"], "awaiting_approval")

    def test_happy_path_unlocks_verdict(self):
        self.finish_all()
        check = self.pg.check_verdict(self.state)
        self.assertTrue(check["verdict_allowed"], check["reasons"])
        self.assertIsNone(check["phase_ceiling"])

    def test_approval_requires_approver_and_basis(self):
        self.pg.submit(self.state, "plan", model_class="strong-reasoning")
        with self.assertRaises(ValueError):
            self.pg.approve(self.state, "plan", "", "同意")
        with self.assertRaises(ValueError):
            self.pg.approve(self.state, "plan", "user", "   ")

    def test_reject_then_resubmit(self):
        self.pg.submit(self.state, "plan", model_class="strong-reasoning")
        self.pg.reject(self.state, "plan", "user", "缺少异常路径")
        self.assertEqual(self.state["phases"]["plan"]["status"], "rejected")
        self.assertEqual(self.state["phases"]["plan"]["rejections"][0]["reason"], "缺少异常路径")
        self.pg.submit(self.state, "plan", model_class="strong-reasoning")
        self.assertEqual(self.state["phases"]["plan"]["status"], "awaiting_approval")

    def test_complete_requires_in_progress(self):
        self.pg.submit(self.state, "plan", model_class="strong-reasoning")
        self.pg.approve(self.state, "plan", "user", "同意")
        with self.assertRaises(ValueError):
            self.pg.complete(self.state, "plan")

    def test_open_amendment_blocks_start_and_verdict(self):
        self.pg.submit(self.state, "plan", model_class="strong-reasoning")
        self.pg.approve(self.state, "plan", "user", "同意")
        self.pg.start(self.state, "plan")
        self.pg.request_amendment(self.state, "plan", "agent", "采集期发现新风险面需补设计")
        check = self.pg.check_verdict(self.state)
        self.assertFalse(check["verdict_allowed"])
        self.assertTrue(any("修订" in reason for reason in check["reasons"]))
        self.pg.approve_amendment(self.state, "AMD-1", "user", "同意补充设计")
        self.assertEqual(self.state["amendments"][0]["status"], "approved")

    def test_routing_mismatch_blocks_until_acknowledged(self):
        self.finish_all(model_classes={"plan": "fast"})
        check = self.pg.check_verdict(self.state)
        self.assertFalse(check["verdict_allowed"], "规划用快模型属路由误配，须压上限")
        self.assertTrue(any(w["code"] == "routing_mismatch" for w in check["warnings"]))
        self.pg.acknowledge_routing(self.state, "plan", "本会话仅有快模型可用，已在报告中披露")
        check = self.pg.check_verdict(self.state)
        self.assertTrue(check["verdict_allowed"], check["reasons"])

    def test_undeclared_routing_warns_but_does_not_block(self):
        self.finish_all()
        self.state["phases"]["plan"]["routing"]["declared_class"] = None
        check = self.pg.check_verdict(self.state)
        self.assertTrue(check["verdict_allowed"])
        self.assertTrue(any(w["code"] == "routing_undeclared" for w in check["warnings"]))

    def test_state_persists_and_reloads(self):
        self.finish_all()
        self.pg.save(self.state_path, self.state)
        reloaded = self.pg.load(self.state_path)
        self.assertEqual(reloaded["phases"]["adjudicate"]["status"], "done")
        self.assertTrue(self.pg.check_verdict(reloaded)["verdict_allowed"])
        self.assertGreaterEqual(len(reloaded["history"]), len(self.pg.PHASES) * 4)

    def test_init_refuses_existing_state(self):
        with self.assertRaises(ValueError):
            self.pg.init_state(self.state_path, "TASK-2")


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

    def test_cli_full_flow_and_verdict_gate(self):
        state = self.folder / "cli-state.json"
        run = self.cli("init", str(state), "--task", "TASK-CLI")
        self.assertEqual(run.returncode, 0, run.stderr)
        blocked = self.cli("check-verdict", str(state))
        self.assertEqual(blocked.returncode, 1)
        self.assertEqual(json.loads(blocked.stdout)["phase_ceiling"], "待核实")

        premature = self.cli("start", str(state), "--phase", "plan")
        self.assertEqual(premature.returncode, 2, "未放行不得开始")

        self.assertEqual(self.cli("submit", str(state), "--phase", "plan",
                                  "--model-class", "strong-reasoning").returncode, 0)
        self.assertEqual(self.cli("approve", str(state), "--phase", "plan",
                                  "--by", "user", "--note", "同意").returncode, 0)
        self.assertEqual(self.cli("start", str(state), "--phase", "plan").returncode, 0)
        self.assertEqual(self.cli("complete", str(state), "--phase", "plan").returncode, 0)
        status = json.loads(self.cli("status", str(state)).stdout)
        self.assertEqual(status["phases"]["plan"]["status"], "done")
        self.assertIn("execute", status["next_action"])

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
    """自攻发现的两条绕过路径：自我放行、手改状态文件。"""

    def test_agent_cannot_self_approve(self):
        self.pg.submit(self.state, "plan", model_class="strong-reasoning")
        for ident in ("agent", "assistant", "system", "砺衡"):
            with self.subTest(identity=ident), self.assertRaises(ValueError):
                self.pg.approve(self.state, "plan", ident, "我自己同意")
        self.assertEqual(self.state["phases"]["plan"]["status"], "awaiting_approval")

    def test_agent_cannot_approve_amendment(self):
        self.pg.submit(self.state, "plan", model_class="strong-reasoning")
        self.pg.request_amendment(self.state, "plan", "agent", "补充风险面")
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
        self.pg.submit(self.state, "plan", model_class="strong-reasoning")
        self.pg.save(self.state_path, self.state)
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        data["history"][0]["action"] = "approve"
        self.state_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.pg.load(self.state_path)

    def test_untampered_state_round_trips(self):
        self.finish_all()
        self.pg.save(self.state_path, self.state)
        reloaded = self.pg.load(self.state_path)
        self.assertTrue(self.pg.check_verdict(reloaded)["verdict_allowed"])


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
        self.pg.submit(self.state, "plan", model_class="strong-reasoning")
        self.pg.approve(self.state, "plan", "user", "同意")
        self.pg.start(self.state, "plan")
        self.pg.complete(self.state, "plan")
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

    def test_unacknowledged_routing_mismatch_caps_verdict(self):
        self.finish_all(model_classes={"adjudicate": "fast"})
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


if __name__ == "__main__":
    unittest.main()
