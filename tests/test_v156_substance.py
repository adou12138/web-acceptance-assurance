# -*- coding: utf-8 -*-
"""v1.56 实质修正批次测试（S1-S10）。

来源：expert-improvement-plan-v2.md 实质审核的修正项——
S1 direction=n/a 须理由；S2 持久化面零一致性路径；S3 逐轴覆盖；
S4 计划参数与实执行对照（F2）；S5 绕径驱动；S6 跨视图同语义量分组；
S7 not_applicable 交叉核验；S8 清单判据下限；S9 数据形状；S10 交付-台账绑定。
"""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG_ROOT / "scripts"))

import adversarial_data as ad  # noqa: E402
import attack_coverage as ac  # noqa: E402
import case_data  # noqa: E402
import copy_consistency as cc  # noqa: E402
import coverage_matrix as cm  # noqa: E402
import execution_ledger as el  # noqa: E402
import phase_gate as pg  # noqa: E402


def _load_sample():
    return json.loads((PKG_ROOT / "skills" / "case-generator" / "tests" /
                       "fixtures" / "cases-sample-12.json").read_text(encoding="utf-8-sig"))


def _record(**overrides):
    rec = {"kind": "FINISH", "attempt_id": "a1", "case_id": "TC-1",
           "path_type": "boundary", "surf_id": "SURF-01",
           "method": "boundary", "oracle_source": "independent",
           "oracle_channel": "第三方账单系统", "oracle_scope": "cross_system",
           "discovery_mode": "designed_probe",
           "hypothesis": "如果输入超过上界则拒绝",
           "hypothesis_outcome": "held",
           "evidence": [{"path": "e.json", "sha256": "x"}]}
    rec.update(overrides)
    return rec


class S1DirectionNAReason(unittest.TestCase):
    """S1：direction=n/a 必须附 direction_reason；其他方向不需要。"""

    def _surface(self, **extra):
        base = {"id": "SURF-01", "entry": "分配弹窗", "role": "管理员", "state": "开启",
                "data": "余额 500", "dependencies": "分配服务",
                "failure_hypothesis": "回收被伪上界拦截", "risk": "high",
                "direction": "n/a"}
        base.update(extra)
        return {"schema_version": "1.0", "plan_id": "ADVPLAN-T1",
                "source_fingerprints": {"material": "a" * 64, "cases_json": "b" * 64},
                "surfaces": [base],
                "oracles": [{"id": "ORC-01", "source": "REQ", "source_ref": "REQ-1",
                             "observation_entry": "o", "applicable_condition": "c"}],
                "obligations": [{"id": "ADV-01", "surface_ref": "SURF-01",
                                 "oracle_ref": "ORC-01", "probe_type": "fault_injection",
                                 "budget": "2", "status": "planned"}]}

    def test_na_without_reason_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            ad.validate(self._surface())
        self.assertIn("direction_reason", str(ctx.exception))

    def test_na_with_reason_accepted(self):
        plan = self._surface(direction_reason="该面为只读展示，不存在输入校验方向")
        summary = ad.validate(plan)
        self.assertEqual(summary["high_risk_surfaces"], 1)

    def test_increase_without_reason_ok(self):
        plan = self._surface(direction="increase")
        ad.validate(plan)  # 不抛


class S2ConsistencyPathAbsent(unittest.TestCase):
    """S2：persistence 面 + 零 data_consistency → 缺口；已声明且零 → 缺口；
    无声明且零 → 披露；有记录 → 通过。"""

    def _run(self, records, persistence):
        return ac.analyse(records, plan_surfaces={"SURF-01"},
                          plan_risks={"SURF-01": "high"},
                          plan_meta={"persistence": persistence or {},
                                     "axes": {}, "probe_inputs": {}})

    def test_persistence_without_dc_is_gap(self):
        result = self._run([_record()], persistence={"SURF-01": True})
        codes = [g["code"] for g in result["gaps"]]
        self.assertIn("consistency_path_absent", codes, f"gaps={codes}")

    def test_persistence_with_dc_passes(self):
        rec = _record(path_type="data_consistency", method="consistency_check")
        result = self._run([rec], persistence={"SURF-01": True})
        codes = [g["code"] for g in result["gaps"]]
        self.assertNotIn("consistency_path_absent", codes)

    def test_no_claim_no_dc_is_disclosure(self):
        result = self._run([_record()], persistence={})
        codes = [g["code"] for g in result["gaps"]]
        self.assertNotIn("consistency_path_absent", codes)
        notice_codes = [n["code"] for n in result.get("notices", [])]
        self.assertIn("consistency_path_unclaimed", notice_codes)


