import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "execution_ledger.py"


class ExecutionLedgerTests(unittest.TestCase):
    def setUp(self):
        if not SCRIPT.exists():
            self.fail("Missing feature: persistent execution and resume ledger")
        spec = importlib.util.spec_from_file_location("execution_ledger", SCRIPT)
        self.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tool)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.workspace = self.folder / "run"
        self.design = json.loads((ROOT / "examples" / "cases-example.json").read_text(encoding="utf-8"))
        self.context = {"target_url": "https://example.invalid/test", "environment": "test", "application_version": "build-1", "feature_flags": {"rename": True}, "role_scope": ["editor"], "dependency_mode": "sandbox"}
        self.evidence = self.folder / "observations.txt"
        self.evidence.write_text("Teaching fixture, not actual Web evidence", encoding="utf-8")
        self.tool.initialize(self.workspace, self.design, self.context, "ROUND-1", [])

    def begin(self, ident="ATT-1", case_id="TC-NAME-001"):
        return self.tool.begin(self.workspace, self.design, self.context, ident, case_id, "Create isolated project and save its name")

    def finish(self, ident="ATT-1", status="PASS", cause="none", bug_ids=None):
        return self.tool.finish(self.workspace, self.design, self.context, ident, status, "Observed fixture result", [str(self.evidence)], [{"check": "项目重新打开后的名称", "actual": "新名称", "matched": status == "PASS"}], cause, bug_ids or [], "No real remote data created")

    def report(self, bugs=None):
        return self.tool.summarize(self.workspace, self.design, self.context, bugs)

    def test_init_has_no_synthetic_pass_or_bug(self):
        report = self.report()
        self.assertEqual(report["counts"]["NOT_RUN"], 2)
        self.assertIsNone(report["pass_rate"])
        self.assertIsNone(report["open_bugs"])
        self.assertEqual(report["baseline"]["known_issues"], [])

    def test_begin_must_reconcile_after_interruption(self):
        self.begin()
        report = self.report()
        self.assertEqual(report["needs_reconcile"], ["TC-NAME-001"])
        self.assertNotIn("TC-NAME-001", report["ready"])
        self.assertEqual(report["counts"]["BLOCKED"], 1)

    def test_nonsequential_resume_uses_missing_set(self):
        self.begin("ATT-2", "TC-NAME-002")
        self.finish("ATT-2")
        self.assertEqual(self.report()["ready"], ["TC-NAME-001"])

    def test_same_event_retry_is_idempotent(self):
        self.begin()
        self.begin()
        self.finish()
        self.finish()
        self.assertEqual(len(self.tool.read_events(self.workspace)), 2)

    def test_conflicting_duplicate_does_not_overwrite(self):
        self.begin()
        self.finish()
        with self.assertRaises(ValueError):
            self.finish(status="FAIL", cause="product", bug_ids=["BUG-1"])
        self.assertEqual(self.report()["counts"]["PASS"], 1)

    def test_unknown_case_rejected(self):
        with self.assertRaises(ValueError):
            self.begin(case_id="TC-MISSING")

    def test_finish_requires_begin(self):
        with self.assertRaises(ValueError):
            self.finish()

    def test_missing_evidence_cannot_create_pass(self):
        self.begin()
        with self.assertRaises(ValueError):
            self.tool.finish(self.workspace, self.design, self.context, "ATT-1", "PASS", "ok", [], [{"check": "x", "actual": 1, "matched": True}], "none", [], "clean")
        self.assertEqual(self.report()["counts"]["PASS"], 0)

    def test_pass_requires_satisfied_assertions(self):
        self.begin()
        with self.assertRaises(ValueError):
            self.tool.finish(self.workspace, self.design, self.context, "ATT-1", "PASS", "bad", [str(self.evidence)], [{"check": "x", "actual": 1, "matched": False}], "none", [], "clean")

    def test_product_failure_is_not_hidden_by_retry(self):
        self.begin()
        self.finish(status="FAIL", cause="product", bug_ids=["BUG-1"])
        self.begin("ATT-2")
        self.finish("ATT-2")
        report = self.report()
        self.assertEqual(report["outcomes"]["TC-NAME-001"]["status"], "FAIL")
        self.assertEqual(report["counts"]["PASS"], 0)
        self.assertEqual(report["executed"], 1)

    def test_unknown_mixed_retry_is_blocked_with_flaky_flag(self):
        self.begin()
        self.finish(status="FAIL", cause="unknown")
        self.begin("ATT-2")
        self.finish("ATT-2")
        report = self.report()
        self.assertEqual(report["counts"]["BLOCKED"], 1)
        self.assertEqual(report["flaky_cases"], ["TC-NAME-001"])
        self.assertEqual(report["executed"], 1)
        self.assertEqual(report["pass_rate"], 0)

    def test_blocked_then_pass_is_not_false_flaky(self):
        self.begin()
        self.finish(status="BLOCKED", cause="environment")
        self.begin("ATT-2")
        self.finish("ATT-2")
        report = self.report()
        self.assertEqual(report["counts"]["PASS"], 1)
        self.assertEqual(report["flaky_cases"], [])

    def test_plan_change_or_context_change_marks_all_stale(self):
        self.begin()
        self.finish()
        changed = copy.deepcopy(self.design)
        changed["testcases"][0]["steps"][0]["expected"] = "changed expectation"
        for design, context in [(changed, self.context), (self.design, dict(self.context, application_version="build-2"))]:
            report = self.tool.summarize(self.workspace, design, context)
            self.assertFalse(report["compatible"])
            self.assertEqual(report["counts"]["PASS"], 0)
            self.assertEqual(report["stale_cases"], ["TC-NAME-001", "TC-NAME-002"])
            with self.assertRaises(ValueError):
                self.tool.begin(self.workspace, design, context, "ATT-new", "TC-NAME-001", "do not auto rerun")

    def test_changed_evidence_invalidates_result(self):
        self.begin()
        self.finish()
        self.evidence.write_text("modified", encoding="utf-8")
        report = self.report()
        self.assertEqual(report["counts"]["BLOCKED"], 1)
        self.assertTrue(report["evidence_issues"])
        self.assertEqual(report["executed"], 0)

    def test_bug_counts_come_from_explicit_bug_state(self):
        bugs = [{"id": "BUG-1", "severity": "blocker", "status": "closed"}, {"id": "BUG-2", "severity": "major", "status": "open"}]
        report = self.report(bugs)
        self.assertEqual(report["open_bugs"], {"blocker": 0, "major": 1, "normal": 0, "minor": 0})
        self.assertEqual(report["bug_delta"]["new_ids"], ["BUG-2"])

    def test_invalid_duplicate_bug_ids_are_rejected(self):
        with self.assertRaises(ValueError):
            self.report([{"id": "BUG-1", "severity": "major", "status": "open"}] * 2)

    def test_assertions_must_cover_designed_checks(self):
        self.begin()
        with self.assertRaises(ValueError):
            self.tool.finish(self.workspace, self.design, self.context, "ATT-1", "PASS", "ok", [str(self.evidence)], [{"check": "unrelated check", "actual": 1, "matched": True}], "none", [], "clean")

    def test_documented_cli_lifecycle(self):
        design_file = self.folder / "design.json"
        context_file = self.folder / "context.json"
        result_file = self.folder / "result.json"
        workspace = self.folder / "cli-round"
        design_file.write_text(json.dumps(self.design), encoding="utf-8")
        context_file.write_text(json.dumps(self.context), encoding="utf-8")
        def cli(*args):
            return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(cli("init", workspace, design_file, context_file, "CLI-1").returncode, 0)
        self.assertEqual(cli("begin", workspace, design_file, context_file, "CLI-A1", "TC-NAME-001", "fixture action").returncode, 0)
        interrupted = cli("summary", workspace, design_file, context_file)
        self.assertEqual(json.loads(interrupted.stdout)["needs_reconcile"], ["TC-NAME-001"])
        result_file.write_text(json.dumps({"attempt_id": "CLI-A1", "status": "PASS", "actual": "fixture", "evidence_paths": [str(self.evidence)], "assertions": [{"check": "项目重新打开后的名称", "actual": "新名称", "matched": True}], "cause": "none", "bug_ids": [], "cleanup": "fixture only"}), encoding="utf-8")
        self.assertEqual(cli("finish", workspace, design_file, context_file, result_file).returncode, 0)
        report = json.loads(cli("summary", workspace, design_file, context_file).stdout)
        self.assertEqual(report["counts"]["PASS"], 1)
        self.assertEqual(report["execution_rate"], 0.5)

    def test_corrupt_journal_is_not_ignored(self):
        self.begin()
        with (self.workspace / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write("{broken\n")
        with self.assertRaises(ValueError):
            self.report()

    def test_existing_round_is_never_reinitialized(self):
        self.begin()
        with self.assertRaises(FileExistsError):
            self.tool.initialize(self.workspace, self.design, self.context, "ROUND-2", [])
        self.assertEqual(len(self.tool.read_events(self.workspace)), 1)

    def test_cross_script_and_cli_roundtrip(self):
        design_file = self.folder / "cases.json"
        context_file = self.folder / "context.json"
        design_file.write_text(json.dumps(self.design), encoding="utf-8")
        context_file.write_text(json.dumps(self.context), encoding="utf-8")
        result = subprocess.run([sys.executable, str(SCRIPT), "summary", str(self.workspace), str(design_file), str(context_file)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["counts"]["NOT_RUN"], 2)

    def test_init_rejects_unclosed_batches_and_failed_gate_report(self):
        target = self.folder / "blocked-run"
        ids = [tc["id"] for tc in self.design["testcases"]]
        good_batches = {"batches": [{"unit_id": "M01", "status": "done", "gate": "pass", "case_ids": ids}]}
        bad_batches = {"batches": [{"unit_id": "M01", "status": "doing", "gate": "not_run", "case_ids": ids}]}
        no_ids_batches = {"batches": [{"unit_id": "M01", "status": "done", "gate": "pass"}]}
        extra_ids_batches = {"batches": [{"unit_id": "M01", "status": "done", "gate": "pass", "case_ids": ids + ["TC-NOT-IN-DESIGN"]}]}
        short_ids_batches = {"batches": [{"unit_id": "M01", "status": "done", "gate": "pass", "case_ids": ids[:1]}]}
        good_gate = {"status": "warning", "counts": {"testcases": len(ids)},
                     "checks": [{"id": "S0", "status": "passed"}, {"id": "V09", "status": "warning"}]}
        bad_gate = {"status": "fail", "checks": [{"id": "S0", "status": "failed"}]}
        cases = [
            (bad_batches, None),
            (no_ids_batches, good_gate),
            (extra_ids_batches, good_gate),
            (short_ids_batches, good_gate),
            (good_batches, bad_gate),
            (good_batches, {"status": "pass", "checks": [{"id": "S0", "status": "failed"}]}),
            # 最小载荷：只有 status 或空 checks，不能解锁执行
            (good_batches, {"status": "pass"}),
            (good_batches, {"status": "pass", "checks": []}),
            (good_batches, {"status": "pass", "counts": {"testcases": len(ids)},
                            "checks": [{"id": "V09", "status": "passed"}]}),
            (good_batches, {"status": "pass", "counts": {"testcases": len(ids) + 99},
                            "checks": [{"id": "S0", "status": "passed"}]}),
            (good_batches, {"status": "pass", "counts": {},
                            "checks": [{"id": "S0", "status": "passed"}]}),
            (good_batches, {"status": "pass", "counts": {"testcases": len(ids)},
                            "checks": [{"status": "passed"}]}),
        ]
        for batches, gate in cases:
            with self.subTest(batches=batches, gate=gate), self.assertRaises(ValueError):
                self.tool.initialize(target, self.design, self.context, "ROUND-BLOCKED", [], batches, gate)
            self.assertFalse(target.exists(), "拒绝初始化时不得留下空工作区")
        baseline = self.tool.initialize(target, self.design, self.context, "ROUND-OK", [], good_batches, good_gate)
        self.assertEqual(baseline["gate_precondition"],
                         {"batches_closed": True, "batches_case_count": len(ids),
                          "gate_report_status": "warning", "gate_report_case_count": len(ids),
                          "binding_verdict": "待核实", "binding_missing": ["binding（缺内容绑定声明，旧报告兼容模式）"],
                          "binding_compat": True,
                          # 未提供对抗门禁且未豁免 → 记 missing 并把上限压到待核实
                          "adversarial_state": "missing", "adversarial_ceiling": "待核实",
                          "adversarial_reasons": ["未提供对抗门禁结论：对抗循环为产出技术结论批次的必选项；"
                                                  "请运行 scripts/adversarial_gate.py（门禁默认启用）并传入 --adversarial-gate"]})

    def test_cli_init_enforces_gate_precondition(self):
        design_file = self.folder / "cases-gate.json"
        context_file = self.folder / "context-gate.json"
        batches_file = self.folder / "batches-gate.json"
        good_gate_file = self.folder / "gate-good.json"
        thin_gate_file = self.folder / "gate-thin.json"
        ids = [tc["id"] for tc in self.design["testcases"]]
        design_file.write_text(json.dumps(self.design, ensure_ascii=False), encoding="utf-8")
        context_file.write_text(json.dumps(self.context, ensure_ascii=False), encoding="utf-8")
        batches_file.write_text(json.dumps({"batches": [{"unit_id": "M01", "status": "done", "gate": "pass", "case_ids": ids}]}), encoding="utf-8")
        good_gate_file.write_text(json.dumps({"status": "pass", "counts": {"testcases": len(ids)},
                                              "checks": [{"id": "S0", "status": "passed"}]}), encoding="utf-8")
        thin_gate_file.write_text(json.dumps({"status": "pass"}), encoding="utf-8")
        blocked = self.folder / "cli-blocked"
        blocked_run = subprocess.run([sys.executable, str(SCRIPT), "init", str(blocked), str(design_file), str(context_file),
                                      "ROUND-CLI-BLOCKED", "--batches", str(batches_file), "--gate-report", str(thin_gate_file)],
                                     capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(blocked_run.returncode, 0, "仅含 status 的门禁报告不得解锁执行")
        self.assertFalse(blocked.exists(), "拒绝初始化时不得留下空工作区")
        short_batches = self.folder / "batches-short.json"
        short_batches.write_text(json.dumps({"batches": [{"unit_id": "M01", "status": "done", "gate": "pass", "case_ids": ids[:-1]}]}), encoding="utf-8")
        short_run = subprocess.run([sys.executable, str(SCRIPT), "init", str(self.folder / "cli-short"), str(design_file), str(context_file),
                                    "ROUND-CLI-SHORT", "--batches", str(short_batches), "--gate-report", str(good_gate_file)],
                                   capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(short_run.returncode, 0, "批次漏登用例不得解锁执行")
        self.assertFalse((self.folder / "cli-short").exists())
        ok = self.folder / "cli-ok"
        ok_run = subprocess.run([sys.executable, str(SCRIPT), "init", str(ok), str(design_file), str(context_file),
                                 "ROUND-CLI-OK", "--batches", str(batches_file), "--gate-report", str(good_gate_file)],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(ok_run.returncode, 0, ok_run.stderr)
        baseline = json.loads((ok / "baseline.json").read_text(encoding="utf-8"))
        self.assertEqual(baseline["gate_precondition"]["gate_report_case_count"], len(ids))

    def test_bug_ledger_accepts_source_namespace_but_rejects_empty_external_id(self):
        valid = [{"id": "BUG-1", "severity": "major", "status": "open",
                  "source": "figma-ui-parity", "external_id": "FIG-7.41-1"}]
        self.assertEqual(self.tool._bugs(valid), valid)
        with self.assertRaises(ValueError):
            self.tool._bugs([dict(valid[0], external_id="")])

    def test_init_hints_qa_pipeline_when_design_shape_is_wrong(self):
        with self.assertRaises(ValueError) as raised:
            self.tool.initialize(self.folder / "qa-run", {"test_cases": [{"id": "TP-1"}]}, self.context, "ROUND-QA")
        self.assertIn("qa-req2testcase-generator", str(raised.exception))


class LedgerEmitsAttackFields(unittest.TestCase):
    """台账必须产出攻击面字段，否则守规矩的执行方必然被压「待核实」。

    历史行为：finish() 的 payload 里没有 path_type/surf_id/method/oracle_source/
    discovery_mode，全仓只有消费者没有生产者——按官方接口记录的执行人拿不到"通过"，
    只能绕过单写者纪律手搓 JSONL。门禁不能惩罚守规矩的人。
    """

    def setUp(self):
        spec = importlib.util.spec_from_file_location("execution_ledger_attack", SCRIPT)
        self.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tool)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.workspace = self.folder / "run"
        self.design = json.loads((ROOT / "examples" / "cases-example.json").read_text(encoding="utf-8"))
        self.context = {"target_url": "https://example.invalid/test", "environment": "test",
                        "application_version": "build-1", "feature_flags": {"rename": True},
                        "role_scope": ["editor"], "dependency_mode": "sandbox"}
        self.evidence = self.folder / "shot.png"
        self.evidence.write_text("fixture evidence", encoding="utf-8")
        self.tool.initialize(self.workspace, self.design, self.context, "ROUND-1", [])

    def attack(self, **changes):
        payload = {"path_type": "ui_content", "surf_id": "SURF-UI-01",
                   "method": "copy_audit", "oracle_source": "independent",
                   "oracle_channel": "网页端截图 + DOM 文本", "discovery_mode": "designed_probe",
                   "probes": 3}
        payload.update(changes)
        return payload

    def finish(self, attack=None, status="FAIL"):
        self.tool.begin(self.workspace, self.design, self.context, "ATT-1", "TC-NAME-001", "检查文案")
        return self.tool.finish(self.workspace, self.design, self.context, "ATT-1", status,
                                "按钮文案显示 {{name}} 未渲染", [str(self.evidence)],
                                [{"check": "项目重新打开后的名称", "actual": "{{name}}", "matched": False}],
                                "product", ["BUG-1"], "无远程数据", attack=attack)

    def last_finish(self):
        events = self.tool.read_events(self.workspace)
        return [e for e in events if e["kind"] == "FINISH"][-1]

    def test_finish_records_attack_fields(self):
        self.finish(self.attack())
        event = self.last_finish()
        self.assertEqual(event["path_type"], "ui_content")
        self.assertEqual(event["surf_id"], "SURF-UI-01")
        self.assertEqual(event["method"], "copy_audit")
        self.assertEqual(event["oracle_source"], "independent")
        self.assertEqual(event["oracle_channel"], "网页端截图 + DOM 文本")
        self.assertEqual(event["discovery_mode"], "designed_probe")
        self.assertEqual(event["probes"], 3)

    def test_unknown_path_type_rejected(self):
        with self.assertRaises(ValueError):
            self.finish(self.attack(path_type="visual"))  # 旧名不在枚举内

    def test_bad_surf_id_rejected(self):
        with self.assertRaises(ValueError):
            self.finish(self.attack(surf_id="x"))

    def test_unknown_method_rejected(self):
        with self.assertRaises(ValueError):
            self.finish(self.attack(method="whatever"))

    def test_independent_requires_channel(self):
        payload = self.attack()
        payload.pop("oracle_channel")
        with self.assertRaises(ValueError):
            self.finish(payload)

    def test_unknown_attack_key_rejected(self):
        with self.assertRaises(ValueError):
            self.finish(self.attack(path="x"))

    def test_legacy_finish_without_attack_still_works(self):
        self.finish(None)
        self.assertNotIn("path_type", self.last_finish())

    def test_ledger_events_feed_attack_coverage(self):
        """端到端：官方接口写出的台账能被 attack_coverage 直接消费，不判「字段缺失」。"""
        sys.path.insert(0, str(ROOT / "scripts"))
        import attack_coverage as ac
        self.finish(self.attack())
        events = self.tool.read_events(self.workspace)
        result = ac.analyse(events, residual_note="未破：并发删除")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["path_type"]["ui_content"], 1)
        self.assertEqual(result["oracle_source"]["independent"], 1)
        self.assertEqual(result["oracle_source"]["unproven_independent"], 0)
        codes = [g["code"] for g in result["gaps"]]
        self.assertNotIn("path_type_missing", codes)
        self.assertNotIn("method_missing", codes)
        self.assertNotIn("oracle_independence_unproven", codes)


class AdversarialGateDisabledTests(unittest.TestCase):
    """D7 回归：门禁被显式关闭须在台账留痕并压上限，不得与「对抗未启用」混算。"""

    def setUp(self):
        spec = importlib.util.spec_from_file_location("execution_ledger_disabled", SCRIPT)
        self.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tool)

    def test_disabled_gate_is_recorded_and_lowers_ceiling(self):
        gate = {"adversarial_enabled": False, "verdict_ceiling": "通过",
                "reasons": [], "adversarial_disabled": True,
                "disable_reason": "本轮只做文档核对"}
        info = self.tool._adversarial_precondition(gate)
        self.assertEqual(info["adversarial_state"], "disabled")
        self.assertTrue(info["adversarial_disabled"])
        self.assertEqual(info["adversarial_disable_reason"], "本轮只做文档核对")
        self.assertEqual(info["adversarial_ceiling"], "待核实")
        self.assertTrue(any("显式关闭" in r for r in info["adversarial_reasons"]))

    def test_disabled_gate_without_reason_still_warns(self):
        gate = {"adversarial_enabled": False, "verdict_ceiling": "通过",
                "reasons": [], "adversarial_disabled": True, "disable_reason": None}
        info = self.tool._adversarial_precondition(gate)
        self.assertEqual(info["adversarial_state"], "disabled")
        self.assertEqual(info["adversarial_ceiling"], "待核实")
        self.assertTrue(any("未提供关闭理由" in r for r in info["adversarial_reasons"]))

    def test_disabled_gate_is_distinct_from_empty_plan(self):
        # 空 plan（对抗未启用）与门禁被关闭是两种状态，不得混算。
        empty = {"adversarial_enabled": False, "verdict_ceiling": "待核实", "reasons": []}
        info = self.tool._adversarial_precondition(empty)
        self.assertEqual(info["adversarial_state"], "not_enabled")
        self.assertNotIn("adversarial_disabled", info)

    def test_enabled_gate_is_unaffected(self):
        gate = {"adversarial_enabled": True, "verdict_ceiling": "通过", "reasons": []}
        info = self.tool._adversarial_precondition(gate)
        self.assertEqual(info["adversarial_state"], "verified")
        self.assertEqual(info["adversarial_ceiling"], "通过")

    def test_disabled_gate_does_not_downgrade_a_failing_verdict(self):
        # 关闭门禁只解除「对抗未启用」的阻断，不得把门禁已算出的"不通过"
        # 降级为"待核实"——那会让已确认未闭环的严重缺陷凭空消失。
        gate = {"adversarial_enabled": True, "verdict_ceiling": "不通过",
                "reasons": ["存在 accepted 且严重度为 blocker/major 的未闭环复核"],
                "adversarial_disabled": True, "disable_reason": "本轮只做文档核对"}
        info = self.tool._adversarial_precondition(gate)
        self.assertEqual(info["adversarial_state"], "disabled")
        self.assertEqual(info["adversarial_ceiling"], "不通过")

    def test_disabled_gate_still_lifts_a_passing_verdict(self):
        # 反向：门禁算出"通过"时，关闭状态仍须压住（不得因关闭而放行）。
        gate = {"adversarial_enabled": True, "verdict_ceiling": "通过",
                "reasons": [], "adversarial_disabled": True, "disable_reason": "x"}
        info = self.tool._adversarial_precondition(gate)
        self.assertEqual(info["adversarial_ceiling"], "待核实")

    def test_gate_and_ledger_agree_on_disabled_ceiling(self):
        # 跨模块一致性：门禁自报的上限必须与台账压制的上限相同。
        # 此前门禁在 --no-enforce 下自报"通过"、台账压"待核实"，
        # 同一批产物出现两个上限，使用方按门禁写结论就会与台账打架。
        import importlib.util
        gate_spec = importlib.util.spec_from_file_location(
            "adv_gate_consistency", ROOT / "scripts" / "adversarial_gate.py")
        gate = importlib.util.module_from_spec(gate_spec)
        gate_spec.loader.exec_module(gate)
        result = gate.evaluate({"surfaces": [], "obligations": []}, [], [], "通过",
                                enforce=False, disable_reason="只读核对")
        info = self.tool._adversarial_precondition(result)
        self.assertEqual(result["verdict_ceiling"], info["adversarial_ceiling"])
        self.assertEqual(info["adversarial_ceiling"], "待核实")

    def test_adversarial_not_enabled_does_not_downgrade_a_failing_verdict(self):
        # 对抗未启用只解除"对抗必选"的阻断，不得把更严结论降级为"待核实"。
        gate = {"adversarial_enabled": False, "verdict_ceiling": "不通过",
                "reasons": ["存在 accepted 且严重度为 blocker/major 的未闭环复核"]}
        info = self.tool._adversarial_precondition(gate)
        self.assertEqual(info["adversarial_state"], "not_enabled")
        self.assertEqual(info["adversarial_ceiling"], "不通过")

    def test_adversarial_not_enabled_waived_does_not_downgrade_a_failing_verdict(self):
        # 豁免成立时同样不得降级更严结论。
        gate = {"adversarial_enabled": False, "verdict_ceiling": "不通过", "reasons": []}
        info = self.tool._adversarial_precondition(gate, waiver="只读小任务",
                                                    read_only=True, case_count=1)
        self.assertEqual(info["adversarial_state"], "waived")
        self.assertEqual(info["adversarial_ceiling"], "不通过")

    def test_waived_reason_message_states_actual_ceiling(self):
        # 文案必须与实际 ceiling 一致：上限已是"不通过"时 reason 不得再写
        # "上限仍待核实"——文案与值矛盾会让交付物自相矛盾。
        gate = {"adversarial_enabled": False, "verdict_ceiling": "不通过", "reasons": []}
        info = self.tool._adversarial_precondition(gate, waiver="只读小任务",
                                                    read_only=True, case_count=1)
        joined = "\n".join(info["adversarial_reasons"])
        self.assertIn("本轮对抗上限为不通过", joined)
        self.assertNotIn("上限仍待核实", joined)

    def test_not_enabled_reason_message_states_actual_ceiling(self):
        gate = {"adversarial_enabled": False, "verdict_ceiling": "不通过",
                "reasons": ["存在 accepted 且严重度为 blocker/major 的未闭环复核"]}
        info = self.tool._adversarial_precondition(gate)
        joined = "\n".join(info["adversarial_reasons"])
        self.assertIn("本轮对抗上限为不通过", joined)
        self.assertNotIn("结论上限待核实", joined)


if __name__ == "__main__":
    unittest.main()
