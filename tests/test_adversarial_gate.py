"""IMP-09 回归：对抗补充门禁结论（adversarial_gate.evaluate 纯函数，离线夹具）。"""
import importlib.util
import json
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

    def test_empty_plan_enabled_by_default_lowers_ceiling(self):
        # D7：对抗门禁默认启用。空 plan 且无豁免理由时上限为待核实——
        # 门禁默认生效，不再依赖使用方记得传 --enforce。
        result = self.gate.evaluate(empty_plan(), [], [], "通过")
        self.assertFalse(result["adversarial_enabled"])
        self.assertEqual(result["verdict_ceiling"], "待核实")
        self.assertTrue(any("对抗循环未启用" in reason for reason in result["reasons"]))

    def test_explicitly_disabled_gate_passes_empty_plan(self):
        # 显式关闭门禁是保留路径：关闭状态与理由必须可见（不得静默关闭），
        # 且关闭态不得自证"通过"——台账侧对 disabled 状态一律压"待核实"，
        # 门禁若自报"通过"，同一批产物会出现两个上限。
        result = self.gate.evaluate(empty_plan(), [], [], "通过", enforce=False,
                                    disable_reason="本轮只做文档核对，不产生技术结论")
        self.assertEqual(result["verdict_ceiling"], "待核实")
        self.assertTrue(result["adversarial_disabled"])
        self.assertEqual(result["disable_reason"], "本轮只做文档核对，不产生技术结论")
        self.assertTrue(any("对抗门禁未启用" in note for note in result["notes"]))

    def test_disabled_gate_without_reason_still_warns(self):
        # 未给关闭理由时仍要留痕提醒：交付物必须声明未启用及理由。
        result = self.gate.evaluate(empty_plan(), [], [], "通过", enforce=False)
        self.assertEqual(result["verdict_ceiling"], "待核实")
        self.assertTrue(result["adversarial_disabled"])
        self.assertIsNone(result["disable_reason"])
        self.assertTrue(any("须在交付物声明" in note for note in result["notes"]))

    def test_disabled_gate_never_raises_above_base_ceiling(self):
        # 关闭态取更严者：基础 gate 已是"不通过"时，关闭状态不得把它降级。
        result = self.gate.evaluate(empty_plan(), [], [], "不通过", enforce=False,
                                    disable_reason="只读核对")
        self.assertEqual(result["verdict_ceiling"], "不通过")

    def test_never_relaxes_base_ceiling(self):
        # 基础 gate 已是待核实时，对抗全绿也不得抬高回"通过"
        result = self.gate.evaluate(build_plan(), [executed_event()], [], "待核实", scenarios=build_scenarios())
        self.assertEqual(result["verdict_ceiling"], "待核实")