class S3AxisCoverage(unittest.TestCase):
    """S3：声明轴中零记录的轴 → 缺口；单轴面 surf_id 匹配即覆盖。"""

    def test_missing_axis_is_gap(self):
        records = [_record(surf_id="SURF-01", axis="短信"),
                   _record(attempt_id="a2", case_id="TC-2", surf_id="SURF-01", axis="短信")]
        result = ac.analyse(records, plan_surfaces={"SURF-01"},
                            plan_risks={"SURF-01": "high"},
                            plan_meta={"persistence": {}, "axes": {},
                                       "probe_inputs": {}})
        # axes 经 plan_meta 传入
        result2 = ac.analyse(records, plan_surfaces={"SURF-01"},
                             plan_risks={"SURF-01": "high"},
                             plan_meta={"persistence": {},
                                        "axes": {"SURF-01": ["短信", "OCR", "多媒体"]},
                                        "probe_inputs": {}})
        codes = [g["code"] for g in result2["gaps"]]
        self.assertIn("axis_uncovered", codes, f"gaps={codes}")
        msg = next(g["message"] for g in result2["gaps"] if g["code"] == "axis_uncovered")
        self.assertIn("OCR", msg)
        self.assertIn("多媒体", msg)
        # 对照：全部轴都有记录时不报
        records_all = records + [_record(attempt_id="a3", case_id="TC-3",
                                         surf_id="SURF-01", axis="OCR"),
                                 _record(attempt_id="a4", case_id="TC-4",
                                         surf_id="SURF-01", axis="多媒体")]
        result3 = ac.analyse(records_all, plan_surfaces={"SURF-01"},
                             plan_risks={"SURF-01": "high"},
                             plan_meta={"persistence": {},
                                        "axes": {"SURF-01": ["短信", "OCR", "多媒体"]},
                                        "probe_inputs": {}})
        self.assertNotIn("axis_uncovered", [g["code"] for g in result3["gaps"]])

    def test_single_axis_matches_by_surf_id(self):
        records = [_record(surf_id="SURF-01")]  # 无 axis 字段
        result = ac.analyse(records, plan_surfaces={"SURF-01"},
                            plan_risks={"SURF-01": "high"},
                            plan_meta={"persistence": {},
                                       "axes": {"SURF-01": ["唯一资源"]},
                                       "probe_inputs": {}})
        self.assertNotIn("axis_uncovered", [g["code"] for g in result["gaps"]])


class S4ProbeInputMismatch(unittest.TestCase):
    """S4：ADV 声明边界 vs actual_inputs——F2 场景机械化。"""

    PLAN = {"persistence": {}, "axes": {},
            "probe_inputs": {"ADV-01": [
                {"field": "分配额度", "op": "eq", "value": 99},
                {"field": "主账户余额", "op": "range", "value": [100, 100]}]}}

    def test_unexecuted_is_notice(self):
        records = [_record(surf_id="SURF-01")]  # 无 adv_id
        result = ac.analyse(records, plan_surfaces={"SURF-01"},
                            plan_risks={"SURF-01": "high"}, plan_meta=self.PLAN)
        codes = [n["code"] for n in result.get("notices", [])]
        self.assertIn("probe_inputs_unexecuted", codes)

    def test_mismatch_is_gap(self):
        # F2 原形：声明差一单位（99），实执行 200
        records = [_record(surf_id="SURF-01", adv_id="ADV-01",
                           actual_inputs=[{"field": "分配额度", "value": 200},
                                          {"field": "主账户余额", "value": 100}])]
        result = ac.analyse(records, plan_surfaces={"SURF-01"},
                            plan_risks={"SURF-01": "high"}, plan_meta=self.PLAN)
        codes = [g["code"] for g in result["gaps"]]
        self.assertIn("probe_input_mismatch", codes, f"gaps={codes}")
        msg = next(g["message"] for g in result["gaps"]
                   if g["code"] == "probe_input_mismatch")
        self.assertIn("分配额度", msg)

    def test_match_passes(self):
        records = [_record(surf_id="SURF-01", adv_id="ADV-01",
                           actual_inputs=[{"field": "分配额度", "value": 99},
                                          {"field": "主账户余额", "value": 100}])]
        result = ac.analyse(records, plan_surfaces={"SURF-01"},
                            plan_risks={"SURF-01": "high"}, plan_meta=self.PLAN)
        self.assertNotIn("probe_input_mismatch", [g["code"] for g in result["gaps"]])

    def test_input_satisfied_ops(self):
        f = ac._input_satisfied
        self.assertTrue(f("eq", 5, 5))
        self.assertFalse(f("eq", 5, 6))
        self.assertTrue(f("lt", 10, 5))
        self.assertFalse(f("lt", 10, 10))
        self.assertTrue(f("in", ["a", "b"], "a"))
        self.assertTrue(f("range", [1, 10], 7))
        self.assertFalse(f("range", [1, 10], 11))
        self.assertFalse(f("ge", 10, "abc"))  # 不可比 → 不满足


