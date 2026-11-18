"""render_csv --only 精选导出 与 render_testpoints 派生视图测试。"""

import csv
import json
import sys
import tempfile
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _util import CASES_12, SCRIPTS, load  # noqa: E402

import render_csv  # noqa: E402
import render_testpoints as rtp  # noqa: E402

POLICY = json.loads((Path(SCRIPTS).parent / "config" / "policy.json").read_text(encoding="utf-8-sig"))


class OnlyFilterTests(unittest.TestCase):
    def setUp(self):
        self.cases = load(CASES_12)
        self.columns = POLICY["views"]["columns"]

    def expected_ids(self, priorities):
        return {tc["id"] for tc in self.cases["testcases"]
                if tc.get("priority") in priorities and "functional" in (tc.get("attributes") or [])}

    def test_only_filters_and_verify_stays_consistent(self):
        only = {"P0", "P1"}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "views"
            result = render_csv.render(self.cases, "t1", out, self.columns,
                                       verify=True, only=only)
            functional = Path(result["功能"]["path"])
            with functional.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual({r["用例编号"] for r in rows}, self.expected_ids(only))
            self.assertEqual(result["功能"]["count"], len(self.expected_ids(only)))

    def test_only_smaller_than_full(self):
        with tempfile.TemporaryDirectory() as tmp:
            full = render_csv.render(self.cases, "t2", Path(tmp) / "a", self.columns)
            picked = render_csv.render(self.cases, "t2", Path(tmp) / "b", self.columns, only={"P0"})
            self.assertLessEqual(picked["功能"]["count"], full["功能"]["count"])

    def test_totals_respect_only(self):
        self.assertEqual(render_csv.totals(self.cases, {"P0"})["unique_cases"],
                         len(self.expected_ids({"P0"})))


class TestPointViewTests(unittest.TestCase):
    def setUp(self):
        self.cases = load(CASES_12)

    def test_rows_grouped_by_feature_and_acceptance(self):
        rows = rtp.build_rows(self.cases)
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(rtp.HEADERS), 9)
        for row in rows:
            self.assertEqual(len(row), 9)
            self.assertTrue(row[0].startswith("FP-"))
            self.assertIn(row[6].isdigit(), (True,))

    def test_render_and_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "test_points.csv"
            rows = rtp.build_rows(self.cases)
            with out.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(rtp.HEADERS)
                writer.writerows(rows)
            rtp.verify(out, len(rows))

    def test_gap_note_surfaces_on_row(self):
        cases = json.loads(json.dumps(self.cases))
        cases["coverage_gaps"] = [{"gap_id": "GAP-01", "kind": "element_uncovered",
                                   "target": "FP-02 全选恢复", "reason": "需求未定义全选边界",
                                   "obligation": "向产品确认后补测"}]
        rows = rtp.build_rows(cases)
        hit = [r for r in rows if r[0] == "FP-02"][0]
        self.assertIn("缺口", hit[3])
        self.assertIn("确认后补测", hit[3])

    def test_priority_order_picks_highest(self):
        rows = {r[0]: r for r in rtp.build_rows(self.cases)}
        self.assertEqual(rows["FP-01"][7], "P0")
        self.assertEqual(rows["FP-02"][7], "P0")
        # FP-03 的用例最高只到 P1，折叠后必须取 P1 而不是默认 P2
        self.assertEqual(rows["FP-03"][7], "P1")


if __name__ == "__main__":
    unittest.main()
