"""首屏事实契约（M8）：完成范围、必查项状态、限档原因由台账摘要唯一产出。

核心约束：**报告首屏与台账摘要消费同一份事实**，不得各算一套——
各算一次必然漂移，而漂移方向通常是报好看的那个。

五种首屏情形（与方案验收一致）：
1. 仅授权限制；2. 执行未完；3. 有阻塞；4. 存在更严失败；5. 旧摘要缺字段。
"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HeadlineBase(unittest.TestCase):
    def setUp(self):
        self.ledger = load("execution_ledger")
        self.pg = load("phase_gate")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.design = json.loads(
            (ROOT / "examples" / "cases-example.json").read_text(encoding="utf-8"))
        self.evidence = self.folder / "observations.txt"
        self.evidence.write_text("fixture observation", encoding="utf-8")

    def context(self, dependency_mode="sandbox"):
        return {"target_url": "https://example.invalid/test", "environment": "test",
                "application_version": "build-1", "feature_flags": {"rename": True},
                "role_scope": ["editor"], "dependency_mode": dependency_mode}

    def make_workspace(self, name="run", dependency_mode="sandbox", **kwargs):
        workspace = self.folder / name
        context = self.context(dependency_mode)
        self.ledger.initialize(workspace, self.design, context, "ROUND-1", [], **kwargs)
        return workspace, context

    def run_case(self, workspace, design, context, attempt_id, case_id, status="PASS",
                 cause="none", bug_ids=None):
        self.ledger.begin(workspace, design, context, attempt_id, case_id, "执行动作")
        self.ledger.finish(workspace, design, context, attempt_id, status,
                           "Observed fixture result", [str(self.evidence)],
                           [{"check": "项目重新打开后的名称", "actual": "新名称",
                             "matched": status == "PASS"}],
                           cause, bug_ids or [], "无远端副作用")

    def settled_phase_state(self):
        """三阶段全部走完的阶段状态（与 phase_gate 产出的字段一致）。"""
        state = self.pg.new_state("TASK-1", "SESS-1", "strong-reasoning", "GPT")
        for phase in ("plan", "execute", "adjudicate"):
            entry = state["phases"][phase]
            entry["status"] = "done"
            entry["approval"] = {"mode": "cross_session"}
            entry["routing"] = {"declared_class": self.pg.ROUTING[phase]["model_class"],
                                "expected_class": self.pg.ROUTING[phase]["model_class"]}
        state["active_session"] = "SESS-3"
        return state

    def verified_adversarial(self):
        return {"verdict_ceiling": "通过", "adversarial_enabled": True, "reasons": []}


class HeadlineContract(HeadlineBase):
    """台账侧：headline 的三组事实与实际一致。"""

    def test_headline_is_present_and_consistent_with_ceiling(self):
        workspace, context = self.make_workspace()
        report = self.ledger.summarize(workspace, self.design, context)
        headline = report["headline"]
        self.assertEqual(headline["schema"], "ledger-headline/1")
        # 上限只有一个来源：headline 与顶层字段必须是同一个值
        self.assertEqual(headline["verdict_ceiling"], report["verdict_ceiling"])
        self.assertEqual(headline["completion_scope"]["planned"], report["planned"])
        self.assertEqual(headline["completion_scope"]["executed"], report["executed"])
        self.assertTrue(headline["required_checks"])
        # 限档原因必须逐条带 code 与可读说明，不许空白
        for item in headline["limitation_reasons"]:
            self.assertTrue(item.get("code"))
            self.assertTrue(str(item.get("text") or "").strip())

    def test_execution_incomplete_is_disclosed(self):
        """情形 2：执行未完。"""
        workspace, context = self.make_workspace()
        report = self.ledger.summarize(workspace, self.design, context)
        headline = report["headline"]
        codes = [item["code"] for item in headline["limitation_reasons"]]
        self.assertIn("execution_incomplete", codes)
        self.assertEqual(headline["completion_scope"]["coverage"], "partial")
        self.assertEqual(headline["verdict_ceiling"], "待核实")

    def test_blocked_case_is_disclosed(self):
        """情形 3：有阻塞。"""
        workspace, context = self.make_workspace()
        self.ledger.begin(workspace, self.design, context, "ATT-1", "TC-NAME-001", "执行动作")
        report = self.ledger.summarize(workspace, self.design, context)
        codes = [item["code"] for item in report["headline"]["limitation_reasons"]]
        self.assertIn("blocked_cases", codes)

    def test_failure_keeps_strictest_ceiling(self):
        """情形 4：存在更严失败 → 上限为「不通过」，且失败原因不被其他维度稀释。"""
        workspace, context = self.make_workspace()
        self.run_case(workspace, self.design, context, "ATT-1", "TC-NAME-001",
                      status="FAIL", cause="product", bug_ids=["BUG-1"])
        report = self.ledger.summarize(workspace, self.design, context,
                                       bugs=[{"id": "BUG-1", "severity": "major",
                                              "status": "open"}])
        headline = report["headline"]
        self.assertEqual(headline["verdict_ceiling"], "不通过")
        codes = [item["code"] for item in headline["limitation_reasons"]]
        self.assertIn("failure_detected", codes)

    def test_authorization_only_allows_readonly_claim(self):
        """情形 1：仅授权限制 → 必查项全通过，可写「只读范围内已完成」。"""
        workspace, context = self.make_workspace("ro", dependency_mode="replay",
                                                 phase_state=self.settled_phase_state(),
                                                 adversarial_gate=self.verified_adversarial())
        for case_id in ("TC-NAME-001", "TC-NAME-002"):
            self.run_case(workspace, self.design, context,
                          f"ATT-{case_id}", case_id)
        report = self.ledger.summarize(workspace, self.design, context,
                                       phase_state=self.settled_phase_state(),
                                       adversarial_gate=self.verified_adversarial())
        headline = report["headline"]
        self.assertEqual(headline["verdict_ceiling"], "通过")
        codes = [item["code"] for item in headline["limitation_reasons"]]
        self.assertEqual(codes, ["authorization_only"])
        self.assertTrue(headline["completion_scope"]["readonly_only"])
        self.assertTrue(headline["completion_scope"]["can_claim_readonly_complete"])
        self.assertTrue(all(c["status"] == "ok" for c in headline["required_checks"]))

    def test_readonly_claim_is_withheld_when_other_gap_exists(self):
        """有其他缺口时不得写「只读范围内已完成」——授权不能当遮羞布。"""
        workspace, context = self.make_workspace("ro2", dependency_mode="replay",
                                                 phase_state=self.settled_phase_state(),
                                                 adversarial_gate=self.verified_adversarial())
        self.run_case(workspace, self.design, context, "ATT-1", "TC-NAME-001")
        report = self.ledger.summarize(workspace, self.design, context,
                                       phase_state=self.settled_phase_state(),
                                       adversarial_gate=self.verified_adversarial())
        headline = report["headline"]
        self.assertFalse(headline["completion_scope"]["can_claim_readonly_complete"])
        self.assertIn("execution_incomplete",
                      [item["code"] for item in headline["limitation_reasons"]])

    def test_required_check_covers_five_dimensions(self):
        """必查项固定五个维度，状态取值受控。"""
        workspace, context = self.make_workspace()
        report = self.ledger.summarize(workspace, self.design, context)
        checks = {c["id"]: c for c in report["headline"]["required_checks"]}
        self.assertEqual(set(checks),
                         {"phase_state", "adversarial_gate", "attack_coverage",
                          "evidence_integrity", "gate_inputs"})
        for check in checks.values():
            self.assertIn(check["status"], {"ok", "limited", "missing", "unknown"})
            self.assertTrue(check["detail"], "必查项必须带可核对的说明，不许空白")

    def test_three_phase_flow_does_not_report_missing_attack_coverage(self):
        """走三阶段时攻击覆盖由 phase_gate 承担，台账不得再报缺失。"""
        workspace, context = self.make_workspace(
            "p3", phase_state=self.settled_phase_state(),
            adversarial_gate=self.verified_adversarial())
        report = self.ledger.summarize(workspace, self.design, context,
                                       phase_state=self.settled_phase_state(),
                                       adversarial_gate=self.verified_adversarial())
        checks = {c["id"]: c for c in report["headline"]["required_checks"]}
        self.assertEqual(checks["attack_coverage"]["status"], "ok")
        self.assertEqual(checks["gate_inputs"]["status"], "ok")


class ReportConsumesSameFacts(HeadlineBase):
    """渲染器侧：首屏消费台账摘要的同一份事实。"""

    def setUp(self):
        super().setUp()
        self.render = load("render_report_html")

    CSV_COLUMNS = ["view", "id", "requirement_ids", "case_id", "target_url",
                   "application_version", "environment", "page_url", "role", "viewport",
                   "state", "action", "risk", "status", "attributes", "observation",
                   "evidence_paths", "evidence", "blocked_reason", "side_effect_pending"]

    def write_csv(self, name, rows):
        import csv
        path = self.folder / name
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def csv_row(self, ident, status="NOT_RUN"):
        return {"view": "功能", "id": ident, "requirement_ids": '["REQ-01"]',
                "case_id": "", "target_url": "https://example.invalid/app",
                "application_version": "build-1", "environment": "本地受控",
                "page_url": "https://example.invalid/app/form", "role": "访客",
                "viewport": "1280x800", "state": "观察状态", "action": "执行动作",
                "risk": "high", "status": status, "attributes": '["visual"]',
                "observation": "", "evidence_paths": "[]", "evidence": "[]",
                "blocked_reason": "", "side_effect_pending": "false"}

    def write_json(self, name, payload):
        path = self.folder / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPTS / "render_report_html.py"),
                               *map(str, args)], capture_output=True, text=True,
                              encoding="utf-8")

    def test_missing_headline_falls_back_with_notice(self):
        """情形 5：旧摘要缺字段 → 回退并如实标注，不得静默按推断口径出首屏。"""
        headline, note = self.render.load_headline(None)
        self.assertIsNone(headline)
        self.assertIn("未提供台账摘要", note)
        path = self.folder / "summary.json"
        path.write_text(json.dumps({"verdict_ceiling": "待核实"}, ensure_ascii=False),
                        encoding="utf-8")
        headline, note = self.render.load_headline(str(path))
        self.assertIsNone(headline)
        self.assertIn("缺首屏事实字段", note)

    def test_present_headline_is_consumed_verbatim(self):
        payload = {"headline": {"schema": "ledger-headline/1",
                                 "verdict_ceiling": "不通过",
                                 "completion_scope": {"text": "计划 2 项，已执行 1 项"},
                                 "required_checks": [{"id": "phase_state", "label": "三阶段闸门",
                                                      "status": "missing", "detail": "未提供"}],
                                 "limitation_reasons": [{"code": "execution_incomplete",
                                                          "text": "1 项用例未执行"}]}}
        path = self.folder / "summary.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        headline, note = self.render.load_headline(str(path))
        self.assertIsNotNone(headline)
        self.assertEqual(note, "")
        self.assertEqual(headline["verdict_ceiling"], "不通过")

    def test_headline_schema_constant_is_shared(self):
        """生产端与消费端的 schema 常量必须同值，否则每份新摘要都会被判"版本不符"。"""
        self.assertEqual(self.render.HEADLINE_SCHEMA, self.ledger.HEADLINE_SCHEMA)

    def test_headline_schema_mismatch_falls_back_with_notice(self):
        """情形 6：schema 版本不符 → 回退推断并标注，不得按当前口径解读旧字段。

        字段语义会随版本变（某个 status 可能从两态扩成三态）。拿旧 schema 的字段
        按新口径渲染，会得出一个"看起来合规"的错结论——那正是首屏事实单一来源
        要消除的漂移，只是换了个位置复发。
        """
        for schema in ("ledger-headline/0", "ledger-headline/2", None, ""):
            payload = {"headline": {"schema": schema, "verdict_ceiling": "通过",
                                    "completion_scope": {"text": "旧口径占位"}}}
            path = self.folder / f"summary-{str(schema).replace('/', '-')}.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            headline, note = self.render.load_headline(str(path))
            self.assertIsNone(headline, f"schema={schema!r} 不得被静默消费")
            self.assertIn("版本不符", note)
            self.assertIn(self.render.HEADLINE_SCHEMA, note)

    def test_report_first_screen_uses_summary_facts(self):
        """端到端：首屏展示的完成范围/必查项/限档原因均来自台账摘要。"""
        ledger = self.write_csv("ledger.csv", [self.csv_row("OBS-a")])
        gate = self.write_json("gate.json", {"gate": {"verdict_ceiling": "通过",
                                                       "missing_views": []}})
        summary = self.write_json("summary.json", {"headline": {
            "schema": "ledger-headline/1",
            "verdict_ceiling": "待核实",
            "completion_scope": {"text": "计划 2 项，已执行 1 项；未执行 1 项",
                                  "can_claim_readonly_complete": False},
            "required_checks": [{"id": "phase_state", "label": "三阶段闸门",
                                 "status": "missing", "detail": "未提供阶段状态"}],
            "limitation_reasons": [{"code": "execution_incomplete",
                                    "text": "1 项用例未执行，验收范围未覆盖"}]}})
        output = self.folder / "report.html"
        result = self.run_cli("--output", str(output), "--title", "验收报告",
                               "--subject", "受控站",
                               "--dataset", f"桌面::{ledger}::{gate}",
                               "--summary", str(summary))
        self.assertEqual(result.returncode, 0, result.stderr)
        page = output.read_text(encoding="utf-8")
        self.assertIn("完成范围", page)
        self.assertIn("计划 2 项，已执行 1 项；未执行 1 项", page)
        self.assertIn("必查项", page)
        self.assertIn("三阶段闸门", page)
        self.assertIn("限档原因", page)
        self.assertIn("1 项用例未执行，验收范围未覆盖", page)
        # 首屏上限必须取台账摘要的值（此处与门禁 json 的「通过」不同，
        # 若渲染器另算一套就会出现两套上限——这正是要防的漂移）
        self.assertIn(">待核实<", page)
        # 门禁表里的「通过」是门禁自身判定，必须标明口径并指明以首屏为准
        self.assertIn("门禁自身结论上限", page)
        self.assertIn("以首屏为准", page)
        self.assertNotIn("首屏口径说明", page)

    def test_report_first_screen_declares_inferred_basis_without_summary(self):
        """未提供摘要时，首屏必须声明限制因素来自渲染器推断。"""
        ledger = self.write_csv("ledger.csv", [self.csv_row("OBS-a")])
        gate = self.write_json("gate.json", {"gate": {"verdict_ceiling": "待核实",
                                                       "missing_views": []}})
        output = self.folder / "report.html"
        result = self.run_cli("--output", str(output), "--title", "验收报告",
                               "--subject", "受控站",
                               "--dataset", f"桌面::{ledger}::{gate}")
        self.assertEqual(result.returncode, 0, result.stderr)
        page = output.read_text(encoding="utf-8")
        self.assertIn("首屏口径说明", page)
        self.assertIn("未提供台账摘要", page)


if __name__ == "__main__":
    unittest.main()
