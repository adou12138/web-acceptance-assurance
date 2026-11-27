"""v1.55 检测器回归：R1 适用性覆盖下限 / R2 同源判定 / R3 校验双方向。

夹具全部用最小 dict 构造或复用既有样例，不依赖任何外部环境。
每条测试对应方案 §3 的验收标准；撤掉对应守卫后本文件必须有测试转红。
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
import coverage_matrix as cm  # noqa: E402
import execution_ledger as el  # noqa: E402


SAMPLE_12 = PKG_ROOT / "skills" / "case-generator" / "tests" / "fixtures" / "cases-sample-12.json"


def _load_sample():
    return json.loads(SAMPLE_12.read_text(encoding="utf-8-sig"))


class R1ApplicabilityFloor(unittest.TestCase):
    """R1：九类逐类裁定是交付前置；缺类是 error 不是 warning。"""

    def test_missing_class_is_warning_at_struct_layer(self):
        # S0/结构层只告警；强制在 coverage_matrix 维度下限（见下一条）。
        data = _load_sample()
        data["applicability"].pop("usability")
        errors, warnings = case_data.validate(data)
        self.assertEqual([e for e in errors if "applicability" in e], [])
        self.assertTrue(any("usability" in w for w in warnings),
                        f"缺类应有告警：{[w for w in warnings if 'applicab' in w][:3]}")

    def test_missing_class_blocks_at_coverage_layer(self):
        data = _load_sample()
        data["applicability"].pop("visual")
        data["testcases"] = [c for c in data["testcases"] if "VISUAL" not in (c.get("coverage_flags") or [])]
        matrix = cm.build_matrix(data, policy={})
        self.assertTrue(any(g["target"] == "visual" for g in matrix["dimension_gaps"]),
                        f"dimension_gaps={matrix['dimension_gaps'][:3]}")

    def test_complete_matrix_has_no_applicability_error(self):
        data = _load_sample()
        errors, warnings = case_data.validate(data)
        self.assertFalse([e for e in errors if "applicability" in e],
                         f"九类齐全不应报 applicability error：{errors[:3]}")
        self.assertFalse([w for w in warnings if "未裁定类型" in w])  # 九类齐全无告警

    def test_coverage_matrix_dimension_floor_triggers(self):
        cases = {
            "applicability": {
                "visual": {"state": "applicable", "reason": "界面可测"},
                "functional": {"state": "applicable", "reason": "r"},
            },
            "features": [],
            "testcases": [{"id": "TC-1", "coverage_flags": ["L1"]}],
        }
        matrix = cm.build_matrix(cases, policy={})
        targets = {g["target"] for g in matrix["dimension_gaps"]}
        self.assertIn("visual", targets, "声明 applicable 却无 VISUAL 标签应报缺口")

    def test_coverage_matrix_dimension_floor_passes_with_tag(self):
        cases = {
            "applicability": {
                "visual": {"state": "applicable", "reason": "界面可测"},
                "usability": {"state": "not_applicable", "reason": "无交互对象"},
                "accessibility": {"state": "insufficient_evidence", "reason": "无基线"},
            },
            "features": [],
            "testcases": [{"id": "TC-1", "coverage_flags": ["L1", "VISUAL"]}],
        }
        matrix = cm.build_matrix(cases, policy={})
        self.assertEqual([g for g in matrix["dimension_gaps"] if g["target"] == "visual"], [])
        self.assertEqual(matrix["dimension_notes"][0]["class"], "visual")

    def test_coverage_matrix_na_without_reason_is_gap(self):
        cases = {
            "applicability": {"visual": {"state": "not_applicable"}},
            "features": [],
            "testcases": [],
        }
        matrix = cm.build_matrix(cases, policy={})
        self.assertTrue(any(g["target"] == "visual" for g in matrix["dimension_gaps"]))

    def test_exit_code_semantics(self):
        # 退出码语义由 main() 决定；这里锁定 build_matrix 的输出契约。
        cases = {"applicability": {"visual": {"state": "applicable", "reason": "x"}},
                 "features": [], "testcases": []}
        matrix = cm.build_matrix(cases, policy={})
        self.assertTrue(matrix["dimension_gaps"])


class R2SameSourceGuard(unittest.TestCase):
    """R2：独立通道必须声明范围；same_backend 与 independent 互斥。"""

    def test_missing_scope_is_same_source_unverified(self):
        rec = {"oracle_source": "independent", "oracle_channel": "getSubUserList 接口",
               "evidence": [{"path": "e.json", "sha256": "x"}]}
        category, reason = ac._oracle_gap_category(rec)
        self.assertEqual(category, "same_source_unverified")
        self.assertIn("oracle_scope", reason)

    def test_same_backend_is_no_criterion(self):
        rec = {"oracle_source": "independent", "oracle_channel": "同域接口",
               "oracle_scope": "same_backend",
               "evidence": [{"path": "e.json", "sha256": "x"}]}
        category, reason = ac._oracle_gap_category(rec)
        self.assertEqual(category, "no_criterion")
        self.assertIn("同源视图", reason)

    def test_cross_system_with_evidence_passes(self):
        rec = {"oracle_source": "independent", "oracle_channel": "交易流水系统",
               "oracle_scope": "cross_system",
               "evidence": [{"path": "e.json", "sha256": "x"}]}
        category, reason = ac._oracle_gap_category(rec)
        self.assertEqual((category, reason), (None, None))
        self.assertIsNone(ac._oracle_unproven(rec))

    def test_ledger_rejects_same_backend_independent(self):
        attack = {"path_type": "data_consistency", "surf_id": "SURF-A", "method": "consistency_check",
                  "oracle_source": "independent", "oracle_channel": "同域接口",
                  "oracle_scope": "same_backend"}
        with self.assertRaises(ValueError) as ctx:
            el._attack_fields(attack, evidence_paths=["e.json"])
        self.assertIn("互斥", str(ctx.exception))

    def test_ledger_rejects_unknown_scope(self):
        attack = {"oracle_scope": "maybe"}
        with self.assertRaises(ValueError) as ctx:
            el._attack_fields(attack, evidence_paths=[])
        self.assertIn("oracle_scope", str(ctx.exception))

    def test_ledger_accepts_valid_scope(self):
        out = el._attack_fields({"oracle_scope": "cross_system"}, evidence_paths=[])
        self.assertEqual(out["oracle_scope"], "cross_system")


class R3DirectionObligation(unittest.TestCase):
    """R3：P25 双方向；单方向全部 held 触发义务，未声明只披露。"""

    @staticmethod
    def build_plan(**overrides):
        plan = {
            "schema_version": "1.0",
            "plan_id": "ADVPLAN-TEST-01",
            "source_fingerprints": {"material": "a" * 64, "cases_json": "b" * 64},
            "surfaces": [
                {"id": "SURF-01", "entry": "额度分配弹窗", "role": "系统管理员", "state": "已开启",
                 "data": "主账户 500", "dependencies": "分配服务",
                 "failure_hypothesis": "回收方向被伪上界拦截", "risk": "high",
                 "direction": "increase"},
            ],
            "oracles": [
                {"id": "ORC-01", "source": "REQ", "source_ref": "REQ-01",
                 "observation_entry": "二次确认文案与两端读回", "applicable_condition": "弹窗可用时"},
            ],
            "obligations": [
                {"id": "ADV-01", "surface_ref": "SURF-01", "oracle_ref": "ORC-01",
                 "probe_type": "fault_injection", "budget": "2 次", "status": "planned"},
            ],
        }
        for key, value in overrides.items():
            if value is None:
                plan.pop(key, None)
            else:
                plan[key] = value
        return plan

    def test_playbook_p25_accepted(self):
        self.assertIn("P25", ad.PLAYBOOK_TEMPLATES)
        summary = ad.validate(self.build_plan())
        self.assertEqual(summary["high_risk_surfaces"], 1)

    def test_direction_invalid_rejected(self):
        bad = self.build_plan(surfaces=[
            {"id": "SURF-01", "entry": "e", "role": "r", "state": "s", "data": "d",
             "dependencies": "dep", "failure_hypothesis": "h", "risk": "high",
             "direction": "sideways"}])
        with self.assertRaises(ValueError) as ctx:
            ad.validate(bad)
        self.assertIn("direction", str(ctx.exception))

    def _direction_result(self, directions, outcomes):
        records = []
        for i, outcome in enumerate(outcomes, 1):
            records.append({"kind": "FINISH", "attempt_id": f"a{i}", "case_id": f"TC-{i}",
                            "path_type": "boundary", "surf_id": "SURF-01",
                            "method": "boundary", "oracle_source": "independent",
                            "oracle_channel": "第三方账单系统", "oracle_scope": "cross_system",
                            "discovery_mode": "designed_probe",
                            "hypothesis": "如果输入超过上界则拒绝",
                            "hypothesis_outcome": outcome,
                            "evidence": [{"path": "e.json", "sha256": "x"}]})
        return ac.analyse(records, plan_surfaces={"SURF-01"},
                          plan_risks={"SURF-01": "high"},
                          plan_directions=directions)

    def test_single_direction_all_held_triggers_obligation(self):
        result = self._direction_result({"SURF-01": "increase"}, ["held", "held"])
        codes = [g["code"] for g in result["gaps"]]
        self.assertIn("direction_single_held", codes,
                      f"gaps={[g['code'] for g in result['gaps']]}")

    def test_broken_hypothesis_does_not_trigger(self):
        result = self._direction_result({"SURF-01": "increase"}, ["held", "broken"])
        codes = [g["code"] for g in result["gaps"]]
        self.assertNotIn("direction_single_held", codes)

    def test_both_direction_no_obligation(self):
        result = self._direction_result({"SURF-01": "both"}, ["held", "held"])
        codes = [g["code"] for g in result["gaps"]]
        self.assertNotIn("direction_single_held", codes)

    def test_undeclared_direction_is_disclosure_only(self):
        result = self._direction_result({}, ["held", "held"])
        codes = [g["code"] for g in result["gaps"]]
        self.assertNotIn("direction_single_held", codes)
        notice_codes = [n["code"] for n in result.get("notices", [])]
        self.assertIn("direction_undeclared", notice_codes)

    def test_tool_version_bumped(self):
        self.assertEqual(ac.TOOL_VERSION, "6")  # v1.56 S2-S5 进入 gaps 判定


class R4UiHandoff(unittest.TestCase):
    """R4：定性 UI 放弃留痕——缺失压档、not_applicable 豁免、结构校验、revise 补正。"""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)

    def _handoff_artifacts(self):
        names = ["review-result.json", "cases-v155.json", "adversarial-plan.json"]
        out = []
        for name in names:
            f = self.folder / name
            if not f.exists():
                f.write_text("{}", encoding="utf-8")
            out.append(str(f))
        return out

    def _state(self):
        import phase_gate as pg
        state = pg.init_state(str(self.folder / "phase-state.json"), "TASK-V155", "SESS-1",
                              model_class="fast", model_name="m")
        pg.start(state, "plan", "SESS-1")
        pg.complete(state, "plan", session="SESS-1")
        artifacts = self._handoff_artifacts()
        pg.submit(state, "execute", artifacts=artifacts, session="SESS-1")
        pg.approve(state, "execute", by="USER", note="同会话测试放行（结论上限本测试不消费）", session="SESS-1")
        pg.start(state, "execute", "SESS-1")
        return state

    def _finish(self, state, ui_handoff=None):
        import phase_gate as pg
        coverage = {"verdict_ceiling": "通过", "gaps": []}
        exploration = {"verdict_ceiling": "通过", "gaps": [], "ready_pending": 0,
                       "missing_views": [], "failed_items": []}
        records = [{"id": "OBS-1", "collection_state": "collected",
                    "evidence_paths": ["evidence/x.png"]}]
        coll = self.folder / "collection.jsonl"
        coll.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
        pg.complete(state, "execute", session="SESS-1",
                    attack_coverage=coverage, exploration=exploration,
                    collection=str(coll), ui_handoff=ui_handoff)
        return state

    def test_missing_ui_handoff_records_not_provided(self):
        import phase_gate as pg
        state = self._finish(self._state())
        self.assertEqual(state["phases"]["execute"]["ui_handoff"]["state"], "not_provided")
        from phase_gate import REASON_POLICY
        self.assertIn("ui_handoff_missing", REASON_POLICY)

    def test_not_applicable_is_explicit_waiver(self):
        state = self._finish(self._state(), ui_handoff="not_applicable")
        self.assertEqual(state["phases"]["execute"]["ui_handoff"]["state"], "not_applicable")

    def test_provided_requires_view_and_item(self):
        bad = self.folder / "bad.json"
        bad.write_text(json.dumps([{"view": "短信页签", "item": "间距"}]), encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            self._finish(self._state(), ui_handoff=str(bad))
        self.assertIn("criterion", str(ctx.exception))

    def test_provided_list_passes(self):
        good = {"items": [{"view": "短信页签", "item": "图标与文案间距",
                           "not_evaluable_reason": "无色值/间距数值判据，转人工视觉通道"}]}
        f = self.folder / "good.json"
        f.write_text(json.dumps(good, ensure_ascii=False), encoding="utf-8")
        state = self._finish(self._state(), ui_handoff=str(f))
        self.assertEqual(state["phases"]["execute"]["ui_handoff"]["state"], "provided")

    def test_revise_can_supply_later(self):
        import phase_gate as pg
        state = self._finish(self._state())  # 不带 ui_handoff
        good = {"items": [{"view": "日志弹窗", "item": "列文案缩略",
                           "criterion_source": "设计稿节点 #log-dialog：列宽与文案对照"}]}
        f = self.folder / "later.json"
        f.write_text(json.dumps(good, ensure_ascii=False), encoding="utf-8")
        pg.revise(state, "execute", ui_handoff=str(f), note="补正 R4 留痕")
        self.assertEqual(state["phases"]["execute"]["ui_handoff"]["state"], "provided")

    def test_empty_list_is_empty_not_provided(self):
        import phase_gate as pg
        f = self.folder / "empty.json"
        f.write_text("[]", encoding="utf-8")
        checked = pg._ui_handoff_check(str(f))
        self.assertEqual(checked["state"], "empty")


class R6LeadKeyNotice(unittest.TestCase):
    """R6：疑似线索键（new_lead 等）出声不阻断。"""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)

    def _handoff_artifacts(self):
        out = []
        for name in ("review-result.json", "cases-v155.json", "adversarial-plan.json"):
            f = self.folder / name
            if not f.exists():
                f.write_text("{}", encoding="utf-8")
            out.append(str(f))
        return out

    def test_stray_lead_key_is_notice_not_blocker_semantics(self):
        import phase_gate as pg
        records = [{"id": "OBS-1", "collection_state": "collected",
                    "evidence_paths": ["e.png"],
                    "new_lead": {"suspicion": "AI 点数为负", "basis": "页面显示", "where": "上传页"}}]
        violations = pg.scan_collection_records(records)
        codes = [v["code"] for v in violations]
        self.assertIn("lead_key_unknown", codes)
        v = [x for x in violations if x["code"] == "lead_key_unknown"][0]
        self.assertTrue(v.get("notice"), "notice 级不得阻断收尾")
        self.assertIn("new_lead", v["keys"])

    def test_plain_lead_still_validated(self):
        import phase_gate as pg
        records = [{"id": "OBS-2", "collection_state": "collected",
                    "evidence_paths": ["e.png"],
                    "lead": {"suspicion": "x", "basis": "y", "where": "z",
                             "severity": "high"}}]
        violations = pg.scan_collection_records(records)
        codes = [v["code"] for v in violations]
        self.assertIn("lead_unknown_field", codes)
        self.assertNotIn("lead_key_unknown", codes)

    def test_complete_records_notice_without_blocking(self):
        # 走一遍流程：含 new_lead 的采集记录应能收尾，且 notice 留痕。
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        folder = Path(tmp.name)
        import phase_gate as pg
        state = pg.init_state(str(folder / "phase-state.json"), "TASK-V155-R6", "SESS-1",
                              model_class="fast", model_name="m")
        pg.start(state, "plan", "SESS-1")
        pg.complete(state, "plan", session="SESS-1")
        pg.submit(state, "execute", artifacts=self._handoff_artifacts(), session="SESS-1")
        pg.approve(state, "execute", by="USER", note="同会话测试放行", session="SESS-1")
        pg.start(state, "execute", "SESS-1")
        records = [{"id": "OBS-1", "collection_state": "collected",
                    "evidence_paths": ["e.png"],
                    "new_lead": {"suspicion": "x", "basis": "y", "where": "z"}}]
        coll = folder / "collection.jsonl"
        coll.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
        pg.complete(state, "execute", session="SESS-1",
                    attack_coverage={"verdict_ceiling": "通过", "gaps": []},
                    exploration={"verdict_ceiling": "通过", "gaps": [], "ready_pending": 0,
                                 "missing_views": [], "failed_items": []},
                    collection=str(coll), ui_handoff="not_applicable")
        coll_entry = state["phases"]["execute"]["collection"]
        self.assertEqual(coll_entry["state"], "provided")
        self.assertTrue(any(n.get("code") == "lead_key_unknown"
                            for n in coll_entry.get("notices", [])))


class R7HeadlineColumns(unittest.TestCase):
    """R7：headline/2 携带 blocked/uncovered；渲染器同版本。"""

    def test_schema_bumped_both_sides(self):
        import render_report_html as rr
        self.assertEqual(el.HEADLINE_SCHEMA, "ledger-headline/2")
        self.assertEqual(rr.HEADLINE_SCHEMA, "ledger-headline/2")

    def test_build_headline_contains_blocked_and_uncovered(self):
        baseline = {"blocked_faces": [{"name": "录音转录", "reason": "无音频样本"}]}
        headline = el._build_headline(
            baseline=baseline,
            counts={"NOT_RUN": 2, "BLOCKED": 0, "FAIL": 0, "SKIPPED": 0, "PASS": 0},
            executed=0, cases=[], final_precondition={},
            ceiling="待核实", evidence_issues=[], flaky_cases=[],
            context={}, missing_inputs=["通讯录管理员样本"], delegated_inputs=[])
        self.assertEqual(headline["blocked"]["count"], 1)
        self.assertEqual(headline["blocked"]["faces"][0]["name"], "录音转录")
        sources = [u["source"] for u in headline["uncovered"]["items"]]
        self.assertIn("baseline.missing_inputs", sources)
        self.assertIn("execution.NOT_RUN", sources)


class R9GateAbsent(unittest.TestCase):
    """R9：门禁缺席检测——有执行产物而无门禁产物必须点名。"""

    def test_absent_detected_on_minimal_workspace(self):
        import gate_absent_check as g
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        folder = Path(tmp.name)
        (folder / "execution-results.csv").write_text("id\n", encoding="utf-8")
        result = g.scan(folder)
        gates = {a["gate"] for a in result["absent"]}
        self.assertEqual(gates, {"execution_ledger", "attack_coverage", "exploration_gate"})
        self.assertEqual(result["verdict_ceiling_suggestion"], "待核实")

    def test_no_execution_artifacts_no_complaint(self):
        import gate_absent_check as g
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        result = g.scan(Path(tmp.name))
        self.assertEqual(result["absent"], [])

    def test_enforce_exit_code(self):
        import gate_absent_check as g
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        folder = Path(tmp.name)
        (folder / "execution-results.csv").write_text("id\n", encoding="utf-8")
        rc = g.main(["--dir", str(folder)])
        self.assertEqual(rc, 0)
        rc = g.main(["--dir", str(folder), "--enforce"])
        self.assertEqual(rc, 1)


class R11SelfCheckDoc(unittest.TestCase):
    """R11：自检清单存在且每问可回链包内规则（orphan 防护的最小锚定）。"""

    def test_self_check_exists_and_covers_rules(self):
        doc = (PKG_ROOT / "references" / "self-check.md").read_text(encoding="utf-8")
        for token in ("coverage_matrix", "oracle_scope", "direction",
                      "gate_absent_check", "ui-handoff", "blocked_faces", "--cross-view"):
            self.assertIn(token, doc, f"自检清单缺锚点：{token}")

    def test_agents_file_links_self_check(self):
        doc = (PKG_ROOT / "agents" / "web-acceptance-assurance.md").read_text(encoding="utf-8")
        self.assertIn("references/self-check.md", doc)


if __name__ == "__main__":
    unittest.main()
