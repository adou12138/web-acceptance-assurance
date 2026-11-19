"""攻击面覆盖门禁（attack_coverage.py）的机械行为。

背景：154 项真实测试里 143 项走正向路径、surf 关联 0 条、方法标注 0 条，最终只产出
4 条 FAIL 且没有一条来自为新攻击面设计的探针。本文件守住三件事：
  1. 全正向台账必须被压上限（不能靠"铺得全"冒充"打得深"）；
  2. 字段缺失必须报缺口而不是默认通过（也不拿 0 冒充统计值）；
  3. 推断值不参与门禁（诊断参考与机械判据分账）。
"""
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import attack_coverage as ac  # noqa: E402


def rec(i, path_type=None, surf=True, method="eq_boundary", oracle="independent",
        mode="designed_probe", status="PASS"):
    record = {"id": f"TC-{i}", "status": status, "desc": "字段校验"}
    if path_type is not None:
        record["path_type"] = path_type
    if surf:
        record["surf_id"] = "SURF-01"
    if method:
        record["method"] = method
    if oracle:
        record["oracle_source"] = oracle
    if mode:
        record["discovery_mode"] = mode
    return record


class AttackDensityGate(unittest.TestCase):
    def test_all_positive_ledger_lowers_ceiling(self):
        result = ac.analyse([rec(i, "positive") for i in range(10)])
        self.assertEqual(result["attack_density"], 0.0)
        self.assertIn("attack_density_low", [g["code"] for g in result["gaps"]])
        self.assertEqual(result["verdict_ceiling"], "待核实")

    def test_healthy_ledger_passes(self):
        records = [rec(i, "positive") for i in range(7)]
        records += [rec(i, "boundary") for i in range(3)]
        result = ac.analyse(records)
        self.assertEqual(result["attack_density"], 0.3)
        self.assertEqual(result["gaps"], [])
        self.assertEqual(result["verdict_ceiling"], "通过")

    def test_missing_fields_report_gaps_not_pass(self):
        # 历史台账形态：只有 id/status/desc，没有任何攻击面字段
        result = ac.analyse([{"id": f"X-{i}", "status": "PASS", "desc": "创建题型"}
                             for i in range(5)])
        codes = [g["code"] for g in result["gaps"]]
        self.assertIn("path_type_missing", codes)
        self.assertIn("surf_linkage_low", codes)
        self.assertIn("method_missing", codes)
        self.assertEqual(result["attack_density"], "NA")
        self.assertEqual(result["verdict_ceiling"], "待核实")

    def test_self_attested_oracle_is_a_gap(self):
        records = [rec(i, "boundary", oracle="self_attested") for i in range(4)]
        result = ac.analyse(records)
        self.assertIn("oracle_self_attested", [g["code"] for g in result["gaps"]])

    def test_incidental_only_without_designed_probe(self):
        records = [rec(i, "boundary", mode="incidental") for i in range(4)]
        result = ac.analyse(records)
        self.assertIn("no_designed_probe", [g["code"] for g in result["gaps"]])

    def test_inferred_values_do_not_gate(self):
        # 显式声明全部达标，但文本里满是"边界/异常"字样 → 推断值不应影响判定
        records = [rec(i, "positive") for i in range(6)]
        records += [rec(i, "boundary") for i in range(4)]
        for r in records:
            r["desc"] = "边界值 异常 失败 重复提交"
        result = ac.analyse(records)
        self.assertEqual(result["gaps"], [])
        self.assertGreaterEqual(result["inferred_path_type"]["boundary"], 0)

    def test_empty_ledger(self):
        result = ac.analyse([])
        self.assertIn("empty_ledger", [g["code"] for g in result["gaps"]])
        self.assertEqual(result["attack_density"], "NA")


class CliContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_enforce_returns_2_with_gaps(self):
        ledger = self.dir / "l.jsonl"
        ledger.write_text("\n".join(json.dumps(rec(i, "positive")) for i in range(5)),
                          encoding="utf-8")
        self.assertEqual(ac.main(["--ledger", str(ledger), "--enforce"]), 2)

    def test_enforce_returns_0_when_clean(self):
        ledger = self.dir / "l.jsonl"
        rows = [rec(i, "positive") for i in range(7)] + [rec(i, "exception") for i in range(3)]
        ledger.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        self.assertEqual(ac.main(["--ledger", str(ledger), "--enforce"]), 0)

    def test_accepts_csv_and_emits_reports(self):
        ledger = self.dir / "l.csv"
        with ledger.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=["id", "path_type", "surf_id", "method",
                                                    "oracle_source", "discovery_mode"])
            writer.writeheader()
            for i in range(3):
                writer.writerow({"id": f"TC-{i}", "path_type": "positive", "surf_id": "SURF-01",
                                 "method": "eq", "oracle_source": "independent",
                                 "discovery_mode": "designed_probe"})
        out_json, out_csv = self.dir / "out.json", self.dir / "out.csv"
        ac.main(["--ledger", str(ledger), "--emit-json", str(out_json), "--emit-csv", str(out_csv)])
        self.assertTrue(out_json.is_file())
        self.assertTrue(out_csv.is_file())
        self.assertEqual(json.loads(out_json.read_text(encoding="utf-8"))["total"], 3)
        self.assertIn("UTF-8", "UTF-8")  # CSV 编码由 BOM 保证，见下方读取校验
        self.assertTrue(out_csv.read_bytes().startswith(b"\xef\xbb\xbf"))


if __name__ == "__main__":
    unittest.main()
