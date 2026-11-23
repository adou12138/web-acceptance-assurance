"""v1.47.2 回归：跨域探索清单契约 + 多份分域 gate 补登记。

背景：本项目星仔在 agent.sojump.cn 域，独立核验页（编辑器/设置/预览/列表）在
drunkyong.sojump.cn 域，单份同源清单无法表达；旧实现把 page_url 锁死与 target_url
同源，跨域布局只能拆两份手工清单，而 phase_gate 的 --exploration 又只收单份，
两份清单始终无法一起登记。本文件锁住四处：
①allowed_origins 放宽后旧清单行为不变；②放宽不放宽门禁（缺视角/FAIL 仍压上限）；
③origin 必须是纯 origin，带路径会引入"看似同域实则不同页"的漏报；④多份合并取最严、
缺口合并、任一缺遍历都算整体缺，且单份调用方零回归。
"""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import phase_gate  # noqa: E402


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CrossOriginContractTests(unittest.TestCase):
    def setUp(self):
        self.tool = _load("exploration_state_1472", "exploration_state.py")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.evidence = self.folder / "证据.txt"
        self.evidence.write_text("夹具证据，不代表业务验收", encoding="utf-8")
        self.base = {
            "schema_version": "1.0",
            "context": {"target_url": "https://agent.sojump.cn",
                        "application_version": "build-1", "environment": "测试环境"},
            "items": [{
                "id": "EXP-1", "requirement_ids": ["REQ-1"],
                "page_url": "https://agent.sojump.cn/t/abc", "role": "尊享版",
                "viewport": "1440x900", "state": "会话首轮", "action": "创建草稿卷",
                "risk": "medium", "status": "NOT_RUN", "observation": "",
                "evidence_paths": [], "blocked_reason": "", "side_effect_pending": False,
                "attributes": ["functional"],
            }],
        }

    def report(self, data):
        return self.tool.summarize(data)

    def test_legacy_plan_without_allowed_origins_is_unchanged(self):
        data = copy.deepcopy(self.base)
        report = self.report(data)
        self.assertEqual(report["ready"], ["EXP-1"])
        self.assertNotIn("allowed_origins", report["context"])

    def test_other_origin_still_rejected_without_declaration(self):
        data = copy.deepcopy(self.base)
        data["items"][0]["page_url"] = "https://drunkyong.sojump.cn/wjx/design.aspx"
        with self.assertRaises(ValueError) as ctx:
            self.report(data)
        self.assertIn("同源", str(ctx.exception))

    def test_declared_origin_allows_cross_domain_item(self):
        data = copy.deepcopy(self.base)
        data["context"]["allowed_origins"] = ["https://drunkyong.sojump.cn"]
        data["items"][0]["page_url"] = "https://drunkyong.sojump.cn/wjx/design.aspx"
        report = self.report(data)
        self.assertEqual(report["ready"], ["EXP-1"])
        self.assertEqual(report["context"]["allowed_origins"], ["https://drunkyong.sojump.cn"])

    def test_default_port_and_case_normalization_still_applies(self):
        data = copy.deepcopy(self.base)
        data["context"]["allowed_origins"] = ["https://DRUNKYONG.SOJUMP.CN:443"]
        data["items"][0]["page_url"] = "https://drunkyong.sojump.cn/other"
        self.assertEqual(self.report(data)["ready"], ["EXP-1"])

    def test_path_query_fragment_in_origin_is_rejected(self):
        for value in ["https://drunkyong.sojump.cn/wjx", "https://drunkyong.sojump.cn/?a=1",
                      "https://drunkyong.sojump.cn/#frag", "https://u:p@drunkyong.sojump.cn"]:
            data = copy.deepcopy(self.base)
            data["context"]["allowed_origins"] = [value]
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.report(data)

    def test_malformed_allowed_origins_array_is_rejected(self):
        for value in ["https://drunkyong.sojump.cn", 123, None, ["", ], ["bad url"]]:
            data = copy.deepcopy(self.base)
            data["context"]["allowed_origins"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.report(data)

    def test_empty_and_duplicate_allowed_origins_are_idempotent(self):
        """空数组等价于不声明；重复项归一到 set，不该把合法清单判成非法。"""
        for value in [[], ["https://drunkyong.sojump.cn", "https://drunkyong.sojump.cn"]]:
            data = copy.deepcopy(self.base)
            data["context"]["allowed_origins"] = value
            with self.subTest(value=value):
                self.assertEqual(self.report(data)["ready"], ["EXP-1"])

    def test_cross_origin_declaration_does_not_unlock_gate(self):
        """放宽的只是清单可表达范围；缺视角/FAIL 依旧把 ceiling 压到待核实。"""
        data = copy.deepcopy(self.base)
        data["context"]["allowed_origins"] = ["https://drunkyong.sojump.cn"]
        data["items"][0]["status"] = "FAIL"
        data["items"][0]["observation"] = "HTTP 405 静默"
        data["items"][0]["evidence_paths"] = [str(self.evidence)]
        data["items"][0]["page_url"] = "https://drunkyong.sojump.cn/wjx"
        gate = self.report(data)["gate"]
        self.assertEqual(gate["verdict_ceiling"], "待核实")
        self.assertIn("EXP-1", gate["failed_items"])

    def test_csv_export_does_not_expand_allowed_origins(self):
        data = copy.deepcopy(self.base)
        data["context"]["allowed_origins"] = ["https://drunkyong.sojump.cn"]
        out = self.folder / "ledger.csv"
        report = self.tool.export_csv(data, out)
        self.assertIn("allowed_origins", report["context"])
        self.assertFileHasNoAllowedOriginsColumn(out)

    def assertFileHasNoAllowedOriginsColumn(self, path):
        header = path.read_text(encoding="utf-8-sig").splitlines()[0]
        self.assertNotIn("allowed_origins", header)

    def test_full_report_of_both_domains_summarizes(self):
        """真实形态：两份分域报告各自只含本域 URL，均可独立 summarize。"""
        for target, url in [("https://agent.sojump.cn", "https://agent.sojump.cn/t/abc"),
                            ("https://drunkyong.sojump.cn", "https://drunkyong.sojump.cn/pm/1.aspx")]:
            data = copy.deepcopy(self.base)
            data["context"]["target_url"] = target
            data["items"][0]["page_url"] = url
            with self.subTest(target=target):
                self.assertEqual(self.report(data)["ready"], ["EXP-1"])


class MultiExplorationGateTests(unittest.TestCase):
    def setUp(self):
        self.pg = phase_gate
        self.gate_pass = {"verdict_ceiling": "通过", "ready_pending": 0,
                          "traversal_recorded": True, "missing_views": [],
                          "mandatory_attributes": {"content": "covered"}, "message": "域A通过"}
        self.gate_hold = {"verdict_ceiling": "待核实", "ready_pending": 0,
                          "traversal_recorded": True, "missing_views": ["基础无障碍", "性能"],
                          "failed_items": ["EXP-004"],
                          "mandatory_attributes": {"content": "covered"}, "message": "域B缺视角"}

    def test_single_gate_behavior_unchanged(self):
        result = self.pg._exploration_check(self.gate_pass)
        self.assertEqual(result["ceiling"], "通过")
        self.assertEqual(result["state"], "provided")
        self.assertNotIn("sources", result)

    def test_multi_gate_takes_strictest_ceiling(self):
        result = self.pg._exploration_check([self.gate_pass, self.gate_hold])
        self.assertEqual(result["ceiling"], "待核实")
        self.assertEqual(len(result["sources"]), 2)
        self.assertIn("failed_items=EXP-004", " ".join(result["gaps"]))

    def test_multi_gate_one_pass_can_not_unlock(self):
        """反后门：任何一份非通过，整体不得为通过——这是补登记的核心约束。"""
        result = self.pg._exploration_check([self.gate_pass, self.gate_pass, self.gate_hold])
        self.assertEqual(result["ceiling"], "待核实")
        self.assertTrue(result["reasons"])

    def test_multi_gate_missing_traversal_in_one_is_missing_overall(self):
        no_trav = dict(self.gate_pass, traversal_recorded=False)
        result = self.pg._exploration_check([self.gate_pass, no_trav])
        self.assertFalse(result["traversal_recorded"])
        self.assertEqual(result["ceiling"], "待核实")

    def test_empty_array_rejected(self):
        with self.assertRaises(ValueError):
            self.pg._exploration_check([])

    def test_read_arg_multi_unwraps_reports_and_plain_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gates.json"
            path.write_text(json.dumps([
                {"gate": self.gate_pass},
                self.gate_hold,
            ], ensure_ascii=False), encoding="utf-8")
            result = self.pg._read_exploration_arg(str(path), multi=True)
            self.assertEqual(result, [self.gate_pass, self.gate_hold])

    def test_source_label_survives_unwrap_and_appears_in_gaps(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gates.json"
            path.write_text(json.dumps([
                {"source": "被测对象域", "gate": self.gate_pass},
                {"source": "独立核验域", "gate": self.gate_hold},
            ], ensure_ascii=False), encoding="utf-8")
            gates = self.pg._read_exploration_arg(str(path), multi=True)
            result = self.pg._exploration_check(gates)
            labels = [s.get("source") for s in result["sources"]]
            self.assertEqual(labels, ["被测对象域", "独立核验域"])
            self.assertIn("被测对象域", result["message"])
            self.assertTrue(any("独立核验域" in g for g in result["gaps"]))

    def test_read_arg_single_report_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gate.json"
            path.write_text(json.dumps({"gate": self.gate_pass}, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(self.pg._read_exploration_arg(str(path)), self.gate_pass)

    def test_revise_accepts_multi_gate_without_unlocking_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "phase-state.json"
            state = self.pg.init_state(path, "多份补登", "SESS-1",
                                       self.pg.ROUTING["plan"]["model_class"],
                                       self.pg.ROUTING["plan"]["model_hint"])
            entry = self.pg._entry(state, "execute")
            entry["status"] = "done"
            entry["session"] = "SESS-1"
            entry["exploration"] = self.pg._exploration_check(None)
            self.assertEqual(entry["exploration"]["ceiling"], "待核实")
            self.pg.revise(state, "execute",
                           exploration=[self.gate_pass, self.gate_hold],
                           session="SESS-1", note="补登两份分域清单")
            entry = self.pg._entry(state, "execute")["exploration"]
            self.assertEqual(entry["ceiling"], "待核实")
            self.assertEqual(len(entry["sources"]), 2)
            self.assertTrue(entry["revised"])

    def test_revise_refuses_unlock_from_multi_gate_to_pass(self):
        """反后门：多份补登也不得把待核实洗成通过。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "phase-state.json"
            state = self.pg.init_state(path, "多份补登", "SESS-1",
                                       self.pg.ROUTING["plan"]["model_class"],
                                       self.pg.ROUTING["plan"]["model_hint"])
            entry = self.pg._entry(state, "execute")
            entry["status"] = "done"
            entry["session"] = "SESS-1"
            entry["exploration"] = self.pg._exploration_check(None)
            with self.assertRaises(ValueError) as ctx:
                self.pg.revise(state, "execute",
                               exploration=[self.gate_pass, self.gate_pass], session="SESS-1")
            self.assertIn("升级为「通过」", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
