import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "observation_bridge.py"
STORE = ROOT / "scripts" / "exploration_state.py"


def observation(findings, status="observed", url="https://example.invalid/app/form", performance=None):
    payload = {
        "schemaVersion": "1.0",
        "status": status,
        "target": {"expectedOrigin": "https://example.invalid", "observedOrigin": "https://example.invalid", "matches": True},
        "page": {"url": url, "title": "示例页", "viewport": {"width": 1280, "height": 800}},
        "coverage": {"totalNodes": 42, "scannedNodes": 42, "truncated": False},
        "findings": findings,
        "confirmedBugs": [],
    }
    if performance is not None:
        payload["performance"] = performance
    return payload


def partial_performance():
    return {
        "status": "partial",
        "source": "Performance API",
        "coverage": {"navigation": "available", "resources": "available", "loading": "available",
                     "contentful": "missing", "interaction": "not-collected", "layout": "not-collected"},
        "notCollected": ["cls", "inp", "longtask"],
    }


class ObservationBridgeTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), "缺少功能：观察结果到探索清单的桥接工具")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        spec = importlib.util.spec_from_file_location("exploration_state", STORE)
        self.store = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.store)

    def write(self, name, payload):
        path = self.folder / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True, encoding="utf-8")

    def standard(self, *extra):
        return [str(self.source), "--target-url", "https://example.invalid/app", "--application-version", "build-1",
                "--environment", "本地受控", "--requirement", "REQ-1", *extra]

    def test_each_finding_becomes_validated_not_run_item(self):
        self.source = self.write("obs.json", observation([
            {"code": "unnamed-control", "classification": "candidate", "needsReview": True, "evidence": {"index": 3, "tag": "button"}},
            {"code": "horizontal-overflow", "classification": "candidate", "needsReview": True, "evidence": {"leftOverflow": 0, "rightOverflow": 40}},
            {"code": "occluded-control", "classification": "candidate", "needsReview": True, "evidence": {"index": 5, "tag": "button", "occluderTag": "div"}},
        ]))
        output = self.folder / "draft.json"
        result = self.run_cli(*self.standard("--output", output))
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(output.read_text(encoding="utf-8"))
        report = self.store.summarize(data)
        self.assertEqual(report["planned"], 3)
        self.assertEqual(report["counts"]["NOT_RUN"], 3)
        by_id = {row["id"]: row for row in report["items"]}
        self.assertEqual(by_id["OBS-unnamed-control"]["attributes"], ["accessibility", "usability"])
        self.assertEqual(by_id["OBS-unnamed-control"]["risk"], "high")
        self.assertEqual(by_id["OBS-horizontal-overflow"]["attributes"], ["visual"])
        self.assertEqual(by_id["OBS-occluded-control"]["risk"], "high")
        for row in report["items"]:
            self.assertEqual(row["status"], "NOT_RUN")
            self.assertEqual(row["observation"], "")
            self.assertEqual(len(row["evidence_paths"]), 1)
            self.assertTrue(Path(row["evidence_paths"][0]).is_absolute())
        self.assertEqual(report["gate"]["verdict_ceiling"], "待核实")

    def test_duplicate_findings_keep_unique_ids_and_original_evidence(self):
        self.source = self.write("dup.json", observation([
            {"code": "text-clipping", "evidence": {"index": 1, "tag": "span"}},
            {"code": "text-clipping", "evidence": {"index": 2, "tag": "span"}},
        ]))
        output = self.folder / "dup-draft.json"
        result = self.run_cli(*self.standard("--output", output))
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(output.read_text(encoding="utf-8"))
        ids = [item["id"] for item in data["items"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 2)

    def test_blocked_or_cross_origin_observation_is_rejected_without_artifact(self):
        cases = {
            "blocked.json": observation([{"code": "unnamed-control", "evidence": {"index": 0}}], status="blocked"),
            "other.json": observation([{"code": "unnamed-control", "evidence": {"index": 0}}], url="https://other.invalid/app"),
        }
        for name, payload in cases.items():
            with self.subTest(name=name):
                self.source = self.write(name, payload)
                output = self.folder / (name + ".draft.json")
                result = self.run_cli(*self.standard("--output", output))
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertFalse(output.exists())
                self.assertTrue(result.stderr.strip())

    def test_unknown_finding_code_is_rejected_instead_of_silently_dropped(self):
        self.source = self.write("unknown.json", observation([{"code": "brand-new-code", "evidence": {}}]))
        output = self.folder / "unknown-draft.json"
        result = self.run_cli(*self.standard("--output", output))
        self.assertEqual(result.returncode, 2)
        self.assertIn("brand-new-code", result.stderr)
        self.assertFalse(output.exists())

    def test_refuses_overwrite_and_requires_output_argument(self):
        self.source = self.write("obs.json", observation([{"code": "broken-image", "evidence": {"index": 2, "tag": "img"}}]))
        existing = self.folder / "existing.json"
        existing.write_text("keep", encoding="utf-8")
        result = self.run_cli(*self.standard("--output", existing))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(existing.read_text(encoding="utf-8"), "keep")
        missing_arg = self.run_cli(*self.standard())
        self.assertEqual(missing_arg.returncode, 2)

    def test_cli_reports_generated_ids_and_keeps_source_untouched(self):
        self.source = self.write("obs.json", observation([
            {"code": "placeholder-link", "evidence": {"index": 7, "tag": "a", "hrefKind": "placeholder"}},
        ]))
        original = self.source.read_bytes()
        output = self.folder / "draft.json"
        result = self.run_cli(*self.standard("--output", output))
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["generated"], 1)
        self.assertEqual(summary["item_ids"], ["OBS-placeholder-link"])
        self.assertEqual(self.source.read_bytes(), original)
        self.assertFalse(output.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_usable_performance_coverage_becomes_tracked_item(self):
        self.source = self.write("perf.json", observation([], performance=partial_performance()))
        output = self.folder / "perf-draft.json"
        result = self.run_cli(*self.standard("--output", output))
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(output.read_text(encoding="utf-8"))
        report = self.store.summarize(data)
        by_id = {row["id"]: row for row in report["items"]}
        self.assertIn("OBS-performance", by_id, "采到性能数据却未进台账，会让门禁误判性能视角缺失")
        item = by_id["OBS-performance"]
        self.assertEqual(item["attributes"], ["performance"])
        self.assertEqual(item["status"], "NOT_RUN")
        self.assertEqual(item["observation"], "")
        self.assertNotIn("性能", report["gate"]["missing_views"])
        self.assertIn("界面样式", report["gate"]["missing_views"])

    def test_performance_item_names_uncollected_families_for_followup(self):
        self.source = self.write("perf.json", observation([], performance=partial_performance()))
        output = self.folder / "perf-draft.json"
        result = self.run_cli(*self.standard("--output", output))
        self.assertEqual(result.returncode, 0, result.stderr)
        item = json.loads(output.read_text(encoding="utf-8"))["items"][0]
        self.assertIn("cls", item["action"].lower())
        self.assertIn("inp", item["action"].lower())
        self.assertEqual(item["risk"], "medium")

    def test_absent_or_unusable_performance_never_fakes_coverage(self):
        cases = {
            "not-requested.json": {"status": "not-requested"},
            "unavailable.json": {"status": "unavailable"},
        }
        for name, perf in cases.items():
            with self.subTest(name=name):
                self.source = self.write(name, observation([], performance=perf))
                output = self.folder / (name + ".draft.json")
                result = self.run_cli(*self.standard("--output", output))
                self.assertEqual(result.returncode, 0, result.stderr)
                data = json.loads(output.read_text(encoding="utf-8"))
                ids = [item["id"] for item in data["items"]]
                self.assertNotIn("OBS-performance", ids)
                self.assertIn("性能", self.store.summarize(data)["gate"]["missing_views"])

    def test_unknown_performance_shape_is_rejected(self):
        self.source = self.write("bad-perf.json", observation([], performance={"status": "probably-fine"}))
        output = self.folder / "bad-perf-draft.json"
        result = self.run_cli(*self.standard("--output", output))
        self.assertEqual(result.returncode, 2)
        self.assertFalse(output.exists())
        self.assertTrue(result.stderr.strip())

    def test_performance_evidence_files_are_attached_and_validated(self):
        trace = self.folder / "trace.json.gz"
        trace.write_bytes(b'{"fake":"trace"}')
        self.source = self.write("perf.json", observation([], performance=partial_performance()))
        output = self.folder / "perf-draft.json"
        result = self.run_cli(*self.standard("--output", output, "--performance-evidence", trace))
        self.assertEqual(result.returncode, 0, result.stderr)
        item = json.loads(output.read_text(encoding="utf-8"))["items"][0]
        self.assertIn(str(trace.resolve()), item["evidence_paths"])
        missing_output = self.folder / "perf-missing.json"
        bad = self.run_cli(*self.standard("--output", missing_output,
                                         "--performance-evidence", self.folder / "nope.json.gz"))
        self.assertEqual(bad.returncode, 2)
        self.assertFalse(missing_output.exists())

    def test_performance_item_coexists_with_visual_findings(self):
        self.source = self.write("mixed.json", observation([
            {"code": "broken-image", "evidence": {"index": 2, "tag": "img"}},
        ], performance=partial_performance()))
        output = self.folder / "mixed-draft.json"
        result = self.run_cli(*self.standard("--output", output))
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(output.read_text(encoding="utf-8"))
        ids = [item["id"] for item in data["items"]]
        self.assertEqual(ids, ["OBS-broken-image", "OBS-performance"])
        report = self.store.summarize(data)
        self.assertEqual(report["planned"], 2)
        self.assertEqual(report["views"]["性能"], ["OBS-performance"])
        self.assertEqual(report["views"]["界面样式"], ["OBS-broken-image"])


    def test_every_observer_finding_code_is_mapped(self):
        """观察器新增检测项必须同步映射，否则真实观察会因未知code中断。"""
        import re
        spec = importlib.util.spec_from_file_location("observation_bridge", SCRIPT)
        bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bridge)
        source = (ROOT / "scripts" / "browser_observer.js").read_text(encoding="utf-8")
        codes = set(re.findall(r"candidate\('([a-z-]+)'", source))
        self.assertTrue(codes, "未从观察器中提取到任何finding code")
        self.assertEqual(codes - set(bridge.FINDING_RULES), set(),
                         "存在未映射的观察代码，需在FINDING_RULES中补充")

    def test_fixture_declared_page_marks_every_item_without_dropping_it(self):
        payload = observation([{"code": "unnamed-control", "classification": "candidate", "needsReview": True,
                                "evidence": {"index": 1, "tag": "button"}}], performance=partial_performance())
        payload["page"]["fixture"] = {"declared": True, "declaredBy": ["title"], "keywords": ["受控"]}
        self.source = self.write("obs.json", payload)
        output = self.folder / "draft.json"
        result = self.run_cli(*self.standard("--output", output))
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual([item["id"] for item in data["items"]], ["OBS-unnamed-control", "OBS-performance"])
        for item in data["items"]:
            self.assertTrue(item["action"].startswith("【受控夹具预埋项"), item["action"])
            self.assertEqual(item["status"], "NOT_RUN")
            self.assertTrue(item["observation"] == "")
        self.assertTrue(json.loads(result.stdout)["fixture_declared"])

    def test_page_without_fixture_declaration_keeps_plain_action(self):
        self.source = self.write("obs.json", observation([
            {"code": "unnamed-control", "classification": "candidate", "needsReview": True,
             "evidence": {"index": 1, "tag": "button"}}]))
        output = self.folder / "draft.json"
        result = self.run_cli(*self.standard("--output", output))
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(output.read_text(encoding="utf-8"))
        self.assertFalse(data["items"][0]["action"].startswith("【"))
        self.assertFalse(json.loads(result.stdout)["fixture_declared"])

    def test_malformed_fixture_shape_is_rejected(self):
        for index, broken in enumerate(({"declared": "yes"}, {})):
            payload = observation([{"code": "unnamed-control", "classification": "candidate",
                                    "needsReview": True, "evidence": {"index": 1, "tag": "button"}}])
            payload["page"]["fixture"] = broken
            self.source = self.write(f"obs-{index}.json", payload)
            output = self.folder / f"draft-{index}.json"
            result = self.run_cli(*self.standard("--output", output))
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertIn("fixture", result.stderr)
            self.assertFalse(output.exists())

if __name__ == "__main__":
    unittest.main()
