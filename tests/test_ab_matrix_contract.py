"""A/B 运行矩阵契约（M9）：配对评分、汇总口径与独立裁定。

要锁住的四件事：
1. **配对按（夹具，重复编号）**，不是臂内聚合——臂内聚合把不同环境的运行混在一起，
   配对差值就不再是"同一条件下换个做法"的差值；
2. **主指标先夹具内取均值、再夹具间等权汇总**，不得直接把所有 run 拉平求和；
3. **无效配对必须被识别并排除**（指纹不同/协议不同/预算不同/同一执行方）；
4. **独立裁定逐条覆盖**，漏一条即拒——否则误报率没有可复算的分母。
"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURES = ROOT / "docs" / "evals-baseline" / "detection-fixtures"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def score_payload(run_id, fixture_sha, protocol_sha, hits, severity_by_id,
                  executor="exec-1", budget=30, near_misses=None, unmatched=None):
    """构造一份 score.json 等价载荷。hits 为命中的答案 id 列表。"""
    detection = {}
    for severity in ("blocker", "major", "normal"):
        total = [aid for aid, sev in severity_by_id.items() if sev == severity]
        got = [aid for aid in total if aid in hits]
        detection[severity] = {"detected": len(got), "total": len(total),
                               "missed": sorted(set(total) - set(got))}
    return {
        "run_id": run_id,
        "scenario": "fixture",
        "holdout": False,
        "protocol_sha256": protocol_sha,
        "fixture_sha256": fixture_sha,
        "executor": {"name": executor},
        "budget_minutes": budget,
        "detection": detection,
        "detected_total": len(hits),
        "answer_total": len(severity_by_id),
        "near_miss_hits": list(near_misses or []),
        "unmatched_findings": list(unmatched or []),
        "verdict": "pass",
        "reasons": [],
        "warnings": [],
    }


class MatrixBase(unittest.TestCase):
    def setUp(self):
        self.be = load("blind_eval")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.results = self.folder / "results"
        self.protocol_sha = "proto-1"
        self.matrix = {
            "schema": "ab-run-matrix/1",
            "matrix_id": "TEST-M1",
            "frozen_at": "2026-11-21",
            "budget_minutes": 30,
            "fixtures": ["f1", "f2"],
            "arms": {"A": "基线", "B": "候选"},
            "repeats": 2,
            "runs": [
                {"fixture": "f1", "arm": "A", "repeat": 1, "run_id": "f1-A-1"},
                {"fixture": "f1", "arm": "B", "repeat": 1, "run_id": "f1-B-1"},
                {"fixture": "f1", "arm": "A", "repeat": 2, "run_id": "f1-A-2"},
                {"fixture": "f1", "arm": "B", "repeat": 2, "run_id": "f1-B-2"},
                {"fixture": "f2", "arm": "A", "repeat": 1, "run_id": "f2-A-1"},
                {"fixture": "f2", "arm": "B", "repeat": 1, "run_id": "f2-B-1"},
                {"fixture": "f2", "arm": "A", "repeat": 2, "run_id": "f2-A-2"},
                {"fixture": "f2", "arm": "B", "repeat": 2, "run_id": "f2-B-2"},
            ],
            "thresholds": {"primary": "主指标大于 0"},
        }
        self.severity = {"A1": "blocker", "A2": "major", "A3": "normal"}
        self.severity2 = {"B1": "blocker", "B2": "major"}
        self.fixture_sha = {"f1": "sha-f1", "f2": "sha-f2"}
        self.answers_by_fixture = {
            "f1": {"answers": dict(self.severity), "near_misses": 2, "sha256": "sha-f1"},
            "f2": {"answers": dict(self.severity2), "near_misses": 1, "sha256": "sha-f2"},
        }

    def write_score(self, fixture, arm, repeat, payload):
        path = self.results / fixture / arm / str(repeat) / "score.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def base_score(self, fixture, arm, repeat, hits, **kwargs):
        severity = self.severity if fixture == "f1" else self.severity2
        run_id = f"{fixture}-{arm}-{repeat}"
        # 协议要求：同一夹具两臂由不同执行方负责（同一执行方跨臂存在学习效应，
        # 配对独立性不成立）。默认按臂派生执行方，需要构造违规场景时显式传入。
        kwargs.setdefault("executor", f"exec-{arm}")
        protocol_sha = kwargs.pop("protocol_sha", self.protocol_sha)
        fixture_sha = kwargs.pop("fixture_sha", self.fixture_sha[fixture])
        payload = score_payload(run_id, fixture_sha, protocol_sha,
                                hits, severity, **kwargs)
        return self.write_score(fixture, arm, repeat, payload)

    def write_matrix(self, matrix=None):
        path = self.folder / "matrix.json"
        path.write_text(json.dumps(matrix or self.matrix, ensure_ascii=False),
                        encoding="utf-8")
        return path


class PairingContract(MatrixBase):
    def test_pairing_is_by_fixture_and_repeat(self):
        """配对键是（夹具，重复编号）。"""
        self.base_score("f1", "A", 1, [])
        self.base_score("f1", "B", 1, ["A1"])
        self.base_score("f1", "A", 2, [])
        self.base_score("f1", "B", 2, ["A1"])
        pair = self.be.pair_fixture(self.matrix, "f1", self.results,
                                    self.answers_by_fixture)
        self.assertEqual([p["repeat"] for p in pair["pairs"]], [1, 2])
        self.assertEqual(pair["pairs"][0]["run_a"], "f1-A-1")
        self.assertEqual(pair["pairs"][0]["run_b"], "f1-B-1")

    def test_primary_metric_averages_within_fixture_then_across(self):
        """主指标：夹具内取均值，夹具间等权汇总（不是所有 run 拉平求和）。"""
        # f1：A 命中 0，B 命中 1 → 每对差值 +1；两对都这样 → 夹具均值 1.0
        self.base_score("f1", "A", 1, [])
        self.base_score("f1", "B", 1, ["A1"])
        self.base_score("f1", "A", 2, [])
        self.base_score("f1", "B", 2, ["A1"])
        # f2：A 命中 1（B2），B 命中 2（B1+B2）→ 差值 +1；两对都这样 → 夹具均值 1.0
        self.base_score("f2", "A", 1, ["B2"])
        self.base_score("f2", "B", 1, ["B1", "B2"])
        self.base_score("f2", "A", 2, ["B2"])
        self.base_score("f2", "B", 2, ["B1", "B2"])
        pair_f1 = self.be.pair_fixture(self.matrix, "f1", self.results,
                                       self.answers_by_fixture)
        pair_f2 = self.be.pair_fixture(self.matrix, "f2", self.results,
                                       self.answers_by_fixture)
        self.assertEqual(pair_f1["mean_delta_bm"], 1.0)
        self.assertEqual(pair_f2["mean_delta_bm"], 1.0)
        equal_weight = (pair_f1["mean_delta_bm"] + pair_f2["mean_delta_bm"]) / 2
        self.assertEqual(equal_weight, 1.0)

    def test_detail_classifies_hits_by_arm(self):
        """缺陷级明细：新增命中 / 共同命中 / A 有 B 无 / 两臂皆漏。"""
        self.base_score("f1", "A", 1, ["A1", "A2"])
        self.base_score("f1", "B", 1, ["A2", "A3"])
        self.base_score("f1", "A", 2, ["A1", "A2"])
        self.base_score("f1", "B", 2, ["A2", "A3"])
        pair = self.be.pair_fixture(self.matrix, "f1", self.results,
                                    self.answers_by_fixture)
        detail = pair["detail"]
        self.assertEqual([d["id"] for d in detail["newly_detected"]], ["A3"])
        self.assertEqual([d["id"] for d in detail["common"]], ["A2"])
        self.assertEqual([d["id"] for d in detail["only_a"]], ["A1"])
        self.assertEqual([d["id"] for d in detail["missed_both"]], [])
        self.assertEqual(detail["newly_detected"][0]["severity"], "normal")

    def test_invalid_pairs_are_excluded_and_explained(self):
        """无效配对必须被识别、排除并给出原因。"""
        self.base_score("f1", "A", 1, [])
        self.base_score("f1", "B", 1, ["A1"], protocol_sha="proto-2")
        self.base_score("f1", "A", 2, [])
        self.base_score("f1", "B", 2, ["A1"])
        pair = self.be.pair_fixture(self.matrix, "f1", self.results,
                                    self.answers_by_fixture)
        self.assertEqual(pair["valid_pairs"], 1)
        invalid = [p for p in pair["pairs"] if not p["valid"]]
        self.assertEqual(len(invalid), 1)
        self.assertTrue(any("协议哈希不同" in r for r in invalid[0]["invalid_reasons"]))
        self.assertFalse(pair["valid"])
    def test_same_executor_invalidates_pair(self):
        """同一执行方跨两臂 → 学习效应，配对独立性不成立。"""
        self.base_score("f1", "A", 1, [], executor="same")
        self.base_score("f1", "B", 1, ["A1"], executor="same")
        pair = self.be.pair_fixture(self.matrix, "f1", self.results,
                                    self.answers_by_fixture)
        self.assertEqual(pair["valid_pairs"], 0)
        self.assertTrue(any("学习效应" in r
                            for r in pair["pairs"][0]["invalid_reasons"]))

    def test_budget_mismatch_invalidates_pair(self):
        """两臂预算不同 → 不是同一条件下的对照。"""
        self.base_score("f1", "A", 1, [], budget=30)
        self.base_score("f1", "B", 1, ["A1"], budget=60)
        pair = self.be.pair_fixture(self.matrix, "f1", self.results,
                                    self.answers_by_fixture)
        self.assertEqual(pair["valid_pairs"], 0)
        self.assertTrue(any("预算不一致" in r
                            for r in pair["pairs"][0]["invalid_reasons"]))

    def test_missing_run_is_recorded_as_invalid(self):
        """缺一侧 run → 记为无效配对，不得静默按半个配对算。"""
        self.base_score("f1", "A", 1, [])
        pair = self.be.pair_fixture(self.matrix, "f1", self.results,
                                    self.answers_by_fixture)
        self.assertEqual(pair["valid_pairs"], 0)
        self.assertTrue(pair["invalid_pairs"])
        self.assertIn("缺少计分结果", pair["invalid_pairs"][0]["reason"])


class AdjudicationContract(MatrixBase):
    def test_adjudication_must_cover_every_unmatched_finding(self):
        """裁定必须逐条覆盖未匹配发现，漏一条即拒。"""
        score_path = self.base_score("f1", "A", 1, [],
                                     unmatched=[{"id": "F-1"}, {"id": "F-2"}])
        path = self.folder / "adj.json"
        path.write_text(json.dumps({"adjudications": [
            {"finding_id": "F-1", "decision": "valid", "reason": "答案键漏了"}]}),
            encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            self.be.adjudicate_findings(str(score_path), str(path))
        self.assertIn("未裁定", str(ctx.exception))

    def test_adjudication_cannot_target_matched_finding(self):
        """机械已命中的发现不得被裁定为无效。"""
        score_path = self.base_score("f1", "A", 1, ["A1"],
                                     unmatched=[{"id": "F-1"}])
        path = self.folder / "adj.json"
        path.write_text(json.dumps({"adjudications": [
            {"finding_id": "A1", "decision": "false_positive", "reason": "x"}]}),
            encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            self.be.adjudicate_findings(str(score_path), str(path))
        self.assertIn("不在本次未匹配发现列表内", str(ctx.exception))

    def test_adjudication_rejects_unknown_decision_and_missing_reason(self):
        score_path = self.base_score("f1", "A", 1, [], unmatched=[{"id": "F-1"}])
        path = self.folder / "adj.json"
        path.write_text(json.dumps({"adjudications": [
            {"finding_id": "F-1", "decision": "maybe", "reason": "x"}]}),
            encoding="utf-8")
        with self.assertRaises(ValueError):
            self.be.adjudicate_findings(str(score_path), str(path))
        path.write_text(json.dumps({"adjudications": [
            {"finding_id": "F-1", "decision": "valid", "reason": "  "}]}),
            encoding="utf-8")
        with self.assertRaises(ValueError):
            self.be.adjudicate_findings(str(score_path), str(path))

    def test_false_positives_are_deduped_by_root_cause(self):
        """同一根因的重复发现归并：误报分子按根因去重。"""
        score_path = self.base_score("f1", "A", 1, [],
                                     unmatched=[{"id": "F-1"}, {"id": "F-2"}])
        path = self.folder / "adj.json"
        path.write_text(json.dumps({"adjudications": [
            {"finding_id": "F-1", "decision": "false_positive",
             "reason": "同一根因", "root_cause": "RC-1"},
            {"finding_id": "F-2", "decision": "false_positive",
             "reason": "同一根因", "root_cause": "RC-1"}]}),
            encoding="utf-8")
        result = self.be.adjudicate_findings(str(score_path), str(path))
        self.assertEqual(result["counts"]["false_positive"], 2)
        self.assertEqual(result["false_positives_deduped"], 1)
        self.assertTrue(Path(result["adjudication_path"]).is_file())


class MatrixSummaryContract(MatrixBase):
    """汇总层：用真实夹具目录跑通 CLI。"""

    def _run_matrix(self, matrix_path, results, extra=None):
        out_csv = self.folder / "summary.csv"
        cmd = [sys.executable, str(SCRIPTS / "blind_eval.py"), "matrix",
               "--matrix", str(matrix_path), "--results", str(results),
               "--fixtures-dir", str(FIXTURES), "--out-csv", str(out_csv)]
        cmd.extend(extra or [])
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        return proc, out_csv

    def test_cli_matrix_reports_invalid_pairs_and_warnings(self):
        matrix = json.loads(json.dumps(self.matrix))
        matrix["fixtures"] = ["01-boundary-form"]
        matrix["runs"] = [
            {"fixture": "01-boundary-form", "arm": "A", "repeat": 1, "run_id": "01-A-1"},
            {"fixture": "01-boundary-form", "arm": "B", "repeat": 1, "run_id": "01-B-1"},
        ]
        matrix_path = self.write_matrix(matrix)
        fixture_sha = self.be.sha256_path(FIXTURES / "01-boundary-form.json")
        severity = {"BA-1": "major", "BA-2": "blocker", "BA-3": "normal"}
        # 两臂夹具指纹不同 → 配对无效，必须被排除并写明原因
        self.write_score("01-boundary-form", "A", 1,
                         score_payload("01-A-1", fixture_sha, "p1", [], severity,
                                       executor="exec-A"))
        self.write_score("01-boundary-form", "B", 1,
                         score_payload("01-B-1", "sha-other", "p1", ["BA-2"], severity,
                                       executor="exec-B"))
        proc, out_csv = self._run_matrix(matrix_path, self.results)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        summary = json.loads(proc.stdout)
        self.assertEqual(summary["primary_metric"]["denominator"], 0)
        self.assertIsNone(summary["primary_metric"]["value"])
        self.assertFalse(summary["comparable"])
        self.assertTrue(summary["invalid_pairs"])
        self.assertTrue(any("无效配对" in w for w in summary["warnings"]))
        self.assertTrue(any("无有效配对" in w for w in summary["warnings"]))
        # 可复算 CSV 必须是 UTF-8 BOM
        raw = out_csv.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        text = raw.decode("utf-8-sig")
        self.assertIn("01-boundary-form", text)
        self.assertIn("mean_delta_bm", text)

    def test_cli_pair_runs_against_real_fixtures(self):
        matrix = json.loads(json.dumps(self.matrix))
        matrix["fixtures"] = ["01-boundary-form"]
        matrix["runs"] = [
            {"fixture": "01-boundary-form", "arm": "A", "repeat": 1, "run_id": "01-A-1"},
            {"fixture": "01-boundary-form", "arm": "B", "repeat": 1, "run_id": "01-B-1"},
        ]
        matrix_path = self.write_matrix(matrix)
        fixture_sha = self.be.sha256_path(FIXTURES / "01-boundary-form.json")
        severity = {"BA-1": "major", "BA-2": "blocker", "BA-3": "normal"}
        self.write_score("01-boundary-form", "A", 1,
                         score_payload("01-A-1", fixture_sha, "p1", ["BA-1"], severity,
                                       executor="exec-A"))
        self.write_score("01-boundary-form", "B", 1,
                         score_payload("01-B-1", fixture_sha, "p1",
                                       ["BA-1", "BA-2"], severity,
                                       executor="exec-B"))
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "blind_eval.py"), "pair",
             "--matrix", str(matrix_path), "--fixture", "01-boundary-form",
             "--results", str(self.results), "--fixtures-dir", str(FIXTURES)],
            capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        pair = json.loads(proc.stdout)
        self.assertEqual(pair["valid_pairs"], 1)
        self.assertEqual(pair["mean_delta_bm"], 1.0)
        self.assertEqual([d["id"] for d in pair["detail"]["newly_detected"]], ["BA-2"])

    def test_summary_denominator_excludes_insufficient_fixture(self):
        """部分配对无效的夹具标为证据不足，不计入汇总分母。"""
        matrix = json.loads(json.dumps(self.matrix))
        matrix["fixtures"] = ["01-boundary-form", "02-role-state-flow"]
        matrix["runs"] = []
        for fixture in matrix["fixtures"]:
            for arm in ("A", "B"):
                for repeat in (1, 2):
                    matrix["runs"].append({"fixture": fixture, "arm": arm,
                                           "repeat": repeat,
                                           "run_id": f"{fixture}-{arm}-{repeat}"})
        matrix_path = self.write_matrix(matrix)
        sha01 = self.be.sha256_path(FIXTURES / "01-boundary-form.json")
        sha02 = self.be.sha256_path(FIXTURES / "02-role-state-flow.json")
        sev01 = {"BA-1": "major", "BA-2": "blocker", "BA-3": "normal"}
        sev02 = {"RS-1": "blocker", "RS-2": "major"}
        for repeat in (1, 2):
            self.write_score("01-boundary-form", "A", repeat,
                             score_payload(f"01-A-{repeat}", sha01, "p1", [], sev01,
                                           executor="exec-A"))
            self.write_score("01-boundary-form", "B", repeat,
                             score_payload(f"01-B-{repeat}", sha01, "p1", ["BA-2"], sev01,
                                           executor="exec-B"))
            self.write_score("02-role-state-flow", "A", repeat,
                             score_payload(f"02-A-{repeat}", sha02, "p1", [], sev02,
                                           executor="exec-A"))
            # 第二个重复的 B 臂协议哈希不同 → 该配对无效 → 夹具证据不足
            self.write_score("02-role-state-flow", "B", repeat,
                             score_payload(f"02-B-{repeat}", sha02,
                                           "p1" if repeat == 1 else "p2", ["RS-1"], sev02,
                                           executor="exec-B"))
        summary = self.be.summarize_matrix(matrix_path, self.results,
                                           fixtures_dir=FIXTURES)
        self.assertEqual(summary["primary_metric"]["denominator"], 1)
        self.assertEqual(summary["primary_metric"]["value"], 1.0)
        self.assertTrue(any(item["fixture"] == "02-role-state-flow"
                            for item in summary["invalid_pairs"]))
        self.assertFalse(summary["comparable"])
        self.assertTrue(any("证据不足" in (f.get("insufficient_reason") or "")
                            for f in summary["per_fixture"]))

    def _real_fixture_runs(self, matrix):
        """按矩阵登记写出一套完整有效的运行（两臂不同执行方）。"""
        sha = {"01-boundary-form": self.be.sha256_path(FIXTURES / "01-boundary-form.json"),
               "02-role-state-flow": self.be.sha256_path(FIXTURES / "02-role-state-flow.json")}
        severity = {
            "01-boundary-form": {"BA-1": "major", "BA-2": "blocker", "BA-3": "normal"},
            "02-role-state-flow": {"RS-1": "blocker", "RS-2": "major"},
        }
        for run in matrix["runs"]:
            hits = (["BA-2"] if run["arm"] == "B" else []) if \
                run["fixture"] == "01-boundary-form" else \
                (["RS-1"] if run["arm"] == "B" else [])
            self.write_score(run["fixture"], run["arm"], run["repeat"],
                             score_payload(run["run_id"], sha[run["fixture"]], "p1",
                                           hits, severity[run["fixture"]],
                                           executor=f"exec-{run['arm']}"))

    def test_threshold_check_reports_each_frozen_criterion(self):
        """冻结阈值逐项对照：只给事实，不替人下效果结论。"""
        matrix = json.loads(json.dumps(self.matrix))
        matrix["fixtures"] = ["01-boundary-form"]
        matrix["runs"] = [
            {"fixture": "01-boundary-form", "arm": "A", "repeat": 1, "run_id": "01-A-1"},
            {"fixture": "01-boundary-form", "arm": "B", "repeat": 1, "run_id": "01-B-1"},
        ]
        matrix["thresholds"] = {
            "primary": "主指标大于 0",
            "no_new_blocker_miss": "B 无新增已知 blocker 漏检",
            "false_positive": "误报数不增加",
        }
        matrix_path = self.write_matrix(matrix)
        sha = self.be.sha256_path(FIXTURES / "01-boundary-form.json")
        severity = {"BA-1": "major", "BA-2": "blocker", "BA-3": "normal"}
        self.write_score("01-boundary-form", "A", 1,
                         score_payload("01-A-1", sha, "p1", [], severity,
                                       executor="exec-A"))
        self.write_score("01-boundary-form", "B", 1,
                         score_payload("01-B-1", sha, "p1", ["BA-2"], severity,
                                       executor="exec-B"))
        summary = self.be.summarize_matrix(matrix_path, self.results, fixtures_dir=FIXTURES)
        names = {item["name"]: item for item in summary["threshold_check"]}
        self.assertEqual(set(names), {"primary", "no_new_blocker_miss", "false_positive"})
        self.assertTrue(names["primary"]["met"])
        self.assertTrue(names["no_new_blocker_miss"]["met"])
        self.assertTrue(names["false_positive"]["met"])
        # 每项都要带冻结阈值原文与观测值，不许只给 met
        for item in summary["threshold_check"]:
            self.assertTrue(item["requirement"])
            self.assertTrue(item["observed_text"])

    def test_newly_missed_blocker_is_reported(self):
        """A 命中、B 未命中的 blocker 属「新增 blocker 漏检」，须被点名。"""
        matrix = json.loads(json.dumps(self.matrix))
        matrix["fixtures"] = ["01-boundary-form"]
        matrix["runs"] = [
            {"fixture": "01-boundary-form", "arm": "A", "repeat": 1, "run_id": "01-A-1"},
            {"fixture": "01-boundary-form", "arm": "B", "repeat": 1, "run_id": "01-B-1"},
        ]
        matrix["thresholds"] = {"no_new_blocker_miss": "B 无新增已知 blocker 漏检"}
        matrix_path = self.write_matrix(matrix)
        sha = self.be.sha256_path(FIXTURES / "01-boundary-form.json")
        severity = {"BA-1": "major", "BA-2": "blocker", "BA-3": "normal"}
        self.write_score("01-boundary-form", "A", 1,
                         score_payload("01-A-1", sha, "p1", ["BA-2"], severity,
                                       executor="exec-A"))
        self.write_score("01-boundary-form", "B", 1,
                         score_payload("01-B-1", sha, "p1", [], severity,
                                       executor="exec-B"))
        summary = self.be.summarize_matrix(matrix_path, self.results, fixtures_dir=FIXTURES)
        self.assertEqual([item["id"] for item in summary["newly_missed_blocker"]], ["BA-2"])
        check = {item["name"]: item for item in summary["threshold_check"]}
        self.assertFalse(check["no_new_blocker_miss"]["met"])

    def test_sanitized_archive_has_no_answer_ids(self):
        """常规去敏归档不得含答案 ID；完整件留在受限区。"""
        matrix = json.loads(json.dumps(self.matrix))
        matrix["fixtures"] = ["01-boundary-form"]
        matrix["runs"] = [
            {"fixture": "01-boundary-form", "arm": "A", "repeat": 1, "run_id": "01-A-1"},
            {"fixture": "01-boundary-form", "arm": "B", "repeat": 1, "run_id": "01-B-1"},
        ]
        matrix_path = self.write_matrix(matrix)
        self._real_fixture_runs(matrix)
        out_dir = self.folder / "archive-sanitized"
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "blind_eval.py"), "archive",
             "--matrix", str(matrix_path), "--results", str(self.results),
             "--fixtures-dir", str(FIXTURES), "--out", str(out_dir), "--sanitized"],
            capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        index = json.loads((out_dir / "index.json").read_text(encoding="utf-8"))
        self.assertTrue(index["sanitized"])
        self.assertFalse(index["contains_answer_ids"])
        # 全部归档文件都不得出现答案 ID
        for path in out_dir.rglob("*"):
            if path.is_file():
                text = path.read_text(encoding="utf-8-sig", errors="ignore")
                for answer_id in ("BA-1", "BA-2", "BA-3"):
                    self.assertNotIn(answer_id, text, f"{path.name} 泄漏答案 ID {answer_id}")
        # 可复算 CSV 带 BOM
        raw = (out_dir / "summary.csv").read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))

    def test_full_archive_keeps_scores_and_records_isolation(self):
        """受限区完整归档保留原始计分，并记录隔离检查。"""
        matrix = json.loads(json.dumps(self.matrix))
        matrix["fixtures"] = ["01-boundary-form"]
        matrix["runs"] = [
            {"fixture": "01-boundary-form", "arm": "A", "repeat": 1, "run_id": "01-A-1"},
            {"fixture": "01-boundary-form", "arm": "B", "repeat": 1, "run_id": "01-B-1"},
        ]
        matrix_path = self.write_matrix(matrix)
        self._real_fixture_runs(matrix)
        out_dir = self.folder / "archive-full"
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "blind_eval.py"), "archive",
             "--matrix", str(matrix_path), "--results", str(self.results),
             "--fixtures-dir", str(FIXTURES), "--out", str(out_dir)],
            capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        index = json.loads((out_dir / "index.json").read_text(encoding="utf-8"))
        self.assertFalse(index["sanitized"])
        self.assertTrue(index["contains_answer_ids"])
        scores = list((out_dir / "scores").glob("*.json"))
        self.assertEqual(len(scores), 2)
        isolation = json.loads(
            (out_dir / "isolation" / "isolation-checks.json").read_text(encoding="utf-8"))
        self.assertEqual(len(isolation), 2)
        self.assertTrue(all(item["checked"] for item in isolation))
        self.assertTrue((out_dir / "summary.json").is_file())
        self.assertTrue((out_dir / "invalid-records.json").is_file())

    def test_frozen_matrix_file_is_valid(self):
        path = ROOT / "docs" / "evals-baseline" / "run-matrix.json"
        matrix = self.be._load_matrix(path)
        self.assertEqual(len(matrix["fixtures"]), 5)
        self.assertEqual(len(matrix["arms"]), 2)
        self.assertEqual(matrix["repeats"], 2)
        self.assertEqual(len(matrix["runs"]), 20)
        # 每个夹具每臂每个重复编号都恰好登记一次
        seen = set()
        for run in matrix["runs"]:
            key = (run["fixture"], run["arm"], run["repeat"])
            self.assertNotIn(key, seen, f"运行矩阵重复登记：{key}")
            seen.add(key)
        self.assertEqual(len(seen), 20)
        # 夹具必须真实存在
        for fixture in matrix["fixtures"]:
            self.assertTrue((FIXTURES / f"{fixture}.json").is_file(), fixture)


if __name__ == "__main__":
    unittest.main()
