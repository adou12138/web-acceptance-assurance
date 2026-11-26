"""M2/M4 专项结果 → OBS-ACT- 线索转换的验收测试（验收影响问题 #2）。

验收条件：交付三类样例（预期 4xx / 后台噪声 / 真实业务失败）各自的
OBS、候选及复核记录，并能沿证据引用回查。核心断言：
- 判定字段（status/severity/confidence/raw）被剥离——转换产物过 `scan-collection` 零违规；
- `lead` 只含 suspicion/basis/where，不携带已裁定结论；
- 预期 4xx 与后台噪声只保留观察、不生成候选；真实业务失败生成 pending 候选；
- 证据链：候选 → OBS（obs_id）→ evidence_paths；复核 → 候选（finding_id）。
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import observation_to_lead as otl
import phase_gate as pg

SAMPLES = ROOT / "examples" / "obs-samples"


def collect_doc(findings=None, errors=None, requests=None, hooks=True, error=None):
    return {"findings": findings or [], "hooksInstalled": hooks, "collectError": error,
            "newEvents": {"errors": errors or [], "requests": requests or []}}


class ConversionTests(unittest.TestCase):
    def test_window_findings_preferred_over_cumulative(self):
        """审核缺陷修复：有 windowFindings 时只转本窗口判定——历史窗口 finding
        不重复转出线索（否则同一 action_id 每窗口重复候选）。"""
        old = {"dimension": "7.35", "property": "js-error", "symptom": "旧窗口错误",
               "status": "confirmed", "severity": "P1"}
        new = {"dimension": "7.36", "property": "network-failure", "symptom": "新窗口 500",
               "status": "confirmed", "severity": "P1"}
        doc = collect_doc([old, new], requests=[
            {"method": "POST", "url": "https://t.example/api", "status": 500, "ok": False, "at": 9}])
        doc["windowFindings"] = [new]
        record, candidates = otl.convert(doc, "ACT-W", "TC-W", "https://t.example/f",
                                         "e.png", "real-failure")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["finding_key"], "network-failure")
        self.assertIn("新窗口", record["lead"]["suspicion"])
        self.assertNotIn("旧窗口", record["lead"]["suspicion"])

    def test_fallback_to_cumulative_findings_for_old_product(self):
        """旧产物无 windowFindings：回退全量 findings（窗口归属不可核对，须人工核对）。"""
        finding = {"dimension": "7.35", "property": "js-error", "symptom": "旧版产物错误",
                   "status": "confirmed", "severity": "P1"}
        record, candidates = otl.convert(collect_doc([finding]), "ACT-O", "TC-O",
                                         "https://t.example/f", "e.png", "real-failure")
        self.assertEqual(len(candidates), 1)
        self.assertIn("旧版产物错误", record["lead"]["suspicion"])

    def test_real_failure_converts_to_lead_and_pending_candidate(self):
        finding = {"dimension": "7.35", "property": "runtime-js-error",
                   "symptom": "未捕获 TypeError", "status": "confirmed",
                   "severity": "P1", "confidence": 0.9, "raw": {"text": "boom"}}
        record, candidates = otl.convert(
            collect_doc([finding], errors=[{"kind": "onerror", "text": "boom", "at": 1}]),
            "ACT-1", "TC-1", "https://t.example/f", "e.png", "real-failure")
        self.assertEqual(record["lead"]["basis"][:5], "独立通道：")
        self.assertEqual(set(record["lead"]), {"suspicion", "basis", "where"})
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["verdict"], "pending")
        self.assertEqual(candidates[0]["obs_id"], record["id"])

    def test_expected_4xx_stays_observation_without_candidate(self):
        """预期校验失败（4xx）：保留观察事实，不生成候选、不升级为缺陷。"""
        req = {"method": "POST", "url": "https://t.example/api/submit",
               "status": 422, "ok": False, "at": 2}
        record, candidates = otl.convert(
            collect_doc(requests=[req]), "ACT-2", "TC-2",
            "https://t.example/f", "e.png", "expected-4xx")
        self.assertEqual(record["observed"]["network"][0]["status"], 422)
        self.assertNotIn("lead", record)
        self.assertEqual(candidates, [])

    def test_background_noise_stays_observation_without_candidate(self):
        """后台噪声（轮询/取消）：只记观察，不候选。"""
        reqs = [{"method": "GET", "url": "https://t.example/api/poll", "status": 499,
                 "ok": False, "at": 3}]
        record, candidates = otl.convert(
            collect_doc(requests=reqs), "ACT-3", "TC-3",
            "https://t.example/f", "e.png", "background-noise")
        self.assertEqual(candidates, [])
        self.assertNotIn("lead", record)

    def test_judgement_fields_stripped_and_passes_gate(self):
        """剥离判定：转换产物过 scan-collection 零违规（status/severity 不落顶层与嵌套）。"""
        finding = {"dimension": "7.36", "property": "business-error-as-200",
                   "symptom": "HTTP200+success:false", "status": "pending",
                   "severity": "P2", "confidence": 0.6}
        record, _ = otl.convert(
            collect_doc([finding], requests=[{"method": "POST", "url": "https://t.example/pay",
                                              "status": 200, "ok": True, "at": 4}]),
            "ACT-4", "TC-4", "https://t.example/pay", "e.png", "real-failure")
        self.assertEqual(pg.scan_collection_records([record]), [])
        text = json.dumps(record, ensure_ascii=False)
        for key in ('"severity"', '"confidence"', '"raw"'):
            self.assertNotIn(key, text)
        # observed.network 的 status 是可观测事实（合法）
        self.assertIn('"status": 200', text)

    def test_blind_spot_when_hook_missing(self):
        record, _ = otl.convert(
            collect_doc(hooks=False, error="evaluate 上下文销毁"),
            "ACT-5", "TC-5", "https://t.example/f", "e.png", "background-noise")
        self.assertTrue(any("钩子未安装" in s for s in record.get("blind_spot", [])))
        self.assertTrue(any("采集异常" in s for s in record.get("blind_spot", [])))

    def test_real_failure_5xx_without_finding_still_candidates(self):
        """无 finding 但出现 5xx：仍生成待复核候选（进入裁决核查，不直接落缺陷）。"""
        record, candidates = otl.convert(
            collect_doc(requests=[{"method": "POST", "url": "https://t.example/api",
                                   "status": 503, "ok": False, "at": 5}]),
            "ACT-6", "TC-6", "https://t.example/f", "e.png", "real-failure")
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["finding_key"], "network-5xx")
        self.assertEqual(record["lead"], {"suspicion": record["lead"]["suspicion"],
                                          "basis": record["lead"]["basis"],
                                          "where": "https://t.example/f"})


class SampleArtifactTests(unittest.TestCase):
    """验收条件要求的「三类样例各自的 OBS、候选及复核记录」+ 证据回查。"""

    def load_jsonl(self, path):
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_samples_exist_for_all_three_classes(self):
        for name in ("expected-4xx", "background-noise", "real-failure"):
            with self.subTest(sample=name):
                folder = SAMPLES / name
                self.assertTrue((folder / "obs.jsonl").is_file(), name)
                self.assertTrue((folder / "candidates.jsonl").is_file(), name)
                self.assertTrue((folder / "reviews.jsonl").is_file(), name)

    def test_sample_obs_passes_scan_collection(self):
        for name in ("expected-4xx", "background-noise", "real-failure"):
            with self.subTest(sample=name):
                records = self.load_jsonl(SAMPLES / name / "obs.jsonl")
                self.assertEqual(pg.scan_collection_records(records), [], name)

    def test_sample_candidates_reference_obs(self):
        for name in ("expected-4xx", "background-noise", "real-failure"):
            with self.subTest(sample=name):
                obs_ids = {r["id"] for r in self.load_jsonl(SAMPLES / name / "obs.jsonl")}
                for cand in self.load_jsonl(SAMPLES / name / "candidates.jsonl"):
                    self.assertIn(cand["obs_id"], obs_ids, f"{name}: 候选回链 OBS 断链")

    def test_sample_reviews_reference_candidates(self):
        for name in ("expected-4xx", "background-noise", "real-failure"):
            with self.subTest(sample=name):
                cand_ids = {c["id"] for c in self.load_jsonl(SAMPLES / name / "candidates.jsonl")}
                for review in self.load_jsonl(SAMPLES / name / "reviews.jsonl"):
                    self.assertIn(review["finding_id"], cand_ids, f"{name}: 复核回链候选断链")

    def test_4xx_and_noise_have_no_candidates_real_failure_has_pending(self):
        self.assertEqual(self.load_jsonl(SAMPLES / "expected-4xx" / "candidates.jsonl"), [])
        self.assertEqual(self.load_jsonl(SAMPLES / "background-noise" / "candidates.jsonl"), [])
        real = self.load_jsonl(SAMPLES / "real-failure" / "candidates.jsonl")
        self.assertTrue(real)
        self.assertTrue(all(c["verdict"] == "pending" for c in real))


if __name__ == "__main__":
    unittest.main()