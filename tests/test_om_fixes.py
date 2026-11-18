"""v1.14.1 OM1-OM6 修复回归：工作区 .gitignore / evidence_refs / plan_vs_actual / 路由样本集。"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG / "scripts"))
sys.path.insert(0, str(PKG / "skills" / "requirement-review" / "scripts"))
sys.path.insert(0, str(PKG / "skills" / "case-generator" / "scripts"))

CASES = PKG / "skills" / "case-generator" / "tests" / "fixtures" / "cases-sample-12.json"
REVIEW = PKG / "skills" / "case-generator" / "tests" / "fixtures" / "review-sample-pass.json"


def run(script, *args):
    return subprocess.run([sys.executable, str(script), *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


class TestOM1WorkspaceGitignore(unittest.TestCase):
    def test_gitignore_written_and_append_semantics(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ctx = tmp / "ctx.json"
            ctx.write_text(json.dumps({"target_url": "https://t.example.com", "environment": "测试环境",
                                       "application_version": "v1", "dependency_mode": "real",
                                       "feature_flags": {}, "role_scope": ["主账号"]}), encoding="utf-8")
            ws = tmp / "ws"
            p = run(PKG / "scripts" / "execution_ledger.py", "init", ws, str(CASES), str(ctx), "R1")
            self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-300:])
            gi = ws / ".gitignore"
            self.assertTrue(gi.exists(), "工作区应自动生成 .gitignore")
            text = gi.read_text(encoding="utf-8")
            self.assertIn(".auth/", text)
            self.assertIn("*.png", text)
            # 追加语义：已有自定义行保留、规则不重复
            gi.write_text(text + "custom.log\n", encoding="utf-8")
            import execution_ledger as el
            el._ensure_gitignore(ws)
            after = gi.read_text(encoding="utf-8")
            self.assertIn("custom.log", after)
            self.assertEqual(after.count(".auth/"), 1)


class TestOM3EvidenceRefs(unittest.TestCase):
    @staticmethod
    def _review():
        return json.loads(REVIEW.read_text(encoding="utf-8-sig"))

    def test_evidence_refs_valid_passes(self):
        import review_check as rc
        data = self._review()
        data["acceptance"][0]["evidence_refs"] = [{"source": "需求正文第2.1节", "quote": "支持按类型筛选回收站问卷"}]
        errors, _, _ = rc.check(data)
        self.assertEqual(errors, [])

    def test_evidence_refs_missing_quote_fails(self):
        import review_check as rc
        data = self._review()
        data["acceptance"][0]["evidence_refs"] = [{"source": "需求正文第2.1节"}]
        errors, _, _ = rc.check(data)
        self.assertTrue(any("evidence_refs[0].quote" in e for e in errors), errors)


class TestOM4PlanVsActual(unittest.TestCase):
    def test_aggregate_reports_plan_vs_actual(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            review = tmp / "review.json"
            review.write_text(json.dumps(json.loads(REVIEW.read_text(encoding="utf-8-sig")), ensure_ascii=False),
                              encoding="utf-8")
            wd = tmp / "wb"
            p = run(PKG / "skills" / "case-generator" / "scripts" / "batch.py", "plan",
                    "--review", str(review), "--workdir", str(wd), "--task-id", "T-OM4")
            self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-300:])
            state = json.loads((wd / "batches.json").read_text(encoding="utf-8"))
            self.assertEqual(state.get("estimated_total"), 12)
            unit = state["batches"][0]["unit_id"]
            run(PKG / "skills" / "case-generator" / "scripts" / "batch.py", "start", "--workdir", str(wd), "--unit", unit)
            run(PKG / "skills" / "case-generator" / "scripts" / "batch.py", "finish", "--workdir", str(wd),
                "--unit", unit, "--cases", str(CASES), "--gate", "pass")
            p = run(PKG / "skills" / "case-generator" / "scripts" / "batch.py", "aggregate",
                    "--workdir", str(wd), "--cases", str(CASES))
            self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-300:])
            report = json.loads((wd / "aggregate-report.json").read_text(encoding="utf-8"))
            pva = report.get("plan_vs_actual", {})
            self.assertEqual(pva.get("estimated"), 12)
            self.assertEqual(pva.get("actual"), 12)
            self.assertEqual(pva.get("ratio"), 1.0)
            self.assertIsNone(pva.get("warning"))


class TestOM6RoutePrompts(unittest.TestCase):
    def test_structure_and_coverage(self):
        data = json.loads((PKG / "references" / "route-prompts.json").read_text(encoding="utf-8-sig"))
        prompts = data["prompts"]
        self.assertGreaterEqual(len(prompts), 8)
        ids = [p["id"] for p in prompts]
        self.assertEqual(len(ids), len(set(ids)))
        valid = {"main", "requirement-review", "case-generator", "api-test", "figma-ui-parity", "pipeline"}
        for p in prompts:
            self.assertIn(p["expected_skill"], valid, p["id"])
            for key in ("prompt", "expected_path", "category"):
                self.assertTrue(p.get(key), f"{p['id']} 缺 {key}")
        categories = {p["category"] for p in prompts}
        for need in ("需求评审", "用例生成", "接口专项", "设计还原", "无设计稿走查", "串行流水线"):
            self.assertIn(need, categories)


if __name__ == "__main__":
    unittest.main()