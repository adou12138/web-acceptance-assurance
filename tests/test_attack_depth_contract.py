"""攻击面深度校验（M4/M5/M6）的机械行为。

本文件守住三件事：

1. **M4 深度校验分开处理**：无判据占位、缺真实执行证据、高风险面方法单一
   三者分开判定——补法不同，合成一条时使用方不知道该补什么。
   另外两条硬规则：**缺结果不能计为 held**（没跑出结果 ≠ 假设没被打破），
   **无独立判据或证据无法支持覆盖**（写了预测 ≠ 这个面被打过）。
   可疑自然语言与同方法重复**仅提示**，不压上限——误伤合法描述会把如实登记变成违规。
2. **M5 残余风险声明三态**：有未破项 / 无未破项 / 尚未验证。
   引用须与风险面、对应观察及验证状态一致——编号不在册是虚构编号，
   状态与实际不符是关联错误，两者都压上限。
3. **M6 ATK 停滞披露**：提供上一可比批次快照后，同范围同稳定 ID 仍未执行、
   验证路径无实质变化的条目判为停滞。**只披露，不改上限**。

浅记录与有效证据双向测试：既不许浅记录冒充已验证，也不许把证据充分的路径误压。
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import attack_coverage as ac  # noqa: E402

CHANNEL = "网页端 DOM 读回 + 接口响应双通道"


def rec(i, path_type="boundary", surf="SURF-01", method="boundary",
        oracle="independent", status="PASS", evidence=True, channel=CHANNEL):
    """一条标准执行记录。oracle=independent 时默认带独立通道与证据。"""
    record = {"id": f"TC-{i}", "status": status, "desc": "字段校验",
              "path_type": path_type, "method": method,
              "discovery_mode": "designed_probe"}
    if surf:
        record["surf_id"] = surf
    if oracle:
        record["oracle_source"] = oracle
    if oracle == "independent":
        if channel is not None:
            record["oracle_channel"] = channel
        if evidence:
            record["evidence"] = [f"evidence/tc-{i}.png"]
    return record


def with_hypothesis(record, text="并发建卷时卷名重复应拒绝", outcome=None):
    record["hypothesis"] = text
    if outcome:
        record["hypothesis_outcome"] = outcome
    return record


class M4DepthJudgements(unittest.TestCase):
    """M4：结构校验与假设质检。"""

    def test_missing_oracle_and_missing_evidence_are_separate(self):
        """无判据占位与缺真实执行证据必须分开报——补法不同。"""
        no_channel = [rec(1), rec(2)]
        no_channel[0].pop("oracle_channel")
        no_channel[1].pop("oracle_channel")
        gaps = [g["code"] for g in ac.analyse(no_channel, residual_note="x")["gaps"]]
        self.assertIn("oracle_no_criterion", gaps)
        self.assertNotIn("oracle_no_evidence", gaps)

        no_evidence = [rec(1, evidence=False), rec(2, evidence=False)]
        gaps = [g["code"] for g in ac.analyse(no_evidence, residual_note="x")["gaps"]]
        self.assertIn("oracle_no_evidence", gaps)
        self.assertNotIn("oracle_no_criterion", gaps)

    def test_missing_result_is_not_counted_as_held(self):
        """缺结果不算「未打破」——否则未验证会冒充已验证。"""
        record = with_hypothesis(rec(1, status="NOT_RUN"))
        result = ac.analyse([record], residual_note="x")
        self.assertEqual(result["hypotheses"]["no_result"], 1)
        self.assertEqual(result["hypotheses"]["held"], 0)
        self.assertIn("hypothesis_without_result",
                      [g["code"] for g in result["gaps"]])

    def test_explicit_held_still_counts(self):
        """显式声明 held 不受影响——机制只针对缺结果。"""
        record = with_hypothesis(rec(1), outcome="held")
        result = ac.analyse([record], residual_note="x")
        self.assertEqual(result["hypotheses"]["held"], 1)
        self.assertEqual(result["hypotheses"]["no_result"], 0)

    def test_shallow_record_cannot_support_coverage(self):
        """写了预测但无独立判据/无证据 → 该面不算已覆盖。"""
        records = [with_hypothesis(rec(i, oracle="self_attested")) for i in range(4)]
        result = ac.analyse(records, plan_surfaces={"SURF-01"},
                            plan_risks={"SURF-01": "high"}, residual_note="x")
        codes = [g["code"] for g in result["gaps"]]
        self.assertIn("surface_hypothesis_unverified", codes)
        self.assertEqual(result["surface_coverage"]["SURF-01"]["supported"], 0)

    def test_valid_evidence_supports_coverage(self):
        """证据充分的路径不得被误压——双向测试的另一半。"""
        records = [rec(i) for i in range(4)]
        with_hypothesis(records[0], outcome="held")
        result = ac.analyse(records, plan_surfaces={"SURF-01"},
                            plan_risks={"SURF-01": "high"}, residual_note="x")
        self.assertEqual(result["surface_coverage"]["SURF-01"]["supported"], 1)
        self.assertNotIn("surface_hypothesis_unverified",
                         [g["code"] for g in result["gaps"]])

    def test_high_risk_single_method_is_disclosed_with_retest_plan(self):
        """高风险面方法单一：单独提示，并给出关联风险面/观察/可用范围的复测建议。"""
        records = [rec(i) for i in range(4)]
        result = ac.analyse(records, plan_surfaces={"SURF-01"},
                            plan_risks={"SURF-01": "high"}, residual_note="x")
        notices = {n["code"]: n for n in result["notices"]}
        self.assertIn("high_risk_surface_single_method", notices)
        self.assertEqual(result["high_risk_single_method_surfaces"], ["SURF-01"])
        # 建议必须三要素齐备：风险面、既有观察、可用方法/路径
        suggestion = result["retest_suggestions"][0]
        self.assertEqual(suggestion["surface"], "SURF-01")
        self.assertTrue(suggestion["observations"])
        self.assertTrue(suggestion["available_methods"])
        self.assertTrue(suggestion["available_paths"])
        # 只提示，不压上限
        self.assertNotIn("high_risk_surface_single_method",
                         [g["code"] for g in result["gaps"]])

    def test_multi_method_high_risk_surface_is_not_flagged(self):
        """多方法的高风险面不得被提示单一方法。"""
        records = [rec(i, method="boundary") for i in range(3)]
        records += [rec(3, method="fault_injection")]
        result = ac.analyse(records, plan_surfaces={"SURF-01"},
                            plan_risks={"SURF-01": "high"}, residual_note="x")
        self.assertNotIn("high_risk_surface_single_method",
                         [n["code"] for n in result["notices"]])
        self.assertEqual(result["retest_suggestions"], [])

    def test_suspicious_wording_is_notice_only(self):
        """可疑自然语言只提示，不压上限。"""
        records = [with_hypothesis(rec(1), text="这个应该没问题")]
        result = ac.analyse(records, residual_note="x")
        self.assertIn("suspicious_wording", [n["code"] for n in result["notices"]])
        self.assertNotIn("suspicious_wording", [g["code"] for g in result["gaps"]])

    def test_legitimate_natural_language_is_not_flagged(self):
        """合法自然语言不得被误伤——否则如实登记变成违规。"""
        records = [with_hypothesis(rec(1), text="并发建卷时卷名重复应拒绝")]
        result = ac.analyse(records, residual_note="x")
        self.assertNotIn("suspicious_wording",
                         [n["code"] for n in result["notices"]])

    def test_single_method_repeat_is_notice_only(self):
        """同方法重复仅提示，不压上限（既定口径：方法多样性不是配额）。"""
        records = [rec(i) for i in range(25)]
        result = ac.analyse(records, residual_note="x")
        self.assertIn("single_method_surface", [n["code"] for n in result["notices"]])
        self.assertNotIn("single_method_surface", [g["code"] for g in result["gaps"]])


class M5ResidualDeclaration(unittest.TestCase):
    """M5：残余风险声明三态与逐条核对。"""

    def setUp(self):
        # SURF-01：可采信验证且未破 → 实际状态 unbroken
        self.records = [rec(i) for i in range(4)]
        with_hypothesis(self.records[0], outcome="held")

    def declare(self, items):
        return json.dumps({"items": items}, ensure_ascii=False)

    def test_true_reference_with_matching_status(self):
        """真编号且状态相符 → 有未破项。"""
        result = ac.analyse(self.records, plan_surfaces={"SURF-01"},
                            residual_note=self.declare(
                                [{"id": "SURF-01", "status": "unbroken"}]))
        declaration = result["residual_declaration"]
        self.assertEqual(declaration["mode"], "structured")
        self.assertEqual(declaration["state"], "has_unbroken")
        self.assertEqual(declaration["items"][0]["verdict"], "matched")

    def test_fabricated_reference_is_a_gap(self):
        """虚构编号不在册 → 记缺口压上限。"""
        result = ac.analyse(self.records, plan_surfaces={"SURF-01"},
                            residual_note=self.declare(
                                [{"id": "SURF-99", "status": "unbroken"}]))
        self.assertIn("residual_ref_not_registered",
                      [g["code"] for g in result["gaps"]])
        self.assertEqual(result["residual_declaration"]["state"], "not_verified")

    def test_status_mismatch_is_a_gap(self):
        """编号存在但状态与实际不符 → 关联错误，记缺口压上限。"""
        result = ac.analyse(self.records, plan_surfaces={"SURF-01"},
                            residual_note=self.declare(
                                [{"id": "SURF-01", "status": "broken"}]))
        self.assertIn("residual_status_mismatch", [g["code"] for g in result["gaps"]])
        self.assertEqual(result["residual_declaration"]["state"], "not_verified")

    def test_zero_items_means_no_unbroken(self):
        """零项 → 无未破项（明确声明没有残留未破攻击面）。"""
        result = ac.analyse(self.records, plan_surfaces={"SURF-01"},
                            residual_note=self.declare([]))
        self.assertEqual(result["residual_declaration"]["state"], "no_unbroken")
        self.assertNotIn("residual_ref_not_registered",
                         [g["code"] for g in result["gaps"]])

    def test_not_verified_item_is_not_verified_state(self):
        """声明为尚未验证 → 三态中的「尚未验证」，如实登记不压上限。"""
        result = ac.analyse(self.records, plan_surfaces={"SURF-01"},
                            residual_note=self.declare(
                                [{"id": "SURF-01", "status": "not_verified"}]))
        self.assertEqual(result["residual_declaration"]["state"], "not_verified")
        self.assertIn("residual_not_verified",
                      [n["code"] for n in result["notices"]])

    def test_legacy_text_entry_still_works(self):
        """旧文本入口保持可用：只判 anchored，不做逐条核对。"""
        result = ac.analyse(self.records, plan_surfaces={"SURF-01"},
                            residual_note="未破：并发删除与跨租户读取")
        self.assertEqual(result["residual_declaration"]["mode"], "text")
        self.assertIsNone(result["residual_declaration"]["state"])
        self.assertNotIn("residual_status_mismatch",
                         [g["code"] for g in result["gaps"]])

    def test_broken_surface_reports_broken_actual_status(self):
        """该面实际已破时，实际状态必须是 broken——声明 broken 才相符。"""
        records = [rec(i) for i in range(4)]
        with_hypothesis(records[0], outcome="broken")
        result = ac.analyse(records, plan_surfaces={"SURF-01"},
                            residual_note=self.declare(
                                [{"id": "SURF-01", "status": "broken"}]))
        self.assertEqual(result["residual_declaration"]["state"], "no_unbroken")


class M6AtkStagnation(unittest.TestCase):
    """M6：ATK 跨轮停滞披露。"""

    @staticmethod
    def atk(ident="ATK-001", status="未执行", path="人工走查下单流程", impact="功能"):
        return {"id": ident, "hypothesis": "重复下单应被拒绝", "validation_path": path,
                "impact": impact, "status": status}

    def test_no_snapshot_means_no_stagnation(self):
        """没有快照 → 无从比较，不报停滞。"""
        result = ac.analyse([rec(1)], residual_note="x",
                            atk_entries=[self.atk()])
        self.assertEqual(result["atk_stagnant_ids"], [])
        self.assertNotIn("atk_stagnant", [n["code"] for n in result["notices"]])
        self.assertFalse(result["atk_stagnant_checked"])

    def test_same_id_same_path_still_unexecuted_is_stagnant(self):
        """同范围同 ID 仍未执行、验证路径无变化 → 停滞。"""
        result = ac.analyse([rec(1)], residual_note="x",
                            atk_entries=[self.atk()],
                            prev_atk_entries=[self.atk()])
        self.assertEqual(result["atk_stagnant_ids"], ["ATK-001"])
        self.assertIn("atk_stagnant", [n["code"] for n in result["notices"]])

    def test_stagnation_does_not_change_ceiling(self):
        """停滞只披露，不改变结论上限。"""
        result = ac.analyse([rec(1)], residual_note="x",
                            atk_entries=[self.atk()],
                            prev_atk_entries=[self.atk()])
        self.assertEqual(result["verdict_ceiling"], "通过")

    def test_progress_makes_it_not_stagnant(self):
        """状态有进展 → 不算停滞。"""
        result = ac.analyse([rec(1)], residual_note="x",
                            atk_entries=[self.atk(status="命中")],
                            prev_atk_entries=[self.atk()])
        self.assertEqual(result["atk_stagnant_ids"], [])

    def test_old_id_reuse_is_not_stagnant(self):
        """旧 ID 被复用（验证路径变了）→ 不是同一件事，不算停滞。"""
        result = ac.analyse([rec(1)], residual_note="x",
                            atk_entries=[self.atk(path="人工走查下单流程")],
                            prev_atk_entries=[self.atk(path="脚本构造并发下单")])
        self.assertEqual(result["atk_stagnant_ids"], [])

    def test_different_scope_is_not_stagnant(self):
        """不同范围的快照（无共同条目）→ 不算停滞。"""
        result = ac.analyse([rec(1)], residual_note="x",
                            atk_entries=[self.atk(ident="ATK-002")],
                            prev_atk_entries=[self.atk(ident="ATK-001")])
        self.assertEqual(result["atk_stagnant_ids"], [])


class ProductVersionStamp(unittest.TestCase):
    """产物必须自带判据版本：同一台账跨版本可能得到不同上限，无版本即无法归因。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)

    def test_cli_output_carries_schema_and_tool_version(self):
        """CLI 产出的 JSON 与 CSV 都要能回答「这条结论是哪一版判据判的」。"""
        ledger = self.folder / "ledger.jsonl"
        ledger.write_text("\n".join(json.dumps(with_hypothesis(rec(i), outcome="held"))
                                    for i in range(1, 3)) + "\n", encoding="utf-8")
        csv_path = self.folder / "out.csv"
        out = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "attack_coverage.py"),
             "--ledger", str(ledger), "--emit-json", str(self.folder / "out.json"),
             "--emit-csv", str(csv_path)],
            capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(out.returncode, 0, out.stderr)
        result = json.loads((self.folder / "out.json").read_text(encoding="utf-8"))
        self.assertEqual(result["schema"], ac.COVERAGE_SCHEMA)
        self.assertEqual(result["tool_version"], ac.TOOL_VERSION)
        self.assertIn("tool_version", csv_path.read_text(encoding="utf-8-sig"))

    def test_tool_version_is_declared_as_criteria_version(self):
        """版本号表达「判定规则变没变」，不是包版本：它必须与 schema 分开维护。"""
        self.assertTrue(ac.TOOL_VERSION)
        self.assertNotEqual(ac.TOOL_VERSION, ac.COVERAGE_SCHEMA)