class S5Driver(unittest.TestCase):
    """S5：绕径驱动 + PASS 须 driver_justified；attack_coverage 出披露。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.workspace = self.folder / "run"
        self.design = json.loads((PKG_ROOT / "examples" / "cases-example.json")
                                 .read_text(encoding="utf-8"))
        self.context = {"target_url": "https://example.invalid/t", "environment": "test",
                        "application_version": "build-156", "dependency_mode": "sandbox",
                        "feature_flags": {}, "role_scope": ["editor"]}
        self.evidence = self.folder / "obs.txt"
        self.evidence.write_text("fixture evidence", encoding="utf-8")
        el.initialize(self.workspace, self.design, self.context, "R-156", [])
        el.begin(self.workspace, self.design, self.context, "a1", "TC-NAME-001",
                 "隔离环境内创建项目")

    def _finish(self, attack, status="PASS"):
        return el.finish(self.workspace, self.design, self.context, "a1", status,
                         "Observed fixture result", [str(self.evidence)],
                         [{"check": "项目重新打开后的名称", "actual": "新名称",
                           "matched": status == "PASS"}],
                         "none", [], "No real remote data created", attack=attack)

    def test_forbidden_driver_pass_requires_justification(self):
        attack = {"path_type": "positive", "surf_id": "SURF-01", "method": "boundary",
                  "oracle_source": "self_attested", "discovery_mode": "designed_probe",
                  "driver": "page_internal_fn"}
        with self.assertRaises(ValueError) as ctx:
            self._finish(attack)
        self.assertIn("driver_justified", str(ctx.exception))

    def test_justified_pass_accepted_and_disclosed(self):
        attack = {"path_type": "positive", "surf_id": "SURF-01", "method": "boundary",
                  "oracle_source": "self_attested", "discovery_mode": "designed_probe",
                  "driver": "page_internal_fn",
                  "driver_justified": "页面内部函数仅读取不改变状态；已另行 UI 路径复测"}
        self._finish(attack)
        events = el.read_events(self.workspace)
        self.assertEqual(events[-1]["driver"], "page_internal_fn")
        result = ac.analyse(events, plan_surfaces={"SURF-01"},
                            plan_risks={"SURF-01": "high"},
                            plan_meta={"persistence": {}, "axes": {}, "probe_inputs": {}})
        codes = [n["code"] for n in result.get("notices", [])]
        self.assertIn("driver_forbidden_observed", codes)

    def test_user_path_pass_ok_without_justification(self):
        attack = {"path_type": "positive", "surf_id": "SURF-01", "method": "boundary",
                  "oracle_source": "self_attested", "discovery_mode": "designed_probe",
                  "driver": "ui_user_path"}
        self._finish(attack)  # 不抛

    def test_invalid_driver_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            el._attack_fields({"driver": "teleport"}, [])
        self.assertIn("driver", str(ctx.exception))


class S6CrossViewGroups(unittest.TestCase):
    """S6：单位/小数互比只在同语义量分组内做；无分组只披露。"""

    def test_grouped_finds_unit_mismatch(self):
        views = {"短信": ["剩余 30 条"], "OCR": ["剩余 12 次"]}
        groups = {"剩余量": {"短信": ["剩余 30 条"], "OCR": ["剩余 12 次"]}}
        result = cc.cross_view_compare(views, groups)
        ids = [f["id"] for f in result["findings"]]
        self.assertIn("剩余量:unit:条", ids)
        self.assertIn("剩余量:unit:次", ids)

    def test_ungrouped_is_disclosure_only(self):
        result = cc.cross_view_compare({"短信": ["剩余 30 条"], "AI": ["489.68 点"]})
        self.assertEqual(result["findings"], [])
        codes = [n["code"] for n in result.get("notices", [])]
        self.assertIn("cross_view_unaligned", codes)

    def test_group_with_same_unit_no_finding(self):
        groups = {"剩余量": {"短信A": ["剩余 30 条"], "短信B": ["剩余 5 条"]}}
        result = cc.cross_view_compare({"短信A": ["剩余 30 条"], "短信B": ["剩余 5 条"]},
                                       groups)
        self.assertEqual(result["findings"], [])

    def test_unknown_group_view_rejected(self):
        with self.assertRaises(ValueError):
            cc.cross_view_compare({"短信": ["x"]}, {"g": {"不存在": ["y"]}})

    def test_analyse_cross_view_carries_notices(self):
        result = cc.analyse_cross_view({"views": {"A": ["5 条"]}})
        self.assertIn("cross_view_unaligned", [n["code"] for n in result["notices"]])


class S7NotApplicableCrossCheck(unittest.TestCase):
    """S7：not_applicable 与 ui_content/usability/accessibility 采集记录矛盾 → 拒收。"""

    def _state(self, folder):
        state = pg.init_state(str(folder / "phase-state.json"), "TASK-V156", "SESS-1",
                              model_class="fast", model_name="m")
        pg.start(state, "plan", "SESS-1")
        pg.complete(state, "plan", session="SESS-1")
        artifacts = []
        for name in ("review-result.json", "cases-v156.json", "adversarial-plan.json"):
            f = folder / name
            if not f.exists():
                f.write_text("{}", encoding="utf-8")
            artifacts.append(str(f))
        pg.submit(state, "execute", artifacts=artifacts, session="SESS-1")
        pg.approve(state, "execute", by="USER", note="同会话测试放行", session="SESS-1")
        pg.start(state, "execute", "SESS-1")
        return state

    def _collection(self, folder, path_type):
        records = [{"id": "OBS-1", "collection_state": "collected",
                    "evidence_paths": ["e.png"], "path_type": path_type}]
        f = folder / "collection.jsonl"
        f.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
        return str(f)

    def _finish(self, state, folder, ui_handoff, path_type="ui_content"):
        coll = self._collection(folder, path_type)
        pg.complete(state, "execute", session="SESS-1",
                    attack_coverage={"verdict_ceiling": "通过", "gaps": []},
                    exploration={"verdict_ceiling": "通过", "gaps": [], "ready_pending": 0,
                                 "missing_views": [], "failed_items": []},
                    collection=coll, ui_handoff=ui_handoff)

    def test_not_applicable_conflicts_with_ui_records(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        folder = Path(tmp.name)
        state = self._state(folder)
        with self.assertRaises(ValueError) as ctx:
            self._finish(state, folder, "not_applicable", path_type="ui_content")
        self.assertIn("not_applicable", str(ctx.exception))

    def test_not_applicable_conflicts_with_nested_path_type(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        folder = Path(tmp.name)
        state = self._state(folder)
        records = [{"id": "OBS-1", "collection_state": "collected",
                    "evidence_paths": ["e.png"],
                    "observed": {"page": {"path_type": "usability"}}}]
        f = folder / "collection.jsonl"
        f.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            pg.complete(state, "execute", session="SESS-1",
                        attack_coverage={"verdict_ceiling": "通过", "gaps": []},
                        exploration={"verdict_ceiling": "通过", "gaps": [], "ready_pending": 0,
                                     "missing_views": [], "failed_items": []},
                        collection=str(f), ui_handoff="not_applicable")
        self.assertIn("usability", str(ctx.exception))

    def test_not_applicable_ok_for_non_ui_records(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        folder = Path(tmp.name)
        state = self._state(folder)
        self._finish(state, folder, "not_applicable", path_type="positive")
        self.assertEqual(state["phases"]["execute"]["ui_handoff"]["state"], "not_applicable")

    def test_revise_cannot_supply_waiver_after_the_fact(self):
        """S7 补口：revise 补交 not_applicable 必须被拒（否则 complete 时漏交、
        revise 时补豁免即可绕过与采集记录的交叉核验）。"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        folder = Path(tmp.name)
        state = self._state(folder)
        self._finish(state, folder, None, path_type="ui_content")  # 漏交 ui_handoff
        with self.assertRaises(ValueError) as ctx:
            pg.revise(state, "execute", ui_handoff="not_applicable", note="补豁免")
        self.assertIn("not_applicable", str(ctx.exception))

    def test_collect_path_types_helper(self):
        out = set()
        pg._collect_path_types({"a": {"path_type": "UI_Content"}, "b": [{"path_type": "x"}]},
                               out)
        self.assertEqual(out, {"ui_content", "x"})


