"""gate_checks.py 门禁测试（阶段 2 验收 + 各检查项触发验证）。"""

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _util import CASES_12, SCRIPTS, load  # noqa: E402

import gate_checks as gc  # noqa: E402

POLICY = json.loads((Path(gc.DEFAULT_POLICY)).read_text(encoding="utf-8-sig"))


def run_gate(cases, policy=None):
    return gc.GateRunner(policy or copy.deepcopy(POLICY)).run(cases)


def failed_ids(report):
    return {c["id"] for c in report["checks"] if c["status"] == "failed"}


class TestFullGate(unittest.TestCase):
    def test_sample12_all_blocks_pass(self):
        report = run_gate(load(CASES_12))
        self.assertEqual(failed_ids(report), set(), json.dumps(report, ensure_ascii=False)[:2000])
        self.assertEqual(report["counts"], {"testcases": 12, "smoke": 2, "p0": 2})

    def test_cov_ui_zero_without_ruling_is_blocked(self):
        cases = load(CASES_12)
        cases["applicability"].pop("visual")
        cases["applicability"].pop("accessibility")
        report = run_gate(cases)
        self.assertIn("COV-UI", failed_ids(report))
        issues = next(c["issues"] for c in report["checks"] if c["id"] == "COV-UI")
        self.assertEqual({i["why"].split()[0] for i in issues}, {"visual", "accessibility"})

    def test_cov_ui_zero_with_exempt_ruling_passes(self):
        report = run_gate(load(CASES_12))
        self.assertNotIn("COV-UI", failed_ids(report))
        self.assertIn("COV-UI", [c["id"] for c in report["checks"]])

    def test_cov_ui_present_case_needs_no_ruling(self):
        cases = load(CASES_12)
        cases["testcases"][0]["attributes"] = ["functional", "visual", "accessibility"]
        cases["applicability"].pop("visual")
        cases["applicability"].pop("accessibility")
        self.assertNotIn("COV-UI", failed_ids(run_gate(cases)))

    def test_cov_ui_legacy_set_without_applicability_warns_not_blocks(self):
        """未声明 applicability 的存量用例集降为告警，报告仍可见覆盖缺口。"""
        cases = load(CASES_12)
        cases.pop("applicability")
        report = run_gate(cases)
        self.assertNotIn("COV-UI", failed_ids(report))
        check = next(c for c in report["checks"] if c["id"] == "COV-UI")
        self.assertEqual(check["status"], "warning")
        self.assertTrue(check["issues"], "存量缺口必须在报告中可见，不得静默通过")

    def test_s0_blocks_downstream(self):
        cases = load(CASES_12)
        del cases["testcases"][0]["assertions"]
        report = run_gate(cases)
        self.assertIn("S0", failed_ids(report))
        block_checks = [c["id"] for c in report["checks"]]
        self.assertEqual(block_checks, ["S0"], "S0 失败时不得计算后续门禁")

    def test_report_has_elapsed_ms(self):
        """D1 校准机制：门禁耗时自动采集。"""
        report = run_gate(load(CASES_12))
        self.assertIsInstance(report.get("elapsed_ms"), int)
        self.assertGreaterEqual(report["elapsed_ms"], 0)

    def test_g1_rules_configurable(self):
        """D3 方案 §12：G1 规则集由 policy.gate_rules.g1 覆盖。"""
        cases = load(CASES_12)
        tc = next(t for t in cases["testcases"] if t["id"] == "TC-M01-003")
        tc["steps"][1]["action"] = "点击「下一页」按钮后进入相关页面"
        policy = copy.deepcopy(POLICY)
        policy["gate_rules"] = {"g1": {"banned_step_patterns": [
            {"pattern": "进入相关页面", "hint": "测试自定义提示"}]}}
        report = run_gate(cases, policy)
        g1 = next(c for c in report["checks"] if c["id"] == "G1")
        self.assertEqual(g1["status"], "failed")
        self.assertIn("测试自定义提示", g1["issues"][0]["why"])


