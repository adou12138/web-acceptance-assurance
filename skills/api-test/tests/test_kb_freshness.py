"""kb_freshness.py 的一致性测试（纯离线夹具，不依赖本机知识库）。

锁定审核意见 P1-3 修正后的判据：
- 主判据 = 源-索引漂移（源文件比 source_mtime/build_at 新 → stale_drift）；
- 墙钟年龄只是提示字段，**旧索引但源未变 → 不得判 stale**；
- crawled_at 只作"数据截至"展示，不参与 stale 判定；
- KB 不可用 → exit 2 + 降级提示（fail closed for the check，fail open for the workflow）。
"""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "kb_freshness.py"


def run_cli(args, kb_path=None):
    env = dict(os.environ)
    env.pop("KB_PATH", None)
    if kb_path is not None:
        env["KB_PATH"] = str(kb_path)
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, timeout=120, cwd=str(SKILL))


class FreshnessFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.kb = Path(self._tmp.name) / "user_knowledge"
        self.project = self.kb / "project"
        (self.project / "_vector_index").mkdir(parents=True)
        (self.project / "showdoc_api_docs" / "demo").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def write_index(self, build_at, source_mtime):
        (self.project / "_vector_index" / "info.json").write_text(
            json.dumps({"build_at": build_at, "source_mtime": source_mtime,
                        "n_chunks": 42, "model": "fixture"}, ensure_ascii=False),
            encoding="utf-8")

    def touch_source(self, name="033_2.1.14 创建AI主页[1000107].md", mtime=None):
        f = self.project / "showdoc_api_docs" / "demo" / name
        f.write_text("# fixture\n", encoding="utf-8")
        if mtime is not None:
            os.utime(f, (mtime, mtime))
        return f

    def write_manifest(self, crawled_at="2026-06-16T16:22:04+08:00"):
        (self.project / "wjx_site").mkdir(exist_ok=True)
        (self.project / "wjx_site" / "manifest.json").write_text(
            json.dumps({"source": "https://www.wjx.cn", "crawled_at": crawled_at, "pages": []},
                       ensure_ascii=False), encoding="utf-8")


class DriftCriteria(FreshnessFixture):
    def test_index_not_behind_source_is_ok_even_if_old(self):
        """墙钟判据被禁用：索引很旧但源未变 → 必须 ok，不得判 stale。"""
        old = time.time() - 400 * 86400  # 400 天前
        self.write_index(build_at="2026-01-01T00:00:00+08:00", source_mtime=old)
        self.touch_source(mtime=old)
        r = run_cli(["--json"], self.kb)
        data = json.loads(r.stdout)
        self.assertEqual(data["verdict"], "ok")
        self.assertFalse(data["stale"])
        self.assertGreater(data["index_age_days"], 30)  # 旧但漂移判据通过

    def test_source_newer_than_index_is_stale_drift(self):
        build_epoch = time.time() - 86400
        self.write_index(build_at="2026-11-17T00:00:00+08:00", source_mtime=build_epoch)
        self.touch_source()  # 刚写入 → mtime 晚于 source_mtime
        r = run_cli(["--json"], self.kb)
        data = json.loads(r.stdout)
        self.assertEqual(data["verdict"], "stale_drift")
        self.assertTrue(data["stale"])
        self.assertIn("关键词通道", data["note"])

    def test_crawled_at_is_data_as_of_only(self):
        self.write_index(build_at="2026-11-17T00:00:00+08:00",
                         source_mtime=time.time() - 60)
        self.touch_source(mtime=time.time() - 120)
        self.write_manifest(crawled_at="2026-06-16T16:22:04+08:00")
        # manifest 本身也是源文件：夹具里把它压到索引 source_mtime 之前，
        # 否则"刚写入的 manifest"会被漂移判据正确地判为 stale（判据没错，是夹具错）。
        m = self.project / "wjx_site" / "manifest.json"
        os.utime(m, (time.time() - 120, time.time() - 120))
        r = run_cli(["--json"], self.kb)
        data = json.loads(r.stdout)
        self.assertEqual(data["verdict"], "ok")          # 快照旧不影响判定
        self.assertEqual(data["site_data_as_of"], "2026-06-16T16:22:04+08:00")

    def test_missing_index_reports_missing_not_stale(self):
        self.touch_source()
        r = run_cli(["--json"], self.kb)
        data = json.loads(r.stdout)
        self.assertEqual(data["verdict"], "missing_index")
        self.assertIn("关键词通道仍可用", data["note"])


class FailOpenContract(unittest.TestCase):
    def test_missing_kb_exits_2_with_degrade_hint(self):
        r = run_cli(["--kb", "Z:/definitely/not/exist"])
        self.assertEqual(r.returncode, 2)
        self.assertIn("kb_missing", r.stdout)
        self.assertIn("不得凭记忆补契约", r.stdout)
        self.assertIn("不阻断", r.stdout)

    def test_env_fallback_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            kb = Path(tmp) / "user_knowledge"
            (kb / "project" / "_vector_index").mkdir(parents=True)
            (kb / "project" / "_vector_index" / "info.json").write_text(
                json.dumps({"build_at": "2026-11-17T00:00:00+08:00",
                            "source_mtime": time.time()}), encoding="utf-8")
            r = run_cli(["--json"], kb)
            data = json.loads(r.stdout)
            self.assertEqual(data["resolved_by"], "KB_PATH 环境变量")
            self.assertEqual(data["verdict"], "ok")


if __name__ == "__main__":
    unittest.main()
