"""finding_summary.py 回归：候选归簇、疑似重复、复现率、最小化、单列与 CSV 导出。"""
import csv
import json
import sys
import tempfile
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import finding_summary as fs  # noqa: E402


def event(eid, finding_id, **extra):
    base = {"event_id": eid, "type": "probe", "probe_ref": "ADV-1", "observation": "现象",
            "evidence_paths": ["evidence/a.png"], "injected": False, "finding_id": finding_id}
    base.update(extra)
    return base


class Clustering(unittest.TestCase):
    def test_normal_cluster_keeps_all_samples(self):
        events = [event("EVT-1", "FIND-1", version="v1", contract="C1", entry="/a", role="admin",
                        result_code="500", reproduced=True),
                  event("EVT-2", "FIND-1", version="v1", contract="C1", entry="/a", role="admin",
                        result_code="500", reproduced=True)]
        summary = fs.summarize(events, [])
        self.assertEqual(summary["cluster_count"], 1)
        cand = summary["candidates"][0]
        self.assertEqual(cand["clusters"][0]["samples"], ["EVT-1", "EVT-2"])
        self.assertEqual(cand["budget"]["probes_used"], 2)
        self.assertEqual(cand["budget"]["evidence_count"], 2)

    def test_same_text_different_entry_not_merged_only_suspected(self):
        events = [event("EVT-1", "FIND-1", version="v1", contract="C1", entry="/a", role="admin",
                        result_code="500"),
                  event("EVT-2", "FIND-2", version="v1", contract="C1", entry="/b", role="admin",
                        result_code="500")]
        summary = fs.summarize(events, [])
        self.assertEqual(summary["cluster_count"], 2)
        by_id = {c["finding_id"]: c for c in summary["candidates"]}
        self.assertEqual(by_id["FIND-1"]["clusters"][0]["key"]["entry"], "/a")
        self.assertEqual(by_id["FIND-2"]["clusters"][0]["key"]["entry"], "/b")
        self.assertEqual(by_id["FIND-1"]["suspected_duplicate_of"], ["FIND-2"])
        self.assertEqual(by_id["FIND-2"]["suspected_duplicate_of"], ["FIND-1"])

    def test_reproduction_rate_counts_failed_attempts(self):
        events = [event("EVT-1", "FIND-1", reproduced=True),
                  event("EVT-2", "FIND-1", reproduced=False),
                  event("EVT-3", "FIND-1", reproduced=False)]
        summary = fs.summarize(events, [])
        cand = summary["candidates"][0]
        self.assertEqual(cand["reproduction"], {"successes": 1, "attempts": 3, "rate": 0.3333})
        # 失败尝试保留：样本全部在簇内，不销号
        self.assertEqual(len(cand["clusters"][0]["samples"]), 3)


class Minimization(unittest.TestCase):
    def test_verified_minimal_marked(self):
        events = [event("EVT-1", "FIND-1", reproduced=True, minimization="verified_minimal")]
        summary = fs.summarize(events, [])
        self.assertEqual(summary["candidates"][0]["minimization"], "verified_minimal")

    def test_reduce_failed_keeps_first_failure(self):
        events = [event("EVT-1", "FIND-1", reproduced=True),   # 首败（复现成功样本保留）
                  event("EVT-2", "FIND-1", reproduced=True, minimization="reduce_failed")]
        summary = fs.summarize(events, [])
        cand = summary["candidates"][0]
        self.assertEqual(cand["minimization"], "reduce_failed")
        self.assertNotEqual(cand["minimization"], "verified_minimal")
        self.assertIn("EVT-1", cand["clusters"][0]["samples"])  # 首败不撤销


class Verdicts(unittest.TestCase):
    def test_unresolved_listed_separately_not_defect_nor_false_positive(self):
        events = [event("EVT-1", "FIND-1"), event("EVT-2", "FIND-2"), event("EVT-3", "FIND-3")]
        reviews = [{"finding_id": "FIND-1", "verdict": "accepted", "reviewer": "r", "rationale": "ok",
                    "tie_to_bug": "BUG-1"},
                   {"finding_id": "FIND-2", "verdict": "rejected", "reviewer": "r", "rationale": "fp"},
                   {"finding_id": "FIND-3", "verdict": "unresolved", "reviewer": "r", "rationale": "?"}]
        summary = fs.summarize(events, reviews)
        self.assertEqual(summary["verdict_counts"],
                         {"accepted": 1, "rejected": 1, "unresolved": 1, "unreviewed": 0})
        self.assertEqual(summary["unresolved_ids"], ["FIND-3"])

    def test_empty_input_does_not_crash(self):
        summary = fs.summarize([], [])
        self.assertEqual(summary["candidates"], [])
        self.assertEqual(summary["cluster_count"], 0)
        self.assertEqual(summary["verdict_counts"]["unreviewed"], 0)
        rows = fs.to_csv_rows(summary)
        self.assertEqual(rows, [])


class CsvExport(unittest.TestCase):
    def test_csv_roundtrip_with_bom(self):
        events = [event("EVT-1", "FIND-1", version="v1", contract="C1", entry="/a", role="admin",
                        result_code="500", reproduced=True),
                  event("EVT-2", "FIND-2", version="v1", contract="C1", entry="/b", role="admin",
                        result_code="500", reproduced=False)]
        reviews = [{"finding_id": "FIND-1", "verdict": "accepted", "reviewer": "r", "rationale": "ok"}]
        summary = fs.summarize(events, reviews)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.csv"
            written = fs.write_csv(summary, path)
            self.assertEqual(written, 2)
            raw = path.read_bytes()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), "CSV 必须带 UTF-8 BOM")
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 2)
        first = rows[0]
        self.assertEqual(first["finding_id"], "FIND-1")
        self.assertEqual(first["entry"], "/a")
        self.assertEqual(first["verdict"], "accepted")
        self.assertEqual(first["samples"], "EVT-1")
        self.assertEqual(first["reproduction_rate"], "1.0")
        self.assertEqual(rows[1]["suspected_duplicate_of"], "FIND-1")

    def test_cli_help_runs(self):
        from contextlib import redirect_stdout
        import io
        buf = io.StringIO()
        with redirect_stdout(buf):
            # --help 通过 argparse 退出码 0（SystemExit）
            with self.assertRaises(SystemExit) as ctx:
                fs.main(["--help"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn("--events", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
