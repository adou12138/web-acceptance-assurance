"""v1.47.2/v1.47.3 回归：多份分域 gate 登记 + 跨域单份混排旁路锁死。

背景：本项目星仔在 agent.sojump.cn 域，独立核验页在 drunkyong.sojump.cn 域。
v1.47.2 曾引入 context.allowed_origins 允许单份清单混入跨域记录，复审实证该
设计构成旁路：视角覆盖按 (page_url, state) 单元跨域同池统计，一域的记录可
补齐另一域的缺口（受控实验：合并单份=通过，两份独立登记=待核实），"算法
没改但输入池被换了"。v1.47.3 回退该字段——本文件锁住四处：
①allowed_origins 是未知字段，显式拒绝（旁路不存在，而非放宽后靠算法弥补）；
②跨域 page_url 拒绝，报错指路"每域各出一份清单 + 多份 gate 合并"；
③多份合并取最严、缺口合并、任一缺遍历都算整体缺，单份调用方零回归；
④数组混 None/嵌套包装显式报错、mandatory_attributes 合并取最严、
--exploration 可重复传（免手工拼合并文件）。
"""
import copy
import importlib.util
import json
from pathlib import Path
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


class CrossOriginBypassBlockedTests(unittest.TestCase):
    """v1.47.3 回退后的契约：单份清单严格同源，跨域只能走多份登记路径。"""

    def setUp(self):
        self.tool = _load("exploration_state_1473", "exploration_state.py")
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
        report = self.report(copy.deepcopy(self.base))
        self.assertEqual(report["ready"], ["EXP-1"])
        self.assertNotIn("allowed_origins", report["context"])

    def test_allowed_origins_is_unknown_field_and_rejected(self):
        """v1.47.2 的旁路字段必须显式拒绝，而不是静默忽略或放宽。"""
        for value in [["https://drunkyong.sojump.cn"], [], "https://drunkyong.sojump.cn",
                      123, None]:
            data = copy.deepcopy(self.base)
            data["context"]["allowed_origins"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.report(data)

    def test_cross_domain_page_url_rejected_with_migration_hint(self):
        """跨域记录被拒时，报错必须指明替代路径（每域一份 + 多份 gate），避免执行者
        在报错面前退回"合成单份"的老路。"""
        data = copy.deepcopy(self.base)
        data["items"][0]["page_url"] = "https://drunkyong.sojump.cn/wjx/design.aspx"
        with self.assertRaises(ValueError) as ctx:
            self.report(data)
        message = str(ctx.exception)
        self.assertIn("同源", message)
        self.assertIn("多份", message)

    def test_v1472_bypass_scenario_is_now_blocked_entirely(self):
        """复审受控实验的旁路场景：域A全覆盖 + 域B仅功能1单元，v1.47.2 允许合并
        单份并判"通过"；v1.47.3 该路径根本不存在——混入即拒绝，只能走两份独立
        清单 + phase_gate 多份合并（结论待核实）。"""
        def item(ident, host, state, attrs):
            return {
                "id": ident, "requirement_ids": ["REQ-1"],
                "page_url": f"https://{host}/p/{ident}", "role": "r",
                "viewport": "1440x900", "state": state, "action": "act",
                "risk": "medium", "status": "PASS", "observation": "观察",
                "evidence_paths": [str(self.evidence)], "blocked_reason": "",
                "side_effect_pending": False, "attributes": [attrs],
                "falsify_action": f"证伪-{ident}",
            }
        # 域A：五视角各2单元 + 遍历条目 —— 域A单份可过（属性走 rulings 全豁免）
        domain_a = []
        for index, attrs in enumerate(["visual", "usability", "accessibility",
                                       "functional", "performance"]):
            domain_a.append(item(f"EXP-A{index}1", "agent.sojump.cn", f"状态{index}1", attrs))
            domain_a.append(item(f"EXP-A{index}2", "agent.sojump.cn", f"状态{index}2", attrs))
        domain_a.append(item("EXP-TRAV-A", "agent.sojump.cn", "遍历态", "functional"))
        rulings = {key: {"state": "not_applicable", "reason": "本轮不涉及"}
                   for key in ("security", "compatibility", "content", "visual",
                               "usability", "accessibility")}

        def make(items, target, extra_context=None):
            context = {"target_url": target, "application_version": "build-1",
                       "environment": "测试环境"}
            if extra_context:
                context.update(extra_context)
            return {"schema_version": "1.0", "context": context,
                    "items": items, "attribute_rulings": rulings}

        gate_a = self.tool.summarize(make(domain_a, "https://agent.sojump.cn"))["gate"]
        self.assertEqual(gate_a["verdict_ceiling"], "通过")

        # 域B：仅功能1单元——覆盖不足，单份必然不达标
        domain_b = [item("EXP-B1", "drunkyong.sojump.cn", "状态B1", "functional"),
                    item("EXP-TRAV-B", "drunkyong.sojump.cn", "遍历态B", "functional")]
        gate_b = self.tool.summarize(
            make(domain_b, "https://drunkyong.sojump.cn"))["gate"]
        self.assertEqual(gate_b["verdict_ceiling"], "待核实")
        self.assertIn("界面样式", gate_b["missing_views"])

        # 两份独立登记（唯一合法路径）：合并取最严 = 待核实，域B缺口完整保留
        merged = phase_gate._exploration_check([gate_a, gate_b])
        self.assertEqual(merged["ceiling"], "待核实")
        self.assertTrue(any("missing_views=界面样式" in gap for gap in merged["gaps"]))

        # v1.47.2 的旁路：把 B 域记录混进 A 域清单 + allowed_origins —— 现在必须被拒
        data = make(domain_a + [item("EXP-B1", "drunkyong.sojump.cn", "状态B1", "functional")],
                    "https://agent.sojump.cn",
                    {"allowed_origins": ["https://drunkyong.sojump.cn"]})
        with self.assertRaises(ValueError):
            self.report(data)

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

    def test_none_or_empty_element_rejected_not_silently_merged(self):
        """复审实证：混入 None 时 state 仍报 provided、该份诊断被丢弃（死变量）。
        修复后必须显式报错，不给"占位混入"留通道。"""
        for bad in [None, {}]:
            with self.subTest(bad=bad), self.assertRaises(ValueError) as ctx:
                self.pg._exploration_check([self.gate_pass, bad])
            self.assertIn("空/None", str(ctx.exception))

    def test_nested_wrapper_element_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self.pg._exploration_check([self.gate_pass, {"source": "x", "gate": self.gate_hold}])
        self.assertIn("外层包装", str(ctx.exception))

    def test_read_arg_multi_unwraps_reports_and_plain_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gates.json"
            path.write_text(json.dumps([
                {"gate": self.gate_pass},
                self.gate_hold,
            ], ensure_ascii=False), encoding="utf-8")
            result = self.pg._read_exploration_arg(str(path), multi=True)
            self.assertEqual(result, [self.gate_pass, self.gate_hold])

    def test_read_arg_rejects_none_placeholder_in_array(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gates.json"
            path.write_text(json.dumps([{"gate": self.gate_pass}, None], ensure_ascii=False),
                            encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                self.pg._read_exploration_arg(str(path), multi=True)
            self.assertIn("空/None", str(ctx.exception))

    def test_read_arg_without_multi_rejects_array_with_clear_error(self):
        """复审实证：multi=False 传数组时报误导性的 verdict_ceiling 错误。
        修复后必须先说清"这是数组，请走多份路径"。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gates.json"
            path.write_text(json.dumps([{"gate": self.gate_pass}], ensure_ascii=False),
                            encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                self.pg._read_exploration_arg(str(path))
            self.assertIn("数组", str(ctx.exception))
            self.assertIn("--exploration", str(ctx.exception))

    def test_read_args_multiple_paths_flatten(self):
        """--exploration action='append'：两份分域清单各传一个路径，免手工拼合并文件。"""
        with tempfile.TemporaryDirectory() as tmp:
            p1 = Path(tmp) / "a.json"
            p2 = Path(tmp) / "b.json"
            p1.write_text(json.dumps({"gate": self.gate_pass}, ensure_ascii=False), encoding="utf-8")
            p2.write_text(json.dumps({"source": "独立核验域", "gate": self.gate_hold},
                                     ensure_ascii=False), encoding="utf-8")
            result = self.pg._read_exploration_args([str(p1), str(p2)])
            self.assertIsInstance(result, list)
            checked = self.pg._exploration_check(result)
            self.assertEqual(checked["ceiling"], "待核实")
            self.assertEqual(checked["sources"][1]["source"], "独立核验域")

    def test_read_args_array_file_flattens_to_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gates.json"
            path.write_text(json.dumps([{"gate": self.gate_pass}, self.gate_hold],
                                       ensure_ascii=False), encoding="utf-8")
            result = self.pg._read_exploration_args([str(path)])
            self.assertEqual(len(result), 2)

    def test_read_args_single_path_keeps_single_gate_shape(self):
        """只传一份时保持单份 gate 形状——既有调用方与已登记数据的形状不变。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gate.json"
            path.write_text(json.dumps({"gate": self.gate_pass}, ensure_ascii=False), encoding="utf-8")
            result = self.pg._read_exploration_args([str(path)])
            self.assertNotIsInstance(result, list)
            self.assertEqual(result, self.gate_pass)

    def test_read_args_none_returns_none(self):
        self.assertIsNone(self.pg._read_exploration_args(None))
        self.assertIsNone(self.pg._read_exploration_args([]))

    def test_multi_gate_mandatory_attributes_merge_strictest(self):
        """合并路径顶层 mandatory_attributes 不再恒为 {}：任一份未闭合即未闭合；
        两份闭合但取值冲突时取 covered（not_applicable 是豁免，covered 是已测，取严）。"""
        open_gate = dict(self.gate_pass,
                         mandatory_attributes={"content": "covered", "security": "unresolved"})
        na_gate = dict(self.gate_hold,
                       mandatory_attributes={"content": "not_applicable", "security": "not_applicable"})
        result = self.pg._exploration_check([open_gate, na_gate])
        self.assertEqual(result["mandatory_attributes"]["security"], "unresolved")
        self.assertEqual(result["mandatory_attributes"]["content"], "covered")
        self.assertTrue(any("security" in gap for gap in result["gaps"]))

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

    def test_raw_plan_file_rejected_with_summarize_hint(self):
        """自审发现：CLI 把原始清单（schema_version/items，无 gate 结论）直接当 gate
        登记时，报的是误导性的 verdict_ceiling 非法——必须指明先跑 summarize。"""
        raw_plan = {"schema_version": "1.0",
                    "context": {"target_url": "https://agent.sojump.cn",
                                "application_version": "build-1", "environment": "测试环境"},
                    "items": []}
        with self.assertRaises(ValueError) as ctx:
            self.pg._exploration_check(raw_plan)
        message = str(ctx.exception)
        self.assertIn("原始探索清单", message)
        self.assertIn("summarize", message)

    def test_read_args_empty_gate_field_rejected_not_silently_missing(self):
        """审核发现：单份文件 {"gate": None} 曾静默降级为"未提供"（state=missing），
        用户以为登记了清单。与数组混 None 同口径：显式报错。"""
        with tempfile.TemporaryDirectory() as tmp:
            for payload in [{"gate": None}, {"gate": {}}]:
                path = Path(tmp) / "empty_gate.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.subTest(payload=payload), self.assertRaises(ValueError) as ctx:
                    self.pg._read_exploration_args([str(path)])
                self.assertIn("为空", str(ctx.exception))

    def test_empty_object_gate_rejected_with_clear_error(self):
        """审核发现：空对象文件 {} 仍报误导的 verdict_ceiling 非法。"""
        with self.assertRaises(ValueError) as ctx:
            self.pg._exploration_check({})
        self.assertIn("为空对象", str(ctx.exception))

    def test_mandatory_attributes_two_open_states_are_order_stable(self):
        """多个未闭合态之间不定义先后：取先遇到者，结果不随顺序抖动；判定不受影响。"""
        a = dict(self.gate_pass, mandatory_attributes={"security": "blocked"})
        b = dict(self.gate_pass, mandatory_attributes={"security": "unresolved"})
        forward = self.pg._exploration_check([a, b])["mandatory_attributes"]["security"]
        backward = self.pg._exploration_check([b, a])["mandatory_attributes"]["security"]
        self.assertEqual(forward, "blocked")
        self.assertEqual(backward, "unresolved")
        # 两种顺序都判未闭合（都在 gaps 里逐份列出）
        for gates in ([a, b], [b, a]):
            result = self.pg._exploration_check(gates)
            self.assertTrue(any("security" in gap for gap in result["gaps"]))

    def test_read_args_summarize_reports_end_to_end(self):
        """CLI append 的完整数据链：两份 summarize 完整报告 → 两个 gate → 合并取最严。"""
        tool = _load("exploration_state_e2e", "exploration_state.py")
        item = {
            "id": "EXP-1", "requirement_ids": ["REQ-1"],
            "page_url": "https://agent.sojump.cn/t/abc", "role": "r",
            "viewport": "1440x900", "state": "s1", "action": "a",
            "risk": "medium", "status": "NOT_RUN", "observation": "",
            "evidence_paths": [], "blocked_reason": "", "side_effect_pending": False,
            "attributes": ["functional"],
        }
        report_a = tool.summarize({
            "schema_version": "1.0",
            "context": {"target_url": "https://agent.sojump.cn",
                        "application_version": "b1", "environment": "T"},
            "items": [dict(item)],
        })
        report_b = tool.summarize({
            "schema_version": "1.0",
            "context": {"target_url": "https://drunkyong.sojump.cn",
                        "application_version": "b1", "environment": "T"},
            "items": [dict(item, page_url="https://drunkyong.sojump.cn/pm/1.aspx")],
        })
        with tempfile.TemporaryDirectory() as tmp:
            p1 = Path(tmp) / "report_a.json"
            p2 = Path(tmp) / "report_b.json"
            p1.write_text(json.dumps(report_a, ensure_ascii=False), encoding="utf-8")
            p2.write_text(json.dumps(report_b, ensure_ascii=False), encoding="utf-8")
            gates = self.pg._read_exploration_args([str(p1), str(p2)])
            self.assertIsInstance(gates, list)
            result = self.pg._exploration_check(gates)
            self.assertEqual(result["ceiling"], "待核实")
            self.assertEqual(len(result["sources"]), 2)

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
            self.assertEqual(entry["mandatory_attributes"]["content"], "covered")

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
