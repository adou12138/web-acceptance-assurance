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
                          "gate_report_status": "warning", "gate_report_case_count": len(ids)})

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


if __name__ == "__main__":
    unittest.main()
