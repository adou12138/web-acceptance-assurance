"""IMP-06 回归：adversarial-plan 契约校验（adversarial_data.py，离线夹具）。"""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_plan(**overrides):
    plan = {
        "schema_version": "1.0",
        "plan_id": "ADVPLAN-TEST-01",
        "source_fingerprints": {
            "material": "a" * 64,
            "cases_json": "b" * 64,
        },
        "surfaces": [
            {"id": "SURF-01", "entry": "订单列表页导出按钮", "role": "编辑者", "state": "已加载",
             "data": "3 行测试订单", "dependencies": "导出服务", "failure_hypothesis": "导出静默失败", "risk": "high"},
        ],
        "oracles": [
            {"id": "ORC-01", "source": "REQ", "source_ref": "REQ-01",
             "observation_entry": "导出任务状态与文件读回", "applicable_condition": "导出按钮可用时"},
        ],
        "obligations": [
            {"id": "ADV-01", "surface_ref": "SURF-01", "oracle_ref": "ORC-01",
             "probe_type": "fault_injection", "budget": "2 次注入", "status": "planned"},
        ],
    }
    for key, value in overrides.items():
        if value is None:
            plan.pop(key, None)
        else:
            plan[key] = value
    return plan


class AdversarialDataTests(unittest.TestCase):
    def setUp(self):
        self.tool = load("adv_data", "scripts/adversarial_data.py")

    def test_valid_plan_passes(self):
        summary = self.tool.validate(build_plan())
        self.assertEqual(summary["surfaces"], 1)
        self.assertEqual(summary["obligations"], 1)
        self.assertEqual(summary["high_risk_surfaces"], 1)

    def test_unknown_surface_reference_rejected(self):
        plan = build_plan(obligations=[
            {"id": "ADV-01", "surface_ref": "SURF-99", "oracle_ref": "ORC-01",
             "probe_type": "challenge", "budget": "1 次挑战", "status": "planned"},
        ])
        with self.assertRaises(ValueError):
            self.tool.validate(plan)

    def test_unknown_oracle_reference_rejected(self):
        plan = build_plan(obligations=[
            {"id": "ADV-01", "surface_ref": "SURF-01", "oracle_ref": "ORC-99",
             "probe_type": "challenge", "budget": "1 次挑战", "status": "planned"},
        ])
        with self.assertRaises(ValueError):
            self.tool.validate(plan)

    def test_duplicate_id_rejected(self):
        plan = build_plan(surfaces=[
            {"id": "SURF-01", "entry": "A", "role": "r", "state": "s",
             "data": "d", "dependencies": "x", "failure_hypothesis": "h", "risk": "high"},
            {"id": "SURF-01", "entry": "B", "role": "r", "state": "s",
             "data": "d", "dependencies": "x", "failure_hypothesis": "h", "risk": "low"},
        ])
        with self.assertRaises(ValueError):
            self.tool.validate(plan)

    def test_fingerprint_missing_and_malformed_rejected(self):
        plan = build_plan(source_fingerprints={"material": "a" * 64})
        with self.assertRaises(ValueError):
            self.tool.validate(plan)
        plan = build_plan(source_fingerprints={"material": "xyz", "cases_json": "b" * 64})
        with self.assertRaises(ValueError):
            self.tool.validate(plan)

    def test_fingerprint_mismatch_rejected_with_real_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            material = Path(tmp) / "requirement.md"
            material.write_text("需求正文 v1", encoding="utf-8")
            cases = Path(tmp) / "cases.json"
            cases.write_text(json.dumps({"testcases": []}, ensure_ascii=False), encoding="utf-8")
            plan = build_plan(source_fingerprints={
                "material": self.tool.fingerprint(material),
                "cases_json": self.tool.fingerprint(cases),
            })
            self.tool.validate(plan, material_path=material, cases_json_path=cases)
            material.write_text("需求正文 v2（已变更）", encoding="utf-8")
            with self.assertRaises(ValueError):
                self.tool.validate(plan, material_path=material, cases_json_path=cases)

    def test_oracle_missing_source_rejected(self):
        plan = build_plan(oracles=[
            {"id": "ORC-01", "observation_entry": "读回", "applicable_condition": "任意"},
        ])
        with self.assertRaises(ValueError):
            self.tool.validate(plan)
        plan = build_plan(oracles=[
            {"id": "ORC-01", "source": "model_guess", "observation_entry": "读回", "applicable_condition": "任意"},
        ])
        with self.assertRaises(ValueError):
            self.tool.validate(plan)

    def test_empty_surfaces_rejected(self):
        plan = build_plan(surfaces=[])
        with self.assertRaises(ValueError):
            self.tool.validate(plan)
        plan = build_plan(surfaces=None)
        with self.assertRaises(ValueError):
            self.tool.validate(plan)

    def test_load_rejects_duplicate_json_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan.json"
            path.write_text('{"schema_version": "1.0", "schema_version": "1.1"}', encoding="utf-8")
            with self.assertRaises(ValueError):
                self.tool.load(path)

    def test_playbook_templates_p21_to_p24_accepted(self):
        # P21~P24（状态组合/控件遍历/文案一致性/断言杀伤）是模板库在册模板，
        # 诚实引用不得被枚举拒绝（此前枚举停在 P20，这四类探针无法合法登记）。
        for template in ("P21", "P22", "P23", "P24"):
            plan = build_plan(obligations=[
                {"id": "ADV-01", "surface_ref": "SURF-01", "oracle_ref": "ORC-01",
                 "probe_type": "challenge", "budget": "1 次挑战", "status": "planned",
                 "playbook_ref": template},
            ])
            self.tool.validate(plan)

    def test_unknown_playbook_template_rejected(self):
        plan = build_plan(obligations=[
            {"id": "ADV-01", "surface_ref": "SURF-01", "oracle_ref": "ORC-01",
             "probe_type": "challenge", "budget": "1 次挑战", "status": "planned",
             "playbook_ref": "P99"},
        ])
        with self.assertRaises(ValueError):
            self.tool.validate(plan)

    def test_playbook_templates_sync_with_playbook_doc(self):
        # 文档 A 节列出的模板编号必须与代码枚举双向一致：加新模板时同步枚举，
        # 防止模板库扩了、枚举没跟上导致诚实引用被拒。
        doc = (ROOT / "references" / "probe-playbook.md").read_text(encoding="utf-8")
        doc_templates = {
            token for line in doc.splitlines()
            if (token := line.strip().removeprefix("|").strip().split("|")[0].strip())
            and token.startswith("P") and token[1:].isdigit()
        }
        self.assertEqual(self.tool.PLAYBOOK_TEMPLATES, doc_templates)


if __name__ == "__main__":
    unittest.main()