class TestSingleChecks(unittest.TestCase):
    def test_g2_vague_expect(self):
        cases = load(CASES_12)
        tc = next(t for t in cases["testcases"] if t["id"] == "TC-M01-003")
        tc["assertions"][0]["expect"] = "验证成功"
        report = run_gate(cases)
        self.assertIn("G2", failed_ids(report))

    def test_s0_catches_placeholder(self):
        cases = load(CASES_12)
        tc = next(t for t in cases["testcases"] if t["id"] == "TC-M01-003")
        tc["assertions"][0]["expect"] = "TODO"
        report = run_gate(cases)
        self.assertIn("S0", failed_ids(report))

    def test_g3_b_class_smoke(self):
        cases = load(CASES_12)
        tc = next(t for t in cases["testcases"] if t["id"] == "TC-M01-002")
        tc["suites"].append("smoke")
        report = run_gate(cases)
        self.assertIn("G3", failed_ids(report))

    def test_g6_merged_action(self):
        cases = load(CASES_12)
        tc = next(t for t in cases["testcases"] if t["id"] == "TC-M01-003")
        tc["steps"][1]["action"] = "点击「下一页」按钮并验证内容"
        report = run_gate(cases)
        self.assertIn("G6", failed_ids(report))

    def test_g5_duplicate_pair(self):
        cases = load(CASES_12)
        src = next(t for t in cases["testcases"] if t["id"] == "TC-M01-003")
        dst = next(t for t in cases["testcases"] if t["id"] == "TC-M01-004")
        dst["title"] = src["title"]
        dst["steps"] = copy.deepcopy(src["steps"])
        dst["assertions"] = copy.deepcopy(src["assertions"])
        report = run_gate(cases)
        self.assertIn("G5", failed_ids(report))

    def test_v05_short_description(self):
        cases = load(CASES_12)
        tc = next(t for t in cases["testcases"] if t["id"] == "TC-M01-003")
        tc["title"] = "短"
        tc["steps"] = [{"action": "a", "expected": "b"}]
        tc["assertions"] = [{"check": "c", "oracle": "o", "expect": 1}]
        report = run_gate(cases)
        self.assertIn("V05", failed_ids(report))

    def test_cov_method_without_witness(self):
        cases = load(CASES_12)
        tc = next(t for t in cases["testcases"] if t["id"] == "TC-M01-006")
        tc["method_evidence"] = {"boundary": None}
        report = run_gate(cases)
        self.assertIn("COV", failed_ids(report))

    def test_delivery_ratio_gate_over_threshold(self):
        cases = load(CASES_12)
        for tc in cases["testcases"][4:10]:
            if "smoke" not in tc["suites"]:
                tc["suites"].append("smoke")
                tc["smoke_source"] = "A"
        policy = copy.deepcopy(POLICY)
        policy["thresholds"]["small_sample"]["case_count_below"] = 5
        report = run_gate(cases, policy)
        self.assertIn("RATIO-SMOKE", failed_ids(report))

    def test_p0_delivery_gate(self):
        cases = load(CASES_12)
        for tc in cases["testcases"][6:12]:
            tc["priority"] = "P0"
        policy = copy.deepcopy(POLICY)
        policy["thresholds"]["small_sample"]["case_count_below"] = 5
        report = run_gate(cases, policy)
        self.assertIn("RATIO-P0", failed_ids(report))

    def test_batch_warn_smoke(self):
        cases = load(CASES_12)
        for tc in cases["testcases"][6:10]:
            if "smoke" not in tc["suites"]:
                tc["suites"].append("smoke")
                tc["smoke_source"] = "A"
        policy = copy.deepcopy(POLICY)
        policy["thresholds"]["small_sample"]["case_count_below"] = 5
        report = run_gate(cases, policy)
        g5w = next(c for c in report["checks"] if c["id"] == "RATIO-SMOKE-W")
        self.assertEqual(g5w["status"], "warning")

    def test_g1_5_accepts_table_quantifiers(self):
        """审核补充：行/列/页 等表格类量词是可观测信号（导出/表格类断言常用）。"""
        cases = load(CASES_12)
        tc = next(t for t in cases["testcases"] if t["id"] == "TC-M01-003")
        tc["steps"][-1] = {"action": "打开导出文件并核对首列问卷标题行数", "expected": "文件含 3 行问卷记录"}
        report = run_gate(cases)
        self.assertNotIn("G1.5", failed_ids(report))

    def test_g7_out_of_scope_feature(self):
        cases = load(CASES_12)
        scope = {"leaf_ids": ["FP-01", "FP-02"]}
        report = gc.GateRunner(copy.deepcopy(POLICY)).run(cases, set(scope["leaf_ids"]))
        self.assertIn("G7", failed_ids(report))


