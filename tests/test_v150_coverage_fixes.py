"""v1.50 覆盖下限整改回归。

背景：v1.49.x 审查发现「界面样式/交互易用性/基础无障碍」三块必选属性在入口
契约（agents）里仍写成「三类」，与脚本六元组和 exploration-runtime 不一致——
执行者按入口契约只裁三类，这三类被记 undeclared 压档，或干脆不测。本文件把
入口契约与脚本枚举锁在一起，并锁住本批次新增的输入盘点与测量证据口径。
"""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_phase_gate import PhaseGateTestBase  # noqa: E402  复用阶段状态机夹具

AGENT_MD = ROOT / "agents" / "web-acceptance-assurance.md"
EXPLORATION_RUNTIME = ROOT / "references" / "exploration-runtime.md"

# 六个必选属性的中文名，必须与 exploration_state.MANDATORY_ATTRIBUTES 一一对应。
ATTRIBUTE_LABELS = {
    "security": "安全",
    "compatibility": "兼容",
    "content": "文案",
    "visual": "界面样式",
    "usability": "交互易用性",
    "accessibility": "基础无障碍",
}


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MandatoryAttributeContractTest(unittest.TestCase):
    """入口契约、参考文档、脚本枚举三方必须同口径（v1.50 漂移修复的守卫）。"""

    @classmethod
    def setUpClass(cls):
        cls.exp = load_module(ROOT / "scripts" / "exploration_state.py", "exp_state_v150")
        cls.agent_text = AGENT_MD.read_text(encoding="utf-8")
        cls.runtime_text = EXPLORATION_RUNTIME.read_text(encoding="utf-8")

    def test_script_enumerates_six_attributes(self):
        self.assertEqual(len(self.exp.MANDATORY_ATTRIBUTES), 6)
        self.assertEqual(set(self.exp.MANDATORY_ATTRIBUTES), set(ATTRIBUTE_LABELS))

    def test_agents_declares_all_six_attribute_labels(self):
        # 入口契约必须逐类点名；只写「三类」会让执行者漏裁界面/易用/无障碍。
        for label in ATTRIBUTE_LABELS.values():
            self.assertIn(label, self.agent_text,
                          f"agents 未点名必选属性「{label}」，执行者会漏裁该类")

    def test_agents_no_longer_says_three_kinds(self):
        self.assertNotIn("三类必选属性", self.agent_text)
        self.assertNotIn("安全/兼容/文案三类", self.agent_text)

    def test_runtime_doc_declares_all_six_attribute_labels(self):
        for label in ATTRIBUTE_LABELS.values():
            self.assertIn(label, self.runtime_text,
                          f"exploration-runtime.md 未点名「{label}」")


class InputInventoryTest(unittest.TestCase):
    """v1.50 任务载荷盘点：识别高价值材料，核对处置。"""

    @classmethod
    def setUpClass(cls):
        cls.inv = load_module(ROOT / "scripts" / "input_inventory.py", "input_inventory_v150")

    def test_recognises_figma_and_contract_and_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            contract = Path(tmp) / "openapi.json"
            contract.write_text('{"openapi": "3.0.0", "paths": {}}', encoding="utf-8")
            trace = Path(tmp) / "flow.har"
            trace.write_text("{}", encoding="utf-8")
            items = [{"source": "https://www.figma.com/design/KEY/x", "disposal": None},
                     {"source": str(contract), "disposal": None},
                     {"source": str(trace), "disposal": None},
                     {"source": "PRD.md", "disposal": None}]
            result = self.inv.build_inventory({"schema_version": "1.0", "items": items})
        detected = [row["detected"] for row in result["items"]]
        self.assertEqual(detected[:3], ["figma_design", "api_contract", "api_trace"])
        self.assertEqual(detected[3], "other")

    def test_unconsumed_high_value_is_gap(self):
        result = self.inv.build_inventory({"schema_version": "1.0", "items": [
            {"source": "https://figma.com/file/KEY/x", "disposal": None}]})
        codes = [gap["code"] for gap in result["gaps"]]
        self.assertIn("unconsumed_input", codes)

    def test_declined_with_reason_is_not_gap(self):
        result = self.inv.build_inventory({"schema_version": "1.0", "items": [
            {"source": "https://figma.com/file/KEY/x",
             "disposal": {"declined": True, "reason": "旧版稿，本轮不比对"}}]})
        self.assertEqual([gap["code"] for gap in result["gaps"]], [])
        self.assertEqual(result["items"][0]["state"], "declined")

    def test_declined_without_reason_is_gap(self):
        result = self.inv.build_inventory({"schema_version": "1.0", "items": [
            {"source": "https://figma.com/file/KEY/x",
             "disposal": {"declined": True}}]})
        self.assertIn("declined_without_reason",
                      [gap["code"] for gap in result["gaps"]])
        # 同一项不得再重复计一条 unconsumed_input
        self.assertNotIn("unconsumed_input", [gap["code"] for gap in result["gaps"]])

    def test_consumed_is_clean(self):
        result = self.inv.build_inventory({"schema_version": "1.0", "items": [
            {"source": "https://figma.com/file/KEY/x",
             "disposal": {"consumed_by": "figma-ui-parity"}}]})
        self.assertEqual(result["gaps"], [])
        self.assertEqual(result["items"][0]["state"], "consumed")

    def test_design_material_unretrievable_is_disclosed(self):
        caps = {"channels": {"design_data": {"state": "missing"}}}
        result = self.inv.build_inventory({"schema_version": "1.0", "items": [
            {"source": "https://figma.com/file/KEY/x",
             "disposal": {"consumed_by": "figma-ui-parity"}}]}, caps)
        codes = [gap["code"] for gap in result["gaps"]]
        self.assertIn("input_not_retrievable", codes)
        # 取不到属 dataMissing 口径，披露项不参与压档
        gap = next(g for g in result["gaps"] if g["code"] == "input_not_retrievable")
        self.assertEqual(gap["severity"], "disclose")


