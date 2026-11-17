#!/usr/bin/env python3
"""补齐"有声明、无落账通道"的维度。

审计得到的四处断点，每处都必须由真实入口驱动断言，不复制生产逻辑：

1. **7.9 免滚动可达性** —— 脚本 D 产出 `needScrollActions`，Python 侧零消费者。
   首屏外的主操作是**观察**而非缺陷（长表单/列表页正常需要滚动），只能是 pending；
   全在首屏内才给 pass 证据。判反方向就是刷假缺陷。
2. **7.19 可访问性** —— `capture_aria.js` 的 added/removed 无消费者，
   而 `aria-diff.md` 承诺"写入报告可访问性章节"。结构变化同样只是 pending。
   首次运行没有 diff 基线，必须 dataMissing 并写明原因，不能当成 pass。
3. **7.12 待确认问题记录** —— 全仓零代码引用：报告里有 pending 项时它仍显示
   notRun，等于"待确认问题"这一维度从未被记录。
4. **场景执行率** —— `scenario_coverage.py` 的 `--enforce` 是防覆盖率虚高的门禁，
   但标准管线从不生成该产物（只把它当可选输入消费），执行率门禁事实上不生效。
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))


def run_struct_checks(tmp: Path, **sources) -> dict:
    """按角色传源文件；返回产物 JSON。"""
    cmd = [sys.executable, str(SCRIPTS / "struct_checks.py")]
    names = {"alignment": "alignment.json", "order": "order.json",
             "aria_diff": "aria_diff.json", "aria_snapshot": "aria.json"}
    for key, filename in names.items():
        payload = sources.get(key)
        if payload is None:
            continue
        path = tmp / filename
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        cmd += ["--" + key.replace("_", "-"), str(path)]
    out = tmp / "findings.json"
    cmd += ["--output", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError("struct_checks rc=%s stderr=%s" % (r.returncode, r.stderr[-400:]))
    return json.loads(out.read_text(encoding="utf-8"))


def by_dim(payload, dim):
    return [f for f in (payload.get("findings") or []) if str(f.get("dimension")) == dim]


class NoScrollReachabilityTests(unittest.TestCase):
    """7.9 免滚动可达性。"""

    def test_action_below_fold_is_pending_not_confirmed(self):
        """首屏外主操作是观察项：长表单本来就该滚动，判 confirmed 会刷假缺陷。"""
        order = {"viewportH": 900, "viewportW": 1440,
                 "order": [], "dividers": [],
                 "needScrollActions": [{"sel": "button.save", "text": "保存",
                                        "top": 1200, "bottom": 1236,
                                        "reason": "below_fold"}]}
        with tempfile.TemporaryDirectory() as td:
            got = run_struct_checks(Path(td), order=order)
        rows = by_dim(got, "7.9")
        self.assertEqual(len(rows), 1, "needScrollActions 未落成 7.9 条目")
        self.assertEqual(rows[0]["status"], "pending")
        self.assertNotEqual(rows[0]["status"], "confirmed")
        self.assertIn("保存", json.dumps(rows[0], ensure_ascii=False))

    def test_no_action_needing_scroll_yields_pass_evidence(self):
        """空列表 = 脚本确实跑过且有候选主操作、且都在首屏内，不能退化成 notRun。"""
        order = {"viewportH": 900, "viewportW": 1440,
                 "order": [{"container": "div.form", "kids": []}],
                 "dividers": [], "needScrollActions": [], "actionCandidates": 3}
        with tempfile.TemporaryDirectory() as td:
            got = run_struct_checks(Path(td), order=order)
        rows = by_dim(got, "7.9")
        self.assertEqual(len(rows), 1, "无命中时也必须留一条 pass 证据")
        self.assertEqual(rows[0]["status"], "pass")

    def test_zero_candidates_is_not_pass(self):
        """页面没有可识别的主操作按钮时，空列表不代表"全部首屏可达"。"""
        order = {"viewportH": 900, "viewportW": 1440,
                 "order": [], "dividers": [], "needScrollActions": [], "actionCandidates": 0}
        with tempfile.TemporaryDirectory() as td:
            got = run_struct_checks(Path(td), order=order)
        rows = by_dim(got, "7.9")
        self.assertEqual(len(rows), 1)
        self.assertNotEqual(rows[0]["status"], "pass",
                            "没有候选主操作却判 pass，等于把「无判定对象」当成「检查通过」")
        self.assertTrue(str(rows[0].get("reason") or "").strip())

    def test_missing_candidate_count_is_not_pass(self):
        """老产物没有候选计数：无法区分"无主操作"与"全部可达"，只能如实降级。"""
        order = {"viewportH": 900, "order": [], "dividers": [], "needScrollActions": []}
        with tempfile.TemporaryDirectory() as td:
            got = run_struct_checks(Path(td), order=order)
        rows = by_dim(got, "7.9")
        self.assertEqual(len(rows), 1)
        self.assertNotEqual(rows[0]["status"], "pass")
        self.assertIn("actionCandidates", str(rows[0].get("reason") or ""))

    def test_malformed_need_scroll_is_not_pass(self):
        """字段形状异常不能被 _dict_rows 吃成空列表后判 pass。"""
        for bad in ("x", 0, {}, None):
            order = {"viewportH": 900, "needScrollActions": bad, "actionCandidates": 2}
            with tempfile.TemporaryDirectory() as td:
                got = run_struct_checks(Path(td), order=order)
            rows = by_dim(got, "7.9")
            self.assertEqual(len(rows), 1, bad)
            self.assertNotEqual(rows[0]["status"], "pass", bad)

    def test_order_and_dividers_do_not_fake_pass(self):
        """7.6/7.7 没有设计侧对照数据，不得凭页面数据给出 pass。"""
        order = {"order": [{"container": "div.list", "kids": [{"domIndex": 0, "text": "A"}]}],
                 "dividers": [{"kind": "hr", "sel": "hr", "y": 100, "w": 300}],
                 "needScrollActions": []}
        with tempfile.TemporaryDirectory() as td:
            got = run_struct_checks(Path(td), order=order)
        self.assertEqual(by_dim(got, "7.6"), [], "7.6 无设计侧顺序对照，不能凭页面数据给结论")
        self.assertEqual(by_dim(got, "7.7"), [], "7.7 无设计侧分隔线对照，不能凭页面数据给结论")


class AriaAccessibilityTests(unittest.TestCase):
    """7.19 可访问性结构 diff。"""

    def test_structure_change_is_pending(self):
        diff = {"aCount": 10, "bCount": 11,
                "added": [{"role": "button", "name": "关闭"}], "removed": [],
                "summary": {"added": 1, "removed": 0}}
        with tempfile.TemporaryDirectory() as td:
            got = run_struct_checks(Path(td), aria_diff=diff)
        rows = by_dim(got, "7.19")
        self.assertEqual(len(rows), 1, "aria 结构变化未落成 7.19 条目")
        self.assertEqual(rows[0]["status"], "pending")
        self.assertIn("关闭", json.dumps(rows[0], ensure_ascii=False))

    def test_no_change_yields_pass(self):
        diff = {"aCount": 10, "bCount": 10, "added": [], "removed": [],
                "summary": {"added": 0, "removed": 0}}
        with tempfile.TemporaryDirectory() as td:
            got = run_struct_checks(Path(td), aria_diff=diff)
        rows = by_dim(got, "7.19")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "pass")

    def test_empty_snapshots_are_not_pass(self):
        """两次都快照 0 个节点时，空 diff 只能说明抓取失败，不能证明结构未变。"""
        diff = {"aCount": 0, "bCount": 0, "added": [], "removed": [],
                "summary": {"added": 0, "removed": 0}}
        with tempfile.TemporaryDirectory() as td:
            got = run_struct_checks(Path(td), aria_diff=diff)
        rows = by_dim(got, "7.19")
        self.assertEqual(len(rows), 1)
        self.assertNotEqual(rows[0]["status"], "pass")
        self.assertTrue(str(rows[0].get("reason") or "").strip())

    def test_malformed_aria_lists_are_not_pass(self):
        for bad in ("x", 0, {}, None):
            diff = {"aCount": 10, "bCount": 10, "added": bad, "removed": []}
            with tempfile.TemporaryDirectory() as td:
                got = run_struct_checks(Path(td), aria_diff=diff)
            rows = by_dim(got, "7.19")
            self.assertEqual(len(rows), 1, bad)
            self.assertNotEqual(rows[0]["status"], "pass", bad)

    def test_empty_aria_object_is_not_silent(self):
        """给了 --aria-diff 却是空对象：不能一条不产、让 7.19 悄悄落回 notRun。

        断言失败原因必须指向「缺字段」而不是「节点数为 0」——两者都是 dataMissing，
        但排查方向完全不同：前者是产物形状损坏，后者是抓取没拿到节点。
        """
        with tempfile.TemporaryDirectory() as td:
            got = run_struct_checks(Path(td), aria_diff={})
        rows = by_dim(got, "7.19")
        self.assertEqual(len(rows), 1, "空 diff 对象被静默丢弃，7.19 无任何判定")
        self.assertEqual(rows[0]["status"], "dataMissing")
        self.assertIn("缺少 added/removed", str(rows[0].get("reason") or ""))

    def test_snapshot_without_baseline_is_datamissing(self):
        """首次运行只有快照：没有对比基线，不能判 pass。"""
        snapshot = {"nodes": [{"role": "button", "name": "保存"}]}
        with tempfile.TemporaryDirectory() as td:
            got = run_struct_checks(Path(td), aria_snapshot=snapshot)
        rows = by_dim(got, "7.19")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "dataMissing")
        self.assertTrue(str(rows[0].get("reason") or "").strip(),
                        "dataMissing 必须写明原因，否则门禁会拒")


class MultiSourceTests(unittest.TestCase):
    """多源合并与输入约束。"""

    def test_sources_merge_without_losing_any(self):
        with tempfile.TemporaryDirectory() as td:
            got = run_struct_checks(
                Path(td),
                alignment={"alignments": [{"kind": "required-mark", "sel": "s",
                                           "anchor": "姓名", "deltaCenterY": 5, "pass": False}]},
                order={"needScrollActions": [{"sel": "b", "text": "提交", "reason": "below_fold"}]},
                aria_diff={"added": [{"role": "link", "name": "帮助"}], "removed": []})
        dims = {f["dimension"] for f in got["findings"]}
        self.assertEqual(dims, {"7.13", "7.9", "7.19"})

    def test_no_source_is_an_error(self):
        """一个源都不给却退出 0，等于静默产出空结果。"""
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "f.json"
            r = subprocess.run([sys.executable, str(SCRIPTS / "struct_checks.py"),
                                "--output", str(out)], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(out.exists(), "没有输入就不应产出空文件冒充已执行")


class PendingDimensionTests(unittest.TestCase):
    """7.12 待确认问题记录：报告有 pending 项时必须体现。"""

    def _assemble(self, report: dict, extra=None) -> dict:
        with tempfile.TemporaryDirectory() as td:
            rp = Path(td) / "report.json"
            rp.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
            cmd = [sys.executable, str(SCRIPTS / "assemble_report.py"),
                   "--report", str(rp), "--write"]
            for name, payload in (extra or {}).items():
                fp = Path(td) / (name + ".json")
                fp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                cmd += ["--findings", str(fp)]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                raise AssertionError("assemble_report rc=%s %s" % (r.returncode, r.stderr[-400:]))
            return json.loads(rp.read_text(encoding="utf-8"))

    def test_pending_defect_turns_712_pending(self):
        report = {"url": "https://p.test/",
                  "defects": [{"dimension": "7.15", "status": "pending",
                               "symptom": "页面存在形似测试数据的文案",
                               "element": {"selector": "p.t"}}]}
        doc = self._assemble(report)
        entry = doc["meta"]["checks"]["7.12"]
        self.assertEqual(entry["status"], "pending",
                         "报告有待确认项，7.12 却仍是 %s" % entry["status"])
        self.assertIn("1", str(entry.get("reason") or ""))

    def test_no_pending_keeps_712_notrun_with_reason(self):
        report = {"url": "https://p.test/",
                  "defects": [{"dimension": "7.1", "status": "confirmed",
                               "symptom": "颜色不符", "element": {"selector": ".btn"}}]}
        doc = self._assemble(report)
        entry = doc["meta"]["checks"]["7.12"]
        self.assertEqual(entry["status"], "notRun")
        self.assertIn("无待确认", str(entry.get("reason") or ""))

    def test_pending_checks_container_turns_712_pending(self):
        """pendingChecks 是报告正式的待确认容器（Excel 待确认页同源），不能被漏掉。"""
        report = {"url": "https://p.test/",
                  "pendingChecks": [
                      {"name": "修改名称图标", "category": "图标形状(异构需视觉核对)",
                       "verdict": "需视觉核对", "reason": "SVG 与图标字体异构",
                       "element": {"selector": "i.comIcon"}}]}
        doc = self._assemble(report)
        entry = doc["meta"]["checks"]["7.12"]
        self.assertEqual(entry["status"], "pending",
                         "报告有 pendingChecks 项，7.12 却仍是 %s" % entry["status"])
        self.assertIn("pendingChecks", str(entry.get("reason") or ""))

    def test_datamissing_counts_as_pending(self):
        """dataMissing 与 pending 在报告的待确认表里同属"待跟进"，口径必须一致。"""
        report = {"url": "https://p.test/",
                  "defects": [{"dimension": "7.4", "status": "dataMissing",
                               "symptom": "视口不可比", "element": {"selector": "div"}}]}
        doc = self._assemble(report)
        entry = doc["meta"]["checks"]["7.12"]
        self.assertEqual(entry["status"], "pending")
        # 但原因必须区分「数据缺失」与「待确认」，否则读者会以为问题都已有结论。
        self.assertIn("数据缺失", str(entry.get("reason") or ""))

    def test_reason_separates_pending_from_datamissing(self):
        report = {"url": "https://p.test/",
                  "defects": [{"dimension": "7.15", "status": "pending",
                               "symptom": "待确认", "element": {"selector": "p"}},
                              {"dimension": "7.4", "status": "dataMissing",
                               "symptom": "不可比", "element": {"selector": "div"}}]}
        doc = self._assemble(report)
        reason = str(doc["meta"]["checks"]["7.12"].get("reason") or "")
        self.assertIn("待确认项", reason)
        self.assertIn("数据缺失", reason)
        self.assertIn("7.15", reason)
        self.assertIn("7.4", reason)

    def test_manual_712_is_preserved(self):
        report = {"url": "https://p.test/",
                  "defects": [{"dimension": "7.15", "status": "pending", "symptom": "x",
                               "element": {"selector": "p"}}],
                  "meta": {"checks": {"7.12": {"status": "pass", "reason": "人工复核完毕"}}}}
        doc = self._assemble(report)
        self.assertEqual(doc["meta"]["checks"]["7.12"]["status"], "pass")

    def test_blocked_scenarios_are_named(self):
        """只说「有待确认项」不够：要能指出卡住了哪几条场景。"""
        report = {"url": "https://p.test/",
                  "defects": [{"dimension": "7.13", "status": "pending", "symptom": "星号偏移",
                               "element": {"selector": "label"},
                               "scenario": "S-0007", "scenarios": ["S-0012"]}]}
        doc = self._assemble(report)
        entry = doc["meta"]["checks"]["7.12"]
        self.assertEqual(entry["blocked_scenarios"], ["S-0007", "S-0012"])

    def test_resolution_condition_is_actionable(self):
        report = {"url": "https://p.test/",
                  "defects": [{"dimension": "7.13", "status": "pending", "symptom": "x",
                               "element": {"selector": "label"}, "scenario": "S-0007"}]}
        cond = self._assemble(report)["meta"]["checks"]["7.12"]["resolution_condition"]
        self.assertIn("7.13", cond)
        self.assertNotIn("待确认", cond, "解除条件不能退回套话")

    def test_no_pending_has_no_blocked_scenarios(self):
        doc = self._assemble({"url": "https://p.test/", "checks": {}})
        entry = doc["meta"]["checks"]["7.12"]
        self.assertEqual(entry["status"], "notRun")
        self.assertNotIn("blocked_scenarios", entry, "无待确认项时不该凭空产出阻塞清单")

    def test_reported_notrun_total_matches_checks(self):
        """7.12 由 notRun 派生成 pending 后，stdout 的计数必须同步减。"""
        report = {"url": "https://p.test/",
                  "defects": [{"dimension": "7.15", "status": "pending", "symptom": "x",
                               "element": {"selector": "p"}}]}
        with tempfile.TemporaryDirectory() as td:
            rp = Path(td) / "report.json"
            rp.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
            r = subprocess.run([sys.executable, str(SCRIPTS / "assemble_report.py"),
                                "--report", str(rp), "--write"],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-400:])
            doc = json.loads(rp.read_text(encoding="utf-8"))
        stats = json.loads(r.stdout[r.stdout.index("{"):])
        actual = sum(1 for v in doc["meta"]["checks"].values()
                     if isinstance(v, dict) and v.get("status") == "notRun")
        self.assertEqual(stats["notRunTotal"], actual,
                         "统计 notRunTotal 与 checks 实际 notRun 数不一致")

    def test_712_is_recomputed_on_rerun(self):
        """「只重出报告」是官方用法：派生值必须每次重算，不能被上一轮的结果固化。"""
        with tempfile.TemporaryDirectory() as td:
            rp = Path(td) / "report.json"
            rp.write_text(json.dumps({
                "url": "https://p.test/",
                "defects": [{"dimension": "7.15", "status": "pending", "symptom": "x",
                             "element": {"selector": "p"}}]}, ensure_ascii=False), encoding="utf-8")
            cmd = [sys.executable, str(SCRIPTS / "assemble_report.py"),
                   "--report", str(rp), "--write"]
            self.assertEqual(subprocess.run(cmd, capture_output=True, text=True).returncode, 0)
            first = json.loads(rp.read_text(encoding="utf-8"))["meta"]["checks"]["7.12"]["status"]
            # 待确认项已定论，重跑时报告里已无 pending
            doc = json.loads(rp.read_text(encoding="utf-8"))
            doc["defects"] = []
            rp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(subprocess.run(cmd, capture_output=True, text=True).returncode, 0)
            second = json.loads(rp.read_text(encoding="utf-8"))["meta"]["checks"]["7.12"]["status"]
        self.assertEqual(first, "pending")
        self.assertEqual(second, "notRun",
                         "7.12 保留了上一轮的 %s，派生值被冻结" % second)


class EndToEndTests(unittest.TestCase):
    """产物必须真的进到 checks，而不是只有 struct_checks 自己认得。"""

    def test_new_sources_reach_report_checks(self):
        import assemble_report
        order = {"viewportH": 900, "viewportW": 1440,
                 "needScrollActions": [{"sel": "button.save", "text": "保存",
                                        "reason": "below_fold"}]}
        diff = {"aCount": 10, "bCount": 10, "added": [], "removed": []}
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            got = run_struct_checks(tmp, order=order, aria_diff=diff)
            out = tmp / "findings.json"
            out.write_text(json.dumps(got, ensure_ascii=False), encoding="utf-8")
            evidence = {}
            assemble_report.collect_findings(out, evidence)
        self.assertEqual([r["status"] for r in evidence["7.9"]], ["pending"])
        self.assertEqual([r["status"] for r in evidence["7.19"]], ["pass"])

    def test_scenario_coverage_accepts_empty_plan(self):
        """管线会无条件跑它；空计划不能崩，否则标准流程直接 degraded。"""
        with tempfile.TemporaryDirectory() as td:
            plan = Path(td) / "scenarios.json"
            plan.write_text(json.dumps({"version": "2.0", "scenarios": []}), encoding="utf-8")
            report = Path(td) / "report.json"
            report.write_text(json.dumps({"url": "https://p.test/", "defects": []}), encoding="utf-8")
            out = Path(td) / "scenario_coverage.json"
            r = subprocess.run([sys.executable, str(SCRIPTS / "scenario_coverage.py"),
                                "--plan", str(plan), "--from-report", str(report),
                                "--out", str(out)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-500:])
            doc = json.loads(out.read_text(encoding="utf-8"))
        self.assertIn("executed", doc)
        self.assertIn("notExecuted", doc)


class PipelineWiringTests(unittest.TestCase):
    """管线必须自己生成这些产物，否则标准流程里永远没有执行率与结构证据。"""

    def _plan(self, files: dict) -> str:
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td) / "work"
            (wd / "extract").mkdir(parents=True)
            for name, payload in files.items():
                path = wd / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            report = Path(td) / "report.json"
            report.write_text(json.dumps({"url": "https://p.test/", "checks": {}}), encoding="utf-8")
            r = subprocess.run([sys.executable, str(SCRIPTS / "run_parity_pipeline.py"),
                                "--workdir", str(wd), "--report", str(report), "--dry-run"],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-600:])
            return r.stdout

    def test_order_and_aria_feed_struct_checks(self):
        out = self._plan({
            "extract/alignment.json": {"alignments": []},
            "order.json": {"needScrollActions": []},
            "aria_diff.json": {"added": [], "removed": []}})
        line = [l for l in out.splitlines() if "struct_checks" in l]
        self.assertEqual(len(line), 1, "struct_checks 未被计划执行")
        for flag in ("--order", "--aria-diff"):
            self.assertIn(flag, line[0], "管线未把 %s 喂给 struct_checks" % flag)

    def test_scenario_coverage_is_generated_not_just_consumed(self):
        out = self._plan({"scenarios.json": {"version": "2.0", "scenarios": []}})
        self.assertIn("scenario_coverage", out,
                      "场景执行率产物无人生成：执行率门禁在标准管线里不生效")
        self.assertIn("assemble_report", out)


if __name__ == "__main__":
    unittest.main()
