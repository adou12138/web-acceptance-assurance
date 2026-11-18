import copy
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "exploration_state.py"


class ExplorationStateTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), "缺少功能：轻量探索清单与下一步候选工具")
        spec = importlib.util.spec_from_file_location("exploration_state", SCRIPT)
        self.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tool)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.evidence = self.folder / "观察.txt"
        self.evidence.write_text("测试夹具证据，不代表真实业务验收", encoding="utf-8")
        self.data = {
            "schema_version": "1.0",
            "context": {"target_url": "https://example.invalid/app", "application_version": "build-1", "environment": "test"},
            "items": [self.item("EXP-1")],
        }

    def item(self, ident, **changes):
        row = {
            "id": ident, "requirement_ids": ["REQ-1"], "page_url": "https://example.invalid/app/form",
            "role": "编辑者", "viewport": "1440x900", "state": "表单打开", "action": "核对保存按钮",
            "risk": "medium", "status": "NOT_RUN", "observation": "", "evidence_paths": [],
            "blocked_reason": "", "side_effect_pending": False, "attributes": ["functional"],
        }
        row.update(changes)
        return row

    def report(self):
        return self.tool.summarize(self.data)

    def cli(self, input_path, output_path):
        return subprocess.run([sys.executable, str(SCRIPT), str(input_path), "--output", str(output_path)], capture_output=True, text=True, encoding="utf-8")

    def test_empty_plan_has_no_synthetic_success(self):
        self.data["items"] = []
        report = self.report()
        self.assertEqual(report["planned"], 0)
        self.assertEqual(report["counts"]["PASS"], 0)
        self.assertEqual(report["ready"], [])
        self.assertIn("不判定业务", report["scope"])
        self.assertIn("总体门禁", report["scope"])

    def test_ready_is_not_run_risk_then_id(self):
        self.data["items"] = [self.item("EXP-Z", risk="low"), self.item("EXP-B", risk="high"), self.item("EXP-A", risk="high"), self.item("EXP-M", risk="medium"), self.item("EXP-D", blocked_reason="等待数据")]
        self.assertEqual(self.report()["ready"], ["EXP-A", "EXP-B", "EXP-M", "EXP-Z"])

    def test_side_effect_reconciliation_overrides_every_status(self):
        statuses = ["NOT_RUN", "PASS", "FAIL", "BLOCKED", "SKIPPED"]
        self.data["items"] = [self.item("EXP-" + status, status=status, observation="夹具观察", evidence_paths=[str(self.evidence)], blocked_reason="等待副作用核对" if status in {"BLOCKED", "SKIPPED"} else "", side_effect_pending=True) for status in statuses]
        report = self.report()
        self.assertEqual(set(report["needs_reconcile"]), {"EXP-" + status for status in statuses})
        self.assertEqual(report["ready"], [])
        self.assertEqual(report["reproduction_candidates"], ["EXP-FAIL"])
        self.data["items"][0]["side_effect_pending"] = False
        self.assertEqual(self.report()["ready"], ["EXP-NOT_RUN"])

    def test_pass_fail_need_observation_and_existing_absolute_evidence(self):
        for status in ["PASS", "FAIL"]:
            for changes in [dict(observation=""), dict(observation="  "), dict(evidence_paths=[]), dict(evidence_paths=["relative.txt"]), dict(evidence_paths=[str(self.folder / "missing.txt")]), dict(evidence_paths=[str(self.folder)])]:
                with self.subTest(status=status, changes=changes):
                    self.data["items"] = [self.item("EXP-1", status=status, observation="已观察", evidence_paths=[str(self.evidence)])]
                    self.data["items"][0].update(changes)
                    with self.assertRaises(ValueError):
                        self.report()

    def test_evidence_hash_and_fail_candidate_are_reported_without_mutation(self):
        self.data["items"] = [self.item("EXP-1", status="FAIL", observation="保存后出现错误", evidence_paths=[str(self.evidence)], case_id="TC-1")]
        before = copy.deepcopy(self.data)
        report = self.report()
        self.assertEqual(report["ready"], [])
        self.assertEqual(report["reproduction_candidates"], ["EXP-1"])
        self.assertEqual(report["items"][0]["evidence"], [{"path": str(self.evidence.resolve()), "sha256": hashlib.sha256(self.evidence.read_bytes()).hexdigest()}])
        self.assertEqual(self.data, before)
        self.evidence.unlink()
        with self.assertRaises(ValueError):
            self.report()

    def test_blocked_and_skipped_require_reason(self):
        for status in ["BLOCKED", "SKIPPED"]:
            self.data["items"] = [self.item("EXP-1", status=status)]
            with self.assertRaises(ValueError):
                self.report()
            self.data["items"][0]["blocked_reason"] = "环境未就绪"
            self.assertEqual(self.report()["ready"], [])

    def test_item_fields_reject_missing_wrong_types_and_invalid_values(self):
        mutations = {"id": ["", "bad id", None], "requirement_ids": [[], "REQ-1", [""], ["REQ-1", "REQ-1"], [False], ["bad id"]], "page_url": [None], "role": ["", 1], "viewport": ["", True], "state": ["", {}], "action": ["", []], "risk": ["urgent", [], None], "status": ["DONE", {}, None], "observation": [None, 1], "evidence_paths": [None, "path", [False], [""], [str(self.evidence), str(self.evidence)]], "blocked_reason": [None, False], "side_effect_pending": ["false", 0, 1, None], "attributes": [[], "visual", ["unknown"], ["visual", "visual"], [[]]], "case_id": ["", "bad id", None]}
        original = copy.deepcopy(self.data["items"][0])
        for field, values in mutations.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    self.data["items"] = [dict(original, **{field: value})]
                    with self.assertRaises(ValueError):
                        self.report()
        for field in original:
            with self.subTest(missing=field):
                self.data["items"] = [copy.deepcopy(original)]
                del self.data["items"][0][field]
                with self.assertRaises(ValueError):
                    self.report()

    def test_root_context_and_items_validation(self):
        for data in [[], None, {}, dict(self.data, schema_version=1.0), dict(self.data, schema_version="2.0"), dict(self.data, items={}), dict(self.data, items=[None]), dict(self.data, context=None)]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.tool.summarize(data)
        for key in self.data["context"]:
            for value in [None, "", False]:
                data = copy.deepcopy(self.data)
                data["context"][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    self.tool.summarize(data)

    def test_unique_stable_ids_are_not_an_execution_oracle(self):
        self.data["items"] = [self.item("EXP-1"), self.item("EXP-1")]
        with self.assertRaises(ValueError):
            self.report()
        self.data["items"] = [self.item("EXP-1", case_id="TC-PASS")]
        report = self.report()
        self.assertEqual(report["counts"]["PASS"], 0)
        self.assertEqual(report["ready"], ["EXP-1"])

    def test_http_same_origin_normalizes_default_port_and_hostname_case(self):
        self.data["items"][0]["page_url"] = "https://EXAMPLE.INVALID:443/different?x=1#section"
        self.assertEqual(self.report()["ready"], ["EXP-1"])
        self.data["context"]["target_url"] = "http://localhost:8080/app"
        self.data["items"][0]["page_url"] = "http://localhost:8080/other"
        self.assertEqual(self.report()["ready"], ["EXP-1"])

    def test_out_of_scope_ambiguous_or_credential_urls_are_rejected(self):
        bad_urls = ["https://other.invalid/", "http://example.invalid/", "https://example.invalid:444/", "//example.invalid/", "file:///tmp/a", "javascript:alert(1)", "https://u:p@example.invalid/", "https://@example.invalid/", "https://example.invalid:bad/", "https://example.invalid:65536/", "https://example.invalid\\@evil.invalid/", "https://example.invalid/\npath", "https://example.invalid /", "https://%65xample.invalid/"]
        for url in bad_urls:
            self.data["items"][0]["page_url"] = url
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.report()
        for url in ["file:///tmp/a", "https://u@example.invalid/", "relative", "https://", "https://example.invalid/\t"]:
            self.data["context"]["target_url"] = url
            with self.subTest(target=url), self.assertRaises(ValueError):
                self.report()

    def test_multitag_views_deduplicate_global_counts(self):
        self.data["items"][0]["attributes"] = ["visual", "usability", "accessibility", "functional", "performance", "security", "reliability", "compatibility"]
        report = self.report()
        self.assertEqual(report["planned"], 1)
        self.assertEqual(report["counts"]["NOT_RUN"], 1)
        self.assertEqual(report["views"], {"界面样式": ["EXP-1"], "交互易用性": ["EXP-1"], "基础无障碍": ["EXP-1"], "功能": ["EXP-1"], "性能": ["EXP-1"]})

    def test_accessibility_is_an_independent_summary_and_csv_view(self):
        self.data["items"] = [self.item("EXP-A", attributes=["accessibility"]), self.item("EXP-U", attributes=["usability"])]
        output = self.folder / "独立视图.csv"
        report = self.tool.export_csv(self.data, output)
        self.assertEqual(report["views"].get("基础无障碍"), ["EXP-A"])
        self.assertEqual(report["views"]["交互易用性"], ["EXP-U"])
        self.assertEqual(report["planned"], 2)
        self.assertEqual(report["counts"]["NOT_RUN"], 2)
        with output.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual({(row["view"], row["id"]) for row in rows}, {("基础无障碍", "EXP-A"), ("交互易用性", "EXP-U")})

    def test_gate_blocks_pass_while_ready_pending_or_required_views_missing(self):
        self.data["items"] = [self.item("EXP-1", attributes=["visual"])]
        gate = self.report()["gate"]
        self.assertEqual(gate["ready_pending"], 1)
        self.assertEqual(gate["ready_pending_ids"], ["EXP-1"])
        self.assertEqual(gate["missing_views"], ["界面样式", "交互易用性", "基础无障碍", "功能", "性能"])
        self.assertEqual(gate["verdict_ceiling"], "待核实")
        self.assertIn("未执行", gate["message"])

    def test_gate_allows_pass_only_after_candidates_consumed_and_views_recorded(self):
        attributes = {"visual": ["visual"], "usability": ["usability"], "accessibility": ["accessibility"],
                      "functional": ["functional"], "performance": ["performance"]}
        items = [self.item("EXP-" + name, status="PASS", observation="实测观察", evidence_paths=[str(self.evidence)], attributes=attrs)
                 for name, attrs in attributes.items()]
        items.append(self.item("EXP-TRAV-1", status="PASS", observation="已清点页面控件", evidence_paths=[str(self.evidence)],
                               attributes=["functional", "visual", "usability"]))
        self.data["attribute_rulings"] = {
            "security": {"state": "not_applicable", "reason": "本次仅校验视图门禁，未涉及权限与会话"},
            "compatibility": {"state": "not_applicable", "reason": "未声明内核/设备范围"},
            "content": {"state": "not_applicable", "reason": "未涉及文案变更"},
        }
        self.data["items"] = items
        gate = self.report()["gate"]
        self.assertEqual(gate["ready_pending"], 0)
        self.assertEqual(gate["missing_views"], [])
        self.assertTrue(gate["traversal_recorded"])
        self.assertEqual(gate["verdict_ceiling"], "通过")

    def test_gate_never_allows_pass_ceiling_while_fail_items_exist(self):
        """有 FAIL 条目时不得给“通过”上限：全 FAIL 与混合 PASS/FAIL 两种形态都覆盖。"""
        all_views = ["visual", "usability", "accessibility", "functional", "performance"]
        self.data["items"] = [
            self.item("EXP-ALL-FAIL", status="FAIL", observation="观察到异常", evidence_paths=[str(self.evidence)], attributes=all_views),
            self.item("EXP-TRAV-1", status="FAIL", observation="控件遍历即异常", evidence_paths=[str(self.evidence)], attributes=["functional"]),
        ]
        gate = self.report()["gate"]
        self.assertEqual(gate["missing_views"], [])
        self.assertEqual(gate["failed_items"], ["EXP-ALL-FAIL", "EXP-TRAV-1"])
        self.assertNotEqual(gate["verdict_ceiling"], "通过")
        self.assertIn("FAIL", gate["message"])
        mixed = [self.item("EXP-" + name, status="PASS", observation="实测观察", evidence_paths=[str(self.evidence)], attributes=[name])
                 for name in all_views]
        mixed.append(self.item("EXP-TRAV-1", status="PASS", observation="已清点控件", evidence_paths=[str(self.evidence)], attributes=["functional"]))
        mixed.append(self.item("EXP-BAD-1", status="FAIL", observation="返回404", evidence_paths=[str(self.evidence)], attributes=["functional"]))
        self.data["items"] = mixed
        gate = self.report()["gate"]
        self.assertEqual(gate["missing_views"], [])
        self.assertTrue(gate["traversal_recorded"])
        self.assertEqual(gate["failed_items"], ["EXP-BAD-1"])
        self.assertEqual(gate["verdict_ceiling"], "待核实")

    def test_gate_tracks_skipped_view_with_reason_but_does_not_count_it_as_executed(self):
        self.data["items"] = [
            self.item("EXP-1", status="SKIPPED", blocked_reason="本需求无性能指标", attributes=["performance"]),
            self.item("EXP-2", status="PASS", observation="实测观察", evidence_paths=[str(self.evidence)], attributes=["visual", "usability", "accessibility", "functional"]),
            self.item("EXP-TRAV-1", status="SKIPPED", blocked_reason="纯接口批次无页面控件可遍历", attributes=["functional", "usability"]),
        ]
        gate = self.report()["gate"]
        self.assertEqual(gate["missing_views"], ["性能"])
        self.assertEqual(gate["skipped_views"]["性能"], ["EXP-1"])
        self.assertTrue(gate["traversal_recorded"])
        self.assertEqual(gate["verdict_ceiling"], "待核实")

    def test_gate_requires_traversal_entry_for_pass_ceiling(self):
        """E3：清单无控件遍历条目（EXP-TRAV-）时结论上限为待核实。"""
        attributes = {"visual": ["visual"], "usability": ["usability"], "accessibility": ["accessibility"],
                      "functional": ["functional"], "performance": ["performance"]}
        self.data["items"] = [self.item("EXP-" + name, status="PASS", observation="实测观察", evidence_paths=[str(self.evidence)], attributes=attrs)
                              for name, attrs in attributes.items()]
        gate = self.report()["gate"]
        self.assertFalse(gate["traversal_recorded"])
        self.assertEqual(gate["traversal_pending"], 0)
        self.assertEqual(gate["verdict_ceiling"], "待核实")
        self.assertIn("控件遍历", gate["message"])

    def test_not_run_traversal_alone_does_not_count_as_recorded(self):
        self.data["items"] = [self.item("EXP-TRAV-1", attributes=["functional", "visual", "usability"])]
        gate = self.report()["gate"]
        self.assertFalse(gate["traversal_recorded"])
        self.assertEqual(gate["traversal_pending"], 1)

    def test_gate_traversal_pending_counted_separately(self):
        """E3：遍历条目仍为 NOT_RUN 时计入 traversal_pending 并压低结论上限。"""
        attributes = {"visual": ["visual"], "usability": ["usability"], "accessibility": ["accessibility"],
                      "functional": ["functional"], "performance": ["performance"]}
        items = [self.item("EXP-" + name, status="PASS", observation="实测观察", evidence_paths=[str(self.evidence)], attributes=attrs)
                 for name, attrs in attributes.items()]
        items.append(self.item("EXP-TRAV-1", attributes=["functional", "visual", "usability"]))
        items.append(self.item("EXP-TRAV-2", status="PASS", observation="已清点控件",
                               evidence_paths=[str(self.evidence)], attributes=["functional", "visual", "usability"]))
        self.data["items"] = items
        gate = self.report()["gate"]
        self.assertTrue(gate["traversal_recorded"])
        self.assertEqual(gate["traversal_pending"], 1)
        self.assertEqual(gate["verdict_ceiling"], "待核实")

    def test_csv_bom_real_roundtrip_special_fields_and_hashes(self):
        action = '检查逗号,双引号"与换行\n第二行'
        observation = '观察到"错误",请核对\n不作为业务通过依据'
        self.data["items"] = [self.item("EXP-1", action=action, observation=observation, status="FAIL", evidence_paths=[str(self.evidence)], attributes=["visual", "usability", "accessibility", "functional", "performance"])]
        output = self.folder / "检查.csv"
        report = self.tool.export_csv(self.data, output)
        self.assertTrue(output.read_bytes().startswith(b"\xef\xbb\xbf"))
        with output.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 5)
        self.assertEqual({r["view"] for r in rows}, set(report["views"]))
        for row in rows:
            self.assertEqual(row["action"], action)
            self.assertEqual(row["observation"], observation)
            self.assertEqual(json.loads(row["attributes"]), self.data["items"][0]["attributes"])
            self.assertEqual(json.loads(row["evidence"])[0]["sha256"], hashlib.sha256(self.evidence.read_bytes()).hexdigest())
            self.assertEqual(row["target_url"], self.data["context"]["target_url"])
        self.assertEqual(report["planned"], 1)

    def test_csv_formula_like_cells_are_neutralized(self):
        for prefix in ["=", "+", "-", "@", "  =", "\t=", "\r=", "\n="]:
            with self.subTest(prefix=prefix):
                action = prefix + "1+1"
                self.data["items"][0]["action"] = action
                output = self.folder / ("formula-" + str(len(list(self.folder.glob("formula-*")))) + ".csv")
                self.tool.export_csv(self.data, output)
                with output.open(encoding="utf-8-sig", newline="") as stream:
                    row = list(csv.DictReader(stream))[0]
                self.assertEqual(row["action"], "'" + action)
                self.assertEqual(self.data["items"][0]["action"], action)

    def test_export_refuses_overwrite_missing_parent_and_non_csv(self):
        output = self.folder / "existing.csv"
        output.write_bytes(b"keep")
        for path in [output, self.folder / "missing" / "new.csv", self.folder / "bad.txt"]:
            with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                self.tool.export_csv(self.data, path)
        self.assertEqual(output.read_bytes(), b"keep")

    def test_cli_full_lifecycle_outputs_json_summary_and_preserves_input(self):
        source = self.folder / "探索.json"
        source.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8-sig")
        original = source.read_bytes()
        output = self.folder / "new.csv"
        result = self.cli(source, output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["ready"], ["EXP-1"])
        with output.open(encoding="utf-8-sig", newline="") as stream:
            self.assertEqual(list(csv.DictReader(stream))[0]["id"], "EXP-1")
        self.assertEqual(source.read_bytes(), original)
        csv_original = output.read_bytes()
        self.assertEqual(self.cli(source, output).returncode, 2)
        self.assertEqual(output.read_bytes(), csv_original)

    def test_cli_bad_input_has_exit_two_and_no_artifact(self):
        source = self.folder / "bad.json"
        output = self.folder / "new.csv"
        for text in ['{"schema_version":', json.dumps(dict(self.data, items=[dict(self.data["items"][0], side_effect_pending="false")])), '{"schema_version":"1.0","schema_version":"1.0","context":{},"items":[]}']:
            source.write_text(text, encoding="utf-8")
            result = self.cli(source, output)
            self.assertEqual(result.returncode, 2)
            self.assertTrue(result.stderr)
            self.assertFalse(output.exists())
        source.write_text(json.dumps(self.data), encoding="utf-8")
        self.assertEqual(self.cli(source, self.folder / "absent" / "new.csv").returncode, 2)

    def test_skipped_and_blocked_views_do_not_satisfy_gate(self):
        self.data["items"] = [
            self.item("EXP-TRAV-1", attributes=["functional"], status="PASS",
                      observation="已实测", evidence_paths=[str(self.evidence)]),
            self.item("EXP-V", attributes=["visual"], status="SKIPPED", blocked_reason="未执行"),
            self.item("EXP-U", attributes=["usability"], status="BLOCKED", blocked_reason="无表单"),
            self.item("EXP-A", attributes=["accessibility"], status="SKIPPED", blocked_reason="未执行"),
            self.item("EXP-P", attributes=["performance"], status="SKIPPED", blocked_reason="无阈值"),
        ]
        report = self.report()
        gate = report["gate"]
        self.assertEqual(gate["verdict_ceiling"], "待核实")
        self.assertEqual(gate["missing_views"], ["界面样式", "交互易用性", "基础无障碍", "性能"])
        self.assertEqual(gate["executed_views"]["功能"], ["EXP-TRAV-1"])
        self.assertEqual(gate["skipped_views"]["性能"], ["EXP-P"])
        self.assertEqual(report["views"]["性能"], ["EXP-P"], "兼容字段仍保留全部记录")

    def test_executed_view_restores_ceiling_with_skipped_sibling(self):
        passed = lambda ident, attr: self.item(ident, attributes=[attr], status="PASS",
                                                observation="已实测", evidence_paths=[str(self.evidence)])
        self.data["attribute_rulings"] = {
            "security": {"state": "not_applicable", "reason": "本次仅校验视图门禁，未涉及权限与会话"},
            "compatibility": {"state": "not_applicable", "reason": "未声明内核/设备范围"},
            "content": {"state": "not_applicable", "reason": "未涉及文案变更"},
        }
        self.data["items"] = [
            self.item("EXP-TRAV-1", attributes=["functional"], status="PASS",
                      observation="已实测", evidence_paths=[str(self.evidence)]),
            passed("EXP-V", "visual"), passed("EXP-U", "usability"),
            passed("EXP-A", "accessibility"), passed("EXP-P", "performance"),
            self.item("EXP-V2", attributes=["visual"], status="SKIPPED", blocked_reason="附带未执行项"),
        ]
        gate = self.report()["gate"]
        self.assertEqual(gate["missing_views"], [])
        self.assertEqual(gate["verdict_ceiling"], "通过")
        self.assertEqual(gate["skipped_views"]["界面样式"], ["EXP-V2"])


if __name__ == "__main__":
    unittest.main()