class InventoryGateTest(PhaseGateTestBase):
    """盘点缺口接入三阶段闸门：unconsumed 压档，未传只披露。"""

    def test_unconsumed_inventory_downgrades(self):
        self.finish_phase("plan")
        self.state["phases"]["execute"]["inventory"] = {
            "state": "provided", "gaps": [
                {"code": "unconsumed_input", "message": "figma_design 未登记处置"}]}
        result = self.pg.check_verdict(self.state)
        self.assertIn("inventory_unconsumed",
                      [item["code"] for item in result["limitations"]])
        self.assertEqual(result["phase_ceiling"], "待核实")

    def test_not_provided_inventory_only_discloses(self):
        self.finish_phase("plan")
        result = self.pg.check_verdict(self.state)
        self.assertNotIn("inventory_unconsumed",
                         [item["code"] for item in result["limitations"]])
        self.assertTrue(any("未登记输入盘点" in w.get("message", "")
                            for w in result["warnings"]))
        self.assertNotIn("未登记输入盘点", " ".join(result["reasons"]))


class MeasurementEvidenceTest(unittest.TestCase):
    """schema 1.1：视觉/无障碍 PASS 必须挂结构化测量产物；1.0 只兼容披露。"""

    @classmethod
    def setUpClass(cls):
        cls.exp = load_module(ROOT / "scripts" / "exploration_state.py", "exp_state_p4")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.shot = self.folder / "页面.png"
        self.shot.write_bytes(b"\x89PNG\r\n\x1a\n")
        self.measure = self.folder / "measure.json"
        self.measure.write_text(json.dumps({"kind": "text_normal", "status": "pass"}),
                                encoding="utf-8")

    def _item(self, ident, attrs, evidence, page, action, status="PASS"):
        return {"id": ident, "requirement_ids": ["REQ-1"],
                "page_url": page, "role": "编辑者", "viewport": "1440x900",
                "state": "打开", "action": action, "risk": "medium", "status": status,
                "observation": "实测", "evidence_paths": [str(evidence)],
                "blocked_reason": "", "side_effect_pending": False, "attributes": attrs}

    def _data(self, version="1.1", visual_evidence=None, with_kbd=True,
              accessibility_ruling=None):
        base = "https://example.invalid/app"
        visual_ev = visual_evidence or self.measure
        items = [
            self._item("EXP-V-1", ["visual"], visual_ev, f"{base}/v1", "看布局"),
            self._item("EXP-V-2", ["visual"], visual_ev, f"{base}/v2", "看断点"),
            self._item("EXP-U-1", ["usability"], self.measure, f"{base}/u1", "点按钮"),
            self._item("EXP-U-2", ["usability"], self.measure, f"{base}/u2", "填表单"),
            self._item("EXP-A-1", ["accessibility"], self.measure, f"{base}/a1", "查对比度"),
            self._item("EXP-A-2", ["accessibility"], self.measure, f"{base}/a2", "查焦点"),
            self._item("EXP-F-1", ["functional", "security", "compatibility"],
                       self.measure, f"{base}/f1", "提交"),
            self._item("EXP-F-2", ["functional", "security", "compatibility"],
                       self.measure, f"{base}/f2", "取消"),
            self._item("EXP-P-1", ["performance"], self.measure, f"{base}/p1", "首屏"),
            self._item("EXP-P-2", ["performance"], self.measure, f"{base}/p2", "切换"),
            self._item("EXP-TRAV-1", ["usability"], self.measure, f"{base}/t1", "遍历控件"),
        ]
        if with_kbd:
            items.append(self._item("EXP-KBD-1", ["usability"], self.measure,
                                    f"{base}/k1", "Tab 走主流程"))
        rulings = {attr: {"state": "not_applicable", "reason": "本批次不涉及"}
                   for attr in ("content",)}
        if accessibility_ruling:
            rulings["accessibility"] = accessibility_ruling
        return {"schema_version": version,
                "context": {"target_url": base, "application_version": "b1",
                            "environment": "test"},
                "items": items, "attribute_rulings": rulings}

    def test_png_only_visual_pass_is_gap_in_1_1(self):
        report = self.exp.summarize(self._data("1.1", visual_evidence=self.shot))
        gate = report["gate"]
        self.assertEqual(sorted(gate["measurement_gaps"]), ["EXP-V-1", "EXP-V-2"])
        self.assertIn("未挂结构化测量产物", gate["message"])

    def test_same_data_in_1_0_only_discloses(self):
        report = self.exp.summarize(self._data("1.0", visual_evidence=self.shot))
        self.assertEqual(report["gate"]["measurement_gaps"], [])

    def test_measure_artifact_closes_gap(self):
        report = self.exp.summarize(self._data("1.1"))
        self.assertEqual(report["gate"]["measurement_gaps"], [])

    def test_missing_keyboard_entry_is_gap_in_1_1(self):
        report = self.exp.summarize(self._data("1.1", with_kbd=False))
        self.assertFalse(report["gate"]["keyboard_recorded"])
        self.assertIn("无键盘遍历条目", report["gate"]["message"])

    def test_keyboard_exempt_when_accessibility_not_applicable(self):
        report = self.exp.summarize(self._data(
            "1.1", with_kbd=False,
            accessibility_ruling={"state": "not_applicable", "reason": "纯接口批次"}))
        self.assertNotIn("无键盘遍历条目", report["gate"]["message"])


    def test_wrapped_records_artifact_closes_gap(self):
        wrapped = self.folder / "wrapped.json"
        wrapped.write_text(json.dumps({"records": [{"kind": "text_normal"}]}),
                           encoding="utf-8")
        report = self.exp.summarize(self._data("1.1", visual_evidence=wrapped))
        self.assertEqual(report["gate"]["measurement_gaps"], [])

    def test_keyboard_entry_exempt_from_measurement_evidence(self):
        data = self._data("1.1")
        for item in data["items"]:
            if item["id"] == "EXP-KBD-1":
                item["attributes"] = ["accessibility"]
                item["evidence_paths"] = [str(self.shot)]
        report = self.exp.summarize(data)
        self.assertEqual(report["gate"]["measurement_gaps"], [])

    def test_unknown_schema_version_rejected(self):
        data = self._data("1.0")
        data["schema_version"] = "1.2"
        with self.assertRaises(ValueError):
            self.exp.summarize(data)


