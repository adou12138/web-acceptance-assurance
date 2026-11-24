"""攻击面覆盖门禁（attack_coverage.py）的机械行为。

背景：154 项真实测试里 143 项走正向路径、surf 关联 0 条、方法标注 0 条，最终只产出
4 条 FAIL 且没有一条来自为新攻击面设计的探针。本文件守住三件事：
  1. 全正向台账必须被压上限（不能靠"铺得全"冒充"打得深"）；
  2. 字段缺失必须报缺口而不是默认通过（也不拿 0 冒充统计值）；
  3. 推断值不参与门禁（诊断参考与机械判据分账）。
"""
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import attack_coverage as ac  # noqa: E402


# 路径类型 → 方法库 ID 的默认搭配（方法必须是 B 节枚举内的稳定 ID）
METHOD_FOR = {"positive": "equivalence", "boundary": "boundary", "exception": "fault_injection",
              "permission": "decision_table", "combo": "concurrency", "ui_content": "copy_audit",
              "usability": "heuristic_review", "accessibility": "visual_walkthrough",
              "data_consistency": "consistency_check", "regression": "regression_impact"}
CHANNEL = "网页端 DOM 读回 + 接口响应双通道"

# N7：结论达「通过」时残余风险声明必须是结构化声明；空 items 表示「无未破攻击面」。
RESIDUAL_CLEAR = {"items": []}
# N4：未提交 ATK 时给出解释理由（零登记不是问题，未解释才是）。
NO_ATK_REASON = "本轮为只读界面回归，无前置攻击假设"


def rec(i, path_type=None, surf=True, method=None, oracle="independent",
        mode="designed_probe", status="PASS"):
    record = {"id": f"TC-{i}", "status": status, "desc": "字段校验"}
    if path_type is not None:
        record["path_type"] = path_type
    if surf:
        record["surf_id"] = "SURF-01"
    resolved = method if method is not None else METHOD_FOR.get(path_type, "equivalence")
    if resolved:
        record["method"] = resolved
    if oracle:
        record["oracle_source"] = oracle
        if oracle == "independent":
            record["oracle_channel"] = CHANNEL
            record["evidence"] = [f"evidence/tc-{i}.png"]
    if mode:
        record["discovery_mode"] = mode
    return record


class AttackDensityGate(unittest.TestCase):
    def test_all_positive_ledger_lowers_ceiling(self):
        """全正向不只是"密度低"：是一条失效条件都没构造，属绝对零缺口。"""
        result = ac.analyse([rec(i, "positive") for i in range(10)])
        self.assertEqual(result["attack_density"], 0.0)
        self.assertIn("no_attack_paths", [g["code"] for g in result["gaps"]])
        # 密度本身只作披露，不再压上限（配额可凑，构造凑不出来）
        self.assertIn("attack_density_low", [n["code"] for n in result["notices"]])
        self.assertEqual(result["verdict_ceiling"], "待核实")

    def test_small_all_positive_ledger_is_disclosed_not_gated(self):
        """3 条全正向不足以判"没攻击"（可能就是冒烟），但密度要如实披露。"""
        result = ac.analyse([rec(i, "positive") for i in range(3)], residual_note="x")
        self.assertNotIn("no_attack_paths", [g["code"] for g in result["gaps"]])
        self.assertIn("attack_density_low", [n["code"] for n in result["notices"]])

    def test_healthy_ledger_passes(self):
        records = [rec(i, "positive") for i in range(7)]
        records += [rec(i, "boundary") for i in range(3)]
        result = ac.analyse(records, residual_note=RESIDUAL_CLEAR,
                             atk_absent_reason=NO_ATK_REASON)
        self.assertEqual(result["attack_density"], 0.3)
        self.assertEqual(result["gaps"], [])
        self.assertEqual(result["verdict_ceiling"], "通过")

    def test_missing_fields_report_gaps_not_pass(self):
        # 历史台账形态：只有 id/status/desc，没有任何攻击面字段
        result = ac.analyse([{"id": f"X-{i}", "status": "PASS", "desc": "创建题型"}
                             for i in range(5)])
        codes = [g["code"] for g in result["gaps"]]
        self.assertIn("path_type_missing", codes)
        self.assertIn("method_missing", codes)
        # 关联率只作披露，不压上限
        self.assertIn("surf_linkage_low", [n["code"] for n in result["notices"]])
        self.assertEqual(result["attack_density"], "NA")
        self.assertEqual(result["verdict_ceiling"], "待核实")

    def test_self_attested_oracle_is_a_gap(self):
        """一条独立通道都没有是"证据结构缺失"，与"比例偏低"不是一件事。"""
        records = [rec(i, "boundary", oracle="self_attested") for i in range(12)]
        result = ac.analyse(records, residual_note="x")
        self.assertIn("oracle_no_independent_evidence", [g["code"] for g in result["gaps"]])

    def test_incidental_only_is_not_punished_but_needs_hypothesis(self):
        """偶然发现不该被判"没设计攻击"——改标注就能过关的门禁只会训练标注。

        换成的判据是"有没有可证伪预测"：凑不出来，编了要能被执行出来。
        """
        few = [rec(i, "boundary", mode="incidental") for i in range(4)]
        self.assertNotIn("no_designed_probe", [g["code"] for g in ac.analyse(few)["gaps"]])
        many = [rec(i, "boundary", mode="incidental") for i in range(24)]
        result = ac.analyse(many, residual_note="x")
        self.assertIn("no_falsifiable_hypothesis", [g["code"] for g in result["gaps"]])

    def test_inferred_values_do_not_gate(self):
        # 显式声明全部达标，但文本里满是"边界/异常"字样 → 推断值不应影响判定
        records = [rec(i, "positive") for i in range(6)]
        records += [rec(i, "boundary") for i in range(4)]
        for r in records:
            r["desc"] = "边界值 异常 失败 重复提交"
        result = ac.analyse(records, residual_note=RESIDUAL_CLEAR,
                            atk_absent_reason=NO_ATK_REASON)
        self.assertEqual(result["gaps"], [])
        self.assertGreaterEqual(result["inferred_path_type"]["boundary"], 0)

    def test_empty_ledger(self):
        result = ac.analyse([])
        self.assertIn("empty_ledger", [g["code"] for g in result["gaps"]])
        self.assertEqual(result["attack_density"], "NA")


class CliContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_enforce_returns_2_with_gaps(self):
        ledger = self.dir / "l.jsonl"
        ledger.write_text("\n".join(json.dumps(rec(i, "positive")) for i in range(5)),
                          encoding="utf-8")
        self.assertEqual(ac.main(["--ledger", str(ledger), "--enforce"]), 2)

    def test_enforce_returns_0_when_clean(self):
        ledger = self.dir / "l.jsonl"
        rows = [rec(i, "positive") for i in range(7)] + [rec(i, "exception") for i in range(3)]
        ledger.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        self.assertEqual(ac.main(["--ledger", str(ledger), "--enforce",
                                  "--residual-note", '{"items": []}',
                                  "--atk-absent-reason", NO_ATK_REASON]), 0)

    def test_accepts_csv_and_emits_reports(self):
        ledger = self.dir / "l.csv"
        with ledger.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=["id", "path_type", "surf_id", "method",
                                                    "oracle_source", "discovery_mode"])
            writer.writeheader()
            for i in range(3):
                writer.writerow({"id": f"TC-{i}", "path_type": "positive", "surf_id": "SURF-01",
                                 "method": "eq", "oracle_source": "independent",
                                 "discovery_mode": "designed_probe"})
        out_json, out_csv = self.dir / "out.json", self.dir / "out.csv"
        ac.main(["--ledger", str(ledger), "--emit-json", str(out_json), "--emit-csv", str(out_csv)])
        self.assertTrue(out_json.is_file())
        self.assertTrue(out_csv.is_file())
        self.assertEqual(json.loads(out_json.read_text(encoding="utf-8"))["total"], 3)
        self.assertIn("UTF-8", "UTF-8")  # CSV 编码由 BOM 保证，见下方读取校验
        self.assertTrue(out_csv.read_bytes().startswith(b"\xef\xbb\xbf"))


class AttackDimensionBreadth(unittest.TestCase):
    """界面/文案/易用性/无障碍/一致性/回归是合法攻击维度，不能被挤出记录。

    历史行为：PATH_TYPES 只有功能五类，如实标 visual 会被判 path_type_missing，
    塞进 positive 又会拉低攻击密度——两条路都被压上限，等于制度性劝阻登记 UI 类发现。
    """

    def test_ui_and_consistency_paths_count_as_attack(self):
        for ptype in ("ui_content", "usability", "accessibility",
                      "data_consistency", "regression"):
            with self.subTest(path_type=ptype):
                records = [rec(i, "positive") for i in range(4)] + [rec(99, ptype)]
                result = ac.analyse(records)
                self.assertEqual(result["path_type"][ptype], 1)
                self.assertEqual(result["path_type"]["unknown"], 0)
                self.assertGreater(result["attack_density"], 0)
                self.assertNotIn("path_type_missing", [g["code"] for g in result["gaps"]])

    def test_unknown_path_type_still_reported(self):
        result = ac.analyse([rec(1, "visual")])  # 旧名不在新枚举内
        self.assertIn("path_type_missing", [g["code"] for g in result["gaps"]])


class DeclarationIsNotBehaviour(unittest.TestCase):
    """字段填满不等于做过：surf/method/independent 都要能被机械核对。"""

    def test_one_char_surf_id_no_longer_counts(self):
        records = [rec(i, "boundary") for i in range(4)]
        for record in records:
            record["surf_id"] = "x"
        result = ac.analyse(records)
        self.assertIn("surf_format_invalid", [g["code"] for g in result["gaps"]])
        self.assertIn("surf_linkage_low", [n["code"] for n in result["notices"]])

    def test_independent_without_channel_is_a_gap(self):
        records = [rec(i, "boundary") for i in range(4)]
        for record in records:
            record.pop("oracle_channel", None)
        codes = [g["code"] for g in ac.analyse(records)["gaps"]]
        self.assertIn("oracle_independence_unproven", codes)

    def test_independent_without_evidence_is_a_gap(self):
        records = [rec(i, "boundary") for i in range(4)]
        for record in records:
            record.pop("evidence", None)
        self.assertIn("oracle_independence_unproven",
                      [g["code"] for g in ac.analyse(records)["gaps"]])

    def test_self_attested_channel_is_not_independent(self):
        records = [rec(i, "boundary") for i in range(4)]
        for record in records:
            record["oracle_channel"] = "agent 回复的内容读回一致"
        self.assertIn("oracle_independence_unproven",
                      [g["code"] for g in ac.analyse(records)["gaps"]])

    def test_unknown_method_is_rejected(self):
        records = [rec(i, "boundary", method="x") for i in range(4)]
        self.assertIn("method_unknown",
                      [g["code"] for g in ac.analyse(records)["gaps"]])

    def test_zero_execution_declaration_cannot_pass(self):
        """历史漏洞复现：10 条只有声明、无证据无独立通道的记录曾判「通过」。"""
        records = [{"id": f"F-{i}", "path_type": "boundary", "surf_id": "SURF-01",
                    "method": "boundary", "oracle_source": "self_attested",
                    "discovery_mode": "designed_probe"} for i in range(10)]
        result = ac.analyse(records, residual_note="未破：并发删除")
        self.assertEqual(result["verdict_ceiling"], "待核实")
        self.assertIn("oracle_no_independent_evidence", [g["code"] for g in result["gaps"]])

    def test_residual_note_required_before_pass(self):
        records = [rec(i, "positive") for i in range(7)] + [rec(i, "boundary") for i in range(3)]
        without = ac.analyse(records, atk_absent_reason=NO_ATK_REASON)
        self.assertIn("residual_note_missing", [g["code"] for g in without["gaps"]])
        self.assertEqual(without["verdict_ceiling"], "待核实")
        # N7：通过档只接受结构化声明；自由文本在无其他缺口时记缺口压待核实。
        free_text = ac.analyse(records, residual_note="未破：并发删除与跨租户读取",
                               atk_absent_reason=NO_ATK_REASON)
        self.assertIn("residual_declaration_required_for_pass",
                      [g["code"] for g in free_text["gaps"]])
        self.assertEqual(free_text["verdict_ceiling"], "待核实")
        structured = ac.analyse(records, residual_note=RESIDUAL_CLEAR,
                               atk_absent_reason=NO_ATK_REASON)
        self.assertEqual(structured["gaps"], [])
        self.assertEqual(structured["verdict_ceiling"], "通过")

    def test_text_residual_is_not_blamed_when_other_gap_blocks_pass(self):
        """N7 判定必须在 ATK 轨道之后：ATK 命中未闭环已把结论压到待核实，
        再追加「结论将达通过，但声明是自由文本」属自相矛盾且误罚。"""
        records = [rec(i, "positive") for i in range(7)] + [rec(i, "boundary") for i in range(3)]
        result = ac.analyse(
            records, residual_note="未破：并发删除",
            atk_entries=[{"id": "ATK-001", "hypothesis": "重复提交可能双扣",
                          "validation_path": "并发提交比对流水", "impact": "功能",
                          "status": "命中"}])
        codes = [g["code"] for g in result["gaps"]]
        self.assertIn("atk_hit_unreviewed", codes)
        self.assertNotIn("residual_declaration_required_for_pass", codes)
        self.assertEqual(result["verdict_ceiling"], "待核实")

    def test_plan_cross_check_flags_unknown_surface(self):
        records = [rec(i, "boundary") for i in range(4)]
        for record in records:
            record["surf_id"] = "SURF-99"
        result = ac.analyse(records, plan_surfaces={"SURF-01"}, residual_note="x")
        # 计划外发现是产出不是违规：只披露并要求 amend，不压上限
        self.assertNotIn("surf_unknown", [g["code"] for g in result["gaps"]])
        self.assertEqual(result["surfaces_beyond_plan"], 1)
        self.assertIn("SURF-99", result["surfaces_beyond_plan_ids"])
        self.assertIn("new_surface", [n["code"] for n in result["notices"]])
        self.assertIn("new_surface_amend_missing", [n["code"] for n in result["notices"]])
        self.assertTrue(result["surf_plan_checked"])

    def test_new_surface_amend_note_clears_the_notice(self):
        records = [rec(i, "boundary") for i in range(4)]
        for record in records:
            record["surf_id"] = "SURF-99"
        result = ac.analyse(records, plan_surfaces={"SURF-01"}, amend_note="已纳入第 2 轮计划")
        codes = [n["code"] for n in result["notices"]]
        self.assertIn("new_surface", codes)
        self.assertNotIn("new_surface_amend_missing", codes)
        self.assertEqual(result["amend_note"], "已纳入第 2 轮计划")

    def test_single_method_surface_is_disclosed_not_gated(self):
        """方法多样性整体仍只披露：配额能被标注操纵，逼人拆碎去测没打算测的东西。

        N6 例外仅约束「高风险 + 全 held + 无复打/理由」的单一方法面，见
        ReplayObligationTests；本夹具无 plan 风险定级，不进入该义务判定。
        """
        records = [rec(i, "boundary") for i in range(25)]
        result = ac.analyse(records, residual_note="x")
        self.assertNotIn("method_low_diversity", [g["code"] for g in result["gaps"]])
        self.assertIn("single_method_surface", [n["code"] for n in result["notices"]])
        self.assertEqual(result["single_method_surfaces"], ["SURF-01"])