class LatestReviewTests(unittest.TestCase):
    """复核 JSONL 为追加写入，同一 finding 多条记录时判定只认最新一条。

    逐条判会把历史记录永久压上限：先 accepted 未闭环、补建 BUG 后追加
    accepted+tie 的候选会被误判为始终未闭环，已闭环候选永远洗不白。
    """

    def setUp(self):
        self.gate = load("adv_gate_latest", "scripts/adversarial_gate.py")

    def test_stale_unclosed_accepted_is_superseded_by_later_tied_review(self):
        stale = {"finding_id": "FIND-01", "verdict": "accepted", "severity": "blocker",
                 "tie_to_bug": None}
        latest = {"finding_id": "FIND-01", "verdict": "accepted", "severity": "blocker",
                  "tie_to_bug": "BUG-01"}
        result = self.gate.evaluate(build_plan(), [executed_event()], [stale, latest],
                                    "通过", scenarios=build_scenarios())
        self.assertEqual(result["verdict_ceiling"], "通过")

    def test_stale_unresolved_is_superseded_by_later_closed_review(self):
        stale = {"finding_id": "FIND-02", "verdict": "unresolved"}
        latest = {"finding_id": "FIND-02", "verdict": "accepted", "severity": "major",
                  "tie_to_bug": "BUG-02"}
        result = self.gate.evaluate(build_plan(), [executed_event()], [stale, latest],
                                    "通过", scenarios=build_scenarios())
        self.assertEqual(result["verdict_ceiling"], "通过")

    def test_latest_rejected_supersedes_stale_unclosed_accepted(self):
        stale = {"finding_id": "FIND-03", "verdict": "accepted", "severity": "major",
                 "tie_to_bug": None}
        latest = {"finding_id": "FIND-03", "verdict": "rejected", "rationale": "误报"}
        result = self.gate.evaluate(build_plan(), [executed_event()], [stale, latest],
                                    "通过", scenarios=build_scenarios())
        self.assertEqual(result["verdict_ceiling"], "通过")

    def test_latest_unclosed_review_still_rejects_when_order_reversed(self):
        # 反向：追加顺序决定最新裁定（列表末条是最新）。最新一条仍未闭环时
        # 必须照压——latest-wins 不是"任一闭环即闭环"。
        stale = {"finding_id": "FIND-04", "verdict": "rejected", "rationale": "旧判"}
        latest = {"finding_id": "FIND-04", "verdict": "accepted", "severity": "major",
                  "tie_to_bug": None}
        result = self.gate.evaluate(build_plan(), [executed_event()], [stale, latest],
                                    "通过", scenarios=build_scenarios())
        self.assertEqual(result["verdict_ceiling"], "不通过")

    def test_empty_plan_message_states_actual_ceiling(self):
        # 文案必须与实际 ceiling 一致：基础 gate 已是"不通过"时空 plan 的
        # reason 不得再写"结论上限为待核实"。
        result = self.gate.evaluate(empty_plan(), [], [], "不通过")
        self.assertEqual(result["verdict_ceiling"], "不通过")
        self.assertTrue(any("本轮结论上限为不通过" in reason for reason in result["reasons"]))

    def test_waiver_note_states_actual_ceiling(self):
        result = self.gate.evaluate(empty_plan(), [], [], "不通过",
                                    waiver="只读小任务")
        self.assertEqual(result["verdict_ceiling"], "不通过")
        self.assertTrue(any("本轮 verdict_ceiling 为 不通过" in note for note in result["notes"]))


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