class SignalProbeTest(MeasurementEvidenceTest):
    """v1.51 升级信号抽样复测：没升级不等于不用细看。

    复用测量证据夹具（它已满足前置门禁：各视角双单元、traversal/keyboard/measure 齐备），
    单独验证「未命中信号」这条路径有没有机械前提。
    """

    def _add_probe(self, data, units):
        for index, unit in enumerate(units, 1):
            page, state = unit.split("|", 1)
            entry = self._item(f"EXP-SIG-{index}", ["visual"], self.measure,
                               page, "按六类信号逐条复测")
            entry["state"] = state
            entry["falsify_action"] = "放大 200% 并切到 320px 视口，看是否溢出"
            entry["observation"] = ("六类信号逐条：键盘可用/对比度达标/焦点可见/"
                                    "CLS 正常/首响 <2000ms/断点无溢出")
            data["items"].append(entry)
        return data

    def test_missing_probe_downgrades(self):
        gate = self.exp.summarize(self._data("1.1"))["gate"]
        self.assertTrue(gate["signal_probe"]["required"])
        self.assertTrue(gate["signal_probe"]["missing"])
        self.assertIn("抽样复测", gate["message"])

    def test_probe_entries_close_gap(self):
        data = self._data("1.1")
        sampled = self.exp.summarize(data)["gate"]["signal_probe"]["sampled"]
        self._add_probe(data, sampled)
        gate = self.exp.summarize(data)["gate"]
        self.assertEqual(gate["signal_probe"]["missing"], [])
        self.assertEqual(gate["signal_probe"]["covered"], sampled)

    def test_deep_entry_covers_only_its_unit(self):
        # 逐单元认定：抽中单元若恰是被升级那页则免复测，未被升级的单元仍需 EXP-SIG-
        data = self._data("1.1")
        data["items"][0]["observation"] = "source=figma-ui-parity:7.41 对比度不足"
        probe = self.exp.summarize(data)["gate"]["signal_probe"]
        self.assertTrue(probe["required"])
        self.assertEqual(probe["reason"], "sampled")
        target = "https://example.invalid/app/v1|打开"
        if target in probe["sampled"]:
            self.assertIn(target, probe["deep_covered"])
        self.assertEqual(set(probe["covered"]) | set(probe["missing"]), set(probe["sampled"]))

    def test_api_only_batch_waives_probe(self):
        base = "https://example.invalid/app"
        items = [self._item("EXP-F-1", ["functional"], self.measure, f"{base}/f1", "提交"),
                 self._item("EXP-F-2", ["functional"], self.measure, f"{base}/f2", "取消")]
        data = {"schema_version": "1.1",
                "context": {"target_url": base, "application_version": "b1",
                            "environment": "test"},
                "items": items}
        probe = self.exp.summarize(data)["gate"]["signal_probe"]
        self.assertFalse(probe["required"])
        self.assertEqual(probe["reason"], "no_page_units")

    def test_traversal_entries_stay_in_pool(self):
        # 遍历/键盘条目不能替代信号复测：否则把页面条目改名成 EXP-TRAV- 就能免测
        base = "https://example.invalid/app"
        items = [self._item("EXP-TRAV-1", ["usability"], self.measure, f"{base}/t1", "遍历控件"),
                 self._item("EXP-KBD-1", ["usability"], self.measure, f"{base}/k1", "Tab 走主流程")]
        data = {"schema_version": "1.1",
                "context": {"target_url": base, "application_version": "b1",
                            "environment": "test"},
                "items": items}
        probe = self.exp.summarize(data)["gate"]["signal_probe"]
        self.assertTrue(probe["required"])
        self.assertTrue(probe["missing"])

    def test_depth_mark_must_be_prefix(self):
        # 任意位置出现 figma 字样不算升级痕迹，只有回写契约的前缀形态才算
        data = self._data("1.1")
        data["items"][0]["observation"] = "参考 source=figma-ui-parity:7.41 的历史结论"
        self.assertEqual(self.exp.summarize(data)["gate"]["signal_probe"]["deep_ids"], [])
        data["items"][0]["observation"] = "source=figma-ui-parity:7.41 对比度不足"
        self.assertEqual(self.exp.summarize(data)["gate"]["signal_probe"]["deep_ids"],
                         ["EXP-V-1"])

    def test_probe_entries_on_fresh_pages_cannot_displace_sample(self):
        # 另编两个页面写两条 EXP-SIG- 不得挤掉真样本：复测条目不建池
        data = self._data("1.1")
        before = self.exp.summarize(data)["gate"]["signal_probe"]["sampled"]
        for index, page in enumerate(("ghost1", "ghost2"), 1):
            entry = self._item(f"EXP-SIG-{index}", ["visual"], self.measure,
                               f"https://example.invalid/{page}", "看渲染")
            entry["falsify_action"] = "切 320px 视口"
            data["items"].append(entry)
        probe = self.exp.summarize(data)["gate"]["signal_probe"]
        self.assertEqual(probe["sampled"], before)
        self.assertEqual(sorted(probe["orphan_probed"]),
                         ["https://example.invalid/ghost1|打开",
                          "https://example.invalid/ghost2|打开"])

    def test_1_0_only_discloses(self):
        gate = self.exp.summarize(self._data("1.0"))["gate"]
        self.assertTrue(gate["signal_probe"]["required"])
        self.assertFalse(gate["signal_probe"]["enforced"])
        self.assertNotIn("抽样复测", gate["message"])

    def test_sampling_is_order_independent(self):
        data = self._data("1.1")
        first = self.exp.summarize(data)["gate"]["signal_probe"]["sampled"]
        data["items"] = list(reversed(data["items"]))
        second = self.exp.summarize(data)["gate"]["signal_probe"]["sampled"]
        self.assertEqual(first, second)

    def _batch(self, entries, site="https://example.invalid/app"):
        return self.exp.summarize({
            "schema_version": "1.1",
            "context": {"target_url": site, "application_version": "b1",
                        "environment": "test"},
            "items": entries})["gate"]["signal_probe"]

    def _paged(self, ident, page, state, attrs=("visual",), status="PASS",
               observation="实测", falsify=None):
        item = self._item(ident, list(attrs), self.measure, page, "看渲染", status)
        item["state"] = state
        item["observation"] = observation
        if status == "BLOCKED":
            item["blocked_reason"] = "环境不可达"
        if falsify:
            item["falsify_action"] = falsify
        return item

    def test_upgrade_waives_only_its_own_unit(self):
        # 单条 figma 回写只免它自己那一页：全局免测会让「在最容易的页升级一次」
        # 洗掉整批义务，正是本条门禁要堵的口子
        probe = self._batch([
            self._paged("EXP-D-1", "https://example.invalid/p1", "打开",
                        observation="source=figma-ui-parity:7.41 对比度不足"),
            self._paged("EXP-D-2", "https://example.invalid/p2", "打开")])
        self.assertTrue(probe["required"])
        self.assertEqual(probe["deep_covered"], ["https://example.invalid/p1|打开"])
        self.assertEqual(probe["missing"], ["https://example.invalid/p2|打开"])

    def test_deep_covered_unit_needs_no_probe_entry(self):
        probe = self._batch([self._paged(
            "EXP-D-1", "https://example.invalid/p1", "打开",
            observation="source=figma-ui-parity:7.43 焦点逃逸")])
        self.assertEqual(probe["missing"], [])
        self.assertTrue(probe["pool_exhausted"])
        self.assertIn("抽样即全量", probe["coverage_note"])

    def test_probe_without_falsify_action_does_not_cover(self):
        probe = self._batch([
            self._paged("EXP-D-1", "https://example.invalid/p1", "打开"),
            self._paged("EXP-SIG-1", "https://example.invalid/p1", "打开")])
        self.assertIn("https://example.invalid/p1|打开", probe["missing"])

    def test_functional_only_probe_does_not_cover(self):
        probe = self._batch([
            self._paged("EXP-D-1", "https://example.invalid/p1", "打开"),
            self._paged("EXP-SIG-1", "https://example.invalid/p1", "打开",
                        attrs=("functional",), falsify="切视口看溢出")])
        self.assertIn("https://example.invalid/p1|打开", probe["missing"])

    def test_blocked_probe_does_not_cover(self):
        probe = self._batch([
            self._paged("EXP-D-1", "https://example.invalid/p1", "打开"),
            self._paged("EXP-SIG-1", "https://example.invalid/p1", "打开",
                        status="BLOCKED", falsify="切视口看溢出")])
        self.assertIn("https://example.invalid/p1|打开", probe["missing"])