def _cov_tc(i, scn_id, path_id):
    return {"id": f"TC-M01-{i:03d}", "title": f"场景{i}正向提交并读回", "feature_ids": ["FP-01"], "acceptance_ids": ["AC-01"],
            "scenario_id": scn_id, "path_id": path_id, "layer": "ui", "attributes": ["functional"], "suites": ["feature"],
            "classification_reason": "验证主流程提交与读回行为", "priority": "P1", "required": True, "role": "测试账号",
            "preconditions": ["测试环境已部署本次版本", "账号已登录数据处理后台", "预置一条可提交的业务数据"], "data": {"input": "样例值"},
            "steps": [{"action": "进入「数据处理」页面并点击「提交」", "expected": "提交成功且提示「已保存」"},
                      {"action": "刷新页面后核对结果区", "expected": "结果区显示 1 条已保存记录"}],
            "assertions": [{"check": "保存后结果区条数", "oracle": "结果区列表分页统计", "expect": 1}],
            "dependency_mode": "real", "cleanup": "删除本次提交的业务数据", "project": "异常路径门禁测试", "menu_path": "数据处理→提交"}


def make_workflow_cases(exception_map=None, na_exception=None, na_permission=None, n=2):
    """E7 测试用最小 workflow 样本（过 S0：required AC 双向、场景路径引用有效）。"""
    exception_map = exception_map or {}
    scenarios, tcs, pid = [], [], 0
    for i in range(1, n + 1):
        paths = {"positive": [], "boundary": [], "exception": [], "permission": []}
        pid += 1
        paths["positive"].append({"id": f"PATH-{pid:03d}", "description": f"场景{i}正向提交并读回"})
        if exception_map.get(i):
            pid += 1
            paths["exception"].append({"id": f"PATH-{pid:03d}", "description": f"场景{i}提交失败后的错误反馈与恢复"})
        na = {}
        if na_exception is not None and not paths["exception"]:
            entry = na_exception[i - 1] if isinstance(na_exception, list) else na_exception
            if entry is not None:
                na["exception"] = entry
        if na_permission is not None:
            entry = na_permission[i - 1] if isinstance(na_permission, list) else na_permission
            if entry is not None:
                na["permission"] = entry
        scenarios.append({"id": f"SCN-{i:02d}", "name": f"业务场景{i}的数据处理旅程", "goal": "验证主流程与异常反馈",
                          "feature_ids": ["FP-01"], "paths": paths, "not_applicable": na})
        tcs.append(_cov_tc(i, f"SCN-{i:02d}", paths["positive"][0]["id"]))
        # v3 G05：声明的异常路径必须有真实关联用例，不能“有声明即覆盖”
        for ep in paths["exception"]:
            et = _cov_tc(i, f"SCN-{i:02d}", ep["id"])
            et["id"] = f"TC-M01-{i:03d}-EX"
            et["title"] = f"场景{i}异常路径用例"
            et["required"] = False
            tcs.append(et)
    return {
        "meta": {"schema_version": "1.0", "id": "REQ-01", "title": "异常路径门禁测试", "flow_kind": "workflow"},
        "requirements": [{"id": "REQ-01", "name": "数据处理", "source": "门禁测试需求", "description": "数据提交与异常反馈", "confirmation": "confirmed"}],
        "features": [{"id": "M01", "name": "数据处理", "description": "数据处理功能组", "children": [
            {"id": "FP-01", "name": "数据处理提交", "description": "提交并读回结果", "requirement_ids": ["REQ-01"]},
            {"id": "FP-02", "name": "异常反馈", "description": "失败时的反馈与恢复", "requirement_ids": ["REQ-01"]},
        ]}],
        "acceptance": [{"id": "AC-01", "requirement_id": "REQ-01", "description": "提交后结果区条数与提交内容可核对", "reason": "核心业务规则",
                        "required": True, "confirmation": "confirmed", "oracle": "结果页数据与提交内容一致",
                        "elements": {"scene": "数据处理页面", "object": "提交按钮与结果区", "condition": "已登录且有一条可提交数据", "focus": "提交后读回一致"},
                        "case_mode": "OR", "alternative_reason": "每场景一条必要正向用例登记", "case_ids": [f"TC-M01-{i:03d}" for i in range(1, n + 1)]}],
        "scenarios": scenarios,
        "testcases": tcs,
    }


