"""v1.26.0 加固回归：义务契约、存量登记、缺口参与判定、门禁绑定、属性裁定、回链层级。

每条都对应一次可复现的错误放行：不写义务仍判覆盖、删字段伪装存量、已登记缺口消失、
内容绑定只能手写、安全/兼容/文案整类省略、回链走错目录仍通过。
"""
import copy
import importlib.util
import json
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
CG = ROOT / "skills" / "case-generator"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(CG / "scripts"))


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture():
    return json.loads((CG / "tests" / "fixtures" / "cases-sample-12.json").read_text(encoding="utf-8-sig"))


def policy():
    return json.loads((CG / "config" / "policy.json").read_text(encoding="utf-8-sig"))


class ObligationContract(unittest.TestCase):
    def setUp(self):
        self.cd = load("cd_126", "scripts/case_data.py")

    def errors(self, mutate):
        cases = fixture()
        mutate(cases)
        return self.cd.validate(cases)[0]

    def test_missing_tc_reference_rejected(self):
        errs = self.errors(lambda c: c["coverage_obligations"][0].update(tc_ids=["TC-NOT-EXIST"]))
        self.assertTrue(any("unknown testcase" in e for e in errs), errs)

    def test_obligation_id_duplicate_rejected(self):
        def mutate(cases):
            cases["coverage_obligations"][1]["id"] = cases["coverage_obligations"][0]["id"]
        self.assertTrue(any("duplicate" in e for e in self.errors(mutate)))

    def test_placeholder_target_rejected(self):
        errs = self.errors(lambda c: c["coverage_obligations"][0].update(target="待补充"))
        self.assertTrue(any("target" in e for e in errs), errs)

    def test_unknown_source_rejected(self):
        errs = self.errors(lambda c: c["coverage_obligations"][0].update(source="AC-99"))
        self.assertTrue(any("unknown reference" in e for e in errs), errs)

    def test_method_layer_not_an_obligation(self):
        errs = self.errors(lambda c: c["coverage_obligations"][0].update(layer="L3"))
        self.assertTrue(any("L1/L2" in e for e in errs), errs)

    def test_tc_must_declare_bound_layer(self):
        def mutate(cases):
            tc = next(t for t in cases["testcases"] if t["id"] == "TC-M01-001")
            tc["coverage_flags"] = ["L2"]
        errs = self.errors(mutate)
        self.assertTrue(any("未声明 L1" in e for e in errs), errs)

    def test_valid_obligations_pass(self):
        self.assertEqual(self.cd.validate(fixture())[0], [])


class ObligationRequiredByGate(unittest.TestCase):
    def setUp(self):
        self.gc = load("gc_126", "skills/case-generator/scripts/gate_checks.py")
        self.pol = policy()

    def run_gate(self, cases):
        return self.gc.GateRunner(copy.deepcopy(self.pol)).run(cases)

    def cov(self, report):
        return next(c for c in report["checks"] if c["id"] == "COV-LAYERS")

    def test_absent_obligations_block_new_asset(self):
        cases = fixture()
        cases.pop("coverage_obligations")
        report = self.run_gate(cases)
        self.assertEqual(self.cov(report)["status"], "failed")

    def test_deleting_flags_does_not_buy_legacy_treatment(self):
        cases = fixture()
        cases.pop("coverage_obligations")
        for tc in cases["testcases"]:
            tc.pop("coverage_flags", None)
        self.assertEqual(self.cov(self.run_gate(cases))["status"], "failed")

    def test_registered_legacy_is_warn_not_pass(self):
        cases = fixture()
        cases.pop("coverage_obligations")
        for tc in cases["testcases"]:
            tc.pop("coverage_flags", None)
        cases["meta"]["legacy_source"] = {"schema_version": "1.0", "migrated_at": "2026-11-17", "owner": "测试负责人"}
        report = self.run_gate(cases)
        self.assertEqual(self.cov(report)["status"], "warning")
        self.assertNotEqual(report["status"], "pass")

    def test_declared_obligations_still_pass(self):
        report = self.run_gate(fixture())
        self.assertEqual(self.cov(report)["status"], "passed")


class RegisteredGapsCount(unittest.TestCase):
    def setUp(self):
        self.cm = load("cm_126", "scripts/coverage_matrix.py")
        self.pol = policy()

    def test_open_gap_forces_layer_missing(self):
        cases = fixture()
        cases["coverage_gaps"] = [{"gap_id": "GAP-01", "kind": "element_uncovered", "target": "FP-01",
                                   "reason": "全选边界未定义", "obligation": "向产品确认后补测"}]
        matrix = self.cm.build_matrix(cases, self.pol)
        self.assertIn("L2", matrix["missing_layers"])
        self.assertTrue(any(g.get("gap_id") == "GAP-01" and g.get("source") == "coverage_gaps"
                            for g in matrix["gaps"]))

    def test_closed_gap_does_not_block(self):
        cases = fixture()
        cases["coverage_gaps"] = [{"gap_id": "GAP-01", "kind": "element_uncovered", "target": "FP-01",
                                   "reason": "全选边界未定义", "obligation": "已补边界用例",
                                   "closed_by": {"testcase": "TC-M01-002", "checked_at": "2026-11-17"}}]
        matrix = self.cm.build_matrix(cases, self.pol)
        self.assertNotIn("L2", matrix["missing_layers"])
        self.assertFalse(matrix["open_registered_gaps"])


