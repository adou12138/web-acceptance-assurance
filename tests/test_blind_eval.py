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

    def test_evidence_accepts_string_form(self):
        # 执行方常把 evidence 写成单个字符串；只认数组会让命中答案也不计检出
        payload = self.payload()
        payload["findings"][0]["evidence"] = "evidence/21.png"
        result = be.score_run(self.out, self.fixture_path, self.write(payload))
        self.assertEqual(result["evidence_issues"], [])
        self.assertEqual(result["detection"]["major"]["detected"], 1)
        self.assertEqual(result["verdict"], "pass", result["reasons"])

    def test_score_output_stays_out_of_run_dir_and_is_idempotent(self):
        # score.json 含答案 ID：留在 run_dir 会被判泄漏，且二次计分被拒
        first = be.score_run(self.out, self.fixture_path, self.write(self.payload()))
        self.assertFalse((self.out / "score.json").exists())
        self.assertTrue(Path(first["score_path"]).is_file())
        second = be.score_run(self.out, self.fixture_path, self.write(self.payload()))
        self.assertEqual(second["verdict"], first["verdict"])

    def test_structured_elements_join_match(self):
        # observed 用同义表达不含答案词元，但 expected/actual 含 → 应能命中
        payload = self.payload()
        payload["findings"][0] = {
            "id": "F-1", "title": "上界之外仍然放行", "observed": "点保存后列表多出一条",
            "expected": "长度 21 应被拒绝", "actual": "未拦截，仍然成功写入",
            "observed_via": "读取列表条数", "severity": "major",
            "evidence": ["evidence/21.png"],
        }
        result = be.score_run(self.out, self.fixture_path, self.write(payload))
        self.assertEqual(result["detection"]["major"]["detected"], 1)

    def test_unmatched_not_labelled_self_fabricated(self):
        payload = self.payload()
        payload["findings"].append({"id": "F-7", "title": "标题为空时页面无任何提示",
                                    "observed": "标题清空后点保存，页面没有反应",
                                    "severity": "normal", "evidence": ["evidence/21.png"]})
        result = be.score_run(self.out, self.fixture_path, self.write(payload))
        self.assertIn("F-7", result["unmatched_findings"])
        joined = " ".join(result["warnings"])
        self.assertIn("待人工复核", joined)
        self.assertNotIn("臆造", joined)


class AdversarialExtensions(unittest.TestCase):
    """对抗增量字段（可选、向后兼容）：reproduction/cost/unresolved_candidates 聚合。"""

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
                 "severity": "major", "evidence": ["evidence/21.png"],
                 "reproduction": {"attempts": 3, "successes": 2}, "cost": {"probes": 4, "minutes": 6}},
                {"id": "F-2", "title": "重复提交生成两条记录", "observed": "连续点击两次得到两条记录，幂等缺失",
                 "severity": "blocker", "evidence": ["evidence/dup.png"],
                 "reproduction": {"attempts": 1, "successes": 1}, "cost": {"probes": 2, "minutes": 4}},
                {"id": "F-3", "title": "校验失败后输入未保留", "observed": "提示后输入框被清空，需重新输入",
                 "severity": "normal", "evidence": ["evidence/keep.png"]},
            ],
            "obligations_untested": [],
            "unresolved_candidates": 2,
            "invalid_actions": 0,
        }

    def write(self, payload):
        path = self.out / "findings.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def test_extensions_aggregated(self):
        result = be.score_run(self.out, self.fixture_path, self.write(self.payload()))
        self.assertEqual(result["verdict"], "pass", result["reasons"])
        self.assertEqual(result["reproduction"], {"attempts": 4, "successes": 3, "rate": 0.75})
        self.assertEqual(result["cost"], {"probes": 6, "minutes": 10.0})
        self.assertEqual(result["unresolved_candidates"], 2)

    def test_unresolved_candidates_as_list_counts_entries(self):
        payload = self.payload()
        payload["unresolved_candidates"] = ["现象甲待裁定", "现象乙待裁定", "现象丙待裁定"]
        result = be.score_run(self.out, self.fixture_path, self.write(payload))
        self.assertEqual(result["unresolved_candidates"], 3)
        self.assertEqual(result["verdict"], "pass", result["reasons"])

    def test_backward_compatible_missing_fields_are_null(self):
        result = be.score_run(self.out, self.fixture_path, self.write(self.payload()))
        payload = self.payload()
        for f in payload["findings"]:
            f.pop("reproduction", None)
            f.pop("cost", None)
        payload.pop("unresolved_candidates", None)
        result = be.score_run(self.out, self.fixture_path, self.write(payload))
        # 缺省输出 null，缺失不当 0，也不影响 verdict
        self.assertIsNone(result["reproduction"])
        self.assertIsNone(result["cost"])
        self.assertIsNone(result["unresolved_candidates"])
        self.assertEqual(result["verdict"], "pass", result["reasons"])

    def test_malformed_reproduction_rejected(self):
        payload = self.payload()
        payload["findings"][0]["reproduction"] = {"attempts": 1, "successes": 3}
        with self.assertRaises(ValueError):
            be.score_run(self.out, self.fixture_path, self.write(payload))

    def test_negative_unresolved_candidates_rejected(self):
        payload = self.payload()
        payload["unresolved_candidates"] = -1
        with self.assertRaises(ValueError):
            be.score_run(self.out, self.fixture_path, self.write(payload))


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


