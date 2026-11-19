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
        result = ac.analyse([rec(i, "positive") for i in range(10)])
        self.assertEqual(result["attack_density"], 0.0)
        self.assertIn("attack_density_low", [g["code"] for g in result["gaps"]])
        self.assertEqual(result["verdict_ceiling"], "待核实")

    def test_healthy_ledger_passes(self):
        records = [rec(i, "positive") for i in range(7)]
        records += [rec(i, "boundary") for i in range(3)]
        result = ac.analyse(records, residual_note="未破：并发删除与跨租户读取")
        self.assertEqual(result["attack_density"], 0.3)
        self.assertEqual(result["gaps"], [])
        self.assertEqual(result["verdict_ceiling"], "通过")

    def test_missing_fields_report_gaps_not_pass(self):
        # 历史台账形态：只有 id/status/desc，没有任何攻击面字段
        result = ac.analyse([{"id": f"X-{i}", "status": "PASS", "desc": "创建题型"}
                             for i in range(5)])
        codes = [g["code"] for g in result["gaps"]]
        self.assertIn("path_type_missing", codes)
        self.assertIn("surf_linkage_low", codes)
        self.assertIn("method_missing", codes)
        self.assertEqual(result["attack_density"], "NA")
        self.assertEqual(result["verdict_ceiling"], "待核实")

    def test_self_attested_oracle_is_a_gap(self):
        records = [rec(i, "boundary", oracle="self_attested") for i in range(4)]
        result = ac.analyse(records)
        self.assertIn("oracle_self_attested", [g["code"] for g in result["gaps"]])

    def test_incidental_only_without_designed_probe(self):
        records = [rec(i, "boundary", mode="incidental") for i in range(4)]
        result = ac.analyse(records)
        self.assertIn("no_designed_probe", [g["code"] for g in result["gaps"]])

    def test_inferred_values_do_not_gate(self):
        # 显式声明全部达标，但文本里满是"边界/异常"字样 → 推断值不应影响判定
        records = [rec(i, "positive") for i in range(6)]
        records += [rec(i, "boundary") for i in range(4)]
        for r in records:
            r["desc"] = "边界值 异常 失败 重复提交"
        result = ac.analyse(records, residual_note="未破：并发删除与跨租户读取")
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
                                  "--residual-note", "未破：并发删除"]), 0)

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
        codes = [g["code"] for g in ac.analyse(records)["gaps"]]
        self.assertIn("surf_format_invalid", codes)
        self.assertIn("surf_linkage_low", codes)

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
        self.assertIn("oracle_self_attested", [g["code"] for g in result["gaps"]])

    def test_residual_note_required_before_pass(self):
        records = [rec(i, "positive") for i in range(7)] + [rec(i, "boundary") for i in range(3)]
        without = ac.analyse(records)
        self.assertIn("residual_note_missing", [g["code"] for g in without["gaps"]])
        self.assertEqual(without["verdict_ceiling"], "待核实")
        with_note = ac.analyse(records, residual_note="未破：并发删除与跨租户读取")
        self.assertEqual(with_note["gaps"], [])
        self.assertEqual(with_note["verdict_ceiling"], "通过")

    def test_plan_cross_check_flags_unknown_surface(self):
        records = [rec(i, "boundary") for i in range(4)]
        for record in records:
            record["surf_id"] = "SURF-99"
        result = ac.analyse(records, plan_surfaces={"SURF-01"}, residual_note="x")
        self.assertIn("surf_unknown", [g["code"] for g in result["gaps"]])
        self.assertTrue(result["surf_plan_checked"])

    def test_method_diversity_required_on_large_ledger(self):
        records = [rec(i, "boundary") for i in range(25)]
        result = ac.analyse(records, residual_note="x")
        self.assertIn("method_low_diversity", [g["code"] for g in result["gaps"]])


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


if __name__ == "__main__":
    unittest.main()
