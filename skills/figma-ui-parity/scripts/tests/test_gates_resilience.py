#!/usr/bin/env python3
"""跑前/汇总前门禁 + 设计侧盘点的变异测试。

三个变异用例对应 2026-10-29 方案的验收标准——把防御撤掉必须变红：
1. 防漏接：run 目录有产物但不在 assemble 入参 → pre_assemble_gate 非零；
2. 防假通过：计划产物是空壳 JSON（缺 requiredKeys）→ pre_assemble_gate 非零；
3. 盘点容错：畸变节点树（rect=None/fills 非法）→ design_inventory 退出 0 且
   坏节点/异常进 unrecognized，不崩溃。
外加 plan_run 的排程契约：manual 维度豁免理由缺失/黑名单话术 → 退出码 2。
以及审查修复回归：report 类产物验真路径、产物目录名漂移兼容、
--design 存在性预检、skip 旗→豁免映射。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
PY = sys.executable


def _run(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([PY, str(HERE / script), *args],
                          capture_output=True, text=True, timeout=60)


def _write_json(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _make_plan(run_dir: Path, artifact: str = "performance.json") -> Path:
    plan = {
        "profile": "full", "designProvided": True,
        "scheduled": [{"dimension": "7.40", "name": "性能", "driver": "x",
                       "artifact": artifact, "artifactKey": artifact,
                       "assembleArg": "--findings", "requiredKeys": ["findings"]}],
        "exempt": [{"dimension": "7.10", "name": "跨环境对比", "reason": "需要多套已部署环境与访问授权，须用户确认后另行排期"}],
    }
    path = run_dir / "run-plan.json"
    _write_json(path, plan)
    return path


class PreAssembleGateMutationTests(unittest.TestCase):
    """变异自证：漏接/假跑防御撤掉任一项，下列用例即变红。"""

    def test_unmapped_artifact_blocks(self):
        """变异用例 1：产物已产出但不在 assemble 入参 → 必须拦截。"""
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td)
            plan = _make_plan(run_dir)
            _write_json(run_dir / "performance.json",
                        {"findings": [], "metrics": {"lcp": 100}})
            r = _run("pre_assemble_gate.py", "--plan", str(plan), "--run-dir", str(run_dir),
                     "--assemble-args", str(_write_json(run_dir / "args.json", [])))
            self.assertEqual(r.returncode, 2, "漏传必须 rc=2")
            self.assertIn("漏传", r.stderr)

    def test_mapped_and_valid_artifact_passes(self):
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td)
            plan = _make_plan(run_dir)
            _write_json(run_dir / "performance.json",
                        {"findings": [], "metrics": {"lcp": 100}})
            args = _write_json(run_dir / "args.json", [str(run_dir / "performance.json")])
            r = _run("pre_assemble_gate.py", "--plan", str(plan), "--run-dir", str(run_dir),
                     "--assemble-args", str(args))
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_hollow_artifact_blocks(self):
        """变异用例 2：空壳产物（缺 requiredKeys）→ 必须拦截，不得当通过。"""
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td)
            plan = _make_plan(run_dir)
            _write_json(run_dir / "performance.json", {"note": "驱动半路崩溃只写了壳"})
            args = _write_json(run_dir / "args.json", [str(run_dir / "performance.json")])
            r = _run("pre_assemble_gate.py", "--plan", str(plan), "--run-dir", str(run_dir),
                     "--assemble-args", str(args))
            self.assertEqual(r.returncode, 2, "空壳产物必须 rc=2")
            self.assertIn("必需键", r.stderr)

    def test_missing_artifact_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td)
            plan = _make_plan(run_dir)  # 不创建 performance.json
            r = _run("pre_assemble_gate.py", "--plan", str(plan), "--run-dir", str(run_dir))
            self.assertEqual(r.returncode, 2)
            self.assertIn("不存在", r.stderr)


class PlanRunContractTests(unittest.TestCase):
    def test_full_profile_schedules_and_exempts(self):
        with tempfile.TemporaryDirectory() as td:
            r = _run("plan_run.py", "--profile", "full", "--run-dir", td, "--write")
            self.assertEqual(r.returncode, 0, r.stderr[-300:])
            plan = json.loads((Path(td) / "run-plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["summary"]["total"], 45)
            self.assertGreater(plan["summary"]["scheduled"], 0, "auto 维度必须被排程")
            for item in plan["exempt"]:
                self.assertTrue(str(item["reason"]).strip(), "豁免理由禁止留空")

    def test_invalid_exempt_reason_blocks(self):
        """黑名单话术（待定/TODO）与过短理由 → 退出码 2。"""
        with tempfile.TemporaryDirectory() as td:
            r = _run("plan_run.py", "--profile", "full", "--run-dir", td,
                     "--exempt", "7.10=待定")
            self.assertEqual(r.returncode, 2, "黑名单话术必须被拦")
            self.assertIn("不合规", r.stderr)


class DesignInventoryResilienceTests(unittest.TestCase):
    """变异用例 3：畸变节点树不得崩溃（盘点崩溃 = 整轮测试瘫痪）。"""

    def test_corrupt_tree_never_crashes(self):
        with tempfile.TemporaryDirectory() as td:
            design = Path(td) / "design_model.json"
            _write_json(design, {"tree": {
                "id": "1", "type": "FRAME",
                "layout": {"mode": "VERTICAL"},
                "children": [
                    {"id": "2", "type": "TEXT", "text": {"characters": "甲"},
                     "rect": None, "fills": "invalid-not-a-list"},
                    {"id": "3", "type": "TEXT", "text": {"characters": "乙"}, "rect": {"x": 0, "y": 40, "w": 10, "h": 10}},
                    "not-a-dict-node",
                ],
            }})
            r = _run("design_inventory.py", "--design", str(design),
                     "--output", str(Path(td) / "inv.json"))
            self.assertEqual(r.returncode, 0, "坏节点必须被容错，盘点不得崩溃")
            inv = json.loads((Path(td) / "inv.json").read_text(encoding="utf-8"))
            self.assertIn("order", inv)
            self.assertIn("unrecognized", inv)

    def test_auto_layout_confidence_and_icons(self):
        with tempfile.TemporaryDirectory() as td:
            design = Path(td) / "design_model.json"
            _write_json(design, {"tree": {
                "id": "1", "type": "FRAME", "layout": {"mode": "VERTICAL"},
                "children": [
                    {"id": "t1", "type": "TEXT", "text": {"characters": "第一"}, "rect": {"x": 0, "y": 0, "w": 10, "h": 10}},
                    {"id": "t2", "type": "TEXT", "text": {"characters": "第二"}, "rect": {"x": 0, "y": 30, "w": 10, "h": 10}},
                    {"id": "i1", "type": "VECTOR", "name": "search-icon", "rect": {"x": 0, "y": 60, "w": 16, "h": 16},
                     "fills": [{"type": "SOLID", "visible": True, "color": {"hex": "#333333"}}]},
                ]}})
            r = _run("design_inventory.py", "--design", str(design),
                     "--output", str(Path(td) / "inv.json"))
            self.assertEqual(r.returncode, 0, r.stderr[-200:])
            inv = json.loads((Path(td) / "inv.json").read_text(encoding="utf-8"))
            self.assertEqual(inv["order"][0]["confidence"], 1.0, "AUTO_LAYOUT 置信度必须 1.0")
            self.assertEqual([c["nodeId"] for c in inv["order"][0]["childOrder"]], ["t1", "t2", "i1"])
            self.assertTrue(any(i["nodeId"] == "i1" and i["confidence"] >= 0.8 for i in inv["icons"]))

    def test_struct_checks_consumes_inventory(self):
        """7.6/7.7 消费方接线：无 --design-inventory 不产条目，提供后必产。"""
        with tempfile.TemporaryDirectory() as td:
            order = Path(td) / "order.json"
            _write_json(order, {"order": [], "dividers": [], "needScrollActions": [],
                                "actionCandidates": 1})
            inv = Path(td) / "inv.json"
            _write_json(inv, {"order": [{"containerId": "c1", "containerName": "列表",
                                         "confidence": 0.6, "derivedBy": "geometric:vertical",
                                         "childCount": 2,
                                         "childOrder": [{"nodeId": "a", "text": "甲"}, {"nodeId": "b", "text": "乙"}]}],
                              "dividers": []})
            base = ["--order", str(order), "--output", str(Path(td) / "f.json")]
            r0 = _run("struct_checks.py", *base)
            self.assertEqual(r0.returncode, 0)
            f0 = json.loads((Path(td) / "f.json").read_text(encoding="utf-8"))
            self.assertNotIn("7.6", [f["dimension"] for f in f0["findings"]],
                             "未提供设计清单时 7.6 必须不产条目（单侧数据下结论=编造）")
            r1 = _run("struct_checks.py", *base, "--design-inventory", str(inv))
            self.assertEqual(r1.returncode, 0, r1.stderr[-200:])
            f1 = json.loads((Path(td) / "f.json").read_text(encoding="utf-8"))
            dims = [f["dimension"] for f in f1["findings"]]
            self.assertIn("7.6", dims, "提供清单后 7.6 必须产条目")
            self.assertIn("7.7", dims, "提供清单后 7.7 必须产条目")
            # 几何推导（0.6 < 0.8）只许 pending，不许 confirmed
            for f in f1["findings"]:
                if f["dimension"] == "7.6" and f["status"] == "confirmed":
                    self.fail("低置信度推导不得出 confirmed 缺陷")


class ReviewFixRegressionTests(unittest.TestCase):
    """2026-10-29 审查修复的回归：每个用例对应一处真实缺陷，撤修复必红。"""

    @staticmethod
    def _plan_with_report_item(run_dir: Path) -> Path:
        plan = {
            "profile": "full", "designProvided": True,
            "scheduled": [
                {"dimension": "7.40", "name": "性能", "driver": "x",
                 "artifact": "performance.json", "artifactKey": "performance.json",
                 "assembleArg": "--findings", "requiredKeys": ["findings"]},
                {"dimension": "7.11", "name": "缺陷定位", "driver": "x",
                 "artifact": "report.json", "location": "report",
                 "artifactKey": None, "assembleArg": None, "requiredKeys": ["defects"]},
            ],
            "exempt": [],
        }
        return _write_json(run_dir / "run-plan.json", plan)

    def test_report_location_validated_via_report_arg(self):
        """7.11/7.12 验真必须走 --report 的真实路径，不得按 run-dir 硬拼。"""
        with tempfile.TemporaryDirectory() as td:
            run_dir, out_dir = Path(td) / "run", Path(td) / "elsewhere"
            run_dir.mkdir()
            plan = self._plan_with_report_item(run_dir)
            _write_json(run_dir / "performance.json", {"findings": []})
            report = _write_json(out_dir / "report.json", {"defects": []})
            args = _write_json(run_dir / "args.json", [str(run_dir / "performance.json")])
            r = _run("pre_assemble_gate.py", "--plan", str(plan), "--run-dir", str(run_dir),
                     "--assemble-args", str(args), "--report", str(report))
            self.assertEqual(r.returncode, 0, r.stderr[-300:])
            # 空壳 report（缺 defects）同样必须拦——报告类产物不豁免形状校验
            hollow = _write_json(out_dir / "hollow.json", {"meta": {}})
            r2 = _run("pre_assemble_gate.py", "--plan", str(plan), "--run-dir", str(run_dir),
                      "--assemble-args", str(args), "--report", str(hollow))
            self.assertEqual(r2.returncode, 2, "report 空壳必须 rc=2")
            # 未提供 --report：合法用法（只重出报告），降级为警告不得熔断
            r3 = _run("pre_assemble_gate.py", "--plan", str(plan), "--run-dir", str(run_dir),
                      "--assemble-args", str(args))
            self.assertEqual(r3.returncode, 0, r3.stderr[-300:])
            self.assertIn("跳过 report 类产物验真", r3.stdout)

    def test_artifact_dir_name_drift_resolved_by_basename(self):
        """manifest 记 button-audit/ 而实际产物在 button_audit/（目录名漂移）：
        不得误报缺失，漏传对账按文件名仍然必须拦住。"""
        drift_key = "button-audit/button_findings.json"
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td)
            plan = _write_json(run_dir / "run-plan.json", {
                "profile": "full", "designProvided": True,
                "scheduled": [{"dimension": "7.22", "name": "按钮审计", "driver": "x",
                               "artifact": drift_key, "artifactKey": drift_key,
                               "assembleArg": "--findings", "requiredKeys": ["findings"]}],
                "exempt": []})
            actual = _write_json(run_dir / "button_audit" / "button_findings.json",
                                 {"findings": []})
            args = _write_json(run_dir / "args.json", [str(actual)])
            r = _run("pre_assemble_gate.py", "--plan", str(plan), "--run-dir", str(run_dir),
                     "--assemble-args", str(args))
            self.assertEqual(r.returncode, 0, "目录名漂移不得误报：" + r.stderr[-300:])
            # 同一产物若没接入参，按 basename 对账必须仍然报漏传
            r2 = _run("pre_assemble_gate.py", "--plan", str(plan), "--run-dir", str(run_dir),
                      "--assemble-args", str(_write_json(run_dir / "args2.json", [])))
            self.assertEqual(r2.returncode, 2, "漂移目录里的产物漏传必须被拦")
            self.assertIn("漏传", r2.stderr)

    def test_plan_run_rejects_missing_design_file(self):
        """--design 指向不存在的文件 → rc=1（设计缺口开跑前暴露，不得排程后炸）。"""
        with tempfile.TemporaryDirectory() as td:
            r = _run("plan_run.py", "--profile", "full", "--run-dir", td,
                     "--design", str(Path(td) / "no-such-design.json"))
            self.assertEqual(r.returncode, 1)
            self.assertIn("不存在", r.stdout + r.stderr)

    def test_skip_flag_exempt_args_maps_via_manifest(self):
        """--no-* 旗必须能从 manifest 反查维度自动转豁免，否则 skip 与门禁互斥。"""
        sys.path.insert(0, str(HERE))
        try:
            import run_parity_pipeline as rpp
            args = argparse.Namespace(no_probes=True, no_button_audit=True,
                                      no_performance=False, no_a11y=False,
                                      no_keyboard=False, no_design_contrast=False,
                                      no_journeys=False)
            out = rpp._skip_flag_exempt_args(args)
            dims = {item.split("=", 1)[0] for item in out[1::2]}
            self.assertIn("7.13", dims, "no-probes 必须连带豁免 struct 一族（输入依赖）")
            self.assertIn("7.4", dims, "no-probes 必须豁免 responsive")
            self.assertIn("7.22", dims, "no-button-audit 必须豁免 7.22")
            self.assertIn("7.44", dims, "no-button-audit 必须豁免 7.44")
            self.assertNotIn("7.40", dims, "未按下的旗不得豁免 7.40")
            # 理由必须通过 plan_run 的合规校验（非空、非黑名单）
            for item in out[1::2]:
                self.assertIn("显式跳过", item, "豁免理由必须留痕跳过来源")
        finally:
            sys.path.pop(0)


if __name__ == "__main__":
    unittest.main()