class EventStreamIntake(unittest.TestCase):
    """台账事件流（BEGIN/FINISH）可直接作为输入：只有 FINISH 带判定与攻击字段。"""

    def test_finish_events_become_records(self):
        events = [
            {"kind": "BEGIN", "attempt_id": "A1", "case_id": "TC-1"},
            {"kind": "FINISH", "attempt_id": "A1", "case_id": "TC-1", "status": "FAIL",
             "actual": "按钮文案显示 {{name}} 未渲染", "path_type": "ui_content",
             "surf_id": "SURF-01", "method": "copy_audit", "oracle_source": "independent",
             "oracle_channel": "网页截图 + DOM 文本", "discovery_mode": "designed_probe",
             "evidence": [{"path": "/tmp/a.png", "sha256": "0" * 64}]},
        ]
        result = ac.analyse(events)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["path_type"]["ui_content"], 1)
        self.assertEqual(result["oracle_source"]["independent"], 1)
        self.assertEqual(result["oracle_source"]["unproven_independent"], 0)
        self.assertEqual(result["method_diversity"], 1)


class HypothesisLedger(unittest.TestCase):
    """失效假设台账：这套门禁的核心判据是"你构造了什么让它坏的条件"。

    与配额的差别：配额问"非正向占比够不够"，能被标注操纵；假设问"如果 X 则 Y 会坏"，
    写了就得能被执行出来，而且"打破了几个"是产出而不是违规。
    """

    def test_broken_hypothesis_is_counted_as_output(self):
        records = [rec(1, "boundary")]
        records[0]["hypothesis"] = "题干含 5000 字时保存应截断且给出提示"
        records[0]["hypothesis_outcome"] = "broken"
        result = ac.analyse(records, residual_note="x")
        self.assertEqual(result["hypotheses"]["declared"], 1)
        self.assertEqual(result["hypotheses"]["broken"], 1)
        self.assertEqual(result["hypotheses"]["break_rate"], 1.0)

    def test_outcome_derived_from_status_when_absent(self):
        records = [rec(1, "boundary", status="FAIL"), rec(2, "boundary", status="PASS"),
                   rec(3, "boundary", status="BLOCKED")]
        for index, record in enumerate(records, start=1):
            record["hypothesis"] = f"假设 {index}"
        result = ac.analyse(records, residual_note="x")
        self.assertEqual(result["hypotheses"]["broken"], 1)
        self.assertEqual(result["hypotheses"]["held"], 1)
        self.assertEqual(result["hypotheses"]["blocked"], 1)

    def test_high_risk_surface_without_hypothesis_is_a_gap(self):
        records = [rec(i, "boundary") for i in range(4)]
        result = ac.analyse(records, plan_surfaces={"SURF-01"},
                            plan_risks={"SURF-01": "high"}, residual_note="x")
        self.assertIn("surface_without_hypothesis", [g["code"] for g in result["gaps"]])

    def test_high_risk_surface_with_hypothesis_passes(self):
        records = [rec(i, "boundary") for i in range(4)]
        records[0]["hypothesis"] = "并发建卷时卷名重复应拒绝"
        result = ac.analyse(records, plan_surfaces={"SURF-01"},
                            plan_risks={"SURF-01": "high"},
                            residual_note={"items": [{"id": "SURF-01", "status": "unbroken"}]},
                            atk_absent_reason=NO_ATK_REASON,
                            replay_notes={"SURF-01": "复用 TC-1 的边界输入变体观察，见既有记录"})
        self.assertEqual(result["gaps"], [])
        self.assertEqual(result["verdict_ceiling"], "通过")

    def test_low_risk_surface_needs_no_hypothesis(self):
        records = [rec(i, "boundary") for i in range(4)]
        result = ac.analyse(records, plan_surfaces={"SURF-01"},
                            plan_risks={"SURF-01": "low"},
                            residual_note=RESIDUAL_CLEAR,
                            atk_absent_reason=NO_ATK_REASON)
        self.assertEqual(result["gaps"], [])

    def test_hypothesis_without_surface_is_a_gap(self):
        records = [rec(1, "boundary", surf=False)]
        records[0]["hypothesis"] = "删除后条目应消失"
        result = ac.analyse(records, residual_note="x")
        self.assertIn("hypothesis_without_surface", [g["code"] for g in result["gaps"]])

    def test_surface_coverage_breakdown(self):
        records = [rec(1, "boundary"), rec(2, "exception"), rec(3, "combo")]
        for index, record in enumerate(records, start=1):
            record["hypothesis"] = f"假设 {index}"
        records[0]["hypothesis_outcome"] = "broken"
        result = ac.analyse(records, residual_note="x")
        bucket = result["surface_coverage"]["SURF-01"]
        self.assertEqual(bucket["records"], 3)
        self.assertEqual(bucket["hypotheses"], 3)
        self.assertEqual(bucket["broken"], 1)
        self.assertEqual(len(bucket["methods"]), 3)


