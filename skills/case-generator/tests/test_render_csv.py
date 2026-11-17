"""五视角 CSV 渲染器测试：列契约、视角过滤、BOM/回读与拒绝覆盖。"""

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _util import CASES_12, SCRIPTS, load  # noqa: E402

import render_csv as rc  # noqa: E402


class RenderCsvTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name) / "views"
        self.cases = load(CASES_12)
        self.policy = load(Path(rc.DEFAULT_POLICY))
        self.columns = self.policy["views"]["columns"]

    def test_five_views_are_derived_from_shared_attribute_map(self):
        result = rc.render(self.cases, "t", self.folder, self.columns, verify=True)
        self.assertEqual(set(result), {"界面样式", "交互易用性", "基础无障碍", "功能", "性能"})
        self.assertEqual(result["功能"]["count"], 12)
        self.assertEqual(result["性能"]["count"], 0)
        for view, info in result.items():
            path = Path(info["path"])
            self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"), view)
            with path.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.reader(stream))
            self.assertEqual(rows[0], [c["header"] for c in self.columns], view)
            self.assertEqual(len(rows) - 1, info["count"], view)

    def test_refuses_overwrite_unless_force_and_verify_catches_tampering(self):
        rc.render(self.cases, "t", self.folder, self.columns)
        with self.assertRaises(FileExistsError):
            rc.render(self.cases, "t", self.folder, self.columns)
        path = self.folder / "测试用例_t_功能.csv"
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.reader(stream))
        id_index = next(i for i, c in enumerate(self.columns) if c["field"] == "case_id")
        rows[1][id_index] = "被篡改的用例编号"
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            csv.writer(stream).writerows(rows)
        with self.assertRaises(ValueError):
            rc.verify_view(path, self.columns, {tc["id"] for tc in self.cases["testcases"]})
        with self.assertRaises(FileExistsError):
            rc.render(self.cases, "t", self.folder, self.columns, verify=True)

    def test_cli_exit_codes_and_json_summary(self):
        cases_file = self.folder.parent / "cases.json"
        cases_file.write_text(json.dumps(self.cases, ensure_ascii=False), encoding="utf-8")
        cmd = [sys.executable, str(SCRIPTS / "render_csv.py"), "--cases", str(cases_file),
               "--out", str(self.folder), "--task-id", "cli", "--verify"]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["views"]["功能"]["count"], 12)
        again = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(again.returncode, 2)


if __name__ == "__main__":
    unittest.main()
