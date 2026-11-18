"""独立盲测工具回归：答案隔离、计分、误报、证据、分母与对照。

对应协议 `docs/evals-baseline/detection-protocol.md`：执行方只见题面，
答案键留在评测者手里；计分只认可复核输入。
"""
import copy
import json
import shutil
import sys
import tempfile
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "docs" / "evals-baseline" / "detection-fixtures"
sys.path.insert(0, str(ROOT / "scripts"))

import blind_eval as be  # noqa: E402


def fixture(name="01-boundary-form.json"):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8-sig"))


class PrepareIsolation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "run01"

    def test_spec_has_no_answers(self):
        result = be.prepare(FIXTURES / "01-boundary-form.json", self.out)
        spec = result["spec"]
        text = json.dumps(spec, ensure_ascii=False)
        for marker in ("hidden_answers", "legit_near_misses", "match_all", "match_any", "BA-1", "LN-1"):
            self.assertNotIn(marker, text)
        self.assertIn("required_branches", spec)
        self.assertEqual(result["run"]["executor_saw_answers"], False)

    def test_existing_content_refused(self):
        self.out.mkdir(parents=True)
        (self.out / "stale.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(ValueError):
            be.prepare(FIXTURES / "01-boundary-form.json", self.out)


class VerifyCatchesLeaks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "run"
        be.prepare(FIXTURES / "01-boundary-form.json", self.out)

    def test_clean_run_passes(self):
        self.assertEqual(be.verify_run(self.out, FIXTURES / "01-boundary-form.json"), [])

    def test_key_file_in_run_dir_detected(self):
        shutil.copy(FIXTURES / "01-boundary-form.json", self.out / "case.key.json")
        issues = be.verify_run(self.out)
        self.assertTrue(any("答案文件" in i for i in issues), issues)

    def test_answer_ids_in_findings_detected(self):
        (self.out / "findings.json").write_text(json.dumps({"findings": [{"id": "BA-1"}]}), encoding="utf-8")
        issues = be.verify_run(self.out, FIXTURES / "01-boundary-form.json")
        self.assertTrue(any("答案 ID" in i for i in issues), issues)

    def test_seen_answers_declared_blocks(self):
        run = json.loads((self.out / "run.json").read_text(encoding="utf-8"))
        run["executor_saw_answers"] = True
        (self.out / "run.json").write_text(json.dumps(run), encoding="utf-8")
        self.assertTrue(any("saw_answers" in i for i in be.verify_run(self.out)))


class Scoring(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "run"
        self.fixture_path = FIXTURES / "01-boundary-form.json"
        be.prepare(self.fixture_path, self.out)
        for name in ("evidence/21.png", "evidence/dup.png", "evidence/keep.png"):
            path = self.out / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fake-bytes")

    def payload(self):
        branches = [{"name": b, "status": "EXECUTED"} for b in fixture()["required_branches"]]
        return {
            "executor": {"name": "agent-a", "tooling": "browser", "saw_answers": False},
            "budget": {"minutes": 30},
            "branches": branches,
            "findings": [
                {"id": "F-1", "title": "长度 21 未拒绝", "observed": "输入 21 字符保存成功，判据未拦截",
                 "severity": "major", "evidence": ["evidence/21.png"]},
                {"id": "F-2", "title": "重复提交生成两条记录", "observed": "连续点击两次得到两条记录，幂等缺失",
                 "severity": "blocker", "evidence": ["evidence/dup.png"]},
                {"id": "F-3", "title": "校验失败后输入未保留", "observed": "提示后输入框被清空，需重新输入",
                 "severity": "normal", "evidence": ["evidence/keep.png"]},
            ],
            "obligations_untested": [],
            "invalid_actions": 0,
        }

    def write(self, payload):
        path = self.out / "findings.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def test_full_detection_passes(self):
        result = be.score_run(self.out, self.fixture_path, self.write(self.payload()))
        self.assertEqual(result["verdict"], "pass", result["reasons"])
        self.assertEqual(result["detection"]["blocker"]["detected"], 1)
        self.assertEqual(result["detection"]["major"]["detected"], 1)
        self.assertEqual(result["branch_rate"], 1.0)
        self.assertEqual(result["evidence_issues"], [])

    def test_missing_blocker_fails(self):
        payload = self.payload()
        payload["findings"] = [f for f in payload["findings"] if f["id"] != "F-2"]
        result = be.score_run(self.out, self.fixture_path, self.write(payload))
        self.assertEqual(result["verdict"], "fail")
        self.assertTrue(any("blocker 未全检出" in r for r in result["reasons"]))

    def test_near_miss_reported_as_defect_counts_false_positive(self):
        payload = self.payload()
        payload["findings"].append({"id": "F-9", "title": "空值提示文案与预期措辞不同",
                                    "observed": "文案写「请输入名称」而预期是「名称不能为空」",
                                    "severity": "normal", "evidence": ["evidence/21.png"]})
        result = be.score_run(self.out, self.fixture_path, self.write(payload))
        self.assertIn("F-9", result["near_miss_hits"])
        self.assertEqual(result["false_positive_categories"], {"copy_preference": ["F-9"]})
        self.assertTrue(any("误报" in r for r in result["reasons"]))

    def test_fabricated_evidence_fails(self):
        payload = self.payload()
        payload["findings"][0]["evidence"] = ["evidence/not-exist.png"]
        result = be.score_run(self.out, self.fixture_path, self.write(payload))
        self.assertTrue(result["evidence_issues"])
        self.assertEqual(result["verdict"], "fail")

    def test_renamed_branch_rejected(self):
        payload = self.payload()
        payload["branches"][0]["name"] = "只跑了正常路径"
        with self.assertRaises(ValueError):
            be.score_run(self.out, self.fixture_path, self.write(payload))

    def test_untested_obligation_fails(self):
        payload = self.payload()
        payload["obligations_untested"] = ["失败恢复后的读回未验证"]
        result = be.score_run(self.out, self.fixture_path, self.write(payload))
        self.assertEqual(result["verdict"], "fail")

    def test_simulated_evidence_does_not_count_as_detection(self):
        payload = self.payload()
        payload["findings"][0]["evidence_grade"] = "E2"   # 模拟执行
        result = be.score_run(self.out, self.fixture_path, self.write(payload))
        self.assertEqual(result["verdict"], "fail")
        self.assertTrue(any("E2" in i["why"] for i in result["evidence_issues"]))
        self.assertEqual(result["detection"]["major"]["detected"], 0)

    def test_leaked_run_refuses_scoring(self):
        shutil.copy(self.fixture_path, self.out / "leak.key.json")
        with self.assertRaises(ValueError):
            be.score_run(self.out, self.fixture_path, self.write(self.payload()))


class Compare(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.a = Path(self.tmp.name) / "a.json"
        self.b = Path(self.tmp.name) / "b.json"

    def base(self, run_id, detected, verdict="fail", executor="agent-a"):
        return {"run_id": run_id, "detected_total": detected, "answer_total": 3, "branch_rate": 0.8,
                "near_miss_hits": [], "obligations_untested": [], "verdict": verdict,
                "executor": {"name": executor}, "fixture_sha256": "f", "protocol_sha256": "p"}

    def test_same_executor_and_protocol_are_flagged(self):
        self.a.write_text(json.dumps(self.base("old", 1)), encoding="utf-8")
        self.b.write_text(json.dumps(self.base("new", 3, "pass")), encoding="utf-8")
        result = be.compare_scores(self.a, self.b)
        self.assertEqual(result["delta"]["detected_total"], 2)
        self.assertTrue(any("同一执行方" in w for w in result["warnings"]))
        self.assertTrue(result["comparable"])

    def test_protocol_change_marks_incomparable(self):
        self.a.write_text(json.dumps(self.base("old", 1)), encoding="utf-8")
        changed = self.base("new", 3, "pass", executor="agent-b")
        changed["protocol_sha256"] = "p2"
        self.b.write_text(json.dumps(changed), encoding="utf-8")
        result = be.compare_scores(self.a, self.b)
        self.assertFalse(result["comparable"])


if __name__ == "__main__":
    unittest.main()
