"""v3 目标审计 A 批回归：错误放行必须被拦截（离线、只读夹具）。

G03 可信存量标识 / G08 必选探索义务 / G09 台账门禁绑定 / G12 适配器无关写授权。
这些是机制反例，合成输入不代表真实产品缺陷。
"""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ExplorationGateObligations(unittest.TestCase):
    def setUp(self):
        self.tool = load("exp_v3", "scripts/exploration_state.py")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.evidence = Path(self.tmp.name) / "观察.txt"
        self.evidence.write_text("夹具证据", encoding="utf-8")
        base = {
            "id": "EXP-TRAV-01", "requirement_ids": ["REQ-1"],
            "page_url": "https://example.invalid/app/form",
            "role": "编辑者", "viewport": "1440x900", "state": "打开",
            "action": "核对控件", "risk": "high", "status": "PASS",
            "observation": "已观察", "evidence_paths": [str(self.evidence)],
            "blocked_reason": "", "side_effect_pending": False,
            "attributes": ["functional", "visual", "usability", "accessibility", "performance"],
        }
        self.base = base
        self.data = {
            "schema_version": "1.0",
            "context": {"target_url": "https://example.invalid/app", "application_version": "b1", "environment": "test"},
            "items": [copy.deepcopy(base),
                      dict(copy.deepcopy(base), id="EXP-TRAV-02",
                           page_url="https://example.invalid/app/list", state="列表")],
        }

    def gate(self, extra):
        self.data["items"] = [copy.deepcopy(self.base), extra]
        return self.tool.summarize(self.data)["gate"]

    def test_blocked_security_obligation_lowers_ceiling(self):
        row = dict(self.base, id="EXP-SEC-01", status="BLOCKED", attributes=["security"],
                   blocked_reason="缺第二个账号")
        g = self.gate(row)
        self.assertNotEqual(g["verdict_ceiling"], "通过", "必选安全阻塞不得放行结论上限")

    def test_pending_side_effect_lowers_ceiling(self):
        row = dict(self.base, id="EXP-WRITE-01", status="NOT_RUN",
                   side_effect_pending=True, attributes=["functional"])
        g = self.gate(row)
        self.assertNotEqual(g["verdict_ceiling"], "通过", "未决副作用不得放行结论上限")
        self.assertTrue(g.get("unreconciled_pending"))

    def test_pending_traversal_lowers_ceiling(self):
        row = dict(self.base, id="EXP-TRAV-02", status="NOT_RUN",
                   blocked_reason="第二页未遍历", attributes=["functional"])
        g = self.gate(row)
        self.assertNotEqual(g["verdict_ceiling"], "通过", "必选页面未遍历不得放行结论上限")

    def test_clean_plan_still_passes(self):
        self.data["attribute_rulings"] = {
            "security": {"state": "not_applicable", "reason": "本轮清单未涉及权限与会话变更"},
            "compatibility": {"state": "not_applicable", "reason": "未声明内核/设备范围"},
            "content": {"state": "not_applicable", "reason": "未涉及文案变更"},
        }
        g = self.tool.summarize(self.data)["gate"]
        self.assertEqual(g["verdict_ceiling"], "通过")


class LedgerGateBinding(unittest.TestCase):
    def setUp(self):
        self.tool = load("ledger_v3", "scripts/execution_ledger.py")
        design = {"schema_version": "1.0", "testcases": [
            {"id": "TC-1", "title": "保存名称", "preconditions": [], "steps": [{"no": 1, "action": "打开", "expected": "显示"}],
             "assertions": [{"check": "名称正确", "actual": "", "matched": False}], "suites": ["smoke"], "priority": "P0"},
        ]}
        self.design = design

    def test_minimal_gate_report_is_marked_restricted(self):
        thin = {"status": "pass", "counts": {"testcases": 1},
                "checks": [{"id": "S0", "status": "passed"}]}
        info = self.tool._gate_precondition(None, thin, ["TC-1"], self.design)
        self.assertEqual(info.get("binding_verdict"), "待核实", "最小门禁报告不得被视为完整绑定")
        self.assertTrue(info.get("binding_missing"))

    def test_bound_gate_report_is_verified(self):
        fp = self.tool.fingerprint(self.design)
        bound = {"status": "pass", "counts": {"testcases": 1},
                 "checks": [{"id": "S0", "status": "passed"}, {"id": "COV", "status": "passed"}],
                 "binding": {"design_hash": fp, "policy_version": "v3", "scope": ["TC-1"],
                             "required_checks": ["S0", "COV"]}}
        info = self.tool._gate_precondition(None, bound, ["TC-1"], self.design)
        self.assertEqual(info.get("binding_verdict"), "已绑定")
        self.assertFalse(info.get("binding_missing"))

    def test_bound_gate_wrong_design_hash_rejected(self):
        bound = {"status": "pass", "counts": {"testcases": 1},
                 "checks": [{"id": "S0", "status": "passed"}],
                 "binding": {"design_hash": "deadbeef", "policy_version": "v3", "scope": ["TC-1"],
                             "required_checks": ["S0"]}}
        with self.assertRaises(ValueError):
            self.tool._gate_precondition(None, bound, ["TC-1"], self.design)


class WriteGateAdapterIndependent(unittest.TestCase):
    def setUp(self):
        self.tool = load("flow_v3", "skills/api-test/scripts/flow.py")

    def test_deny_adapter_blocks_write_for_any_adapter_name(self):
        class Deny:
            def is_write_allowed(self, env):
                return False
        env = {"environment": {"type": "production", "write_enabled": False}}
        for name in ("rest", "graphql", "grpc", "sojump"):
            with self.subTest(name=name):
                allowed, _ = self.tool.write_gate(Deny(), name, env, "删除")
                self.assertFalse(allowed, "拒绝策略必须对任何适配器名称生效")

    def test_explicit_test_env_allows_write_without_adapter_method(self):
        class NoMethod:
            pass
        env = {"environment": {"type": "test", "write_enabled": True}}
        allowed, _ = self.tool.write_gate(NoMethod(), "rest", env, "创建")
        self.assertTrue(allowed, "显式测试环境授权应独立于适配器名称放行")

    def test_unknown_op_denied(self):
        class NoMethod:
            pass
        allowed, _ = self.tool.write_gate(NoMethod(), "rest", {}, "随便一个未知动作")
        self.assertFalse(allowed, "未知操作默认不可写")


if __name__ == "__main__":
    unittest.main()
