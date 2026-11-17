import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "case_data.py"


class CaseDataTests(unittest.TestCase):
    def setUp(self):
        if not SCRIPT.exists():
            self.fail("Missing feature: packaged case data validator does not exist")
        spec = importlib.util.spec_from_file_location("case_data", SCRIPT)
        self.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tool)
        self.data = json.loads((ROOT / "examples" / "cases-example.json").read_text(encoding="utf-8"))

    def test_valid_design_is_not_execution(self):
        errors, warnings = self.tool.validate(self.data)
        self.assertEqual((errors, warnings), ([], []))
        self.assertNotIn("runs", self.data)

    def test_rename_does_not_break_id_links(self):
        self.data["features"][0]["children"][0]["name"] = "新显示名"
        self.assertEqual(self.tool.validate(self.data)[0], [])

    def test_duplicate_global_id_rejected(self):
        self.data["features"][0]["children"][0]["id"] = "REQ-01"
        self.assertTrue(any("duplicate" in x for x in self.tool.validate(self.data)[0]))

    def test_broken_feature_reference_rejected(self):
        self.data["testcases"][0]["feature_ids"] = ["FP-MISSING"]
        self.assertTrue(any("FP-MISSING" in x for x in self.tool.validate(self.data)[0]))

    def test_missing_business_assertion_rejected(self):
        self.data["testcases"][0]["assertions"] = []
        self.assertTrue(any("assertions" in x for x in self.tool.validate(self.data)[0]))

    def test_required_ac_cannot_use_optional_case(self):
        self.data["testcases"][0]["required"] = False
        self.assertTrue(any("required" in x for x in self.tool.validate(self.data)[0]))

    def test_acceptance_reverse_reference_rejected(self):
        self.data["testcases"][0]["acceptance_ids"] = []
        self.assertTrue(any("acceptance" in x for x in self.tool.validate(self.data)[0]))

    def test_no_keyword_type_override(self):
        self.data["testcases"][0]["title"] = "密码权限token与接口显示"
        self.assertEqual(self.tool.validate(self.data)[0], [])
        self.assertEqual(self.data["testcases"][0]["layer"], "e2e")

    def test_empty_input_value_preserved(self):
        self.assertEqual(self.data["testcases"][1]["data"]["name"], "")
        self.assertEqual(self.tool.validate(self.data)[0], [])

    def test_template_exemption_across_scenarios_rejected(self):
        """E5：同一句豁免跨场景复用必须报 errors（拦截模板化批量豁免）。"""
        data = copy.deepcopy(self.data)
        scene = copy.deepcopy(data["scenarios"][0])
        scene["id"] = "SCN-02"
        scene["name"] = "查看者浏览项目列表"
        scene["paths"] = {"positive": [], "boundary": [], "exception": [], "permission": []}
        scene["not_applicable"] = {"exception": "示例未列接口失败，真实测试需按风险补充"}
        data["scenarios"].append(scene)
        errors, _ = self.tool.validate(data)
        self.assertTrue(any("跨场景复用" in x for x in errors))

    def test_object_exemption_with_linked_business_object_clean(self):
        """E5：对象形态且 business_object 关联 reason → 不新增告警（迁移示例形态）。"""
        data = copy.deepcopy(self.data)
        data["scenarios"][0]["not_applicable"] = {
            "exception": {"reason": "示例未列接口失败，真实测试需按风险补充", "business_object": "接口失败", "checked_at": "2026-11-17"},
            "permission": {"reason": "示例仅编辑者；真实测试需另行评估角色", "business_object": "角色", "checked_at": "2026-11-17"},
        }
        errors, warnings = self.tool.validate(data)
        self.assertEqual((errors, warnings), ([], []))

    def test_object_exemption_unlinked_business_object_warns(self):
        """E5：business_object 与 reason 及场景名均无交集 → warning。"""
        data = copy.deepcopy(self.data)
        data["scenarios"][0]["not_applicable"] = {
            "exception": {"reason": "示例未列接口失败，真实测试需按风险补充", "business_object": "支付回调", "checked_at": "2026-11-17"},
        }
        errors, warnings = self.tool.validate(data)
        self.assertEqual(errors, [])
        self.assertTrue(any("business_object" in w and "存疑" in w for w in warnings))

    def test_string_exemption_deprecation_warning(self):
        """E5：字符串形态降级 warning（向后兼容），--strict 下视为失败。"""
        data = copy.deepcopy(self.data)
        scene = copy.deepcopy(data["scenarios"][0])
        scene["id"] = "SCN-02"
        scene["name"] = "查看者浏览项目列表"
        scene["paths"] = {"positive": [], "boundary": [], "exception": [], "permission": []}
        scene["not_applicable"] = {"exception": "查看场景无独立的失败重试入口可举证"}
        data["scenarios"].append(scene)
        errors, warnings = self.tool.validate(data)
        self.assertEqual(errors, [])
        self.assertTrue(any("字符串形态已废弃" in w for w in warnings))

    def test_missing_path_case_is_coverage_warning(self):
        self.data["scenarios"][0]["paths"]["exception"] = [{"id": "PATH-03", "description": "超时恢复"}]
        del self.data["scenarios"][0]["not_applicable"]["exception"]
        self.assertTrue(any("PATH-03" in x for x in self.tool.validate(self.data)[1]))

    def test_scenario_path_must_belong_to_same_scenario(self):
        self.data["testcases"][0]["path_id"] = "PATH-MISSING"
        self.assertTrue(any("PATH-MISSING" in x for x in self.tool.validate(self.data)[0]))

    def test_required_requirement_cannot_have_no_acceptance(self):
        self.data["acceptance"] = []
        self.assertTrue(any("acceptance" in x for x in self.tool.validate(self.data)[0]))

    def test_bad_stats_rejected(self):
        self.data["meta"]["stats"] = {"testcases": 999}
        self.assertTrue(any("stats" in x for x in self.tool.validate(self.data)[0]))

    def test_malformed_containers_report_errors_not_crash(self):
        for key in ["features", "testcases", "requirements", "acceptance", "scenarios"]:
            with self.subTest(key=key):
                data = copy.deepcopy(self.data)
                data[key] = "wrong"
                self.assertTrue(self.tool.validate(data)[0])

    def test_malformed_nested_references_report_errors(self):
        mutations = [
            ("acceptance", "case_ids", None),
            ("testcases", "acceptance_ids", None),
            ("testcases", "feature_ids", 1),
            ("scenarios", "feature_ids", None),
        ]
        for section, field, value in mutations:
            with self.subTest(section=section, field=field):
                changed = copy.deepcopy(self.data)
                changed[section][0][field] = value
                self.assertTrue(self.tool.validate(changed)[0])

    def test_invalid_or_rule_requires_reason(self):
        self.data["acceptance"][0]["case_mode"] = "OR"
        self.assertTrue(any("alternative_reason" in x for x in self.tool.validate(self.data)[0]))

    def test_real_cli_chain_and_strict_gap_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "cases.yaml"
            source = ROOT / "examples" / "cases-example.json"
            def cli(*args):
                return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(cli("to-yaml", source, out).returncode, 0)
            self.assertEqual(cli("validate", out, "--strict").returncode, 0)
            self.assertEqual(cli("diff", source, out).returncode, 0)
            changed = json.loads(source.read_text(encoding="utf-8"))
            changed["testcases"][0]["title"] = "changed"
            bad = Path(tmp) / "changed.json"
            bad.write_text(json.dumps(changed), encoding="utf-8")
            self.assertEqual(cli("diff", bad, out).returncode, 1)
            changed["scenarios"][0]["paths"]["exception"] = [{"id": "PATH-X", "description": "uncovered"}]
            del changed["scenarios"][0]["not_applicable"]["exception"]
            bad.write_text(json.dumps(changed), encoding="utf-8")
            self.assertEqual(cli("validate", bad, "--strict").returncode, 1)
            bad.write_text("{", encoding="utf-8")
            self.assertEqual(cli("validate", bad).returncode, 2)

    def test_noncommittal_acceptance_rejected(self):
        self.data["acceptance"][0]["description"] = "正常"
        errors = self.tool.validate(self.data)[0]
        self.assertTrue(any("空话" in x for x in errors), errors)

    def test_noncommittal_assertion_check_rejected(self):
        self.data["testcases"][0]["assertions"][0]["check"] = "功能可用"
        errors = self.tool.validate(self.data)[0]
        self.assertTrue(any("assertions.check" in x for x in errors), errors)

    def test_placeholder_in_acceptance_rejected(self):
        self.data["acceptance"][0]["oracle"] = "TODO 补充观测入口"
        errors = self.tool.validate(self.data)[0]
        self.assertTrue(any("占位" in x for x in errors), errors)

    def test_missing_elements_is_strict_warning(self):
        del self.data["acceptance"][0]["elements"]
        errors, warnings = self.tool.validate(self.data)
        self.assertEqual(errors, [])
        self.assertTrue(any("elements" in x for x in warnings), warnings)

    def test_incomplete_elements_rejected(self):
        self.data["acceptance"][0]["elements"].pop("focus")
        errors = self.tool.validate(self.data)[0]
        self.assertTrue(any("elements.focus" in x for x in errors), errors)

    def test_vague_element_rejected(self):
        self.data["acceptance"][0]["elements"]["focus"] = "符合预期"
        errors = self.tool.validate(self.data)[0]
        self.assertTrue(any("elements.focus" in x for x in errors), errors)

    def test_unknown_element_key_rejected(self):
        self.data["acceptance"][0]["elements"]["owner"] = "张三"
        errors = self.tool.validate(self.data)[0]
        self.assertTrue(any("unknown keys" in x for x in errors), errors)

    def test_invalid_applicability_state_rejected(self):
        self.data["applicability"]["functional"]["state"] = "skip"
        errors = self.tool.validate(self.data)[0]
        self.assertTrue(any("applicability" in x for x in errors), errors)

    def test_not_applicable_requires_reason(self):
        self.data["applicability"]["security"]["reason"] = ""
        errors = self.tool.validate(self.data)[0]
        self.assertTrue(any("applicability.security.reason" in x for x in errors), errors)

    def test_unadjudicated_type_is_warning(self):
        del self.data["applicability"]["visual"]
        errors, warnings = self.tool.validate(self.data)
        self.assertEqual(errors, [])
        self.assertTrue(any("visual" in x for x in warnings), warnings)

    def test_excessive_smoke_is_warning(self):
        for tc in self.data["testcases"]:
            tc["suites"] = ["smoke"]
        errors, warnings = self.tool.validate(self.data)
        self.assertEqual(errors, [])
        self.assertTrue(any("smoke" in x for x in warnings), warnings)

    def test_method_claim_without_witness_rejected(self):
        self.data["testcases"][1]["classification_reason"] = "使用等价类划分覆盖合法与非法输入"
        self.data["testcases"][1]["data"] = {}
        errors = self.tool.validate(self.data)[0]
        self.assertTrue(any("设计方法" in x for x in errors), errors)


if __name__ == "__main__":
    unittest.main()
