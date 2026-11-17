"""render_json.py 交付 JSON 视图测试（阶段 4 输出路由）。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _util import CASES_12, SCRIPTS, load  # noqa: E402

import render_json as rj  # noqa: E402


class TestBuildView(unittest.TestCase):
    def test_summary_counts(self):
        view = rj.build_view(load(CASES_12))
        self.assertEqual(view["view"], "delivery-json")
        self.assertEqual(view["summary"]["testcases"], 12)
        self.assertEqual(view["summary"]["smoke"], 2)
        self.assertEqual(view["summary"]["p0"], 2)
        self.assertEqual(view["summary"]["feature_points"], 3)
        self.assertEqual(view["summary"]["acceptance"], 3)

    def test_projection_keeps_assertions(self):
        view = rj.build_view(load(CASES_12))
        tc1 = next(t for t in view["testcases"] if t["id"] == "TC-M01-001")
        self.assertEqual(len(tc1["assertions"]), 3)
        self.assertIn("oracle", tc1["assertions"][0])

    def test_trace_chain(self):
        view = rj.build_view(load(CASES_12))
        ac_ids = {a["id"] for a in view["trace"]["acceptance"]}
        self.assertEqual(ac_ids, {"AC-01", "AC-02", "AC-03"})

    def test_cli_id_consistency(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "delivery.json"
            p = subprocess.run(
                [sys.executable, str(SCRIPTS / "render_json.py"),
                 "--cases", str(CASES_12), "--out", str(out)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            view = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(sorted(t["id"] for t in view["testcases"]),
                             sorted(t["id"] for t in load(CASES_12)["testcases"]))


if __name__ == "__main__":
    unittest.main()