class HighRiskAbsentDisclosureTest(unittest.TestCase):
    """plan 有风险面却零 high：只披露不压档（防整体定级下调）。"""

    @classmethod
    def setUpClass(cls):
        cls.ac = load_module(ROOT / "scripts" / "attack_coverage.py", "attack_cov_v150")

    ATK = [{"id": "ATK-01", "hypothesis": "如果并发提交则重复入账", "validation_path": "手工作业",
            "impact": "数据", "status": "未执行"}]

    def _run(self, risks):
        return self.ac.analyse([], plan_surfaces=sorted(risks), plan_risks=risks,
                               residual_note={"note": "引用 SURF 的未破面"},
                               atk_entries=self.ATK)

    def test_all_medium_or_low_discloses(self):
        result = self._run({"SURF-01": "medium", "SURF-02": "low"})
        codes = [notice["code"] for notice in result["notices"]]
        self.assertIn("high_risk_surface_absent", codes)
        # 披露项不得出现在 gaps（不参与压档）
        self.assertNotIn("high_risk_surface_absent",
                         [gap["code"] for gap in result["gaps"]])

    def test_with_high_surface_no_disclosure(self):
        result = self._run({"SURF-01": "high", "SURF-02": "medium"})
        codes = [notice["code"] for notice in result["notices"]]
        self.assertNotIn("high_risk_surface_absent", codes)


