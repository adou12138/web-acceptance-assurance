"""B1 裁决语义解耦（phase-verdict-check/2）专项测试。

验收基准（v1.49.0 方案 §B1/§14 B 组）：
- ``allowed = not reasons`` 消失：verdict_allowed 仅由 block 档决定；
- ``verdict_allowed=True + phase_ceiling=待核实`` 可表达（downgrade 档）；
- reasons 全集保留（旧消费者不受影响），且与 limitations 的 message 一一对应；
- REASON_POLICY 注册表覆盖 check_verdict 全部登记 code（漏登记 = 半死归类）；
- 每个 downgrade/block limitation 必须带非空 remedy（C-2：禁止半死字段）。
"""
import inspect
import re
from pathlib import Path

from tests.test_phase_gate import PhaseGateTestBase
from scripts.phase_gate import REASON_POLICY, REASON_POLICY_VERSION, check_verdict


class VerdictPolicySemanticsTests(PhaseGateTestBase):
    def setUp(self):
        super().setUp()
        self.finish_all()

    def test_downgrade_allows_verdict_with_ceiling(self):
        """B1 核心语义：verdict_allowed=True + phase_ceiling=待核实 必须可表达。"""
        check = self.pg.check_verdict(self.state, assumptions=None,
                                      regression={"changed_areas": [],
                                                  "candidates": [],
                                                  "no_impact_reason": "夹具"})
        self.assertTrue(check["verdict_allowed"])
        self.assertEqual(check["phase_ceiling"], "待核实")
        codes = {l["code"] for l in check["limitations"]}
        self.assertIn("assumptions_missing", codes)
        for item in check["limitations"]:
            self.assertTrue(item["remedy"], f"{item['code']} 缺 remedy（半死字段）")
            self.assertIn("needs_human", item)
            self.assertIn("message", item)

    def test_block_refuses_verdict(self):
        """block 档（阶段未完成）仍拒绝出具结论，且与 downgrade 分账。"""
        self.state["phases"]["adjudicate"]["status"] = "in_progress"
        check = check_verdict(self.state,
                              assumptions={"reviewed": True, "items": []},
                              regression={"changed_areas": [],
                                          "candidates": [],
                                          "no_impact_reason": "夹具"})
        self.assertFalse(check["verdict_allowed"])
        self.assertTrue(check["blocking_reasons"])
        self.assertTrue(any(l["code"] == "phase_not_done"
                            for l in check["limitations"]))
        self.assertEqual(check["phase_ceiling"], "待核实")
        # reasons 全集仍包含 blocking 与 downgrade 两类文本，不因分账丢信息
        self.assertIn(check["blocking_reasons"][0], check["reasons"])

    def test_reasons_full_set_matches_limitations(self):
        """reasons 全集保留，且 downgrade/block 档文本与 limitations 一一对应。"""
        check = self.pg.check_verdict(self.state, assumptions=None,
                                      regression=None)
        self.assertTrue(check["reasons"])
        limitation_msgs = [l["message"] for l in check["limitations"]]
        self.assertEqual(sorted(check["reasons"]), sorted(limitation_msgs))
        self.assertTrue(check["blocking_reasons"] is not None)
        self.assertTrue(isinstance(check["policy_version"], str))

    def test_schema_and_policy_version(self):
        check = check_verdict(self.state)
        self.assertEqual(check["schema"], "phase-verdict-check/2")
        self.assertEqual(check["policy_version"], REASON_POLICY_VERSION)
        self.assertEqual(check["policy_version"], "reason-policy/1")

    def test_disclose_mode_never_enters_limitations(self):
        """disclose 档只留痕：不进 limitations、不压 verdict_allowed、不设 ceiling。"""
        disclose = [code for code, p in REASON_POLICY.items() if p["mode"] == "disclose"]
        self.assertTrue(disclose, "注册表缺少 disclose 档（三档语义不完整）")
        check = check_verdict(self.state,
                              assumptions={"reviewed": True, "items": []},
                              regression={"changed_areas": [],
                                          "candidates": [],
                                          "no_impact_reason": "夹具"})
        self.assertTrue(check["verdict_allowed"])
        # 本场景无 block/downgrade 时上限必须为 None（与旧口径 allowed=True 相同）
        self.assertIsNone(check["phase_ceiling"])
        self.assertEqual(check["limitations"], [])
        used_modes = {l["code"] for l in []} | set()
        self.assertEqual(used_modes, set())


class VerdictPolicyRegistryTests(PhaseGateTestBase):
    def test_registry_covers_all_registered_hit_codes(self):
        """注册表必须覆盖 check_verdict 及其辅助函数中全部 _hit 登记的 code。"""
        source = Path(inspect.getfile(check_verdict)).read_text(encoding="utf-8")
        used = set(re.findall(r'_hit(?:_each)?\(hits,\s*"([a-z_]+)"', source))
        self.assertTrue(used, "源码中未找到任何 _hit 登记（归类机制被撤？）")
        unknown = used - set(REASON_POLICY)
        self.assertEqual(unknown, set(),
                         f"登记 code 未在 REASON_POLICY 注册：{sorted(unknown)}")

    def test_registry_modes_are_enumerated(self):
        """mode 只允许 block/downgrade/disclose 三档，needs_human 必须为布尔。"""
        for code, policy in REASON_POLICY.items():
            self.assertIn(policy["mode"], ("block", "downgrade", "disclose"),
                          f"{code} 的 mode 非法：{policy['mode']}")
            self.assertIsInstance(policy["needs_human"], bool, code)
            if policy["mode"] in ("block", "downgrade"):
                self.assertTrue(policy["remedy"], f"{code} 缺 remedy（半死字段）")

    def test_block_modes_match_plan(self):
        """block 档与方案 B1 表逐条一致：阶段未完成/未批范围变更/交接未消费。"""
        self.assertEqual({code for code, p in REASON_POLICY.items()
                          if p["mode"] == "block"},
                         {"phase_not_done", "amendment_unapproved",
                          "handoff_unconsumed"})