class SamplingReviewTests(unittest.TestCase):
    """D7 回归：抽样复核查「该登记却没登记」，登记豁免只解除单项阻断、不上抬上限。"""

    def setUp(self):
        self.gate = load("adv_gate", "scripts/adversarial_gate.py")

    def test_missing_scope_is_not_reported_as_sampling_passed(self):
        # 清单缺失 ≠ 抽样通过：必须显式记为未执行，不能显示成"查过了没问题"。
        result = self.gate.evaluate(build_plan(), [executed_event()], [], "通过",
                                    scenarios=build_scenarios())
        self.assertEqual(result["sampling"]["status"], "not_provided")
        self.assertTrue(any("未执行抽样复核" in note for note in result["notes"]))

    def test_sampled_registered_surface_keeps_ceiling(self):
        scope = [{"id": "SURF-01"}, {"id": "SURF-02"}]
        result = self.gate.evaluate(build_plan(), [executed_event()], [], "通过",
                                    scenarios=build_scenarios(), scope=scope)
        self.assertEqual(result["sampling"]["status"], "ok")
        self.assertEqual(result["verdict_ceiling"], "通过")

    def test_sampled_unregistered_surface_lowers_ceiling(self):
        # 抽中未登记的功能面且无豁免说明 → 待核实。
        # 用足量候选确保抽中 SURF-03（清单里未登记的那个）。
        scope = [{"id": "SURF-01"}, {"id": "SURF-02"}, {"id": "SURF-03"},
                 {"id": "SURF-04"}, {"id": "SURF-05"}, {"id": "SURF-06"}]
        result = self.gate.evaluate(build_plan(), [executed_event()], [], "通过",
                                    scenarios=build_scenarios(), scope=scope,
                                    sample_size=6, sample_seed=7)
        self.assertEqual(result["sampling"]["status"], "gap")
        self.assertTrue(any("抽样复核抽中未登记的功能面" in r for r in result["reasons"]))
        self.assertEqual(result["verdict_ceiling"], "待核实")

    def test_exemption_clears_sampling_block_but_not_other_rules(self):
        # 豁免解除抽样对单项的阻断，但不上抬其它规则已压低的上限。
        scope = [{"id": "SURF-03"}]
        plan = build_plan()
        plan["obligations"][0]["status"] = "planned"  # 另有规则压上限
        result = self.gate.evaluate(plan, [], [], "通过", scenarios=build_scenarios(),
                                    scope=scope, exemptions={"SURF-03": "纯静态展示，无状态迁移"})
        self.assertEqual(result["sampling"]["status"], "exempted")
        self.assertEqual(result["sampling"]["exempted"][0]["reason"], "纯静态展示，无状态迁移")
        self.assertFalse(any("抽样复核抽中未登记" in r for r in result["reasons"]))
        self.assertTrue(any("登记豁免" in note and "纯静态展示" in note
                            for note in result["notes"]))
        self.assertEqual(result["verdict_ceiling"], "待核实")

    def test_exemption_does_not_raise_ceiling_above_base(self):
        scope = [{"id": "SURF-03"}]
        result = self.gate.evaluate(build_plan(), [executed_event()], [], "待核实",
                                    scenarios=build_scenarios(), scope=scope,
                                    exemptions={"SURF-03": "纯静态展示"})
        self.assertEqual(result["verdict_ceiling"], "待核实")

    def test_sampling_is_stable_across_reruns(self):
        # 同轮重跑复用同一批样本：种子未给时由清单内容派生。
        scope = [{"id": "SURF-01"}, {"id": "SURF-03"}, {"id": "SURF-05"}]
        first = self.gate.evaluate(build_plan(), [executed_event()], [], "通过",
                                   scenarios=build_scenarios(), scope=scope, sample_size=1)
        second = self.gate.evaluate(build_plan(), [executed_event()], [], "通过",
                                    scenarios=build_scenarios(), scope=scope, sample_size=1)
        self.assertEqual(first["sampling"]["sampled"], second["sampling"]["sampled"])
        self.assertEqual(first["sampling"]["sample_seed"], second["sampling"]["sample_seed"])

    def test_scope_smaller_than_sample_size_takes_all(self):
        scope = [{"id": "SURF-01"}, {"id": "SURF-03"}]
        result = self.gate.evaluate(build_plan(), [executed_event()], [], "通过",
                                    scenarios=build_scenarios(), scope=scope, sample_size=5)
        self.assertEqual(result["sampling"]["sample_size"], 2)

    def test_disabled_gate_reports_sampling_without_lowering_ceiling(self):
        # 门禁显式关闭时抽样只作披露，不因抽样再压上限；
        # 但关闭态本身仍把上限压到"待核实"（与台账侧口径一致）。
        scope = [{"id": "SURF-03"}]
        result = self.gate.evaluate(build_plan(), [executed_event()], [], "通过",
                                    scenarios=build_scenarios(), scope=scope,
                                    enforce=False, disable_reason="只读核对")
        self.assertEqual(result["verdict_ceiling"], "待核实")
        self.assertTrue(any("仅作披露" in note for note in result["notes"]))
        # 抽样本身没有压上限（压的是关闭态）
        self.assertFalse(any("抽样复核抽中未登记" in r for r in result["reasons"]))

    def test_sampling_distribution_is_not_skewed(self):
        # 抽样位置分布须大致均匀：此前的 LCG 洗牌低阶位周期极短，
        # 实测某一位只占 5.7%（期望 20%），抽样就永远查不到那一类问题。
        picks = {}
        for i in range(400):
            scope = [{"id": f"FP-{i}-{j}", "surf_id": f"FP-{i}-{j}"} for j in range(5)]
            picked, _ = self.gate._sample_entries(scope, 1, None)
            key = picked[0]["id"].rsplit("-", 1)[-1]
            picks[key] = picks.get(key, 0) + 1
        # 期望各 80；卡宽松阈值以免偶发抖动误判，但须挡住 5.7% 的严重偏斜
        for key in ("0", "1", "2", "3", "4"):
            self.assertGreater(picks.get(key, 0), 40, f"位置 {key} 抽中过少：{picks}")

    def test_sampling_is_independent_of_scope_order(self):
        # 同一组功能面无论清单如何排列都抽到同一批：仅调顺序不应换掉样本。
        scope = [{"id": f"SURF-0{j}", "surf_id": f"SURF-0{j}"} for j in range(1, 6)]
        first, _ = self.gate._sample_entries(scope, 2, None)
        second, _ = self.gate._sample_entries(list(reversed(scope)), 2, None)
        self.assertEqual(sorted(e["id"] for e in first),
                         sorted(e["id"] for e in second))

    def test_sampling_never_repeats_an_entry(self):
        scope = [{"id": f"SURF-0{j}", "surf_id": f"SURF-0{j}"} for j in range(1, 6)]
        picked, _ = self.gate._sample_entries(scope, 3, None)
        self.assertEqual(len(picked), 3)
        self.assertEqual(len({e["id"] for e in picked}), 3)

    def test_sample_size_zero_samples_nothing(self):
        scope = [{"id": "SURF-01"}, {"id": "SURF-03"}]
        result = self.gate.evaluate(build_plan(), [executed_event()], [], "通过",
                                    scenarios=build_scenarios(), scope=scope, sample_size=0)
        self.assertEqual(result["sampling"]["sample_size"], 0)
        self.assertEqual(result["sampling"]["sampled"], [])
        self.assertEqual(result["sampling"]["status"], "ok")


