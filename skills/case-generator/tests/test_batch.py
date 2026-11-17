"""batch.py 分批调度、状态机、指纹与汇总测试（阶段 3 验收）。"""

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _util import CASES_12, REVIEW_PASS, SCRIPTS, load  # noqa: E402

import review_bridge as rb  # noqa: E402  (确保 import 链可用)


def run_batch(*args):
    import subprocess
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "batch.py"), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def two_module_review():
    review = load(REVIEW_PASS)
    review["features"].append({
        "feature_id": "FP-05", "module_code": "M02", "name": "回收站导出",
        "description": "导出回收站清单", "source": "需求正文第2.5节",
    })
    review["modules"][0]["estimated_case_count"] = 22
    review["modules"].append({
        "module_code": "M02", "name": "导出", "feature_ids": ["FP-05"],
        "estimated_case_count": 18,
    })
    review["acceptance"].append({
        "acceptance_id": "AC-04", "requirement_id": "REQ-01", "feature_id": "FP-05",
        "required": True, "confirmation": "confirmed",
        "elements": {
            "scene": "问卷管理→回收站列表页", "object": "「导出」按钮",
            "condition": "回收站内已有记录", "focus": "导出文件行数与列表一致",
        },
        "oracle": "导出文件打开后行数与列表分页统计一致",
    })
    review["go_ahead"].append("FP-05")
    return review


def write_json(tmp, name, data):
    p = Path(tmp) / name
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


class TestPlan(unittest.TestCase):
    def test_plan_split_over_30(self):
        with tempfile.TemporaryDirectory() as tmp:
            review = write_json(tmp, "review.json", two_module_review())
            p = run_batch("plan", "--review", str(review), "--workdir", tmp, "--task-id", "T-TEST-01")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            state = json.loads((Path(tmp) / "batches.json").read_text(encoding="utf-8"))
            self.assertEqual([b["unit_id"] for b in state["batches"]], ["M01", "M02"])
            self.assertIn("分批阈值", state["split_reason"])
            self.assertTrue(list(Path(tmp).glob("cases-*.json")))
            self.assertTrue(list(Path(tmp).glob("scope-*.json")))

    def test_plan_single_below_15(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = run_batch("plan", "--review", str(REVIEW_PASS), "--workdir", tmp, "--task-id", "T-TEST-02")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            state = json.loads((Path(tmp) / "batches.json").read_text(encoding="utf-8"))
            self.assertEqual(len(state["batches"]), 1)
            self.assertIn("免分批下限", state["split_reason"])


class TestStateMachine(unittest.TestCase):
    def make_workdir(self, tmp):
        review = write_json(tmp, "review.json", load(REVIEW_PASS))
        p = run_batch("plan", "--review", str(review), "--workdir", tmp, "--task-id", "T-FLOW")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_start_finish_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make_workdir(tmp)
            p = run_batch("start", "--workdir", tmp, "--unit", "M01")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            state = json.loads((Path(tmp) / "batches.json").read_text(encoding="utf-8"))
            self.assertEqual(state["current"], "M01")
            p = run_batch("finish", "--workdir", tmp, "--unit", "M01",
                          "--cases", str(CASES_12), "--gate", "pass")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            state = json.loads((Path(tmp) / "batches.json").read_text(encoding="utf-8"))
            unit = state["batches"][0]
            self.assertEqual(unit["status"], "done")
            self.assertEqual(unit["gate"], "pass")
            self.assertEqual(len(unit["case_ids"]), 12)

    def test_start_rejects_parallel(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make_workdir(tmp)
            run_batch("start", "--workdir", tmp, "--unit", "M01")
            p = run_batch("start", "--workdir", tmp, "--unit", "M01")
            self.assertEqual(p.returncode, 1)

    def test_finish_requires_doing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make_workdir(tmp)
            p = run_batch("finish", "--workdir", tmp, "--unit", "M01",
                          "--cases", str(CASES_12), "--gate", "pass")
            self.assertEqual(p.returncode, 1)

    def test_resume_resets_interrupted(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make_workdir(tmp)
            run_batch("start", "--workdir", tmp, "--unit", "M01")
            p = run_batch("resume", "--workdir", tmp)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("M01", p.stdout)
            state = json.loads((Path(tmp) / "batches.json").read_text(encoding="utf-8"))
            self.assertEqual(state["batches"][0]["status"], "pending")
            self.assertEqual(state["current"], None)


class TestFingerprint(unittest.TestCase):
    def prepare(self, tmp):
        review_data = load(REVIEW_PASS)
        text_path = Path(tmp) / "requirement.txt"
        text_path.write_text("问卷回收站需求正文内容", encoding="utf-8")
        digest = "sha256:" + hashlib.sha256(text_path.read_bytes()).hexdigest()
        review_data["requirement"]["fingerprint"] = digest
        review = write_json(tmp, "review.json", review_data)
        p = run_batch("plan", "--review", str(review), "--workdir", tmp, "--task-id", "T-FP")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return text_path

    def test_fingerprint_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            text_path = self.prepare(tmp)
            p = run_batch("fingerprint", "--workdir", tmp,
                          "--requirement-text", str(text_path), "--review", str(Path(tmp) / "review.json"))
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_fingerprint_mismatch_resets(self):
        with tempfile.TemporaryDirectory() as tmp:
            text_path = self.prepare(tmp)
            run_batch("start", "--workdir", tmp, "--unit", "M01")
            text_path.write_text("需求正文被修改后的新内容", encoding="utf-8")
            p = run_batch("fingerprint", "--workdir", tmp,
                          "--requirement-text", str(text_path), "--review", str(Path(tmp) / "review.json"))
            self.assertEqual(p.returncode, 1)
            self.assertIn("全量重跑", p.stdout)
            state = json.loads((Path(tmp) / "batches.json").read_text(encoding="utf-8"))
            self.assertTrue(all(b["status"] == "pending" and not b["case_ids"] for b in state["batches"]))


class TestAggregate(unittest.TestCase):
    def test_aggregate_requires_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            review = write_json(tmp, "review.json", load(REVIEW_PASS))
            run_batch("plan", "--review", str(review), "--workdir", tmp, "--task-id", "T-AGG")
            p = run_batch("aggregate", "--workdir", tmp, "--cases", str(CASES_12))
            self.assertEqual(p.returncode, 1)

    def test_aggregate_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            review = write_json(tmp, "review.json", load(REVIEW_PASS))
            run_batch("plan", "--review", str(review), "--workdir", tmp, "--task-id", "T-AGG2")
            run_batch("start", "--workdir", tmp, "--unit", "M01")
            p = run_batch("finish", "--workdir", tmp, "--unit", "M01",
                          "--cases", str(CASES_12), "--gate", "pass")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            p = run_batch("aggregate", "--workdir", tmp, "--cases", str(CASES_12))
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            report = json.loads((Path(tmp) / "aggregate-report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["counts"]["testcases"], 12)
            self.assertNotEqual(report["status"], "fail")


if __name__ == "__main__":
    unittest.main()