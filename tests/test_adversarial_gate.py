"""IMP-09 回归：对抗补充门禁结论（adversarial_gate.evaluate 纯函数，离线夹具）。"""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_plan():
    return {
        "schema_version": "1.0",
        "plan_id": "ADVPLAN-GATE-01",
        "source_fingerprints": {"material": "a" * 64, "cases_json": "b" * 64},
        "surfaces": [
            {"id": "SURF-01", "entry": "导出", "role": "编辑者", "state": "已加载",
             "data": "3 行", "dependencies": "导出服务", "failure_hypothesis": "静默失败", "risk": "high"},
            {"id": "SURF-02", "entry": "审批", "role": "审批人", "state": "待审批",
             "data": "1 单", "dependencies": "审批服务", "failure_hypothesis": "驳回后状态残留", "risk": "medium"},
        ],
        "oracles": [
            {"id": "ORC-01", "source": "REQ", "observation_entry": "任务读回", "applicable_condition": "导出可用"},
        ],
        "obligations": [
            {"id": "ADV-01", "surface_ref": "SURF-01", "oracle_ref": "ORC-01",
             "probe_type": "fault_injection", "budget": "2 次", "status": "executed",
             "method": "fault_injection", "playbook_ref": "P6"},
            {"id": "ADV-02", "surface_ref": "SURF-02", "oracle_ref": "ORC-01",
             "probe_type": "challenge", "budget": "1 次", "status": "planned",
             "method": "boundary", "playbook_ref": "P7"},
        ],
    }


def executed_event():
    return {"event_id": "EVT-01", "type": "fault_injection", "probe_ref": "ADV-01",
            "observation": "注入后导出失败且有提示", "evidence_paths": ["e1.json"], "injected": True}


def build_scenarios():
    """对抗场景：cross_module 覆盖两个高风险面，满足场景门槛（审核 C5）。"""
    return [{
        "id": "SCN-ADV-01",
        "name": "导出与审批组合失效",
        "kind": "cross_module",
        "surfaces": ["SURF-01", "SURF-02"],
        "sequence": ["加载导出页", "切到审批人角色", "触发导出"],
        "expected_basis": "REQ-01 导出权限",
    }]


def empty_plan():
    return {"schema_version": "1.0", "plan_id": "ADVPLAN-EMPTY",
            "source_fingerprints": {"material": "a" * 64, "cases_json": "b" * 64},
            "surfaces": [], "oracles": [], "obligations": []}


class AdversarialGateTests(unittest.TestCase):
    def setUp(self):
        self.gate = load("adv_gate", "scripts/adversarial_gate.py")

    def test_all_green_passes_through(self):
        result = self.gate.evaluate(build_plan(), [executed_event()], [], "通过", scenarios=build_scenarios())
        self.assertEqual(result["verdict_ceiling"], "通过")
        self.assertTrue(result["adversarial_enabled"])
        self.assertEqual(result["reasons"], [])

    def test_high_risk_surface_without_probe_lowers_ceiling(self):
        plan = build_plan()
        plan["obligations"][0]["status"] = "planned"
        result = self.gate.evaluate(plan, [], [], "通过")
        self.assertEqual(result["verdict_ceiling"], "待核实")
        self.assertTrue(any("无对应已执行探针" in reason for reason in result["reasons"]))
        self.assertEqual(result["stats"]["high_risk_uncovered"], 1)

    def test_executed_obligation_without_event_is_not_covered(self):
        # executed 但无 EVT 佐证 → 同样视为未覆盖
        plan = build_plan()
        result = self.gate.evaluate(plan, [], [], "通过")
        self.assertEqual(result["verdict_ceiling"], "待核实")

    def test_unclosed_blocker_rejects(self):
        review = {"finding_id": "FIND-01", "verdict": "accepted", "reviewer": "砺衡",
                  "rationale": "判据成立", "severity": "blocker", "tie_to_bug": None}
        result = self.gate.evaluate(build_plan(), [executed_event()], [review], "通过", scenarios=build_scenarios())
        self.assertEqual(result["verdict_ceiling"], "不通过")

    def test_closed_blocker_does_not_reject(self):
        review = {"finding_id": "FIND-01", "verdict": "accepted", "reviewer": "砺衡",
                  "rationale": "已建缺陷", "severity": "major", "tie_to_bug": "BUG-01"}
        result = self.gate.evaluate(build_plan(), [executed_event()], [review], "通过", scenarios=build_scenarios())
        self.assertEqual(result["verdict_ceiling"], "通过")

    def test_unresolved_candidate_lowers_ceiling(self):
        review = {"finding_id": "FIND-02", "verdict": "unresolved", "reviewer": "砺衡",
                  "rationale": "证据不足"}
        result = self.gate.evaluate(build_plan(), [executed_event()], [review], "通过", scenarios=build_scenarios())
        self.assertEqual(result["verdict_ceiling"], "待核实")

    def test_empty_plan_enables_nothing_with_note(self):
        result = self.gate.evaluate(empty_plan(), [], [], "通过")
        self.assertFalse(result["adversarial_enabled"])
        self.assertEqual(result["verdict_ceiling"], "通过")
        self.assertTrue(any("未启用对抗" in note for note in result["notes"]))

    def test_never_relaxes_base_ceiling(self):
        # 基础 gate 已是待核实时，对抗全绿也不得抬高回"通过"
        result = self.gate.evaluate(build_plan(), [executed_event()], [], "待核实", scenarios=build_scenarios())
        self.assertEqual(result["verdict_ceiling"], "待核实")


