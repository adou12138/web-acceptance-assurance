"""Synthetic design to regression candidates; no target website is executed."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import csv

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/change_impact.py"


class ChangeImpactTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), "Missing change-impact tool")
        spec = importlib.util.spec_from_file_location("change_impact", SCRIPT)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        base = json.loads((ROOT / "examples/cases-example.json").read_text(encoding="utf-8"))
        base["meta"]["flow_kind"] = "simple"
        base.pop("scenarios")
        base.pop("diagrams")
        base["features"][0]["children"] = []
        base["requirements"] = []
        base["acceptance"] = []
        base["testcases"] = []
        original = json.loads((ROOT / "examples/cases-example.json").read_text(encoding="utf-8"))
        for index in range(1, 4):
            req = f"REQ-{index:02d}"
            fp = f"FP-{index:02d}"
            ac = f"AC-{index:02d}"
            tc = f"TC-{index:02d}"
            base["requirements"].append({**original["requirements"][0], "id": req, "name": f"Requirement {index}"})
            leaf = copy.deepcopy(original["features"][0]["children"][0])
            leaf.update(id=fp, requirement_ids=[req])
            base["features"][0]["children"].append(leaf)
            base["acceptance"].append({**original["acceptance"][0], "id": ac, "requirement_id": req, "case_ids": [tc]})
            case = copy.deepcopy(original["testcases"][0])
            case.pop("scenario_id")
            case.pop("path_id")
            case.update(id=tc, title=f"Case, {index}", feature_ids=[fp], acceptance_ids=[ac], priority=["P1", "P0", "P2"][index-1])
            base["testcases"].append(case)
        self.design = base
        self.changes = {"schema_version": "1.0", "changed_requirements": ["REQ-01"], "dependencies": [
            {"requirement": "REQ-02", "depends_on": ["REQ-01"], "source": "confirmed interface consumer"},
            {"requirement": "REQ-03", "depends_on": ["REQ-02"], "source": "confirmed business flow"}
        ]}

    def test_transitive_direction_priority_and_no_mutation(self):
        before = copy.deepcopy(self.design)
        result = self.module.analyze(self.design, self.changes)
        self.assertEqual([x["id"] for x in result["selected_cases"]], ["TC-02", "TC-01", "TC-03"])
        req = next(x for x in result["impacted_requirements"] if x["id"] == "REQ-03")
        self.assertEqual(req["paths"][0]["path"], ["REQ-01", "REQ-02", "REQ-03"])
        self.assertEqual(self.design, before)
        self.assertTrue(all(x["rerun_candidate"] for x in result["selected_cases"]))
        self.assertNotIn("status", result["selected_cases"][0])
        self.changes["changed_requirements"] = ["REQ-03"]
        self.assertEqual([x["id"] for x in self.module.analyze(self.design, self.changes)["selected_cases"]], ["TC-03"])

    def test_cycles_multiple_seeds_and_deterministic_paths(self):
        self.changes["changed_requirements"] = ["REQ-03", "REQ-01"]
        self.changes["dependencies"].append({"requirement": "REQ-01", "depends_on": ["REQ-03"], "source": "cycle"})
        result = self.module.analyze(self.design, self.changes)
        self.assertEqual(len(result["selected_cases"]), 3)
        self.assertTrue(all(len(x["paths"]) == 2 for x in result["impacted_requirements"]))
        self.assertEqual(result, self.module.analyze(self.design, self.changes))

    def test_missing_dependencies_are_explicit_gap(self):
        self.changes["dependencies"] = []
        result = self.module.analyze(self.design, self.changes)
        self.assertEqual([x["id"] for x in result["selected_cases"]], ["TC-01"])
        self.assertTrue(result["gaps"])

    def test_design_coverage_gap_is_rejected_before_candidate_selection(self):
        self.design["testcases"] = [x for x in self.design["testcases"] if x["id"] != "TC-03"]
        self.design["acceptance"] = [x for x in self.design["acceptance"] if x["id"] != "AC-03"]
        # Design's own structural validation is still authoritative.
        with self.assertRaises(ValueError):
            self.module.analyze(self.design, self.changes)

    def test_unknown_requirement_and_self_edge_rejected(self):
        self.changes["changed_requirements"] = ["REQ-NOT-FOUND"]
        with self.assertRaises(ValueError):
            self.module.analyze(self.design, self.changes)
        self.changes["changed_requirements"] = ["REQ-01"]
        self.changes["dependencies"][0]["depends_on"] = ["REQ-02"]
        with self.assertRaises(ValueError):
            self.module.analyze(self.design, self.changes)

    def test_malformed_and_missing_source_rejected(self):
        for value in [None, {}, {**self.changes, "dependencies": "wrong"},
                      {**self.changes, "dependencies": [{"requirement": "REQ-02", "depends_on": ["REQ-01"], "source": ""}]}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.module.analyze(self.design, value)

    def test_duplicate_edge_and_reference_rejected(self):
        self.changes["dependencies"].append(copy.deepcopy(self.changes["dependencies"][0]))
        with self.assertRaises(ValueError):
            self.module.analyze(self.design, self.changes)
        self.changes["dependencies"].pop()
        self.changes["dependencies"][0]["depends_on"].append("REQ-01")
        with self.assertRaises(ValueError):
            self.module.analyze(self.design, self.changes)

    def test_cli_chain_csv_bom_quotes_and_exclusive_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            design, changes, output = work / "design.json", work / "changes.json", work / "regression.csv"
            design.write_text(json.dumps(self.design), encoding="utf-8")
            changes.write_text(json.dumps(self.changes), encoding="utf-8")
            command = [sys.executable, str(SCRIPT), str(design), str(changes), "--output", str(output)]
            run = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(len(json.loads(run.stdout)["selected_cases"]), 3)
            self.assertTrue(output.read_bytes().startswith(b"\xef\xbb\xbf"))
            with output.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.reader(stream))
            self.assertEqual(len(rows), 4)
            self.assertEqual(rows[1][1], "Case, 2")
            before = output.read_bytes()
            again = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(again.returncode, 2)
            self.assertEqual(output.read_bytes(), before)

    def test_cli_invalid_input_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            design, changes, output = work / "design.json", work / "changes.json", work / "regression.csv"
            design.write_text(json.dumps(self.design), encoding="utf-8")
            changes.write_text("{}", encoding="utf-8")
            run = subprocess.run([sys.executable, str(SCRIPT), str(design), str(changes), "--output", str(output)], capture_output=True)
            self.assertEqual(run.returncode, 2)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
