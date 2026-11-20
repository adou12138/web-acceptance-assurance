"""门禁链接线闭环：措辞—行为一致（M1）、跨脚本契约（M2）、缺失即留痕与收尾刷新（M3）。

三件事必须同时成立，缺一条整套门禁链就是纸面的：

1. **M1 措辞与行为一致**：CLI 帮助文本承诺的处置（拒绝建账 / 压结论上限 / 仅披露）
   必须与实际行为一致。帮助文本写"拒绝建账"而实现是"压上限"，会让使用方
   按错误的预期编排流程——本文件逐条锁定。
2. **M2 契约同源**：plan 交叉核对是三态（not_provided / empty / checked），
   生产端产出，两个消费者（phase_gate / execution_ledger）按同一口径消费。
   空 plan 不得自证覆盖，弱化调用必须被两个消费者同时识别。
3. **M3 缺失即留痕 + 收尾刷新**：缺失逐项披露；同轮补齐消除临时缺口；
   已成立的更严结论不因刷新而放松；**同一职责不得被计两次缺失**
   （走三阶段流程时，跳间校验与攻击覆盖由 phase_gate 承担，台账不得重复索要）。
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
LEDGER = SCRIPTS / "execution_ledger.py"
PHASE_GATE = SCRIPTS / "phase_gate.py"
ATTACK_COVERAGE = SCRIPTS / "attack_coverage.py"
# attack_coverage 从同目录 import adversarial_data（方法库单一事实源），
# 独立加载时须把 scripts/ 放进 sys.path，否则生产者根本跑不起来。
sys.path.insert(0, str(SCRIPTS))


def load(name, path=None):
    spec = importlib.util.spec_from_file_location(name, path or SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LedgerBase(unittest.TestCase):
    def setUp(self):
        self.ledger = load("execution_ledger", LEDGER)
        self.pg = load("phase_gate", PHASE_GATE)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.design = json.loads((ROOT / "examples" / "cases-example.json").read_text(encoding="utf-8"))
        self.context = {"target_url": "https://example.invalid/test", "environment": "test",
                        "application_version": "b1", "feature_flags": {}, "role_scope": ["editor"],
                        "dependency_mode": "sandbox"}
        self.adversarial = {"adversarial_enabled": True, "verdict_ceiling": "通过", "reasons": []}
        self.evidence = self.folder / "observations.txt"
        self.evidence.write_text("教学夹具，非真实远端证据", encoding="utf-8")

    def init_ledger(self, name, **kwargs):
        workspace = self.folder / name
        baseline = self.ledger.initialize(workspace, self.design, self.context, "R1",
                                          [], **kwargs)
        return workspace, baseline

    def run_all_cases(self, workspace):
        for index, case in enumerate(self.design["testcases"]):
            attempt = f"ATT-{index}"
            self.ledger.begin(workspace, self.design, self.context, attempt, case["id"], "动作")
            checks = [{"check": item["check"], "actual": "观察值", "matched": True}
                      for item in case["assertions"]]
            self.ledger.finish(workspace, self.design, self.context, attempt, "PASS",
                               "观察结果", [str(self.evidence)], checks, "none", [], "无真实远端数据")


class M1WordingMatchesBehaviour(LedgerBase):
    """M1：帮助文本承诺的处置必须与实际行为一致。"""

    def _help(self, *args):
        out = subprocess.run([sys.executable, str(LEDGER), *args, "--help"],
                             capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout

    def _help_option(self, text, option):
        """取出某个选项的帮助正文。

        argparse 会把长帮助文本折成多行，按行匹配会漏掉后半段——
        这里从选项所在行起，取到下一个顶格选项行止。
        """
        lines = text.splitlines()
        starts = [i for i, line in enumerate(lines) if line.lstrip().startswith(option)]
        self.assertTrue(starts, f"帮助里找不到选项 {option}")
        start = starts[0]
        tail = []
        for line in lines[start + 1:]:
            if line and not line[0].isspace():
                break
            tail.append(line.strip())
        return " ".join([lines[start].strip()] + tail)

    def test_batches_help_says_reject_not_reject_when_absent(self):
        """--batches 的帮助必须区分「提供但未闭环」与「未提供」两种处置。"""
        row = self._help_option(self._help("init"), "--batches")
        self.assertIn("拒绝建台账", row, "提供且未闭环时必须拒绝建台账")
        self.assertIn("不提供则留痕并压上限", row, "未提供时是留痕并压上限，不是拒绝")

    def test_adversarial_gate_help_does_not_claim_reject(self):
        """--adversarial-gate 未提供时是压上限，不是拒绝建台账——文案不得说反。"""
        row = self._help_option(self._help("init"), "--adversarial-gate")
        self.assertNotIn("拒绝建台账", row)
        self.assertIn("压待核实", row)
        self.assertIn("不拒绝建账", row)

    def test_phase_state_help_does_not_claim_reject(self):
        """--phase-state 未提供时是留痕并压上限，不得写成拒绝建台账。"""
        row = self._help_option(self._help("init"), "--phase-state")
        self.assertNotIn("拒绝建台账", row)
        self.assertIn("留痕并压上限", row)

    def test_help_promises_match_reality(self):
        """帮助承诺「不提供则留痕并压上限」，实际必须真的留痕并压上限。"""
        workspace, baseline = self.init_ledger("no-gate-inputs")
        # 未提供任何门禁输入 → 逐项留痕
        self.assertTrue(baseline["missing_inputs"],
                        "未提供门禁输入时必须在 baseline 顶层留痕")
        self.assertIn("phase_state", baseline["missing_inputs"])
        # 且收尾按实际口径压上限
        self.run_all_cases(workspace)
        summary = self.ledger.summarize(workspace, self.design, self.context, [])
        self.assertEqual(summary["verdict_ceiling"], "待核实")
        self.assertTrue(summary["missing_input_reasons"],
                        "缺口必须逐项给出可读原因")

    def test_adversarial_gate_provided_but_not_closed_still_rejects(self):
        """对抗门禁结论「不通过」时结论上限压不通过，不因缺其他输入而变成待核实。"""
        gate = {"adversarial_enabled": True, "verdict_ceiling": "不通过", "reasons": ["有未闭环 blocker"]}
        workspace, baseline = self.init_ledger("adversarial-fail", adversarial_gate=gate)
        self.assertEqual(baseline["gate_precondition"]["adversarial_ceiling"], "不通过")
        self.run_all_cases(workspace)
        summary = self.ledger.summarize(workspace, self.design, self.context, [])
        self.assertEqual(summary["verdict_ceiling"], "不通过",
                         "已成立的不通过不得被缺项产生的待核实覆盖")


class M2SharedContract(LedgerBase):
    """M2：plan 交叉核对三态，生产端产出，两个消费者同口径消费。"""

    def coverage(self, ceiling="通过", gaps=None, density=0.4, plan_check_state=None):
        payload = {"verdict_ceiling": ceiling, "gaps": gaps or [], "attack_density": density}
        if plan_check_state is not None:
            payload["plan_check_state"] = plan_check_state
        return payload

    def test_producer_emits_three_states(self):
        ac = load("attack_coverage")
        self.assertEqual(ac._plan_check_state(None), "not_provided")
        self.assertEqual(ac._plan_check_state([]), "empty")
        self.assertEqual(ac._plan_check_state(["SURF-01"]), "checked")

    def test_producer_output_carries_contract_fields(self):
        """真实生产者输出必须带 plan_check_state / atk_checked，供消费者消费。"""
        ac = load("attack_coverage")
        result = ac.analyse([], plan_surfaces=["SURF-01"])
        self.assertEqual(result["plan_check_state"], "checked")
        self.assertIn("atk_checked", result)
        self.assertFalse(result["atk_checked"])

    def test_ledger_consumes_weak_call(self):
        """弱化调用（plan 未交叉核对）时台账必须压上限。"""
        for state in ("not_provided", "empty"):
            with self.subTest(state=state):
                workspace, baseline = self.init_ledger(
                    "weak-" + state, attack_coverage=self.coverage(plan_check_state=state))
                gate = baseline["gate_precondition"]
                self.assertEqual(gate["attack_coverage_ceiling"], "待核实")
                self.assertEqual(gate["attack_plan_check_state"], state)
                self.run_all_cases(workspace)
                summary = self.ledger.summarize(workspace, self.design, self.context, [])
                self.assertEqual(summary["verdict_ceiling"], "待核实")

    def test_phase_gate_consumes_weak_call(self):
        """同一弱化调用在 phase_gate 侧必须同样被识别——两个消费者同一口径。"""
        for state in ("not_provided", "empty"):
            with self.subTest(state=state):
                result = self.pg._attack_check(self.coverage(plan_check_state=state))
                self.assertEqual(result["ceiling"], "待核实")
                self.assertEqual(result["plan_check_state"], state)
                self.assertTrue(any("交叉核对" in reason for reason in result["reasons"]),
                                result["reasons"])

    def test_checked_state_not_capped(self):
        """完成交叉核对的同轮产物不得被误压。"""
        result = self.pg._attack_check(self.coverage(plan_check_state="checked"))
        self.assertEqual(result["ceiling"], "通过")
        workspace, baseline = self.init_ledger(
            "checked", attack_coverage=self.coverage(plan_check_state="checked"))
        self.assertEqual(baseline["gate_precondition"]["attack_coverage_ceiling"], "通过")

    def test_absent_state_is_backward_compatible(self):
        """旧产物没有该字段时不压上限——字段缺失不等于弱化调用。"""
        result = self.pg._attack_check(self.coverage())
        self.assertEqual(result["ceiling"], "通过")
        workspace, baseline = self.init_ledger("legacy", attack_coverage=self.coverage())
        self.assertEqual(baseline["gate_precondition"]["attack_coverage_ceiling"], "通过")

    def test_producer_consumer_chain(self):
        """真实生产者输出 → 两个消费者串链，结论一致。"""
        ac = load("attack_coverage")
        result = ac.analyse([], plan_surfaces=None)
        payload = {k: result[k] for k in ("verdict_ceiling", "gaps", "attack_density",
                                          "plan_check_state", "atk_checked")}
        pg_result = self.pg._attack_check(payload)
        self.assertEqual(pg_result["plan_check_state"], "not_provided")
        self.assertEqual(pg_result["ceiling"], "待核实")
        workspace, baseline = self.init_ledger("chain", attack_coverage=payload)
        self.assertEqual(baseline["gate_precondition"]["attack_coverage_ceiling"], "待核实")


class M3MissingTraceAndRefresh(LedgerBase):
    """M3：缺失即留痕、收尾刷新、同一职责不重复计。"""

    def test_missing_recorded_per_item(self):
        """缺口逐项记录，不用笼统的「有缺失」。"""
        _, baseline = self.init_ledger("missing-all")
        self.assertEqual(sorted(baseline["missing_inputs"]),
                         ["attack_coverage", "batches", "gate_report", "phase_state"])

    def _three_phase_state(self):
        """走完整跨会话流程的三阶段状态（plan → execute → adjudicate 全 done）。

        必须真实走 submit/checkpoint/resume：阶段放行与交接是机械校验的，
        手工拼状态会绕过校验，测出来的「三阶段已完成」不是真的。
        """
        handoff = self.folder / "handoff"
        handoff.mkdir(exist_ok=True)
        artifacts = []
        for name in ("review-result.json", "cases-demo.json", "adversarial-plan.json"):
            path = self.folder / name
            path.write_text("{}", encoding="utf-8")
            artifacts.append(str(path))
        state = self.pg.init_state(self.folder / "phase-state.json", "TASK-1", "SESS-1",
                                    self.pg.ROUTING["plan"]["model_class"],
                                    self.pg.ROUTING["plan"]["model_hint"])
        for phase in self.pg.PHASES:
            session = f"SESS-{self.pg.PHASES.index(phase) + 1}"
            if phase != "plan":
                prior = self.pg.PHASES[self.pg.PHASES.index(phase) - 1]
                token = state["phases"][prior]["handoff"]["token"]
                state = self.pg.resume(state, phase, session,
                                        f"续跑 {phase}，确认码 {token}",
                                        self.pg.ROUTING[phase]["model_class"],
                                        self.pg.ROUTING[phase]["model_hint"])
            self.pg.start(state, phase, session)
            coverage = ({"verdict_ceiling": "通过", "gaps": [], "attack_density": 0.4,
                         "plan_check_state": "checked"}
                        if phase == "execute" else None)
            exploration = ({
                "verdict_ceiling": "通过", "ready_pending": 0, "missing_views": [],
                "failed_items": [], "traversal_recorded": True,
                "mandatory_attributes": {"security": "covered"},
                "message": "ok"} if phase == "execute" else None)
            self.pg.complete(state, phase, session=session, attack_coverage=coverage,
                             exploration=exploration)
            nxt = self.pg.NEXT_PHASE[phase]
            if nxt:
                self.pg.submit(state, nxt, session=session,
                               artifacts=artifacts if nxt == "execute" else None,
                               model_class=self.pg.ROUTING[nxt]["model_class"],
                               model_name=self.pg.ROUTING[nxt]["model_hint"])
            self.pg.checkpoint(state, phase, handoff, session=session)
        return state

    def test_same_duty_not_counted_twice(self):
        """走三阶段流程时，跳间校验与攻击覆盖由 phase_gate 承担，台账不得重复索要。

        这是本文件最关键的一条：重复索要会把合法三阶段流程误压成待核实，
        让「走了完整流程」比「只做一半」更差。
        """
        state = self._three_phase_state()
        workspace, baseline = self.init_ledger(
            "three-phase", adversarial_gate=self.adversarial, phase_state=state)
        self.assertEqual(baseline["missing_inputs"], [],
                         "三阶段流程已承担跳间校验与攻击覆盖，台账不得再计同一职责的缺失")
        self.run_all_cases(workspace)
        summary = self.ledger.summarize(workspace, self.design, self.context, [])
        self.assertEqual(summary["verdict_ceiling"], "通过")

    def test_legacy_workflow_missing_still_caps(self):
        """旧工作流缺证据仍按实际口径限档，且原因逐项清晰。"""
        workspace, _ = self.init_ledger("legacy-missing")
        self.run_all_cases(workspace)
        summary = self.ledger.summarize(workspace, self.design, self.context, [])
        self.assertEqual(summary["verdict_ceiling"], "待核实")
        self.assertEqual(len(summary["missing_input_reasons"]),
                         len(summary["missing_inputs"]))

    def test_final_refresh_clears_temporary_gap(self):
        """同轮补齐消除临时缺口：收尾补交攻击覆盖结论后，缺口消失。"""
        workspace, baseline = self.init_ledger("refresh")
        self.assertIn("attack_coverage", baseline["missing_inputs"])
        self.run_all_cases(workspace)
        coverage = {"verdict_ceiling": "通过", "gaps": [], "attack_density": 0.4,
                    "plan_check_state": "checked"}
        summary = self.ledger.summarize(workspace, self.design, self.context, [],
                                        attack_coverage=coverage)
        self.assertNotIn("attack_coverage", summary["missing_inputs"],
                         "收尾补齐的证据必须消除对应临时缺口")
        self.assertEqual(summary["verdict_ceiling"], "待核实",
                         "其余缺口仍在，结论不得因单项补齐而放宽")

    def test_refresh_does_not_relax_stricter_conclusion(self):
        """收尾刷新不得放松已成立的更严结论。"""
        gate = {"adversarial_enabled": True, "verdict_ceiling": "不通过", "reasons": ["未闭环 blocker"]}
        workspace, _ = self.init_ledger("relax", adversarial_gate=gate)
        self.run_all_cases(workspace)
        coverage = {"verdict_ceiling": "通过", "gaps": [], "attack_density": 0.4,
                    "plan_check_state": "checked"}
        summary = self.ledger.summarize(workspace, self.design, self.context, [],
                                        attack_coverage=coverage)
        self.assertEqual(summary["verdict_ceiling"], "不通过")

    def test_final_precondition_refreshes_category(self):
        """收尾复核用同轮最终产物刷新建账时的过期快照。"""
        workspace, baseline = self.init_ledger("stale")
        stale_ceiling = baseline["gate_precondition"]["adversarial_ceiling"]
        self.assertEqual(stale_ceiling, "待核实")
        gate = {"adversarial_enabled": True, "verdict_ceiling": "通过", "reasons": []}
        final = self.ledger._final_precondition(baseline, adversarial_gate=gate,
                                                read_only=False, case_count=2)
        self.assertEqual(final["adversarial_ceiling"], "通过")

    def test_gate_precondition_shape_unchanged(self):
        """缺口是「另记」，不得混进 gate_precondition（门禁判定结果）。"""
        _, baseline = self.init_ledger("shape")
        self.assertNotIn("missing_inputs", baseline["gate_precondition"])
        self.assertIn("missing_inputs", baseline)


if __name__ == "__main__":
    unittest.main()