class ABCompare(unittest.TestCase):
    """审核四 A2 回归：A/B 两臂聚合对照——聚合、可比性、派生指标与 NA 纪律。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.arm_a = Path(self.tmp.name) / "armA"
        self.arm_b = Path(self.tmp.name) / "armB"

    def score(self, arm, run_id, detected=1, detected_map=None, executor="agent-a",
              protocol="p", fixture="f", answer_total=3, near_misses=(),
              reproduction=None, cost=None, fp_categories=None):
        d = {"run_id": run_id,
             "detection": detected_map or {sev: {"detected": detected if sev == "blocker" else 0,
                                                 "total": 1, "missed": []}
                                           for sev in be.SEVERITIES},
             "detected_total": detected, "answer_total": answer_total,
             "near_miss_hits": list(near_misses),
             "false_positive_categories": fp_categories or {},
             "unmatched_findings": [], "obligations_untested": [], "invalid_actions": 0,
             "reproduction": reproduction, "cost": cost, "unresolved_candidates": 0,
             "verdict": "fail", "executor": {"name": executor},
             "fixture_sha256": fixture, "protocol_sha256": protocol}
        run_dir = arm / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "score.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        return run_dir / "score.json"

    def test_aggregates_multiple_runs_per_arm(self):
        self.score(self.arm_a, "run01", detected=1)
        self.score(self.arm_a, "run02", detected=2, executor="agent-a")
        self.score(self.arm_b, "run01", detected=2, executor="agent-b")
        self.score(self.arm_b, "run02", detected=1, executor="agent-b")
        result = be.compare_ab(self.arm_a, self.arm_b)
        self.assertEqual(result["arm_a"]["runs"], 2)
        self.assertEqual(result["arm_a"]["detected_total"], 3)
        self.assertEqual(result["delta"]["detected_total"], 0)
        self.assertTrue(result["comparable"])

    def test_unequal_run_counts_flag_answer_denominator(self):
        # 两臂 run 数不等 → 答案分母不同 → 检出计数不可直接相减，必须留痕
        self.score(self.arm_a, "run01", detected=1)
        self.score(self.arm_b, "run01", detected=1, executor="agent-b")
        self.score(self.arm_b, "run02", detected=1, executor="agent-b")
        result = be.compare_ab(self.arm_a, self.arm_b)
        self.assertTrue(any("答案分母不同" in w for w in result["warnings"]))
        self.assertFalse(result["comparable"])

    def test_collects_scores_written_under_scores_dir(self):
        # score_run 现在把结果写到 scores/<run>.json（run_dir 外）——ab 须能收集
        scores_dir = self.arm_a / "scores"
        scores_dir.mkdir(parents=True, exist_ok=True)
        (scores_dir / "realrun.json").write_text(json.dumps({
            "run_id": "realrun", "detected_total": 1, "answer_total": 3,
            "detection": {sev: {"detected": 1 if sev == "blocker" else 0, "total": 1, "missed": []}
                          for sev in be.SEVERITIES},
            "near_miss_hits": [], "false_positive_categories": {}, "unmatched_findings": [],
            "obligations_untested": [], "invalid_actions": 0, "reproduction": None, "cost": None,
            "unresolved_candidates": 0, "verdict": "fail", "executor": {"name": "agent-a"},
            "fixture_sha256": "f", "protocol_sha256": "p",
        }, ensure_ascii=False), encoding="utf-8")
        self.score(self.arm_b, "run01", executor="agent-b")
        result = be.compare_ab(self.arm_a, self.arm_b)
        self.assertEqual(result["arm_a"]["runs"], 1)
        self.assertEqual(result["arm_a"]["run_ids"], ["realrun"])

    def test_false_positive_aggregation_and_small_sample_precision_na(self):
        # 分母 = detected + fp = 3 < 10 → 只报计数，不给比例（协议纪律）
        self.score(self.arm_a, "run01", detected=2, near_misses=["NM-1"])
        self.score(self.arm_b, "run01", detected=3, executor="agent-b")
        result = be.compare_ab(self.arm_a, self.arm_b)
        self.assertEqual(result["arm_a"]["false_positives"], 1)
        self.assertEqual(result["delta"]["false_positives"], -1)
        self.assertEqual(result["derived"]["A"]["precision_proxy"], "NA")
        self.assertEqual(result["derived"]["A"]["precision_proxy_denominator"], 3)
        self.assertEqual(result["derived"]["B"]["precision_proxy"], "NA")

    def test_precision_proxy_reported_at_ten_samples(self):
        # 分母 = 8 + 2 = 10 → 达到样本下限，给比例并附分母
        self.score(self.arm_a, "run01", detected=8, near_misses=["NM-1", "NM-2"])
        self.score(self.arm_b, "run01", detected=8, executor="agent-b")
        result = be.compare_ab(self.arm_a, self.arm_b)
        self.assertEqual(result["derived"]["A"]["precision_proxy"], round(8 / 10, 4))
        self.assertEqual(result["derived"]["A"]["precision_proxy_denominator"], 10)

    def test_reproduction_and_cost_derived(self):
        self.score(self.arm_a, "run01", reproduction={"attempts": 4, "successes": 2},
                   cost={"probes": 20, "minutes": 30}, executor="agent-a")
        self.score(self.arm_b, "run01", executor="agent-b",
                   reproduction={"attempts": 6, "successes": 5},
                   cost={"probes": 40, "minutes": 45})
        result = be.compare_ab(self.arm_a, self.arm_b)
        self.assertEqual(result["derived"]["A"]["reproduction_rate"], 0.5)
        self.assertEqual(result["derived"]["B"]["effective_findings_per_100_probes"],
                         round(1 / 40 * 100, 2))

    def test_missing_cost_yields_na_not_zero(self):
        self.score(self.arm_a, "run01")
        self.score(self.arm_b, "run01", executor="agent-b")
        result = be.compare_ab(self.arm_a, self.arm_b)
        self.assertEqual(result["derived"]["A"]["effective_findings_per_100_probes"], "NA")
        self.assertEqual(result["derived"]["A"]["reproduction_rate"], "NA")

    def test_same_executor_across_arms_flagged(self):
        self.score(self.arm_a, "run01", executor="agent-x")
        self.score(self.arm_b, "run01", executor="agent-x")
        result = be.compare_ab(self.arm_a, self.arm_b)
        self.assertTrue(any("同一执行方" in w for w in result["warnings"]))

    def test_protocol_change_across_arms_incomparable(self):
        self.score(self.arm_a, "run01", protocol="p1")
        self.score(self.arm_b, "run01", protocol="p2", executor="agent-b")
        result = be.compare_ab(self.arm_a, self.arm_b)
        self.assertFalse(result["comparable"])

    def test_inconsistent_protocol_within_arm_flagged(self):
        self.score(self.arm_a, "run01", protocol="p1")
        self.score(self.arm_a, "run02", protocol="p2")
        self.score(self.arm_b, "run01", executor="agent-b", protocol="p1")
        result = be.compare_ab(self.arm_a, self.arm_b)
        self.assertTrue(any("臂 A 内协议哈希不一致" in w for w in result["warnings"]))

    def test_empty_arm_dir_rejected(self):
        self.arm_a.mkdir(parents=True)
        self.arm_b.mkdir(parents=True)
        with self.assertRaises(ValueError):
            be.compare_ab(self.arm_a, self.arm_b)


if __name__ == "__main__":
    unittest.main()