class S8CriterionSource(unittest.TestCase):
    """S8：非 not_evaluable 项必须有 criterion_source；裸 criterion 不算判据。"""

    def test_bare_criterion_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            pg._ui_handoff_check_text(
                json.dumps([{"view": "v", "item": "i", "criterion": "已对照"}]))
        self.assertIn("criterion_source", str(ctx.exception))

    def test_criterion_source_accepted(self):
        checked = pg._ui_handoff_check_text(
            json.dumps([{"view": "v", "item": "i",
                         "criterion_source": "设计稿节点 #a1"}]))
        self.assertEqual(checked["state"], "provided")

    def test_not_evaluable_reason_still_accepted(self):
        checked = pg._ui_handoff_check_text(
            json.dumps([{"view": "v", "item": "i",
                         "not_evaluable_reason": "无数值判据，转人工"}]))
        self.assertEqual(checked["state"], "provided")


class S9DataShapes(unittest.TestCase):
    """S9：init --data-shapes 落 baseline；impossible 进 headline.uncovered。"""

    def test_data_shapes_validation(self):
        with self.assertRaises(ValueError):
            el._validate_data_shapes([{"shape": "800MB 视频", "constructible": "maybe"}])
        shapes = el._validate_data_shapes([
            {"shape": "800MB 视频", "constructible": "impossible", "note": "环境带宽"},
            {"shape": "重复文件", "constructible": "constructible"}])
        self.assertEqual(len(shapes), 2)

    def test_headline_includes_impossible_shapes(self):
        baseline = {"data_shapes": [{"shape": "800MB 视频", "constructible": "impossible"},
                                    {"shape": "重复文件", "constructible": "constructible"}]}
        headline = el._build_headline(
            baseline=baseline,
            counts={"NOT_RUN": 0, "BLOCKED": 0, "FAIL": 0, "SKIPPED": 0, "PASS": 0},
            executed=0, cases=[], final_precondition={},
            ceiling="通过", evidence_issues=[], flaky_cases=[],
            context={}, missing_inputs=[], delegated_inputs=[])
        sources = [u["source"] for u in headline["uncovered"]["items"]]
        self.assertIn("baseline.data_shapes.impossible", sources)
        items = next(u for u in headline["uncovered"]["items"]
                     if u["source"] == "baseline.data_shapes.impossible")["items"]
        self.assertEqual(items, ["800MB 视频"])


