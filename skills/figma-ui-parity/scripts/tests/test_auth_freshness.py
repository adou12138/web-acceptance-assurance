#!/usr/bin/env python3
"""_auth_freshness_snapshot 契约测试。

多份凭据并存且旧凭据被静默选中是 2026-10-29 轮假缺陷的直接成因，收口动作是
start_test.py 启动门禁扫描全部 .auth 目录出清单。本测试锁定四条契约：
1) 多目录并存且含超 24h 旧凭据 → warning 要求显式指定 PARITY_AUTH_DIR；
2) 全部凭据新鲜（<=24h）→ 无 warning；
3) 无 auth_info.json 的 .auth 目录跳过（不制造假条目）；
4) 损坏的 auth_info.json → error=unreadable 条目而非崩溃，且计入 stale
   （读不出年龄 = 不能证明新鲜，按"未证明=未通过"口径）。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

from start_test import _auth_freshness_snapshot  # noqa: E402


def _write_auth(root: Path, rel: str, saved_at_iso: str | None, corrupt: bool = False) -> Path:
    d = root / rel
    d.mkdir(parents=True, exist_ok=True)
    info = d / "auth_info.json"
    if corrupt:
        info.write_text("{not-json", encoding="utf-8")
    elif saved_at_iso is not None:
        info.write_text(json.dumps({"savedAt": saved_at_iso}), encoding="utf-8")
    return d


class AuthFreshnessSnapshotTests(unittest.TestCase):
    def test_multiple_dirs_with_stale_item_warns(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fresh = datetime.now(timezone.utc) - timedelta(hours=3)
            stale = datetime.now(timezone.utc) - timedelta(hours=907)
            _write_auth(root, "a/.auth", fresh.isoformat())
            old_dir = _write_auth(root, "agent-sojump/.auth", stale.isoformat())
            snap = _auth_freshness_snapshot(root)
            self.assertEqual(len(snap["dirs"]), 2)
            self.assertIn(str(old_dir), snap["staleDirs"])
            self.assertIsNotNone(snap["warning"], "多目录+旧凭据必须出声")
            self.assertIn("PARITY_AUTH_DIR", snap["warning"])

    def test_all_fresh_no_warning(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            recent = datetime.now(timezone.utc) - timedelta(hours=3)
            _write_auth(root, "a/.auth", recent.isoformat())
            snap = _auth_freshness_snapshot(root)
            self.assertEqual(snap["staleDirs"], [])
            self.assertIsNone(snap["warning"])

    def test_auth_dir_without_info_file_is_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_auth(root, "a/.auth", datetime.now(timezone.utc).isoformat())
            (root / "empty/.auth").mkdir(parents=True)  # 无 auth_info.json
            snap = _auth_freshness_snapshot(root)
            self.assertEqual(len(snap["dirs"]), 1, "无凭据文件的 .auth 不得计入清单")

    def test_corrupt_info_counts_as_stale_not_crash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            recent = datetime.now(timezone.utc) - timedelta(hours=1)
            _write_auth(root, "a/.auth", recent.isoformat())
            bad = _write_auth(root, "b/.auth", None, corrupt=True)
            snap = _auth_freshness_snapshot(root)
            entry = [e for e in snap["dirs"] if e["dir"] == str(bad)][0]
            self.assertEqual(entry.get("error"), "unreadable")
            self.assertIn(str(bad), snap["staleDirs"], "读不出年龄=未证明新鲜，必须进 stale")

    def test_unparseable_savedat_yields_null_age(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            d = _write_auth(root, "c/.auth", None)
            (d / "auth_info.json").write_text(json.dumps({"savedAt": "not-a-date"}), encoding="utf-8")
            snap = _auth_freshness_snapshot(root)
            self.assertIsNone(snap["dirs"][0]["ageHours"])
            self.assertIn(str(d), snap["staleDirs"])


if __name__ == "__main__":
    unittest.main()
