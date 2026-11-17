# -*- coding: utf-8 -*-
"""剥离适配回归：compare_fields 的输入错误必须按同包 CLI 惯例返回 2 + 友好消息。

此前缺输入直接 read_text 抛裸 Traceback（rc=1），与 render_reports/validate_defects
的 rc=2 契约不一致——跨脚本编排（pipeline / smoke_chain）会把"用法/数据错误"
误读成"判定不通过"。
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPTS / "compare_fields.py"), *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


class CompareInputErrorTests(unittest.TestCase):
    def test_missing_design_returns_2_with_message(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            page = tmp / "page.json"
            page.write_text('{"viewport":{"w":1440},"items":[]}', encoding="utf-8")
            p = run("--design", tmp / "nope.json", "--page", page, "--output", tmp / "out.json")
            self.assertEqual(p.returncode, 2, p.stderr[-200:])
            self.assertIn("输入文件不存在", p.stderr)
            self.assertNotIn("Traceback", p.stderr)

    def test_broken_json_returns_2_with_message(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            design = tmp / "design.json"
            design.write_text("{broken", encoding="utf-8")
            page = tmp / "page.json"
            page.write_text('{"viewport":{"w":1440},"items":[]}', encoding="utf-8")
            p = run("--design", design, "--page", page, "--output", tmp / "out.json")
            self.assertEqual(p.returncode, 2, p.stderr[-200:])
            self.assertIn("输入解析失败", p.stderr)
            self.assertNotIn("Traceback", p.stderr)


if __name__ == "__main__":
    unittest.main()