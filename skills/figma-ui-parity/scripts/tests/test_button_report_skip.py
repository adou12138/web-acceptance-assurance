"""按钮报告对「跳过」项的判定锁（纯 Python 驱动 node 脚本，真实产物断言）。

为什么要单独锁这条：跳过项没有 `clicked` / `effect` 字段，而 verdictOf 的历史分支
是「没有 effect → 点击无响应 / 高等级」。于是「按策略没点」和「点了没反应」在
报告里长得一模一样。实测后果：状态漂移一次就让 6 个按钮变成高等级假缺陷。

本测试用最小 fixture 跑真实的 gen_button_report.cjs，只断言对外可见的 CSV 结论，
不复制生产逻辑。
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent


def _run(cmd, cwd=None):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, cwd=cwd)


class ButtonReportSkipVerdictTests(unittest.TestCase):

    def _build(self, tmp: Path) -> Path:
        page_dir = tmp / "p1"
        page_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "url": "https://x.test/", "count": 3, "uniqueCount": 3, "hiddenCount": 0,
            "clicked": 1, "clickFailed": 0, "skipped": 2,
            "clickResults": [
                {"sel": "button.a", "nth": 1, "text": "正常按钮", "risk": "safe", "clicked": True,
                 "effect": {"effective": True, "signals": ["dom"]}},
                {"sel": "button.b", "text": "漂移按钮", "risk": "safe", "skipped": True,
                 "stateDrift": True,
                 "reason": "状态漂移：前序操作改变了页面状态，目标元素在当前页面已不存在",
                 "clickable": {"ok": True}},
                {"sel": "button.c", "text": "按策略跳过", "risk": "danger", "skipped": True,
                 "reason": "danger 级未点击：未取得 all + testEnvironmentConfirmed 授权",
                 "clickable": {"ok": True}},
            ],
            "static": [
                {"sel": "button.a", "nth": 1, "text": "正常按钮", "risk": "safe",
                 "clickable": {"ok": True, "blocks": []}, "bbox": {"x": 10, "y": 20, "w": 80, "h": 32}},
                {"sel": "button.b", "text": "漂移按钮", "risk": "safe",
                 "clickable": {"ok": True, "blocks": []}, "bbox": {"x": 10, "y": 60, "w": 80, "h": 32}},
                {"sel": "button.c", "text": "按策略跳过", "risk": "danger",
                 "clickable": {"ok": True, "blocks": []}, "bbox": {"x": 10, "y": 100, "w": 80, "h": 32}},
            ],
            "noEffect": [], "genericClickables": [], "containers": [],
        }
        (page_dir / "button_audit.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        out = tmp / "out"
        r = _run(["node", SCRIPTS / "gen_button_report.cjs", "--dir", tmp, "--out", out])
        self.assertEqual(r.returncode, 0, (r.stdout or "")[-400:] + (r.stderr or "")[-400:])
        return out

    def _rows(self, out: Path):
        rows = {}
        with (out / "button-audit.csv").open(encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                rows[row["按钮文案"]] = row
        return rows

    def test_state_drift_skip_is_not_reported_as_no_response(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rows = self._rows(self._build(Path(tmpdir)))
        drift = rows["漂移按钮"]
        self.assertEqual(drift["结论"], "跳过(状态漂移)", drift)
        self.assertNotEqual(drift["结论"], "点击无响应",
                            "状态漂移被报成「点击无响应」= 高等级假缺陷")
        self.assertEqual(drift["等级"], "待确认", "跳过项不得升为高等级结论")

    def test_policy_skip_is_not_reported_as_defect(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rows = self._rows(self._build(Path(tmpdir)))
        skipped = rows["按策略跳过"]
        self.assertEqual(skipped["结论"], "跳过", skipped)
        self.assertEqual(skipped["等级"], "待确认")

    def test_nonzero_nth_still_pairs_with_click_result(self):
        """配对键是 sel#nth：nth 缺失/不匹配会让点过的按钮显示成「未验证」。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            rows = self._rows(self._build(Path(tmpdir)))
        row = rows["正常按钮"]
        self.assertEqual(row["点击结果"], "成功", row)
        self.assertEqual(row["结论"], "正常", row)

    def test_normal_button_still_passes(self):
        """反向用例：修复不得把所有项都变成跳过。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            rows = self._rows(self._build(Path(tmpdir)))
        self.assertEqual(rows["正常按钮"]["结论"], "正常", rows["正常按钮"])


class ResponseTimingReliabilityTests(unittest.TestCase):
    """全 0ms 的时延样本必须被判为不可用（页面持续变动时的背景噪声）。"""

    def _summarize(self, samples):
        script = (
            "const T=require('./node/response_timer.cjs');"
            "const s=%s;"
            "process.stdout.write(JSON.stringify(T.summarize(s.map(v=>({clicked:true,first:{elapsedMs:v}})))));"
            % json.dumps(samples)
        )
        r = _run(["node", "-e", script], cwd=SCRIPTS)
        self.assertEqual(r.returncode, 0, (r.stderr or "")[-300:])
        return json.loads(r.stdout)

    def test_all_zero_samples_are_unreliable(self):
        out = self._summarize([0, 0, 0, 0])
        self.assertFalse(out["reliable"], out)
        self.assertIn("背景噪声", out["unreliableReason"])

    def test_normal_samples_stay_reliable(self):
        out = self._summarize([120, 2400, 300])
        self.assertTrue(out["reliable"], out)
        self.assertEqual(out["max"], 2400)

    def test_single_sample_does_not_claim_unreliable(self):
        """样本不足时只能存疑，不能断言退化——否则一次点击就把整个维度判死。"""
        out = self._summarize([0])
        self.assertTrue(out["reliable"], out)


class StateDriftGuardContractTests(unittest.TestCase):
    """状态漂移防线必须在点击前生效，且跳过项带可辨识标记。"""

    def test_guard_counts_elements_before_clicking(self):
        src = (SCRIPTS / "node" / "run_button_audit.cjs").read_text(encoding="utf-8")
        self.assertIn("stateDrift", src)
        self.assertIn("locator(b.sel).count()", src)
        # 必须是「跳过」而不是「点击失败」语义
        self.assertIn("未取得可信点击结论", src)
        self.assertIn("skipped = true", src)


if __name__ == "__main__":
    unittest.main()