class AtkTrackTests(unittest.TestCase):
    """D8 回归：ATK 走独立校验轨道——允许模糊假设与人工验证路径，未执行不压上限。"""

    def setUp(self):
        self.ac = ac

    @staticmethod
    def atk(ident="ATK-001", status="未执行", impact="功能", **extra):
        item = {"id": ident, "hypothesis": "如果并发提交则可能重复扣款",
                "validation_path": "两人同时点提交，比对后台流水",
                "impact": impact, "status": status}
        item.update(extra)
        return item

    def test_unexecuted_atk_does_not_lower_ceiling(self):
        # 关键让步：未执行不压上限，允许假设长期悬置（保留锐度的有意取舍）。
        records = [rec(1, "boundary")]
        records[0]["hypothesis"] = "越界输入应被拒绝"
        records[0]["hypothesis_outcome"] = "held"
        result = self.ac.analyse(records, residual_note=RESIDUAL_CLEAR,
                                  atk_entries=[self.atk(status="未执行")])
        self.assertEqual(result["atk"]["unexecuted"], 1)
        self.assertNotIn("atk_hit_unreviewed", [g["code"] for g in result["gaps"]])
        self.assertEqual(result["verdict_ceiling"], "通过")

    def test_atk_hit_with_function_impact_becomes_gap(self):
        result = self.ac.analyse([rec(1, "boundary")], residual_note="x",
                                 atk_entries=[self.atk(status="命中", impact="功能")])
        self.assertIn("atk_hit_unreviewed", [g["code"] for g in result["gaps"]])
        self.assertEqual(result["atk"]["severity_candidates"][0]["id"], "ATK-001")
        self.assertEqual(result["verdict_ceiling"], "待核实")

    def test_reviewed_atk_hit_no_longer_gap(self):
        result = self.ac.analyse([rec(1, "boundary")], residual_note="x",
                                 atk_entries=[self.atk(status="命中", impact="数据")],
                                 reviews=[{"finding_id": "ATK-001", "verdict": "accepted",
                                           "tie_to_bug": "BUG-01"}])
        self.assertNotIn("atk_hit_unreviewed", [g["code"] for g in result["gaps"]])

    def test_experience_impact_hit_is_disclosure_only(self):
        # 体验类命中不压上限：影响面不属功能/数据/权限时不算严重度候选。
        result = self.ac.analyse([rec(1, "boundary")], residual_note="x",
                                 atk_entries=[self.atk(status="命中", impact="体验")])
        self.assertEqual(result["atk"]["severity_candidates"], [])
        self.assertNotIn("atk_hit_unreviewed", [g["code"] for g in result["gaps"]])

    def test_atk_not_hit_is_disclosure_only(self):
        result = self.ac.analyse([rec(1, "boundary")], residual_note="x",
                                 atk_entries=[self.atk(status="未命中")])
        self.assertEqual(result["atk"]["not_hit"], 1)
        self.assertNotIn("atk_hit_unreviewed", [g["code"] for g in result["gaps"]])

    def test_atk_requires_fuzzy_hypothesis_fields(self):
        # 模糊假设允许不严谨，但缺字段/非法枚举必须报错，不能默认通过。
        broken = [{"id": "ATK-001", "hypothesis": "", "validation_path": "x",
                   "impact": "功能", "status": "未执行"}]
        result = self.ac.analyse([rec(1, "boundary")], residual_note="x", atk_entries=broken)
        self.assertIn("atk_registry_invalid", [g["code"] for g in result["gaps"]])

    def test_atk_id_prefix_is_enforced(self):
        broken = [self.atk(ident="HYP-001")]
        result = self.ac.analyse([rec(1, "boundary")], residual_note="x", atk_entries=broken)
        self.assertIn("atk_registry_invalid", [g["code"] for g in result["gaps"]])

    def test_invalid_impact_and_status_rejected(self):
        bad_impact = [self.atk(impact="未知")]
        self.assertIn("atk_registry_invalid",
                      [g["code"] for g in self.ac.analyse([rec(1, "boundary")], residual_note="x",
                                                          atk_entries=bad_impact)["gaps"]])
        bad_status = [self.atk(status="大概")]
        self.assertIn("atk_registry_invalid",
                      [g["code"] for g in self.ac.analyse([rec(1, "boundary")], residual_note="x",
                                                          atk_entries=bad_status)["gaps"]])

    def test_atk_does_not_require_plan_amend(self):
        # ATK 存续不依赖事后 amend 纳入计划：不给 plan 也不产生缺口。
        result = self.ac.analyse([rec(1, "boundary")], residual_note="x",
                                 atk_entries=[self.atk(status="未执行")])
        self.assertNotIn("new_surface_amend_missing", [n["code"] for n in result["notices"]])

    def test_unresolved_review_does_not_close_atk_hit(self):
        # 未裁定（unresolved）不算闭环：证据不足的命中仍须压上限，
        # 否则一条 unresolved 记录就能让未裁定的命中悄悄放行。
        result = self.ac.analyse([rec(1, "boundary")], residual_note="x",
                                 atk_entries=[self.atk(status="命中", impact="数据")],
                                 reviews=[{"finding_id": "ATK-001", "verdict": "unresolved"}])
        self.assertIn("atk_hit_unreviewed", [g["code"] for g in result["gaps"]])
        self.assertEqual(result["verdict_ceiling"], "待核实")

    def test_accepted_review_without_bug_link_does_not_close_atk_hit(self):
        # accepted 但未建 BUG/未关联 → 未闭环（与台账同一口径）。
        result = self.ac.analyse([rec(1, "boundary")], residual_note="x",
                                 atk_entries=[self.atk(status="命中", impact="功能")],
                                 reviews=[{"finding_id": "ATK-001", "verdict": "accepted"}])
        self.assertIn("atk_hit_unreviewed", [g["code"] for g in result["gaps"]])

    def test_rejected_review_closes_atk_hit(self):
        # rejected（判为误报）已定性，不再压上限。
        result = self.ac.analyse([rec(1, "boundary")], residual_note="x",
                                 atk_entries=[self.atk(status="命中", impact="数据")],
                                 reviews=[{"finding_id": "ATK-001", "verdict": "rejected"}])
        self.assertNotIn("atk_hit_unreviewed", [g["code"] for g in result["gaps"]])

    def test_review_without_verdict_does_not_close_atk_hit(self):
        # 只凭"有一条记录"不算裁定：verdict 缺失按未闭环处理。
        result = self.ac.analyse([rec(1, "boundary")], residual_note="x",
                                 atk_entries=[self.atk(status="命中", impact="数据")],
                                 reviews=[{"finding_id": "ATK-001"}])
        self.assertIn("atk_hit_unreviewed", [g["code"] for g in result["gaps"]])

    def test_later_unclosed_review_reopens_closed_hit(self):
        # 复核 JSONL 为追加写入，判定只认每条 finding 的**最新**记录
        # （latest-wins，与 adversarial_gate 折叠口径一致）：旧 rejected
        # 不得盖住新 accepted 未闭环的翻案裁定。
        result = self.ac.analyse([rec(1, "boundary")], residual_note="x",
                                 atk_entries=[self.atk(status="命中", impact="数据")],
                                 reviews=[{"finding_id": "ATK-001", "verdict": "rejected"},
                                          {"finding_id": "ATK-001", "verdict": "accepted",
                                           "tie_to_bug": None}])
        self.assertIn("atk_hit_unreviewed", [g["code"] for g in result["gaps"]])

    def test_later_tied_review_closes_previously_unclosed_hit(self):
        # 反向：先 accepted 未闭环、补建 BUG 后追加 accepted+tie ——
        # 最新记录已闭环，历史记录不得永久压上限。
        result = self.ac.analyse([rec(1, "boundary")], residual_note="x",
                                 atk_entries=[self.atk(status="命中", impact="数据")],
                                 reviews=[{"finding_id": "ATK-001", "verdict": "accepted",
                                           "tie_to_bug": None},
                                          {"finding_id": "ATK-001", "verdict": "accepted",
                                           "tie_to_bug": "BUG-01"}])
        self.assertNotIn("atk_hit_unreviewed", [g["code"] for g in result["gaps"]])
        self.assertEqual(result["atk"]["verdict_ceiling"], "通过")

    def test_atk_sub_conclusion_matches_top_level(self):
        # 子结论与顶层结论必须一致：已闭环时顶层"通过"，atk 子结论不得仍是"待核实"。
        result = self.ac.analyse([rec(1, "boundary")], residual_note=RESIDUAL_CLEAR,
                                 atk_entries=[self.atk(status="命中", impact="数据")],
                                 reviews=[{"finding_id": "ATK-001", "verdict": "accepted",
                                           "tie_to_bug": "BUG-01"}])
        self.assertEqual(result["verdict_ceiling"], "通过")
        self.assertEqual(result["atk"]["verdict_ceiling"], "通过")
        self.assertEqual(result["atk"]["pending_candidates"], [])
        self.assertEqual(len(result["atk"]["closed_candidates"]), 1)

    def test_atk_explicit_severity_is_honoured(self):
        # 显式登记的严重度不得被静默忽略（此前恒为 major）。
        result = self.ac.analyse([rec(1, "boundary")], residual_note="x",
                                 atk_entries=[self.atk(status="命中", impact="数据",
                                                      severity="blocker")])
        self.assertEqual(result["atk"]["severity_candidates"][0]["severity"], "blocker")

    def test_atk_default_severity_follows_impact(self):
        # 未登记严重度时按影响面推定：功能/数据/权限 → major，体验 → minor。
        result = self.ac.analyse([rec(1, "boundary")], residual_note="x",
                                 atk_entries=[self.atk(status="命中", impact="数据")])
        self.assertEqual(result["atk"]["severity_candidates"][0]["severity"], "major")

    def test_atk_entry_details_are_exposed(self):
        # 登记内容必须能被读到——linked_tc/linked_ac/note 被归一化后不得成为死字段。
        entry = self.atk(status="未执行", linked_tc="TC-9", note="待后续轮次")
        result = self.ac.analyse([rec(1, "boundary")], residual_note="x", atk_entries=[entry])
        exported = result["atk"]["entries"][0]
        self.assertEqual(exported["linked_tc"], "TC-9")
        self.assertEqual(exported["note"], "待后续轮次")
        self.assertEqual(result["surfaces_beyond_plan"], 0)

    def test_no_atk_input_keeps_legacy_behaviour(self):
        result = self.ac.analyse([rec(1, "boundary")], residual_note="x")
        self.assertEqual(result["atk"]["registered"], 0)
        self.assertNotIn("atk_hit_unreviewed", [g["code"] for g in result["gaps"]])