class S10DeliveryBinding(unittest.TestCase):
    """S10：交付 CSV 每条须在台账 events 有对应 FINISH。"""

    def setUp(self):
        import delivery_ledger_binding as d
        self.d = d
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)

    def test_missing_ids_reported(self):
        ledger = self.folder / "events.jsonl"
        # BEGIN 不算执行完成——只有 FINISH 才绑定交付
        rows = [{"kind": "FINISH", "attempt_id": "a1", "case_id": "TC-1"},
                {"kind": "BEGIN", "attempt_id": "a2", "case_id": "TC-2"}]
        ledger.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        csv_f = self.folder / "delivery.csv"
        csv_f.write_text("用例ID\nTC-1\nTC-2\nTC-3\n", encoding="utf-8")
        result = self.d.check(str(csv_f), str(ledger))
        self.assertEqual(result["missing"], ["TC-2", "TC-3"])
        self.assertEqual(result["verdict_ceiling_suggestion"], "待核实")

    def test_all_bound_passes(self):
        ledger = self.folder / "events.jsonl"
        ledger.write_text(json.dumps({"kind": "FINISH", "case_id": "TC-1"}), encoding="utf-8")
        csv_f = self.folder / "delivery.csv"
        csv_f.write_text("用例ID\nTC-1\n", encoding="utf-8")
        result = self.d.check(str(csv_f), str(ledger))
        self.assertEqual(result["missing"], [])
        self.assertIsNone(result["verdict_ceiling_suggestion"])

    def test_enforce_exit_code(self):
        ledger = self.folder / "events.jsonl"
        ledger.write_text(json.dumps({"kind": "FINISH", "case_id": "TC-1"}), encoding="utf-8")
        csv_f = self.folder / "delivery.csv"
        csv_f.write_text("用例ID\nTC-1\nTC-9\n", encoding="utf-8")
        self.assertEqual(self.d.main(["--delivery", str(csv_f), "--ledger", str(ledger)]), 0)
        self.assertEqual(self.d.main(["--delivery", str(csv_f), "--ledger", str(ledger),
                                      "--enforce"]), 1)

    def test_missing_ledger_is_error(self):
        csv_f = self.folder / "delivery.csv"
        csv_f.write_text("用例ID\nTC-1\n", encoding="utf-8")
        result = self.d.check(str(csv_f), str(self.folder / "nope"))
        self.assertIn("error", result)
        self.assertEqual(result["verdict_ceiling_suggestion"], "待核实")

    def test_tool_version_bumped(self):
        self.assertEqual(ac.TOOL_VERSION, "6")


if __name__ == "__main__":
    unittest.main()
