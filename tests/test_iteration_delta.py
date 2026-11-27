"""迭代增量口径（S2）与循环产物边界（S3）专项测试。

验收基准：
- 新增执行只数 status∈{PASS,FAIL} 且执行签名（case_id, surf_id, intended_action）
  未在历史出现的 FINISH；BLOCKED 与重放不计；
- 证据 sha256 复用只披露、不排除：确定性证据不会把重跑误判成零增量；
- 计划外 surf_id 计入增量并标记 unregistered_expansion（与 attack_coverage 同口径）；
- 循环产物扫描覆盖顶层与嵌套判定字段，且包含 accepted。
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import iteration_delta as delta


def begin(attempt_id, case_id, action, seq):
    return {"kind": "BEGIN", "attempt_id": attempt_id, "case_id": case_id,
            "intended_action": action, "seq": seq}


def finish(attempt_id, case_id, status, seq, surf_id="SURF-01",
           evidence=("a" * 64,), oracle_source="deterministic", channel=None,
           method=None):
    event = {"kind": "FINISH", "attempt_id": attempt_id, "case_id": case_id,
             "status": status, "seq": seq, "surf_id": surf_id,
             "oracle_source": oracle_source,
             "evidence": [{"path": f"/e/{attempt_id}.png", "sha256": digest}
                          for digest in evidence]}
    if channel:
        event["oracle_channel"] = channel
    if method:
        event["method"] = method
    return event


class RoundDeltaTests(unittest.TestCase):
    def test_only_new_pass_fail_counted(self):
        events = [
            begin("A1", "TC-1", "点保存", 1),
            finish("A1", "TC-1", "PASS", 2),
            begin("A2", "TC-2", "点导出", 3),
            finish("A2", "TC-2", "BLOCKED", 4, evidence=()),
            begin("A3", "TC-3", "点审批", 5),
            finish("A3", "TC-3", "FAIL", 6),
            # 同 case × 同 surf × 同动作的重放：签名重复，不计入增量
            begin("A4", "TC-3", "点审批", 7),
            finish("A4", "TC-3", "FAIL", 8, evidence=("b" * 64,)),
        ]
        result = delta.round_delta(events)
        self.assertEqual(result["new_executions"], 2)
        self.assertEqual(result["new_attempt_ids"], ["A1", "A3"])
        self.assertEqual(result["blocked_not_counted"], 1)
        self.assertEqual(result["replay_not_counted"], 1)

    def test_action_rewording_does_not_add_increment(self):
        """intended_action 是自由文本：换措辞不能把同一 case×surf 包装成新执行。"""
        events = [
            begin("A1", "TC-1", "点保存", 1), finish("A1", "TC-1", "PASS", 2),
            begin("A2", "TC-1", "点击保存按钮", 3),
            finish("A2", "TC-1", "PASS", 4, evidence=("b" * 64,)),
        ]
        result = delta.round_delta(events)
        self.assertEqual(result["new_executions"], 1)
        self.assertEqual(result["replay_not_counted"], 1)

    def test_coverage_expansion_disclosed(self):
        events = [begin("A1", "TC-1", "点保存", 1),
                  finish("A1", "TC-1", "PASS", 2, method="boundary"),
                  begin("A2", "TC-2", "点导出", 3),
                  finish("A2", "TC-2", "PASS", 4, surf_id="SURF-02",
                        method="fault_injection")]
        result = delta.round_delta(events)
        self.assertEqual(result["new_case_ids"], ["TC-1", "TC-2"])
        self.assertEqual(result["new_surf_ids"], ["SURF-01", "SURF-02"])
        self.assertEqual(result["method_counts"],
                         {"boundary": 1, "fault_injection": 1})

    def test_since_seq_watermark(self):
        events = [begin("A1", "TC-1", "点保存", 1), finish("A1", "TC-1", "PASS", 2),
                  begin("A2", "TC-2", "点导出", 3), finish("A2", "TC-2", "PASS", 4)]
        result = delta.round_delta(events, since_seq=2)
        self.assertEqual(result["new_attempt_ids"], ["A2"])
        self.assertEqual(result["through_seq"], 4)

    def test_evidence_reuse_is_disclosure_only(self):
        """证据哈希复用不排除新签名：否则确定性证据会让增量永远为 0。"""
        events = [
            begin("A1", "TC-1", "点保存", 1),
            finish("A1", "TC-1", "PASS", 2, evidence=("a" * 64,)),
            begin("A2", "TC-9", "点导出", 3),
            finish("A2", "TC-9", "PASS", 4, surf_id="SURF-09",
                   evidence=("a" * 64,)),
        ]
        result = delta.round_delta(events, since_seq=2)
        self.assertEqual(result["new_executions"], 1)
        self.assertEqual(result["evidence_reused"], 1)

    def test_unregistered_expansion_counted_and_flagged(self):
        events = [begin("A1", "TC-1", "点导出", 1),
                  finish("A1", "TC-1", "PASS", 2, surf_id="SURF-99")]
        result = delta.round_delta(events, plan_surfaces={"SURF-01"})
        self.assertEqual(result["new_executions"], 1)
        self.assertEqual(result["unregistered_expansion"], ["A1"])

    def test_independent_oracle_disclosed_only(self):
        events = [
            begin("A1", "TC-1", "点导出", 1),
            finish("A1", "TC-1", "PASS", 2, oracle_source="independent",
                   channel="数据库只读查询"),
            begin("A2", "TC-2", "点审批", 3),
            finish("A2", "TC-2", "PASS", 4, oracle_source="independent"),
        ]
        result = delta.round_delta(events)
        self.assertEqual(result["new_executions"], 2)
        # 缺通道的 independent 只披露不计入独立口径
        self.assertEqual(result["oracle_attempt_ids"], ["A1"])

    def test_plan_surface_ids(self):
        plan = {"surfaces": [{"id": "SURF-01"}, {"entry": "无 ID"}]}
        self.assertEqual(delta.plan_surface_ids(plan), {"SURF-01"})
        self.assertIsNone(delta.plan_surface_ids({}))
        with self.assertRaises(ValueError):
            delta.plan_surface_ids({"surfaces": "SURF-01"})


class LoopProductScanTests(unittest.TestCase):
    def test_flags_top_level_and_nested_judgement_fields(self):
        products = [
            {"id": "C1", "observation": "候选", "verdict": "PASS"},
            {"id": "C2", "observation": "候选", "accepted": True},
            {"id": "C3", "observation": {"severity": "major"}},
            {"id": "C4", "observation": "候选", "iteration_id": "ITER-001"},
        ]
        violations = delta.scan_loop_products(products)
        keys = {item.get("key") for item in violations}
        self.assertIn("verdict", keys)
        self.assertIn("accepted", keys)
        self.assertIn("observation.severity", keys)
        self.assertEqual({item["id"] for item in violations}, {"C1", "C2", "C3"})

    def test_clean_products_pass(self):
        products = [{"id": "C1", "phenomenon": "保存后无提示",
                     "evidence_paths": ["/e/a.png"], "iteration_id": "ITER-001",
                     # 嵌套 status 是可观测事实（HTTP 500），不是裁决字段
                     "observed": {"network": [{"url": "/save", "status": 500}]}}]
        self.assertEqual(delta.scan_loop_products(products), [])

    def test_nested_accepted_flagged(self):
        products = [{"id": "C1", "observed": {"lead": {"accepted": True}}}]
        violations = delta.scan_loop_products(products)
        self.assertEqual([item["key"] for item in violations], ["observed.lead.accepted"])


if __name__ == "__main__":
    unittest.main()
