"""challenge_summary.py 回归：手算样例、未完成判定、零分母 NA、注入分账与 CSV 导出。"""
import csv
import io
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import challenge_summary as cs  # noqa: E402


def chl(cid, kind, outcome, injected=True, baseline_green=True, restore_verified=True):
    return {"challenge_id": cid, "kind": kind, "target_assertion": "A1",
            "baseline_green": baseline_green, "outcome": outcome,
            "evidence_paths": ["evidence/x.png"], "restore_verified": restore_verified,
            "injected": injected}


class SourceMutationAccount(unittest.TestCase):
    def test_hand_computed_example_matches(self):
        # G=6（1 条 baseline_green=false 不入分母）；I=1、E=1、K=2、survived=1、U=1
        records = [chl("CHL-1", "source_mutation", "killed"),
                   chl("CHL-2", "source_mutation", "killed"),
                   chl("CHL-3", "source_mutation", "invalid_build"),
                   chl("CHL-4", "source_mutation", "equivalent"),
                   chl("CHL-5", "source_mutation", "tool_error"),
                   chl("CHL-6", "source_mutation", "survived"),
                   chl("CHL-7", "source_mutation", "killed", baseline_green=False)]
        account = cs.source_mutation_account(records)
        self.assertEqual((account["G"], account["I"], account["E"], account["K"],
                          account["survived"], account["U"], account["baseline_failed"]),
                         (6, 1, 1, 2, 1, 1, 1))
        # 手算：分母 6-1-1=4；U=1>0 → 未完成，保守下界 2/4=0.5
        self.assertEqual(account["status"], "未完成")
        self.assertIsNone(account["score"])
        self.assertEqual(account["conservative_lower_bound"], 0.5)
        self.assertEqual(account["lower_bound_label"], "固定分母的保守检出下界")

    def test_tool_error_zero_means_complete_score(self):
        records = [chl("CHL-1", "source_mutation", "killed"),
                   chl("CHL-2", "source_mutation", "killed"),
                   chl("CHL-3", "source_mutation", "invalid_build"),
                   chl("CHL-4", "source_mutation", "equivalent"),
                   chl("CHL-5", "source_mutation", "survived")]
        account = cs.source_mutation_account(records)
        # 手算：G=5，I=1，E=1，K=2，U=0 → 完整分数 2/(5-1-1)=0.6667
        self.assertEqual(account["status"], "完成")
        self.assertEqual(account["score"], 0.6667)

    def test_zero_denominator_outputs_na_not_100_percent(self):
        records = [chl("CHL-1", "source_mutation", "invalid_build"),
                   chl("CHL-2", "source_mutation", "equivalent")]
        account = cs.source_mutation_account(records)
        self.assertEqual(account["status"], "完成")
        self.assertEqual(account["score"], "NA")

    def test_tool_error_yields_zero_lower_bound_when_nothing_killed(self):
        # U>0 且 K=0：分母 = G−I−E ≥ U > 0（U 记录本身留在分母内），下界为 0.0
        records = [chl("CHL-1", "source_mutation", "tool_error"),
                   chl("CHL-2", "source_mutation", "invalid_build")]
        account = cs.source_mutation_account(records)
        self.assertEqual(account["status"], "未完成")
        self.assertEqual(account["conservative_lower_bound"], 0.0)


class BlackBoxAccount(unittest.TestCase):
    def test_injected_not_counted_as_natural_candidates(self):
        records = [chl("CHL-1", "black_box_injection", "killed", injected=True, restore_verified=True),
                   chl("CHL-2", "black_box_injection", "killed", injected=True, restore_verified=False),
                   chl("CHL-3", "black_box_injection", "killed", injected=False),
                   chl("CHL-4", "black_box_injection", "survived", injected=True),
                   chl("CHL-5", "black_box_injection", "survived", injected=False)]
        account = cs.black_box_account(records)
        self.assertEqual(account["injected_detections"], 2)
        self.assertEqual(account["health_restored"], 1)
        self.assertEqual(account["false_positives"], 1)
        self.assertEqual(account["natural_candidates"], 1)
        self.assertEqual(account["injected_missed"], 1)
        self.assertEqual(account["health_alarms"], 1)

    def test_two_accounts_stay_separate(self):
        challenges = [chl("CHL-1", "source_mutation", "killed"),
                      chl("CHL-2", "black_box_injection", "killed", injected=True),
                      chl("CHL-3", "black_box_injection", "killed", injected=True)]
        summary = cs.summarize(challenges, [])
        self.assertEqual(summary["source_mutation"]["G"], 1)   # 黑盒记录不入源码变异分母
        self.assertEqual(summary["source_mutation"]["K"], 1)
        self.assertEqual(summary["black_box_injection"]["injected_detections"], 2)

    def test_verdict_counts_split(self):
        reviews = [{"finding_id": "FIND-1", "verdict": "accepted", "reviewer": "r", "rationale": "ok"},
                   {"finding_id": "FIND-2", "verdict": "rejected", "reviewer": "r", "rationale": "fp"},
                   {"finding_id": "FIND-3", "verdict": "unresolved", "reviewer": "r", "rationale": "?"}]
        summary = cs.summarize([], reviews)
        self.assertEqual(summary["verdict_counts"],
                         {"accepted": 1, "rejected": 1, "unresolved": 1, "unreviewed": 0})


class CsvExport(unittest.TestCase):
    def test_csv_roundtrip_with_bom(self):
        challenges = [chl("CHL-1", "source_mutation", "killed"),
                      chl("CHL-2", "black_box_injection", "killed", injected=False)]
        summary = cs.summarize(challenges, [])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.csv"
            written = cs.write_csv(summary, path)
            raw = path.read_bytes()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), "CSV 必须带 UTF-8 BOM")
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
        self.assertGreater(written, 0)
        cells = {(r["account"], r["metric"]): r["value"] for r in rows}
        self.assertEqual(cells[("source_mutation", "G")], "1")
        self.assertEqual(cells[("source_mutation", "score")], "1.0000")
        self.assertEqual(cells[("black_box_injection", "natural_candidates")], "1")
        self.assertEqual(cells[("black_box_injection", "injected_detections")], "0")

    def test_cli_help_runs(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            with self.assertRaises(SystemExit) as ctx:
                cs.main(["--help"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn("--challenges", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