class ExplorationStrictGapTest(PhaseGateTestBase):
    """v1.52 审核补强：自报 ceiling=通过 洗不掉 schema 1.1 的结构化缺口。

    此前 _exploration_check_one 的缺口白名单只认 ready_pending / missing_views /
    traversal_recorded / mandatory_attributes，测量证据、键盘遍历、信号复测三项
    全靠 ceiling 自报——上游写「通过」就整块静默，"自相矛盾从严"那道防线看不到它们。
    """

    SIX = ("security", "compatibility", "content", "visual", "usability", "accessibility")

    def _gate(self, **over):
        gate = {"verdict_ceiling": "通过", "input_schema_version": "1.1",
                "traversal_recorded": True, "keyboard_recorded": True,
                "mandatory_attributes": {a: "covered" for a in self.SIX},
                "measurement_gaps": [], "signal_probe": {"missing": []}}
        gate.update(over)
        return gate

    def test_signal_missing_presses_despite_pass_claim(self):
        checked = self.pg._exploration_check_one(
            self._gate(signal_probe={"missing": ["https://e.invalid/p2|打开"]}))
        self.assertEqual(checked["ceiling"], "待核实")
        self.assertTrue(any("EXP-SIG-" in gap for gap in checked["gaps"]))

    def test_measurement_gaps_press_despite_pass_claim(self):
        checked = self.pg._exploration_check_one(
            self._gate(measurement_gaps=["EXP-V-1", "EXP-V-2"]))
        self.assertEqual(checked["ceiling"], "待核实")
        self.assertTrue(any("结构化测量产物" in gap for gap in checked["gaps"]))

    def test_keyboard_missing_presses_for_1_1_only(self):
        pressed = self.pg._exploration_check_one(self._gate(keyboard_recorded=False))
        self.assertEqual(pressed["ceiling"], "待核实")
        self.assertTrue(any("EXP-KBD-" in gap for gap in pressed["gaps"]))
        legacy = self.pg._exploration_check_one(
            self._gate(keyboard_recorded=False, input_schema_version="1.0"))
        self.assertEqual(legacy["ceiling"], "通过")
        self.assertEqual([g for g in legacy["gaps"] if "EXP-KBD-" in g], [])

    def test_unmarked_summary_discloses_but_does_not_press(self):
        self.finish_phase("plan")
        gate = self._gate()
        gate.pop("input_schema_version")
        self.state["phases"]["execute"]["exploration"] = self.pg._exploration_check_one(gate)
        check = self.pg.check_verdict(self.state)
        self.assertTrue(any(w["code"] == "exploration_summary_incomplete"
                            for w in check["warnings"]))
        self.assertNotIn("exploration_summary_incomplete",
                         [item["code"] for item in check["limitations"]])

    def test_marked_summary_carries_fields_without_warning(self):
        checked = self.pg._exploration_check_one(self._gate())
        self.assertEqual(checked["ceiling"], "通过")
        self.assertEqual(checked["input_schema_version"], "1.1")
        self.assertIn("signal_probe", checked)