class AtkCliTests(unittest.TestCase):
    """CLI 回归：--atk / --reviews 从文件读入并进入结论。"""

    def setUp(self):
        self.ac = ac

    def test_atk_and_reviews_from_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.jsonl"
            ledger.write_text(json.dumps({"id": "T-1", "path_type": "boundary",
                                          "hypothesis": "越界应被拒", "hypothesis_outcome": "held",
                                          "method": "boundary", "oracle_source": "self_attested"},
                                         ensure_ascii=False) + "\n", encoding="utf-8")
            atk_path = Path(tmp) / "atk.json"
            atk_path.write_text(json.dumps([{"id": "ATK-001", "hypothesis": "可能重复扣款",
                                             "validation_path": "并发提交比对流水",
                                             "impact": "功能", "status": "命中"}],
                                           ensure_ascii=False), encoding="utf-8")
            reviews = Path(tmp) / "reviews.jsonl"
            reviews.write_text(json.dumps({"finding_id": "ATK-001", "verdict": "accepted",
                                           "severity": "major", "tie_to_bug": "BUG-01"},
                                          ensure_ascii=False) + "\n", encoding="utf-8")
            code = self.ac.main(["--ledger", str(ledger), "--atk", str(atk_path),
                                  "--reviews", str(reviews), "--residual-note", "x"])
            self.assertEqual(code, 0)

    def test_input_errors_exit_with_code_two(self):
        # 输入/解析错误以 2 退出——与包内其它脚本口径一致，
        # 裸抛异常会变成退出码 1，调用方按 2 判失败就漏判。
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.jsonl"
            self.assertEqual(self.ac.main(["--ledger", str(missing)]), 2)
            bad_atk = Path(tmp) / "atk.json"
            bad_atk.write_text(json.dumps({"assumptions": "not-a-list"}), encoding="utf-8")
            self.assertEqual(self.ac.main(["--ledger", "/dev/null",
                                           "--atk", str(bad_atk)]), 2)


