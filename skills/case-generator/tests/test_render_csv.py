"""五视角 CSV 渲染器测试：列契约、视角过滤、BOM/回读与拒绝覆盖。"""

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
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
        with self.assertRaises(rc.VerifyError):
            rc.verify_view(path, self.columns, {tc["id"] for tc in self.cases["testcases"]})
        with self.assertRaises(FileExistsError):
            rc.render(self.cases, "t", self.folder, self.columns, verify=True)

    def test_task_id_is_validated_as_filename_fragment(self):
        for bad in ("../evil", "含中文", "a/b", "", "x" * 65):
            with self.subTest(task_id=bad), self.assertRaises(rc.UsageError):
                rc.render(self.cases, bad, self.folder, self.columns)
        self.assertFalse(self.folder.exists(), "非法 task_id 不得创建目录")

    def test_view_rows_and_unique_cases_are_reported_separately(self):
        rc.render(self.cases, "t", self.folder, self.columns)
        cases = json.loads(json.dumps(self.cases))
        cases["testcases"][0]["attributes"] = sorted(set(cases["testcases"][0]["attributes"]) | {"visual"})
        summary = rc.totals(cases)
        self.assertEqual(summary["view_rows"], 13, "同一用例出现在两个视角应产生两行")
        self.assertEqual(summary["unique_cases"], 12, "跨视角重复行不得算作用例数")

    def test_cli_exit_codes_and_json_summary(self):
        cases_file = self.folder.parent / "cases.json"
        cases_file.write_text(json.dumps(self.cases, ensure_ascii=False), encoding="utf-8")
        cmd = [sys.executable, str(SCRIPTS / "render_csv.py"), "--cases", str(cases_file),
               "--out", str(self.folder), "--task-id", "cli", "--verify"]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["views"]["功能"]["count"], 12)
        self.assertEqual(payload["unique_cases"], 12)
        self.assertIn("view_rows", payload)
        again = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(again.returncode, 2)

    def test_exit_code_mapping_is_exception_based(self):
        cases_file = self.folder.parent / "cases-map.json"
        cases_file.write_text(json.dumps(self.cases, ensure_ascii=False), encoding="utf-8")
        argv = ["render_csv.py", "--cases", str(cases_file), "--out", str(self.folder)]
        for exc, code in ((rc.VerifyError("回读集合不一致"), 1), (rc.UsageError("task_id 非法"), 2),
                          (ValueError("坏输入"), 2), (FileExistsError("已存在"), 2)):
            with self.subTest(exc=type(exc).__name__), mock.patch.object(rc, "render", side_effect=exc), \
                    mock.patch.object(sys, "argv", argv), self.assertRaises(SystemExit) as raised:
                rc.main()
            self.assertEqual(raised.exception.code, code, f"{type(exc).__name__} 应映射退出码 {code}")


if __name__ == "__main__":
    unittest.main()