class EnforceModeTests(unittest.TestCase):
    """G4 反例回归：对抗必选模式下，未建 plan 不得放行"通过"。"""

    def setUp(self):
        self.gate = load("adv_gate", "scripts/adversarial_gate.py")

    def test_enforce_empty_plan_without_waiver_lowers_ceiling(self):
        result = self.gate.evaluate(empty_plan(), [], [], "通过", enforce=True)
        self.assertEqual(result["verdict_ceiling"], "待核实")
        self.assertTrue(any("未提供豁免理由" in reason for reason in result["reasons"]))

    def test_enforce_missing_plan_without_waiver_lowers_ceiling(self):
        result = self.gate.evaluate(None, [], [], "通过", enforce=True)
        self.assertEqual(result["verdict_ceiling"], "待核实")

    def test_enforce_empty_plan_with_waiver_records_but_never_passes(self):
        # A1 修复（审核四）：豁免只留痕解除"未启用"阻断，不上抬上限——
        # 与台账"豁免不得自授即通过"对齐；ceiling 最高仍为待核实。
        result = self.gate.evaluate(empty_plan(), [], [], "通过", enforce=True,
                                    waiver="纯只读小任务，无写操作与高风险面")
        self.assertEqual(result["verdict_ceiling"], "待核实")
        self.assertTrue(result["waiver_granted"])
        self.assertTrue(any("对抗循环豁免" in note and "纯只读小任务" in note
                            and "不上抬上限" in note
                            for note in result["notes"]))

    def test_enforce_empty_plan_waiver_with_stricter_base_keeps_stricter(self):
        # 基础上限已是"不通过"时，豁免不得把上限放松
        result = self.gate.evaluate(empty_plan(), [], [], "不通过", enforce=True,
                                    waiver="豁免")
        self.assertEqual(result["verdict_ceiling"], "不通过")

    def test_enforce_does_not_relax_base_ceiling(self):
        result = self.gate.evaluate(empty_plan(), [], [], "待核实", enforce=True,
                                    waiver="豁免")
        self.assertEqual(result["verdict_ceiling"], "待核实")

    def test_enforce_nonempty_plan_ignores_waiver(self):
        # plan 非空时豁免不参与判定，正常按风险面覆盖计算
        result = self.gate.evaluate(build_plan(), [executed_event()], [], "通过",
                                    enforce=True, waiver="不应生效", scenarios=build_scenarios())
        self.assertEqual(result["verdict_ceiling"], "通过")
        self.assertTrue(result["adversarial_enabled"])
        self.assertFalse(result["waiver_granted"])


if __name__ == "__main__":
    unittest.main()


class CoverageGapTests(unittest.TestCase):
    """复审 H2/H4 回归：探针未注明方法、或方法/模板单一，必须被门禁识别。"""

    def setUp(self):
        self.gate = load("adv_gate", "scripts/adversarial_gate.py")

    def test_obligation_without_method_lowers_ceiling(self):
        plan = build_plan()
        for obligation in plan["obligations"]:
            obligation.pop("method", None)
            obligation.pop("playbook_ref", None)
        result = self.gate.evaluate(plan, [executed_event()], [], "通过")
        self.assertEqual(result["verdict_ceiling"], "待核实")
        self.assertTrue(any("未注明方法/挑刺模板" in r for r in result["reasons"]))
        self.assertEqual(result["stats"]["obligations_without_method"], 2)

    def test_playbook_ref_alone_is_enough(self):
        plan = build_plan()
        for obligation in plan["obligations"]:
            obligation.pop("method", None)
        result = self.gate.evaluate(plan, [executed_event()], [], "通过",
                                    scenarios=build_scenarios())
        self.assertEqual(result["verdict_ceiling"], "通过")


class ScenarioGapTests(unittest.TestCase):
    """审核 C5 回归：零场景/单点伪装成场景，必须被门禁识别。"""

    def setUp(self):
        self.gate = load("adv_gate", "scripts/adversarial_gate.py")

    def test_no_scenarios_lowers_ceiling(self):
        result = self.gate.evaluate(build_plan(), [executed_event()], [], "通过")
        self.assertEqual(result["verdict_ceiling"], "待核实")
        self.assertTrue(any("对抗场景缺口" in r for r in result["reasons"]))
        self.assertEqual(result["stats"]["scenarios"], 0)

    def test_scenarios_present_passes(self):
        result = self.gate.evaluate(build_plan(), [executed_event()], [], "通过",
                                    scenarios=build_scenarios())
        self.assertEqual(result["verdict_ceiling"], "通过")
        self.assertEqual(result["stats"]["scenarios"], 1)

    def test_high_surface_not_covered_by_scenario_is_gap(self):
        # 把 SURF-02 提到 high：此时只覆盖 SURF-01 的场景必须被判为缺口
        plan = build_plan()
        plan["surfaces"][1]["risk"] = "high"
        partial = [{"id": "SCN-ADV-01", "kind": "operation_sequence",
                    "surfaces": ["SURF-01"], "sequence": ["a", "b"]}]
        result = self.gate.evaluate(plan, [executed_event()], [], "通过",
                                    scenarios=partial)
        self.assertEqual(result["verdict_ceiling"], "待核实")
        self.assertTrue(any("未被任何场景覆盖" in r for r in result["reasons"]))