class GateBindingProduced(unittest.TestCase):
    def setUp(self):
        self.gc = load("gc_bind", "skills/case-generator/scripts/gate_checks.py")
        self.el = load("el_bind", "scripts/execution_ledger.py")
        self.pol = policy()
        self.cases = fixture()

    def test_gate_output_is_bound_without_handwriting(self):
        report = self.gc.GateRunner(copy.deepcopy(self.pol)).run(copy.deepcopy(self.cases))
        self.assertIn("binding", report, "门禁必须自己产出内容绑定")
        ids = [tc["id"] for tc in self.cases["testcases"]]
        info = self.el._gate_precondition(None, report, ids, self.cases)
        self.assertEqual(info["binding_verdict"], "已绑定")

    def test_tampered_design_is_rejected(self):
        report = self.gc.GateRunner(copy.deepcopy(self.pol)).run(copy.deepcopy(self.cases))
        tampered = copy.deepcopy(self.cases)
        tampered["testcases"][0]["title"] = "被改过的标题"
        with self.assertRaises(ValueError):
            self.el._gate_precondition(None, report, [tc["id"] for tc in tampered["testcases"]], tampered)


class MandatoryAttributeRulings(unittest.TestCase):
    def setUp(self):
        self.tool = load("exp_126", "scripts/exploration_state.py")
        self.base = {
            "id": "EXP-TRAV-01", "requirement_ids": ["REQ-1"], "page_url": "https://example.invalid/app",
            "role": "编辑者", "viewport": "1440x900", "state": "打开", "action": "走查", "risk": "high",
            "status": "PASS", "observation": "已观察",
            "evidence_paths": [str(ROOT / "README.md")], "blocked_reason": "", "side_effect_pending": False,
            "attributes": ["functional", "visual", "usability", "accessibility", "performance"],
        }
        self.data = {"schema_version": "1.0",
                     "context": {"target_url": "https://example.invalid/app", "application_version": "b",
                                 "environment": "test"},
                     "items": [dict(self.base),
                               dict(self.base, id="EXP-TRAV-02",
                                    page_url="https://example.invalid/app/list", state="列表")]}

    def ceiling(self):
        return self.tool.summarize(self.data)["gate"]["verdict_ceiling"]

    def test_undeclared_mandatory_attributes_hold_ceiling(self):
        self.assertEqual(self.ceiling(), "待核实")

    def test_not_applicable_with_reason_releases_ceiling(self):
        self.data["attribute_rulings"] = {
            attr: {"state": "not_applicable", "reason": "本次需求未涉及该质量属性"}
            for attr in ("security", "compatibility", "content")
        }
        self.assertEqual(self.ceiling(), "通过")

    def test_applicable_without_execution_holds_ceiling(self):
        self.data["attribute_rulings"] = {
            "security": {"state": "applicable", "reason": "涉及越权访问"},
            "compatibility": {"state": "not_applicable", "reason": "未声明内核范围"},
            "content": {"state": "not_applicable", "reason": "无文案变更"},
        }
        gate = self.tool.summarize(self.data)["gate"]
        self.assertEqual(gate["verdict_ceiling"], "待核实")
        self.assertEqual(gate["mandatory_attributes"]["security"], "applicable")

    def test_invalid_ruling_state_rejected(self):
        self.data["attribute_rulings"] = {"security": {"state": "忽略", "reason": "x"}}
        with self.assertRaises(ValueError):
            self.tool.summarize(self.data)


class ReferenceResolvesPerLevel(unittest.TestCase):
    def test_wrong_level_reference_does_not_resolve(self):
        mod = load("ref_126", "tests/test_reference_links.py")
        skill_md = ROOT / "skills" / "case-generator" / "SKILL.md"
        self.assertFalse(mod.resolves(skill_md, "check-dimensions.md"),
                         "skill 文档不得靠包级/别 skill 的同名文件蒙混")
        self.assertTrue(mod.resolves(skill_md, "design-spec.md"))

    def test_package_doc_does_not_borrow_skill_local_file(self):
        mod = load("ref_126b", "tests/test_reference_links.py")
        package_doc = ROOT / "references" / "bug-detection-map.md"
        self.assertFalse(mod.resolves(package_doc, "figma-fields.md"))
        self.assertTrue(mod.resolves(package_doc, "methodology.md"))