class TestCovPath(unittest.TestCase):
    """E7：异常/权限路径配比与豁免举证门禁（COV-PATH）。"""

    def policy_with(self, small_below=0, min_ratio=0.5):
        policy = copy.deepcopy(POLICY)
        policy["thresholds"]["small_sample"]["case_count_below"] = small_below
        policy["thresholds"]["path_coverage"] = {"exception_coverage_min": min_ratio}
        return policy

    def cov(self, report):
        return next(c for c in report["checks"] if c["id"] == "COV-PATH")

    def test_simple_flow_not_applicable(self):
        report = run_gate(load(CASES_12), self.policy_with())
        cov = self.cov(report)
        self.assertEqual(cov["status"], "passed")
        self.assertIn("不适用", cov["passed_msg"])

    def test_blocks_zero_exception_with_object_evidence(self):
        cases = make_workflow_cases(
            na_exception=[{"reason": f"场景{i}无失败重试入口：无重试按钮与错误码提示", "business_object": "失败重试", "checked_at": "2026-11-17"} for i in (1, 2)],
            na_permission=[{"reason": f"场景{i}仅单角色可用，无权限分支可举证", "business_object": "角色", "checked_at": "2026-11-17"} for i in (1, 2)])
        report = run_gate(cases, self.policy_with(small_below=0))
        cov = self.cov(report)
        self.assertEqual(cov["status"], "failed")
        self.assertTrue(any("exception_coverage" in i["why"] for i in cov["issues"]))

    def test_blocks_string_exemption(self):
        cases = make_workflow_cases(n=1, na_exception="场景无失败重试入口可举证", na_permission="仅单角色可用")
        report = run_gate(cases, self.policy_with(small_below=0))
        cov = self.cov(report)
        self.assertEqual(cov["status"], "failed")
        self.assertTrue(any("字符串形态" in i["why"] for i in cov["issues"]))

    def test_warn_on_small_sample(self):
        cases = make_workflow_cases(
            na_exception=[{"reason": f"场景{i}无失败重试入口：无重试按钮与错误码提示", "business_object": "失败重试", "checked_at": "2026-11-17"} for i in (1, 2)],
            na_permission=[{"reason": f"场景{i}仅单角色可用，无权限分支可举证", "business_object": "角色", "checked_at": "2026-11-17"} for i in (1, 2)])
        report = run_gate(cases, self.policy_with(small_below=20))
        cov = self.cov(report)
        self.assertEqual(cov["status"], "warning")
        self.assertTrue(any("小样本例外" in i["why"] for i in cov["issues"]))

    def test_pass_with_mixed_exception_paths(self):
        cases = make_workflow_cases(
            exception_map={1: True, 2: True},
            na_exception=[None, None, {"reason": "纯查询旅程无写入失败面：页面无提交与重试入口", "business_object": "纯查询", "checked_at": "2026-11-17"}],
            na_permission=[{"reason": f"场景{i}仅单角色可用，无权限分支可举证", "business_object": "角色", "checked_at": "2026-11-17"} for i in (1, 2, 3)],
            n=3)
        report = run_gate(cases, self.policy_with(small_below=0))
        cov = self.cov(report)
        self.assertEqual(cov["status"], "passed")
        self.assertEqual(cov["issues"], [])


class TestGateCli(unittest.TestCase):
    def test_cli_warning_exit_0(self):
        import subprocess
        p = subprocess.run(
            [sys.executable, str(SCRIPTS / "gate_checks.py"), "--cases", str(CASES_12)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_cli_missing_input_exit_2(self):
        import subprocess
        p = subprocess.run(
            [sys.executable, str(SCRIPTS / "gate_checks.py"), "--cases", "Z:/none.json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        self.assertEqual(p.returncode, 2)


if __name__ == "__main__":
    unittest.main()