"""v1.47.1 影响面复审整改回归：旧产物兼容、条目数防漂移、第二处字段诊断。

背景：v1.47.0 的改动只覆盖了「改的时候想到的那一个消费方」，复审实测出三类遗漏：
①报告新增「页面×状态单元」列在 v1.46 形态门禁产物上渲染成 0，与「有台账记录」自相
矛盾（把「字段没采」读成「零覆盖」）；②主提示词仍写「9 条代表 prompt」而基线已 10
条，照字面执行会跳过新增的 R10，恰好使 E07 的防回归机制失效；③E01 的字段点名只修了
exploration_state，adversarial_data 仍是不透明报错——而手写 adversarial-plan.json 正
是执行者会走的那条路。本文件把三处锁住。
"""
import csv
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import adversarial_data  # noqa: E402

AGENT_MD = ROOT / "agents" / "web-acceptance-assurance.md"
ROUTE_PROMPTS = ROOT / "references" / "route-prompts.json"
RENDER_SCRIPT = ROOT / "scripts" / "render_report_html.py"

LEDGER_COLUMNS = [
    "view", "id", "requirement_ids", "case_id", "target_url", "application_version",
    "environment", "page_url", "role", "viewport", "state", "action", "risk", "status",
    "attributes", "observation", "evidence_paths", "evidence", "blocked_reason",
    "side_effect_pending",
]


def _write_ledger(path):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=LEDGER_COLUMNS)
        writer.writeheader()
        writer.writerow({
            "view": "功能", "id": "EXP-001", "requirement_ids": '["REQ-1"]',
            "case_id": "", "target_url": "https://example.invalid",
            "application_version": "v1", "environment": "本地受控",
            "page_url": "https://example.invalid/app", "role": "访客",
            "viewport": "1280x800", "state": "初始态", "action": "提交表单",
            "risk": "low", "status": "PASS", "attributes": '["functional"]',
            "observation": "提交成功", "evidence_paths": "[]", "evidence": "[]",
            "blocked_reason": "", "side_effect_pending": "false",
        })


def _render(folder, gate_payload):
    ledger = folder / "ledger.csv"
    _write_ledger(ledger)
    gate = folder / "gate.json"
    gate.write_text(json.dumps(gate_payload, ensure_ascii=False), encoding="utf-8")
    out = folder / "report.html"
    result = subprocess.run(
        [sys.executable, str(RENDER_SCRIPT), "--output", str(out),
         "--dataset", f"样本::{ledger}::{gate}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise AssertionError(result.stderr[-600:])
    return out.read_text(encoding="utf-8")


def _gate(**overrides):
    payload = {
        "schema_version": "1.0", "planned": 1, "counts": {"PASS": 1},
        "gate": {"ready_pending": 0, "ready_pending_ids": [], "ready_high_risk": 0,
                 "missing_views": [], "failed_items": [], "traversal_recorded": True,
                 "mandatory_attributes": {"visual": "covered"},
                 "verdict_ceiling": "待核实", "message": "样本"},
    }
    payload["gate"].update(overrides)
    return payload


class TestLegacyGateCompatibility(unittest.TestCase):
    """旧门禁产物没有 view_cells/thin_views 时，报告不得渲染出「0 单元」。"""

    def test_legacy_gate_omits_cells_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            html = _render(Path(tmp), _gate())
            # 列头缺失；说明文字里出现该词是允许的（那正是「数据未提供」的告知）
            self.assertNotIn("<th style='width:130px'>页面×状态单元</th>", html)
            self.assertIn("有台账记录", html)
            # 三列表：视角 / 本轮条目 / 状态，不留第四列的空壳
            row = re.search(r"<tr><td>功能</td>(.*?)</tr>", html)
            self.assertIsNotNone(row)
            self.assertEqual(len(re.findall(r"<td", row.group(1))), 2, row.group(1))

    def test_legacy_gate_states_data_absent_in_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            html = _render(Path(tmp), _gate())
            self.assertIn("未提供页面×状态单元数据", html)

    def test_partial_cells_show_dash_not_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            html = _render(Path(tmp), _gate(
                view_cells={"界面样式": ["https://example.invalid/app|初始态"]},
                missing_views=["基础无障碍"]))
            self.assertIn("页面×状态单元", html)
            # 功能视角没有单元数据 → 必须是「—」，不能是 0
            row = re.search(r"<tr><td>功能</td>(.*?)</tr>", html)
            self.assertIsNotNone(row)
            self.assertIn("—", row.group(1))
            self.assertNotIn("<td>0</td>", row.group(1))


class TestRouteBaselineCountGuard(unittest.TestCase):
    """主提示词声明的基线条目数必须等于文件实际条数——防止 R10 被字面跳过。"""

    def test_declared_count_matches_file(self):
        prompts = json.loads(ROUTE_PROMPTS.read_text(encoding="utf-8-sig"))["prompts"]
        text = AGENT_MD.read_text(encoding="utf-8")
        match = re.search(r"route-prompts\.json`（(\d+)\s*条代表 prompt", text)
        self.assertIsNotNone(match, "主提示词未声明基线条目数")
        self.assertEqual(int(match.group(1)), len(prompts),
                         f"声明 {match.group(1)} 条，实际 {len(prompts)} 条")


class TestAdversarialFieldDiagnostics(unittest.TestCase):
    """手写 adversarial-plan.json 时，字段错误必须点名到具体字段。"""

    def test_missing_field_is_named(self):
        with self.assertRaises(ValueError) as ctx:
            adversarial_data.validate({"schema_version": "1.0", "plan_id": "PLAN-1"})
        self.assertIn("缺字段", str(ctx.exception))
        self.assertIn("surfaces", str(ctx.exception))

    def test_unknown_field_is_named(self):
        plan = {"schema_version": "1.0", "plan_id": "PLAN-1",
                "source_fingerprints": {"material": "a", "cases_json": "b"},
                "surfaces": [], "oracles": [], "obligations": [],
                "typo_field": 1}
        with self.assertRaises(ValueError) as ctx:
            adversarial_data.validate(plan)
        self.assertIn("未知字段", str(ctx.exception))
        self.assertIn("typo_field", str(ctx.exception))

    def test_missing_and_unknown_are_separate_parts(self):
        with self.assertRaises(ValueError) as ctx:
            adversarial_data.validate({"schema_version": "1.0", "ooops": 1})
        message = str(ctx.exception)
        self.assertIn("缺字段", message)
        self.assertIn("未知字段", message)
        self.assertIn("ooops", message)


if __name__ == "__main__":
    unittest.main()
