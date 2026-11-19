"""C 组发现能力（状态矩阵 / 控件遍历 / 文案一致性 / 断言杀伤率）的机械行为。

这四项的共同点：它们**增加**发现问题的能力，而不是给既有流程加锁。
上一轮 14 条整改全是加锁，检出数没变——这才是根因。

每个文件守住两条：
1. 必须能报警（已知故障要抓到），不是哑弹；
2. 必须能说清"哪些没覆盖"，而不是用通过率掩盖缺口。
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StateMatrix(unittest.TestCase):
    def setUp(self):
        self.m = load("state_matrix")

    def test_cartesian_expansion_and_stable_ids(self):
        payload = {"dimensions": {"role": ["owner", "viewer"], "state": ["draft", "published"]}}
        first = self.m.analyse(payload)
        second = self.m.analyse(payload)
        self.assertEqual(first["total_cells"], 4)
        self.assertEqual([c["cell_id"] for c in first["uncovered_cells"]],
                         [c["cell_id"] for c in second["uncovered_cells"]])

    def test_uncovered_cells_are_listed_not_hidden(self):
        result = self.m.analyse({"dimensions": {"role": ["a", "b"], "state": ["x", "y"]},
                                 "executed": []})
        self.assertEqual(result["uncovered"], 4)
        self.assertIn("cells_uncovered", [g["code"] for g in result["gaps"]])
        self.assertEqual(result["verdict_ceiling"], "待核实")

    def test_exclude_needs_reason(self):
        payload = {"dimensions": {"role": ["a"]}, "exclude": [{"cell": "MAT-xxxx"}]}
        with self.assertRaises(ValueError):
            self.m.analyse(payload)

    def test_value_never_exercised_is_flagged(self):
        """"某个角色整体没测"不能因为总体覆盖率还行就被掩盖。"""
        base = {"dimensions": {"role": ["owner", "editor"], "state": ["draft"]}}
        one = self.m.analyse(base)
        covered = one["uncovered_cells"][0]["cell_id"]
        result = self.m.analyse({**base, "executed": [covered]})
        values = [g["value"] for g in result["value_gaps"]]
        self.assertEqual(len(values), 1)

    def test_combo_explosion_is_refused(self):
        payload = {"dimensions": {"a": [str(i) for i in range(50)],
                                  "b": [str(i) for i in range(50)]}}
        with self.assertRaises(ValueError):
            self.m.analyse(payload)

    def test_cli_enforce(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "m.json"
            path.write_text(json.dumps({"dimensions": {"role": ["a"]}}), encoding="utf-8")
            code = subprocess.run([sys.executable, str(SCRIPTS / "state_matrix.py"),
                                   "--input", str(path), "--enforce"], capture_output=True)
            self.assertEqual(code.returncode, 2)


class ControlTraversal(unittest.TestCase):
    def setUp(self):
        self.m = load("control_traversal")
        self.inventory = {"page": "https://example.invalid/app",
                          "controls": [{"control_id": "CTL-01", "element": "button#save"},
                                       {"control_id": "CTL-02", "element": "button#del"}]}

    def test_untouched_control_is_a_gap(self):
        result = self.m.analyse(self.inventory, [
            {"control_id": "CTL-01", "action": "click", "effect": "保存成功",
             "evidence": "/tmp/a.png"}])
        self.assertEqual(result["untouched"], 1)
        self.assertEqual(result["untouched_controls"][0]["control_id"], "CTL-02")
        self.assertEqual(result["verdict_ceiling"], "待核实")

    def test_full_coverage_passes(self):
        result = self.m.analyse(self.inventory, [
            {"control_id": "CTL-01", "action": "click", "effect": "保存成功",
             "evidence": "/tmp/a.png"},
            {"control_id": "CTL-02", "action": "click", "effect": "弹出确认框",
             "evidence": "/tmp/b.png"}])
        self.assertEqual(result["gaps"], [])
        self.assertEqual(result["verdict_ceiling"], "通过")

    def test_no_effect_is_a_lead_not_a_defect(self):
        """点完没反应可能是死控件，也可能是异步完成——线索不等于缺陷。"""
        result = self.m.analyse(self.inventory, [
            {"control_id": "CTL-01", "action": "click", "effect": "none",
             "evidence": "/tmp/a.png"},
            {"control_id": "CTL-02", "action": "click", "effect": "弹出确认框",
             "evidence": "/tmp/b.png"}])
        self.assertEqual(result["gaps"], [])
        self.assertEqual(len(result["no_effect_candidates"]), 1)
        self.assertIn("线索不是缺陷", result["scope"])

    def test_control_without_evidence_is_a_gap(self):
        result = self.m.analyse(self.inventory, [
            {"control_id": "CTL-01", "action": "click", "effect": "保存"},
            {"control_id": "CTL-02", "action": "click", "effect": "取消"}])
        self.assertIn("control_without_evidence", [g["code"] for g in result["gaps"]])

    def test_empty_inventory_cannot_claim_traversal(self):
        result = self.m.analyse({"page": "p", "controls": []}, [])
        self.assertIn("empty_inventory", [g["code"] for g in result["gaps"]])


class CopyConsistency(unittest.TestCase):
    def setUp(self):
        self.m = load("copy_consistency")

    def test_unrendered_placeholder_is_a_defect(self):
        result = self.m.analyse([{"id": "S1", "role": "text", "text": "你好 {{name}}"}])
        self.assertEqual(result["defects"], 1)
        self.assertIn("copy_defects", [g["code"] for g in result["gaps"]])

    def test_raw_runtime_value_is_a_defect(self):
        result = self.m.analyse([{"id": "S1", "role": "text", "text": "金额 undefined 元"}])
        codes = [f["code"] for f in result["findings"]]
        self.assertIn("raw_runtime_value", codes)

    def test_duplicate_punctuation_detected(self):
        result = self.m.analyse([{"id": "S1", "role": "text", "text": "保存成功，，请稍候"}])
        self.assertIn("duplicate_punctuation", [f["code"] for f in result["findings"]])

    def test_failure_message_with_success_word_is_flagged(self):
        result = self.m.analyse([{"id": "S1", "role": "error", "text": "保存成功"}])
        self.assertIn("failure_message_says_success",
                      [f["code"] for f in result["findings"]])

    def test_inconsistent_wording_is_advisory(self):
        """删除/移除 这种同一个动作两种叫法，机械上只能给线索，要人确认是否有意区分。"""
        # 相似度按字符重合算，"保存成功/保存成功了"这种才是 0.8 以上
        result = self.m.analyse([{"id": "S1", "role": "toast", "text": "保存成功"},
                                 {"id": "S2", "role": "toast", "text": "保存成功了"}])
        self.assertEqual(result["defects"], 0)
        self.assertIn("inconsistent_wording", [f["code"] for f in result["findings"]])

    def test_glossary_variant_mix(self):
        """只给术语组、没说必须用哪个 → 只能报线索，不能自己定对错。"""
        result = self.m.analyse(
            [{"id": "S1", "role": "button", "text": "删除"},
             {"id": "S2", "role": "button", "text": "移除"}],
            {"term_groups": [["删除", "移除", "清除"]]})
        self.assertEqual(result["defects"], 0)
        self.assertIn("term_variant_mixed", [f["code"] for f in result["findings"]])

    def test_glossary_preferred_deviation_is_a_defect(self):
        """术语表明确了统一写法时，偏离就是可判定的缺陷。"""
        result = self.m.analyse(
            [{"id": "S1", "role": "button", "text": "删除"},
             {"id": "S2", "role": "button", "text": "移除"}],
            {"term_groups": [["删除", "移除"]],
             "preferred": {"删除": "统一用删除"}})
        self.assertGreaterEqual(result["defects"], 1)
        self.assertIn("terminology_deviation", [f["code"] for f in result["findings"]])

    def test_clean_copy_passes(self):
        result = self.m.analyse([{"id": "S1", "role": "button", "text": "保存"},
                                 {"id": "S2", "role": "button", "text": "取消"}])
        self.assertEqual(result["gaps"], [])


class AssertionMutation(unittest.TestCase):
    def setUp(self):
        self.m = load("assertion_mutation")
        self.assertions = [{"id": f"AS-{i:02d}", "case_id": "TC-1", "check": f"断言{i}",
                            "required": True} for i in range(1, 11)]

    def test_sampling_is_hash_based_not_chosen(self):
        """抽样由哈希决定：否则会专挑容易验证的断言，等于自证。"""
        first = self.m.select_sample(self.assertions, 0.5)
        second = self.m.select_sample(self.assertions, 0.5)
        self.assertEqual([a["id"] for a in first], [a["id"] for a in second])
        self.assertEqual(len(first), 5)
        other = self.m.select_sample(self.assertions, 0.5, seed="round-2")
        self.assertNotEqual([a["id"] for a in first], [a["id"] for a in other])

    def test_survived_mutation_means_false_green(self):
        sample = self.m.select_sample(self.assertions, 1.0)
        records = {a["id"]: {"mutation": "注入故障", "caught": True} for a in sample}
        records[sample[0]["id"]] = {"mutation": "注入故障", "caught": False}
        result = self.m.analyse(self.assertions, records, 1.0)
        self.assertIn("assertion_survived_mutation", [g["code"] for g in result["gaps"]])
        self.assertEqual(result["survived"], [sample[0]["id"]])

    def test_full_kill_rate_passes(self):
        sample = self.m.select_sample(self.assertions, 1.0)
        records = {a["id"]: {"mutation": "注入故障", "caught": True} for a in sample}
        result = self.m.analyse(self.assertions, records, 1.0)
        self.assertEqual(result["kill_rate"], 1.0)
        self.assertEqual(result["gaps"], [])

    def test_missing_record_is_unverified(self):
        result = self.m.analyse(self.assertions, {}, 0.5)
        self.assertIn("assertion_unverified", [g["code"] for g in result["gaps"]])
        self.assertEqual(result["kill_rate"], None)

    def test_mutation_text_is_required(self):
        sample = self.m.select_sample(self.assertions, 1.0)
        records = {a["id"]: {"mutation": "", "caught": True} for a in sample}
        with self.assertRaises(ValueError):
            self.m.analyse(self.assertions, records, 1.0)

    def test_unsampled_required_are_disclosed(self):
        sample = self.m.select_sample(self.assertions, 0.5)
        records = {a["id"]: {"mutation": "注入", "caught": True} for a in sample}
        result = self.m.analyse(self.assertions, records, 0.5)
        self.assertEqual(len(result["unsampled_required"]), 5)
        self.assertIn("不得写「通过」", result["scope"])


class MeasureEngineContract(unittest.TestCase):
    """取数引擎的契约：背景来源必须显式声明，绝不兜底成白色。"""

    def setUp(self):
        self.text = (SCRIPTS / "ui_measure.js").read_text(encoding="utf-8")

    def test_declares_background_source(self):
        self.assertIn("background_source", self.text)
        for source in ("solid", "image", "gradient", "unknown"):
            self.assertIn(source, self.text)

    def test_returns_unknown_instead_of_white(self):
        self.assertIn("return { source: 'unknown'", self.text)

    def test_implements_target_exceptions(self):
        for field in ("inline_in_text", "spacing_clear", "ua_controlled", "essential"):
            self.assertIn(field, self.text)

    def test_spacing_probe_is_differential(self):
        self.assertIn("before_clipped", self.text)
        self.assertIn("after_clipped", self.text)


if __name__ == "__main__":
    unittest.main()
