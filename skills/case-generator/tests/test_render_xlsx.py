"""render_xlsx.py 19 列渲染测试（阶段 4 输出路由；openpyxl 缺失时降级路径）。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _util import CASES_12, SCRIPTS, load  # noqa: E402

try:
    import openpyxl  # noqa: F401
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

COLUMNS = json.loads(
    (SCRIPTS.parent / "config" / "policy.json").read_text(encoding="utf-8-sig")
)["views"]["columns"]

import render_xlsx as rx  # noqa: E402


class TestRowProjection(unittest.TestCase):
    def test_row_19_columns(self):
        cases = load(CASES_12)
        tc = cases["testcases"][0]
        row = rx.row_for(tc, COLUMNS)
        self.assertEqual(len(row), 19)
        self.assertEqual(row[0], "问卷回收站")          # 项目
        self.assertEqual(row[1], "ui")                   # 类型 = layer
        self.assertEqual(row[2], "TC-M01-001")           # 用例编号
        self.assertEqual(row[3], "问卷回收站")           # 需求 = project 值（勿按列名直取）
        self.assertEqual(row[4], "P0")                   # 优先级
        self.assertEqual(row[10], "是")                  # 是否冒烟
        self.assertEqual(row[11], "AI")                  # 创建者
        self.assertEqual(row[15], "")                    # 执行结果留空
        self.assertEqual(row[16], "")                    # 截图留空

    def test_non_smoke_row(self):
        cases = load(CASES_12)
        tc = next(t for t in cases["testcases"] if t["id"] == "TC-M01-003")
        row = rx.row_for(tc, COLUMNS)
        self.assertEqual(row[10], "否")

    def test_markdown_fallback_renders(self):
        cases = load(CASES_12)
        md = rx.render_markdown(cases, COLUMNS)
        self.assertIn("降级输出", md)
        self.assertIn("测试用例", md)
        self.assertIn("TC-M01-001", md)

    def test_required_14_columns_nonempty(self):
        """阶段 2 验收：required:true 的 14 列非空率 100%，其余 5 列按 policy 允许为空。"""
        cases = load(CASES_12)
        required_idx = [i for i, c in enumerate(COLUMNS) if c.get("required")]
        self.assertEqual(len(required_idx), 14)
        for tc in cases["testcases"]:
            row = rx.row_for(tc, COLUMNS)
            empty = [COLUMNS[i]["header"] for i in required_idx if not str(row[i]).strip()]
            self.assertEqual(empty, [], f"{tc['id']} 必填列出现空值: {empty}")


@unittest.skipUnless(HAS_OPENPYXL, "openpyxl 未安装：降级路径已由 TestRowProjection 覆盖")
class TestXlsxRender(unittest.TestCase):
    def test_cli_render_and_verify(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "测试用例_T-TEST.xlsx"
            p = subprocess.run(
                [sys.executable, str(SCRIPTS / "render_xlsx.py"),
                 "--cases", str(CASES_12), "--out", str(out),
                 "--task-id", "T-TEST", "--verify"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertTrue(out.is_file())
            wb = openpyxl.load_workbook(out, read_only=True)
            ws = wb["测试用例"]
            headers = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
            self.assertEqual(headers, [c["header"] for c in COLUMNS])
            wb.close()

    def test_verify_only_detects_drift(self):
        """防回流：视图已渲染后 JSON 变化（或视图被手工改），--verify-only 必须判不一致。"""
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "drift.xlsx"
            work = Path(tmp) / "work.json"
            work.write_text(json.dumps(load(CASES_12), ensure_ascii=False), encoding="utf-8")
            p = subprocess.run(
                [sys.executable, str(SCRIPTS / "render_xlsx.py"),
                 "--cases", str(work), "--out", str(out), "--task-id", "T-TEST"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            data = json.loads(work.read_text(encoding="utf-8"))
            data["testcases"] = [t for t in data["testcases"] if t["id"] != "TC-M01-003"]
            work.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            p = subprocess.run(
                [sys.executable, str(SCRIPTS / "render_xlsx.py"),
                 "--cases", str(work), "--out", str(out), "--verify-only"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            self.assertEqual(p.returncode, 1)
            self.assertIn("视图一致性失败", p.stderr)

    def test_dual_view_id_consistency(self):
        """阶段 4 验收：双产出时两份视图 case_id 集合与条数一致。"""
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            xlsx = Path(tmp) / "19col.xlsx"
            delivery = Path(tmp) / "delivery.json"
            p1 = subprocess.run(
                [sys.executable, str(SCRIPTS / "render_xlsx.py"),
                 "--cases", str(CASES_12), "--out", str(xlsx), "--task-id", "T-DUAL"],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
            p2 = subprocess.run(
                [sys.executable, str(SCRIPTS / "render_json.py"),
                 "--cases", str(CASES_12), "--out", str(delivery)],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
            self.assertEqual(p1.returncode, 0, p1.stdout + p1.stderr)
            self.assertEqual(p2.returncode, 0, p2.stdout + p2.stderr)
            view = json.loads(delivery.read_text(encoding="utf-8-sig"))
            wb = openpyxl.load_workbook(xlsx, read_only=True)
            ws = wb["测试用例"]
            rows = list(ws.iter_rows(min_row=2, values_only=True))
            wb.close()
            xlsx_ids = sorted(str(r[2]) for r in rows if r)
            json_ids = sorted(t["id"] for t in view["testcases"])
            self.assertEqual(xlsx_ids, json_ids)
            self.assertEqual(len(xlsx_ids), 12)


if __name__ == "__main__":
    unittest.main()