class AtkAbsentReasonTests(unittest.TestCase):
    """N4：零登记不是问题，未解释才是；三向双向验收。"""

    def test_no_atk_no_reason_is_a_gap(self):
        result = ac.analyse([rec(1, "boundary")], residual_note=RESIDUAL_CLEAR)
        self.assertIn("atk_absent_unexplained", [g["code"] for g in result["gaps"]])

    def test_no_atk_with_reason_is_notice_only(self):
        result = ac.analyse([rec(1, "boundary")], residual_note=RESIDUAL_CLEAR,
                           atk_absent_reason="本轮为只读界面回归，无前置攻击假设")
        self.assertNotIn("atk_absent_unexplained", [g["code"] for g in result["gaps"]])
        self.assertIn("atk_not_submitted", [n["code"] for n in result["notices"]])

    def test_atk_provided_is_unaffected(self):
        atk = [{"id": "ATK-001", "hypothesis": "重复提交应被拒绝",
                "validation_path": "双人提交比对后台流水", "impact": "功能", "status": "未执行"}]
        result = ac.analyse([rec(1, "boundary")], residual_note=RESIDUAL_CLEAR, atk_entries=atk)
        self.assertNotIn("atk_absent_unexplained", [g["code"] for g in result["gaps"]])

    def test_empty_atk_registry_needs_reason(self):
        """`--atk` 传空数组仍是零登记：认 None 不认空文件会留下免解释旁路，
        还会把「什么都没写」标成已核对。"""
        result = ac.analyse([rec(1, "boundary")], residual_note=RESIDUAL_CLEAR,
                            atk_entries=[])
        self.assertIn("atk_absent_unexplained", [g["code"] for g in result["gaps"]])
        self.assertFalse(result["atk_checked"])

    def test_empty_atk_registry_with_reason_is_disclosed(self):
        result = ac.analyse([rec(1, "boundary")], residual_note=RESIDUAL_CLEAR,
                            atk_entries=[], atk_absent_reason=NO_ATK_REASON)
        self.assertNotIn("atk_absent_unexplained", [g["code"] for g in result["gaps"]])
        self.assertIn("atk_not_submitted", [n["code"] for n in result["notices"]])


class HypothesisSharpnessTests(unittest.TestCase):
    """N5：空话假设不计覆盖；锐利自然语言假设不得被误伤。"""

    def test_vague_hypothesis_is_gap_and_not_counted_supported(self):
        record = rec(1, "boundary")
        record["hypothesis"] = "如果输入不对就会坏"
        result = ac.analyse([record], residual_note=RESIDUAL_CLEAR)
        self.assertIn("hypothesis_vague", [g["code"] for g in result["gaps"]])
        self.assertEqual(result["surface_coverage"]["SURF-01"]["supported"], 0)

    def test_sharp_natural_language_is_not_flagged(self):
        record = rec(1, "boundary")
        record["hypothesis"] = "删除后条目应消失"
        result = ac.analyse([record], residual_note=RESIDUAL_CLEAR)
        self.assertNotIn("hypothesis_vague", [g["code"] for g in result["gaps"]])

    def test_numeric_hypothesis_is_sharp(self):
        record = rec(1, "boundary")
        record["hypothesis"] = "卷名 256 字符时保存应拒绝并保留输入"
        result = ac.analyse([record], residual_note=RESIDUAL_CLEAR,
                            atk_absent_reason=NO_ATK_REASON)
        self.assertNotIn("hypothesis_vague", [g["code"] for g in result["gaps"]])

    def test_vague_hypothesis_that_broke_something_is_output_not_gap(self):
        """笼统假设真的打破了：打破即产出，只补披露不压档——
        按缺口处理会让「诚实但写得笼统、却挖到真 bug」的记录反被扣分。"""
        record = rec(1, "boundary")
        record["hypothesis"] = "如果输入不对就会坏"
        record["hypothesis_outcome"] = "broken"
        result = ac.analyse([record], residual_note=RESIDUAL_CLEAR,
                            atk_absent_reason=NO_ATK_REASON)
        self.assertNotIn("hypothesis_vague", [g["code"] for g in result["gaps"]])
        self.assertIn("hypothesis_vague_but_broken", [n["code"] for n in result["notices"]])
        self.assertEqual(result["hypotheses"]["broken"], 1)
        self.assertEqual(result["hypotheses"]["vague_but_broken"], 1)


class ReplayObligationTests(unittest.TestCase):
    """N6：高风险面单一方法且结果全部 held，须换方法复打或提交点名不复打理由。"""

    def _records(self, broken=False, second_method=False):
        records = [rec(i, "boundary") for i in range(4)]
        records[0]["hypothesis"] = "卷名 256 字符时保存应拒绝并保留输入"
        if broken:
            records[0]["hypothesis_outcome"] = "broken"
        if second_method:
            records[3]["method"] = "fault_injection"
        return records

    def _analyse(self, records, **kwargs):
        return ac.analyse(
            records, plan_surfaces={"SURF-01"}, plan_risks={"SURF-01": "high"},
            residual_note={"items": [{"id": "SURF-01", "status": "unbroken"}]},
            atk_absent_reason=NO_ATK_REASON, **kwargs)

    def test_all_held_single_method_without_replay_is_gap(self):
        result = self._analyse(self._records())
        self.assertIn("surface_unreplayed_unexplained", [g["code"] for g in result["gaps"]])

    def test_replay_note_clears_gap(self):
        result = self._analyse(self._records(),
                               replay_notes={"SURF-01": "复用 TC-1 边界输入变体，见既有观察"})
        self.assertNotIn("surface_unreplayed_unexplained", [g["code"] for g in result["gaps"]])

    def test_broken_surface_is_not_gated(self):
        result = self._analyse(self._records(broken=True))
        self.assertNotIn("surface_unreplayed_unexplained", [g["code"] for g in result["gaps"]])

    def test_second_method_probe_clears_gap(self):
        result = self._analyse(self._records(second_method=True))
        self.assertNotIn("surface_unreplayed_unexplained", [g["code"] for g in result["gaps"]])

    def test_critical_is_not_treated_as_high_risk(self):
        """风险枚举全项目统一为 high/medium/low：critical 不是合法高风险档位，
        不进 N6 单一方法义务，也不进高风险复测建议（防枚举回退到 high/critical 双档）。"""
        result = ac.analyse(
            self._records(), plan_surfaces={"SURF-01"},
            plan_risks={"SURF-01": "critical"},
            residual_note={"items": [{"id": "SURF-01", "status": "unbroken"}]},
            atk_absent_reason=NO_ATK_REASON)
        self.assertNotIn("surface_unreplayed_unexplained", [g["code"] for g in result["gaps"]])
        self.assertEqual(result["high_risk_single_method_surfaces"], [])
        self.assertEqual(result["retest_suggestions"], [])