class SignalProbeDocContractTest(unittest.TestCase):
    """文档必须与 v1.52 的逐单元语义同口径（旧的全局免测口径不得回潮）。"""

    def test_runtime_doc_declares_per_unit_fields(self):
        text = EXPLORATION_RUNTIME.read_text(encoding="utf-8")
        for token in ("deep_covered", "probed_covered", "pool_exhausted", "orphan_probed"):
            self.assertIn(token, text, f"exploration-runtime.md 未说明 gate 字段 {token}")
        # 全局免测已废除：文档若还写这个取值，执行者会以为升级一次就整批免测
        self.assertNotIn("deep_upgrade_recorded", text)

    def test_agents_declares_wiring_and_limits(self):
        text = AGENT_MD.read_text(encoding="utf-8")
        self.assertIn("signal_probe.missing", text)
        self.assertIn("一条升级只免它自己那一页", text)
        self.assertNotIn("deep_upgrade_recorded", text)

    def test_gate_emits_documented_fields(self):
        exp = load_module(ROOT / "scripts" / "exploration_state.py", "exp_v152_fields")
        keys = set(exp._signal_probe([], "https://x.invalid", True))
        self.assertTrue({"deep_ids", "sampled", "covered", "missing",
                         "enforced", "reason"} <= keys)


if __name__ == "__main__":
    unittest.main()