class CliContract(unittest.TestCase):
    """CLI 入口：新参数必须真的可用，不是死参数。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)

    def _run(self, args):
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "attack_coverage.py"), *args],
            capture_output=True, text=True, encoding="utf-8")

    def test_residual_declaration_file_is_consumed(self):
        """--residual-declaration 传入的结构化声明必须被真实消费。"""
        ledger = self.folder / "ledger.jsonl"
        ledger.write_text(json.dumps(rec(1)), encoding="utf-8")
        declaration = self.folder / "residual.json"
        declaration.write_text(json.dumps({"items": [{"id": "SURF-01", "status": "unbroken"}]}),
                               encoding="utf-8")
        out = self._run(["--ledger", str(ledger), "--residual-declaration", str(declaration),
                         "--emit-json", str(self.folder / "out.json")])
        self.assertEqual(out.returncode, 0, out.stderr)
        result = json.loads((self.folder / "out.json").read_text(encoding="utf-8"))
        self.assertEqual(result["residual_declaration"]["mode"], "structured")

    def test_atk_prev_file_is_consumed(self):
        """--atk-prev 传入的快照必须被真实消费。"""
        ledger = self.folder / "ledger.jsonl"
        ledger.write_text(json.dumps(rec(1)), encoding="utf-8")
        entry = {"id": "ATK-001", "hypothesis": "重复下单应被拒绝",
                 "validation_path": "人工走查下单流程", "impact": "功能", "status": "未执行"}
        atk = self.folder / "atk.json"
        atk.write_text(json.dumps([entry]), encoding="utf-8")
        prev = self.folder / "atk-prev.json"
        prev.write_text(json.dumps([entry]), encoding="utf-8")
        out = self._run(["--ledger", str(ledger), "--atk", str(atk),
                         "--atk-prev", str(prev), "--emit-json", str(self.folder / "out.json")])
        self.assertEqual(out.returncode, 0, out.stderr)
        result = json.loads((self.folder / "out.json").read_text(encoding="utf-8"))
        self.assertEqual(result["atk_stagnant_ids"], ["ATK-001"])
        self.assertTrue(result["atk_stagnant_checked"])


if __name__ == "__main__":
    unittest.main()