class AtkNegativeEvidenceTests(unittest.TestCase):
    """M1：ATK「未命中」的证据等级门禁——推演不能冒充实测，practical 须挂证据。

    判定三重交集：status=未命中 × verified_by × impact 属功能/数据/权限。
    - 推演/走读（simulation/code_walk）判「未命中」→ 须 --replay-note 点名解释或补实操；
    - practical 判「未命中」→ 须 attempt_id 绑定 E1 FINISH（case_id 关联 + 哈希复核）
      或 evidence 落盘文件+哈希；--replay-note 不能替代实操证据；
    - verified_by 缺省 unknown（旧台账）→ 零改动兼容，不触发任何新门禁。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.evidence_file = self.dir / "probe-result.png"
        self.evidence_file.write_bytes(b"probe evidence bytes")

    def _evidence(self):
        import hashlib
        return [{"path": str(self.evidence_file.resolve()),
                 "sha256": hashlib.sha256(self.evidence_file.read_bytes()).hexdigest()}]

    def _finish(self, attempt_id="AT-1", case_id="TC-9", status="PASS", grade="E1"):
        event = {"kind": "FINISH", "attempt_id": attempt_id, "case_id": case_id,
                 "status": status, "evidence": self._evidence()}
        if grade:
            event["evidence_grade"] = grade
        return event

    def _atk(self, **extra):
        item = {"id": "ATK-001", "hypothesis": "并发提交可能重复扣款",
                "validation_path": "并发提交比对流水", "impact": "功能",
                "status": "未命中"}
        item.update(extra)
        return item

    def _codes(self, result):
        return [g["code"] for g in result["gaps"]]

    def _notice_codes(self, result):
        return [n["code"] for n in result["notices"]]

    # 1) 推演判「未命中」且无解释 → gap
    def test_simulation_negative_without_note_is_gap(self):
        result = ac.analyse([rec(1, "boundary")], residual_note="x",
                            atk_entries=[self._atk(verified_by="simulation")])
        self.assertIn("atk_negative_by_simulation_unexplained", self._codes(result))

    # 2) 同上 + replay_note 点名该 ATK → 豁免
    def test_simulation_negative_with_replay_note_is_exempt(self):
        result = ac.analyse([rec(1, "boundary")], residual_note="x",
                            atk_entries=[self._atk(verified_by="simulation")],
                            replay_notes={"ATK-001": "推演依据第 3 轮并发观察 OBS-7，见既有台账"})
        self.assertNotIn("atk_negative_by_simulation_unexplained", self._codes(result))

    # 3) practical 无 attempt_id 且无 evidence → gap
    def test_practical_negative_without_evidence_is_gap(self):
        result = ac.analyse([rec(1, "boundary")], residual_note="x",
                            atk_entries=[self._atk(verified_by="practical")])
        self.assertIn("atk_practical_negative_unproven", self._codes(result))

    # 4) attempt_id 指向 E1 FINISH、case_id 匹配、哈希完好 → 无 gap
    def test_practical_negative_bound_to_e1_finish_passes(self):
        atk = self._atk(verified_by="practical", attempt_id="AT-1", linked_tc="TC-9")
        result = ac.analyse([self._finish()], residual_note="x", atk_entries=[atk])
        self.assertNotIn("atk_practical_negative_unproven", self._codes(result))
        self.assertNotIn("atk_practical_negative_grade_unset", self._notice_codes(result))

    # 5) case_id 与 linked_tc 不匹配 → gap（跨案件挂靠）
    def test_case_id_mismatch_is_gap(self):
        atk = self._atk(verified_by="practical", attempt_id="AT-1", linked_tc="TC-OTHER")
        result = ac.analyse([self._finish()], residual_note="x", atk_entries=[atk])
        self.assertIn("atk_practical_negative_unproven", self._codes(result))

    # 6) E2 等级不合格 → gap
    def test_e2_grade_is_gap(self):
        atk = self._atk(verified_by="practical", attempt_id="AT-1", linked_tc="TC-9")
        result = ac.analyse([self._finish(grade="E2")], residual_note="x", atk_entries=[atk])
        self.assertIn("atk_practical_negative_unproven", self._codes(result))

    # 7) 旧格式 FINISH（未设 evidence_grade）→ 无 gap 但产生 notice
    def test_legacy_finish_without_grade_passes_with_notice(self):
        atk = self._atk(verified_by="practical", attempt_id="AT-1", linked_tc="TC-9")
        result = ac.analyse([self._finish(grade=None)], residual_note="x", atk_entries=[atk])
        self.assertNotIn("atk_practical_negative_unproven", self._codes(result))
        self.assertIn("atk_practical_negative_grade_unset", self._notice_codes(result))

    # 7b) 审核修复：键存在但为空串不是"未设"——只能来自手工拼 JSON 绕过工具写入，
    #     属新写入规避，必须判 gap 而不是洗成历史兼容 notice
    def test_empty_string_grade_is_evasion_not_legacy(self):
        event = self._finish(grade=None)
        event["evidence_grade"] = ""
        atk = self._atk(verified_by="practical", attempt_id="AT-1", linked_tc="TC-9")
        result = ac.analyse([event], residual_note="x", atk_entries=[atk])
        self.assertIn("atk_practical_negative_unproven", self._codes(result))
        self.assertNotIn("atk_practical_negative_grade_unset", self._notice_codes(result))

    # 7c) 键为 null 与缺键同义（写入侧 None 即不写键），按历史兼容处理
    def test_null_grade_is_treated_as_legacy(self):
        event = self._finish(grade=None)
        event["evidence_grade"] = None
        atk = self._atk(verified_by="practical", attempt_id="AT-1", linked_tc="TC-9")
        result = ac.analyse([event], residual_note="x", atk_entries=[atk])
        self.assertNotIn("atk_practical_negative_unproven", self._codes(result))
        self.assertIn("atk_practical_negative_grade_unset", self._notice_codes(result))

    # 7d) 小写 "e1" 与写入侧合法枚举等价（ledger 校验前会拒绝小写，手工值宽容到大小写）
    def test_lowercase_grade_is_accepted(self):
        atk = self._atk(verified_by="practical", attempt_id="AT-1", linked_tc="TC-9")
        result = ac.analyse([self._finish(grade="e1")], residual_note="x", atk_entries=[atk])
        self.assertNotIn("atk_practical_negative_unproven", self._codes(result))
        self.assertNotIn("atk_practical_negative_grade_unset", self._notice_codes(result))

    # 8) 体验类不扩大义务
    def test_experience_impact_practical_negative_not_gated(self):
        result = ac.analyse([rec(1, "boundary")], residual_note="x",
                            atk_entries=[self._atk(impact="体验", verified_by="practical")])
        self.assertNotIn("atk_practical_negative_unproven", self._codes(result))
        self.assertNotIn("atk_negative_by_simulation_unexplained", self._codes(result))

    # 9) 回归：旧台账无 verified_by（缺省 unknown）→ 不触发任何新门禁
    def test_legacy_registry_without_verified_by_untouched(self):
        result = ac.analyse([rec(1, "boundary")], residual_note="x",
                            atk_entries=[self._atk()])
        self.assertNotIn("atk_negative_by_simulation_unexplained", self._codes(result))
        self.assertNotIn("atk_practical_negative_unproven", self._codes(result))
        self.assertEqual(result["atk"]["not_hit_verified_by"]["unknown"], 1)

    def test_invalid_verified_by_rejected(self):
        result = ac.analyse([rec(1, "boundary")], residual_note="x",
                            atk_entries=[self._atk(verified_by="脑内推演")])
        self.assertIn("atk_registry_invalid", self._codes(result))

    def test_evidence_without_hash_rejected(self):
        # evidence 不是 Path.exists() 的放宽版：落盘证据必须带哈希
        atk = self._atk(verified_by="practical", evidence=[{"path": "x.png"}])
        result = ac.analyse([rec(1, "boundary")], residual_note="x", atk_entries=[atk])
        self.assertIn("atk_registry_invalid", self._codes(result))

    def test_hash_mismatch_evidence_is_gap(self):
        atk = self._atk(verified_by="practical",
                        evidence=[{"path": str(self.evidence_file.resolve()),
                                   "sha256": "0" * 64}])
        result = ac.analyse([rec(1, "boundary")], residual_note="x", atk_entries=[atk])
        self.assertIn("atk_practical_negative_unproven", self._codes(result))

    def test_attempt_id_not_in_ledger_is_gap(self):
        atk = self._atk(verified_by="practical", attempt_id="AT-MISSING", linked_tc="TC-9")
        result = ac.analyse([self._finish()], residual_note="x", atk_entries=[atk])
        self.assertIn("atk_practical_negative_unproven", self._codes(result))

    def test_blocked_finish_does_not_count(self):
        # status ∈ {PASS, FAIL} 才算执行完成：BLOCKED 是中途弃置，不能挂靠
        atk = self._atk(verified_by="practical", attempt_id="AT-1", linked_tc="TC-9")
        result = ac.analyse([self._finish(status="BLOCKED")], residual_note="x",
                            atk_entries=[atk])
        self.assertIn("atk_practical_negative_unproven", self._codes(result))

    def test_file_evidence_fallback_passes(self):
        # 降级路径：确实不依附任何执行时可用落盘证据（note 须写明为何没有 run）
        atk = self._atk(verified_by="practical", evidence=self._evidence(),
                        note="独立探针，不依附任何台账执行")
        result = ac.analyse([rec(1, "boundary")], residual_note="x", atk_entries=[atk])
        self.assertNotIn("atk_practical_negative_unproven", self._codes(result))

    def test_linked_ac_only_unproven_carries_manual_check_note(self):
        # linked_tc 为空但 linked_ac 非空：绑定仍可用，但 gap 消息须附人工核对义务
        atk = self._atk(verified_by="practical", attempt_id="AT-MISSING",
                        linked_ac="AC-3")
        result = ac.analyse([self._finish()], residual_note="x", atk_entries=[atk])
        gap = [g for g in result["gaps"] if g["code"] == "atk_practical_negative_unproven"][0]
        self.assertIn("人工核对 AC 与执行记录的对应性", gap["message"])

    def test_not_hit_verified_by_distribution_exposed(self):
        # M3 披露口径：未命中的验证方式分布（practical/simulation/code_walk/unknown）
        entries = [self._atk(verified_by="simulation"),
                   self._atk(id="ATK-002", verified_by="practical"),
                   self._atk(id="ATK-003", verified_by="code_walk")]
        result = ac.analyse([rec(1, "boundary")], residual_note="x", atk_entries=entries)
        self.assertEqual(result["atk"]["not_hit_verified_by"],
                         {"practical": 1, "simulation": 1, "code_walk": 1, "unknown": 0})

    def test_atk_stagnant_grouped_by_impact(self):
        # M3：atk_stagnant 按 impact 分组呈现，功能/数据/权限单列一行
        atk_prev = [self._atk(status="未执行"),
                    {"id": "ATK-002", "hypothesis": "h2", "validation_path": "路径 v",
                     "impact": "体验", "status": "未执行"}]
        atk_curr = [self._atk(status="未执行"),
                    {"id": "ATK-002", "hypothesis": "h2", "validation_path": "路径 v",
                     "impact": "体验", "status": "未执行"}]
        result = ac.analyse([rec(1, "boundary")], residual_note="x",
                            atk_entries=atk_curr, prev_atk_entries=atk_prev)
        stagnant = [n for n in result["notices"] if n["code"] == "atk_stagnant"][0]
        self.assertIn("功能：", stagnant["message"])
        self.assertIn("体验：", stagnant["message"])


if __name__ == "__main__":
    unittest.main()