class SamplingCliTests(unittest.TestCase):
    """CLI 回归：--no-enforce 必须带理由；scope/exemptions 可从文件读入。"""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.gate = load("adv_gate", "scripts/adversarial_gate.py")

    def _write(self, name, payload):
        path = Path(self.tmp.name) / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def test_no_enforce_without_reason_is_rejected(self):
        code = self.gate.main(["--no-enforce"])
        self.assertEqual(code, 2)

    def test_enforce_and_no_enforce_are_mutually_exclusive(self):
        code = self.gate.main(["--enforce", "--no-enforce", "--disable-reason", "x"])
        self.assertEqual(code, 2)

    def test_scope_and_exemptions_from_files(self):
        scope_path = self._write("scope.json", [{"id": "SURF-01"}, {"id": "SURF-03"}])
        ex_path = self._write("exemptions.json", [{"id": "SURF-03", "reason": "纯静态展示"}])
        code = self.gate.main(["--scope", scope_path, "--exemptions", ex_path,
                               "--sample-size", "2", "--sample-seed", "1"])
        self.assertEqual(code, 0)


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


class CeilingSignalTests(unittest.TestCase):
    """档位必须来自结构化 flag，不能靠扫描 reason 文案里的「待核实/不通过」反推。

    文案改写（去掉档位字样）不应影响上限；守卫源码防止回退到字符串扫描。
    """

    def setUp(self):
        self.gate = load("adv_gate_ceiling", "scripts/adversarial_gate.py")

    def test_ceiling_not_derived_from_reason_string_scan(self):
        source = (ROOT / "scripts" / "adversarial_gate.py").read_text(encoding="utf-8")
        self.assertNotIn('"不通过" in reason', source)
        self.assertNotIn('"待核实" in reason', source)

    def test_unclosed_blocker_flag_suppresses_ceiling(self):
        review = {"finding_id": "FIND-01", "verdict": "accepted", "severity": "blocker",
                  "tie_to_bug": None}
        result = self.gate.evaluate(build_plan(), [executed_event()], [review], "通过",
                                    scenarios=build_scenarios())
        self.assertEqual(result["verdict_ceiling"], "不通过")

    def test_single_method_across_high_risk_surfaces_lowers_ceiling(self):
        # 多样性缺口（方法单一）同样必须登记待核实 flag：3 个高风险面、三条义务
        # 都已执行且有事件佐证、场景覆盖三面，唯一缺口是方法种类不足——上限仍须压待核实。
        plan = {
            "schema_version": "1.0",
            "plan_id": "ADVPLAN-DIV-01",
            "source_fingerprints": {"material": "a" * 64, "cases_json": "b" * 64},
            "surfaces": [
                {"id": "SURF-01", "entry": "导出", "role": "编辑者", "state": "已加载",
                 "data": "3 行", "dependencies": "导出服务", "failure_hypothesis": "静默失败", "risk": "high"},
                {"id": "SURF-02", "entry": "审批", "role": "审批人", "state": "待审批",
                 "data": "1 单", "dependencies": "审批服务", "failure_hypothesis": "状态残留", "risk": "high"},
                {"id": "SURF-03", "entry": "列表", "role": "查看者", "state": "已加载",
                 "data": "10 行", "dependencies": "列表服务", "failure_hypothesis": "陈旧值", "risk": "high"},
            ],
            "oracles": [{"id": "ORC-01", "source": "REQ", "observation_entry": "任务读回",
                          "applicable_condition": "可用"}],
            "obligations": [
                {"id": "ADV-01", "surface_ref": "SURF-01", "oracle_ref": "ORC-01",
                 "probe_type": "challenge", "budget": "1 次", "status": "executed",
                 "method": "boundary", "playbook_ref": "P6"},
                {"id": "ADV-02", "surface_ref": "SURF-02", "oracle_ref": "ORC-01",
                 "probe_type": "challenge", "budget": "1 次", "status": "executed",
                 "method": "boundary", "playbook_ref": "P7"},
                {"id": "ADV-03", "surface_ref": "SURF-03", "oracle_ref": "ORC-01",
                 "probe_type": "challenge", "budget": "1 次", "status": "executed",
                 "method": "boundary", "playbook_ref": "P8"},
            ],
        }
        events = [
            {"event_id": "EVT-01", "type": "challenge", "probe_ref": "ADV-01",
             "observation": "已挑战", "evidence_paths": ["e1.json"]},
            {"event_id": "EVT-02", "type": "challenge", "probe_ref": "ADV-02",
             "observation": "已挑战", "evidence_paths": ["e2.json"]},
            {"event_id": "EVT-03", "type": "challenge", "probe_ref": "ADV-03",
             "observation": "已挑战", "evidence_paths": ["e3.json"]},
        ]
        scenarios = [{"id": "SCN-ADV-01", "name": "三面组合失效", "kind": "cross_module",
                      "surfaces": ["SURF-01", "SURF-02", "SURF-03"],
                      "sequence": ["加载", "切换", "触发"], "expected_basis": "REQ-01"}]
        result = self.gate.evaluate(plan, events, [], "通过", scenarios=scenarios)
        self.assertEqual(result["verdict_ceiling"], "待核实")
        self.assertTrue(any("方法过于单一" in r for r in result["reasons"]))

    def test_every_gating_reason_carries_matching_flag(self):
        # 不变量：evaluate 内每条带明确档位字样的压档 reason，必须登记同档位 flag；
        # 防止以后新增压档规则时只写 reason、漏加 flag 导致上限静默变松。
        source = (ROOT / "scripts" / "adversarial_gate.py").read_text(encoding="utf-8")
        lines = source.splitlines()
        start = next(i for i, line in enumerate(lines) if line.startswith("def evaluate("))
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("def ")),
                   len(lines))
        block = lines[start:end]
        reason_wait = sum(1 for line in block
                          if "结论上限为待核实" in line and not line.strip().startswith("#"))
        flag_wait = sum(1 for line in block if 'ceiling_flags.append("待核实")' in line)
        reason_fail = sum(1 for line in block
                           if "结论上限为不通过" in line and not line.strip().startswith("#"))
        flag_fail = sum(1 for line in block if 'ceiling_flags.append("不通过")' in line)
        # 抽样 reason 由 extend 引入，其 flag 按 status==gap 登记，故待核实 flag 数 >= append 数
        self.assertGreaterEqual(flag_wait, reason_wait)
        self.assertEqual(flag_fail, reason_fail)
        if any("reasons.extend(sampling_reasons)" in line for line in block):
            self.assertTrue(any('sampling["status"] == "gap"' in line for line in block))
            self.assertGreaterEqual(flag_wait, 1)