class MalformedWitnessDoesNotCrash(unittest.TestCase):
    """真实存量数据里 method_evidence 可能是数组；校验器必须判"无见证"而不是抛异常。"""

    def setUp(self):
        self.cm = load("cm_shape", "scripts/coverage_matrix.py")
        self.pol = policy()

    def test_list_witness_is_unwitnessed_not_crash(self):
        cases = fixture()
        cases["testcases"][0]["method_evidence"] = {"decision_table": ["条件A", "动作B"],
                                                    "equivalence": ["有效类"]}
        matrix = self.cm.build_matrix(cases, self.pol)
        declared = matrix["layer_matrix"]["L3"]["detail"]["declared_without_evidence"]
        methods = {item["method"] for item in declared}
        self.assertIn("decision_table", methods)
        self.assertIn("equivalence", methods)
        self.assertFalse(matrix["layer_matrix"]["L3"]["covered"])


class LegacyScanClassification(unittest.TestCase):
    def setUp(self):
        self.scan = load("scan_126", "scripts/legacy_scan.py")

    def test_three_way_classification(self):
        cases = fixture()
        self.assertEqual(self.scan.classify(cases)[0], "obligations")
        legacy = copy.deepcopy(cases)
        legacy.pop("coverage_obligations")
        legacy["meta"]["legacy_source"] = {"schema_version": "1.0", "migrated_at": "2026-11-17", "owner": "负责人"}
        self.assertEqual(self.scan.classify(legacy)[0], "legacy")
        legacy.pop("legacy_source", None)
        legacy["meta"].pop("legacy_source", None)
        self.assertEqual(self.scan.classify(legacy)[0], "blocked")


class FailClosedDependencies(unittest.TestCase):
    """审核修复：缺关键依赖必须 fail closed，不得静默降级。"""

    PKG = Path(__file__).resolve().parents[1]

    def test_gate_checks_exits_when_fingerprint_missing(self):
        """design_fingerprint 缺失时门禁必须 rc=2，且不产出报告（否则 binding 静默消失）。"""
        import shutil, subprocess, tempfile
        src = self.PKG / "scripts" / "design_fingerprint.py"
        cases = self.PKG / "skills" / "case-generator" / "tests" / "fixtures" / "cases-sample-12.json"
        with tempfile.TemporaryDirectory() as td:
            backup = Path(td) / "df.py"
            shutil.copy2(src, backup)
            try:
                src.unlink()
                r = subprocess.run(
                    [sys.executable, str(self.PKG / "skills" / "case-generator" / "scripts" / "gate_checks.py"),
                     "--cases", str(cases), "--json"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn("design_fingerprint", r.stderr)
                self.assertEqual(r.stdout.strip(), "", "fail closed 时不得产出报告")
            finally:
                shutil.copy2(backup, src)


class BlindEvalDisclosesNormalMisses(unittest.TestCase):
    """审核修复：normal 级漏检不改 verdict，但必须出 warnings。"""

    def setUp(self):
        self.be = load("be_audit", "scripts/blind_eval.py")
        self.fx = Path(__file__).resolve().parents[1] / "docs" / "evals-baseline" / "detection-fixtures" / "01-boundary-form.json"
        self.key = json.loads(self.fx.read_text(encoding="utf-8"))

    def _score(self, skip_severity):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            run = Path(td) / "run"
            self.be.prepare(self.fx, run)
            findings = []
            picked = [a for a in self.key["hidden_answers"] if a["severity"] != skip_severity]
            for i, a in enumerate(picked, 1):
                ev = f"evidence/f{i}.txt"
                (run / ev).write_text("e", encoding="utf-8")
                findings.append({"id": f"F{i}", "severity": a["severity"], "evidence": [ev],
                                 "title": "t", "observed": " ".join(a["match_all"]) + " " + " ".join(a["match_any"])})
            branches = [{"name": str(b), "status": "EXECUTED"} for b in self.key.get("required_branches") or []]
            payload = {"executor": {"id": "t"}, "findings": findings, "branches": branches,
                       "obligations_untested": [], "invalid_actions": 0}
            pf = run / "findings.json"
            pf.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            return self.be.score_run(run, self.fx, pf)

    def test_normal_miss_is_disclosed_not_silent(self):
        r = self._score("normal")
        self.assertEqual(r["verdict"], "pass", "协议规定 normal 不影响 verdict")
        self.assertTrue(any("normal" in w for w in r["warnings"]), r["warnings"])

    def test_full_detection_has_no_warnings(self):
        r = self._score("__none__")
        self.assertEqual(r["warnings"], [])


if __name__ == "__main__":
    unittest.main()
