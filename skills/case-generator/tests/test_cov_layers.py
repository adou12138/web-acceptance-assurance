"""COV-LAYERS（L2/L4 矩阵）、pairwise 证据要求与 coverage_gaps 校验测试。"""

import copy
import importlib.util
import json
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _util import CASES_12, SCRIPTS, load  # noqa: E402

import gate_checks as gc  # noqa: E402

POLICY = json.loads(Path(gc.DEFAULT_POLICY).read_text(encoding="utf-8-sig"))
ROOT = Path(__file__).resolve().parents[3]


def load_pairwise():
    spec = importlib.util.spec_from_file_location("pairwise", ROOT / "scripts" / "pairwise.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_gate(cases, policy=None):
    return gc.GateRunner(policy or copy.deepcopy(POLICY)).run(cases)


def check(report, cid):
    return next(c for c in report["checks"] if c["id"] == cid)


def failed_ids(report):
    return {c["id"] for c in report["checks"] if c["status"] == "failed"}


class CovLayersTests(unittest.TestCase):
    def setUp(self):
        self.cases = load(CASES_12)

    def test_fixture_passes_layers_and_reports_matrix(self):
        report = run_gate(self.cases)
        self.assertEqual(check(report, "COV-LAYERS")["status"], "passed")
        self.assertIn("coverage_matrix", report)
        self.assertEqual(report["coverage_matrix"]["missing_layers"], [])

    def test_l2_gap_blocks_when_applicability_declared(self):
        cases = copy.deepcopy(self.cases)
        for tc in cases["testcases"]:
            if "FP-03" in tc["feature_ids"]:
                tc["coverage_flags"] = [f for f in tc.get("coverage_flags") or [] if f != "L2"]
        # 义务与标记必须一致：删标记也要删对应 L2 义务，否则先在 S0 被契约拦下（另测）
        cases["coverage_obligations"] = [ob for ob in cases.get("coverage_obligations") or []
                                         if not (ob.get("layer") == "L2" and "FP-03" in str(ob.get("target")))]
        report = run_gate(cases)
        self.assertIn("COV-LAYERS", failed_ids(report))
        why = " ".join(i.get("why", "") for i in check(report, "COV-LAYERS")["issues"])
        self.assertIn("L2", why)
        self.assertIn("FP-03", why)

    def test_layers_downgraded_to_warn_without_applicability(self):
        cases = copy.deepcopy(self.cases)
        cases.pop("applicability", None)
        report = run_gate(cases)
        self.assertEqual(check(report, "COV-LAYERS")["status"], "warning")
        self.assertNotIn("COV-LAYERS", failed_ids(report))

    def test_legacy_set_without_flags_and_without_registration_blocks(self):
        """删字段不等于老数据：没有 legacy_source 登记就必须失败（v3 G03/A3）。"""
        cases = copy.deepcopy(self.cases)
        for tc in cases["testcases"]:
            tc.pop("coverage_flags", None)
        cases.pop("coverage_obligations", None)
        report = run_gate(cases)
        self.assertEqual(check(report, "COV-LAYERS")["status"], "failed")
        why = " ".join(i.get("why", "") for i in check(report, "COV-LAYERS")["issues"])
        self.assertIn("coverage_obligations", why)
        self.assertIn("legacy_source", why)

    def test_registered_legacy_set_is_warn_with_gap(self):
        """显式登记来源/迁移日期/责任人后，才允许降为告警并披露未迁移义务。"""
        cases = copy.deepcopy(self.cases)
        for tc in cases["testcases"]:
            tc.pop("coverage_flags", None)
        cases.pop("coverage_obligations", None)
        cases["meta"]["legacy_source"] = {"schema_version": "1.0", "migrated_at": "2026-11-17",
                                          "owner": "测试负责人"}
        report = run_gate(cases)
        self.assertEqual(check(report, "COV-LAYERS")["status"], "warning")
        why = " ".join(i.get("why", "") for i in check(report, "COV-LAYERS")["issues"])
        self.assertIn("未声明 coverage_obligations", why)

    def test_new_set_with_flags_but_missing_layer_blocks(self):
        cases = copy.deepcopy(self.cases)
        for tc in cases["testcases"]:
            tc["coverage_flags"] = [f for f in (tc.get("coverage_flags") or []) if f != "L1"]
        cases["coverage_obligations"] = [ob for ob in cases.get("coverage_obligations") or []
                                         if ob.get("layer") != "L1"]
        report = run_gate(cases)
        self.assertIn("COV-LAYERS", failed_ids(report))

    def test_valid_pairwise_witness_passes(self):
        pw = load_pairwise()
        witness = pw.generate(
            [{"name": "角色", "values": ["管理员", "普通用户"]}, {"name": "权限", "values": ["查看", "编辑"]}],
            [[{"name": "角色", "value": "普通用户"}, {"name": "权限", "value": "编辑"}]],
        )
        cases = copy.deepcopy(self.cases)
        cases["testcases"][1]["method_evidence"]["pairwise"] = witness
        report = run_gate(cases)
        self.assertNotIn("COV", failed_ids(report))

    def test_tampered_pairwise_witness_blocks(self):
        pw = load_pairwise()
        witness = pw.generate(
            [{"name": "角色", "values": ["管理员", "普通用户"]}, {"name": "权限", "values": ["查看", "编辑"]}],
            None,
        )
        witness["combinations"] = witness["combinations"][:1]
        cases = copy.deepcopy(self.cases)
        cases["testcases"][1]["method_evidence"]["pairwise"] = witness
        report = run_gate(cases)
        self.assertIn("COV", failed_ids(report))
        why = " ".join(i.get("why", "") for i in check(report, "COV")["issues"])
        self.assertIn("独立重算", why)

    def test_pairwise_string_witness_rejected(self):
        cases = copy.deepcopy(self.cases)
        cases["testcases"][1]["method_evidence"]["pairwise"] = "已覆盖全部参数对"
        report = run_gate(cases)
        self.assertIn("COV", failed_ids(report))


class CoverageGapsTests(unittest.TestCase):
    def setUp(self):
        self.cases = load(CASES_12)
        self.tool = gc.cd

    def gaps_errors(self, gaps):
        cases = copy.deepcopy(self.cases)
        cases["coverage_gaps"] = gaps
        errors, _ = self.tool.validate(cases)
        return [e for e in errors if e.startswith("coverage_gaps")]

    def test_valid_gaps_accepted(self):
        errors = self.gaps_errors([{
            "gap_id": "GAP-01", "kind": "capability_unsupported",
            "target": "FP-03", "reason": "当前环境无邮件通道，通知链路无法验证",
            "obligation": "由测试环境负责人开通 SMTP 后补测通知类用例",
        }])
        self.assertEqual(errors, [])

    def test_unknown_kind_rejected(self):
        errors = self.gaps_errors([{
            "gap_id": "GAP-01", "kind": "whatever", "target": "FP-03",
            "reason": "缺环境", "obligation": "等环境就绪后补测",
        }])
        self.assertTrue(any("kind" in e for e in errors))

    def test_placeholder_obligation_rejected(self):
        errors = self.gaps_errors([{
            "gap_id": "GAP-01", "kind": "data_unavailable", "target": "FP-02",
            "reason": "无历史数据", "obligation": "待补充",
        }])
        self.assertTrue(any("obligation" in e for e in errors))

    def test_duplicate_gap_id_rejected(self):
        gap = {"gap_id": "GAP-01", "kind": "budget_exceeded", "target": "FP-01",
               "reason": "组合过多超出预算", "obligation": "下一轮补组合用例"}
        errors = self.gaps_errors([gap, dict(gap)])
        self.assertTrue(any("duplicate" in e for e in errors))

    def test_gate_reports_gaps_via_matrix(self):
        cases = copy.deepcopy(self.cases)
        cases["coverage_gaps"] = [{
            "gap_id": "GAP-01", "kind": "element_uncovered", "target": "FP-01 时间筛选",
            "reason": "需求未给出时间范围上限", "obligation": "向产品确认后补边界用例",
        }]
        report = run_gate(cases)
        # counts 是稳定契约不扩键；缺口条数经 coverage_matrix 出表，避免破坏既有消费方
        self.assertEqual(report["counts"], {"testcases": 12, "smoke": 2, "p0": 2})


if __name__ == "__main__":
    unittest.main()
