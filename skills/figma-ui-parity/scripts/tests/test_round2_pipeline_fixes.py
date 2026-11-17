#!/usr/bin/env python3
"""第二轮深审管线修复的锁定测试（P2-9 / P2-12 / P2-16）。

P2-9  非门禁 stage 失败必须反映在退出码与 pipeline-log（degraded）——
      旧版 stabilize/diff_score/compare_fields 崩掉时管线仍 return 0。
P2-12 stage stdout 落盘（journey_coverage 完整矩阵此前只留 400 字符 tail）。
P2-16 dry-run + --page-url 时 state_scenarios 必须记 planned，
      不得报与事实相反的「缺少 --page-url」。
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SKILL = Path(__file__).resolve().parents[2]
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))

import run_parity_pipeline as RPP  # noqa: E402


def _run_pipeline(*extra, workdir, outdir):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "run_parity_pipeline.py"),
         "--workdir", str(workdir), "--outdir", str(outdir), "--no-patterns", *extra],
        capture_output=True, text=True, timeout=120)


class TestRound2PipelineFailedStageExitCode(unittest.TestCase):
    """P2-9：非门禁 stage 失败 → rc=1 + pipelineStatus=degraded + stderr 告警。"""

    def test_diff_score_failure_fails_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp) / "wd"
            outdir = Path(tmp) / "out"
            # 两个不存在的 PNG 路径：Path 非空即进 stage，diff_score.js 打不开文件
            # 必然非零退出——非门禁 stage 的确定性失败。
            r = _run_pipeline("--figma-png", str(Path(tmp) / "nope1.png"),
                              "--page-png", str(Path(tmp) / "nope2.png"),
                              workdir=workdir, outdir=outdir)
            self.assertEqual(r.returncode, 1, r.stderr[-300:])
            self.assertIn("stage 失败", r.stderr)
            log = json.loads((outdir / "pipeline-log.json").read_text(encoding="utf-8"))
            self.assertEqual(log["pipelineStatus"], "degraded")
            self.assertIn("diff_score", log["failedStages"])

    def test_clean_partial_run_stays_zero(self):
        """无 stage 失败的部分链路（不传任何可选产物）仍 rc=0——P2-9 不误伤。"""
        with tempfile.TemporaryDirectory() as tmp:
            r = _run_pipeline(workdir=Path(tmp) / "wd", outdir=Path(tmp) / "out")
            self.assertEqual(r.returncode, 0, r.stderr[-300:])


class TestRound2StageStdoutCapture(unittest.TestCase):
    """P2-12：stdout_path 落盘——成功写全量、失败/空输出不写半截产物。"""

    def _stage_with(self, rc, stdout, tmpdir):
        log = {}
        out = Path(tmpdir) / "reports" / "journey_coverage.json"
        fake = SimpleNamespace(returncode=rc, stdout=stdout, stderr="")
        with mock.patch.object(RPP.subprocess, "run", return_value=fake):
            RPP.stage("probe", ["cmd"], False, log, stdout_path=out)
        return log, out

    def test_success_writes_full_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = json.dumps({"rate": 0.5, "matrix": ["x" * 5000]})
            log, out = self._stage_with(0, payload, tmp)
            self.assertEqual(log["probe"]["status"], "ran")
            self.assertEqual(log["probe"]["stdoutFile"], str(out))
            written = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(written["rate"], 0.5)
            self.assertGreater(len(written["matrix"][0]), 4000, "落盘必须全量而非 400 字符 tail")

    def test_failure_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            log, out = self._stage_with(1, '{"half": 1}', tmp)
            self.assertEqual(log["probe"]["status"], "failed")
            self.assertNotIn("stdoutFile", log["probe"])
            self.assertFalse(out.exists(), "失败 stage 不得产出半截产物")

    def test_empty_stdout_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            log, out = self._stage_with(0, "", tmp)
            self.assertNotIn("stdoutFile", log["probe"])
            self.assertFalse(out.exists())


class TestRound2DryRunPlansStateScenarios(unittest.TestCase):
    """P2-16：dry-run + --page-url → state_scenarios 记 planned（对齐 stabilize），
    不得报与事实相反的「缺少 --page-url」。"""

    def test_dry_run_with_page_url_plans_state_scenarios(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _run_pipeline("--dry-run", "--page-url", "https://example.test/",
                              workdir=Path(tmp) / "wd", outdir=Path(tmp) / "out")
            self.assertEqual(r.returncode, 0, r.stderr[-300:])
            planned = [line for line in r.stdout.splitlines()
                       if line.startswith("[plan]") and "run_state_scenarios" in line]
            self.assertTrue(planned, "dry-run 计划必须包含 state_scenarios 阶段")
            self.assertNotIn("缺少 --page-url", r.stdout, "reason 不得与事实相反")

    def test_no_page_url_still_explains(self):
        """真跑（非 dry-run）无 page-url：notRun + 准确的缺失说明进 pipeline-log。"""
        with tempfile.TemporaryDirectory() as tmp:
            r = _run_pipeline(workdir=Path(tmp) / "wd", outdir=Path(tmp) / "out")
            self.assertEqual(r.returncode, 0, r.stderr[-300:])
            log = json.loads((Path(tmp) / "out" / "pipeline-log.json").read_text(encoding="utf-8"))
            self.assertEqual(log["state_scenarios"]["status"], "notRun")
            self.assertIn("缺少 --page-url", log["state_scenarios"]["reason"])


class TestFigmaRawPipeline(unittest.TestCase):
    """通过真实入口验证新设计输入链路，不访问页面或读取外部 fixture。"""

    def _main(self, argv, run):
        with mock.patch.object(sys, "argv", ["run_parity_pipeline.py", *map(str, argv)]), \
                mock.patch.object(RPP.subprocess, "run", side_effect=run):
            return RPP.main()

    def test_failed_design_stage_never_consumes_previous_outputs(self):
        for failed_stage in ("figma_mcp_to_json", "extract_design_fields"):
            for failure in ("nonzero", "missing", "permission", "timeout", "no_output"):
                with self.subTest(stage=failed_stage, failure=failure), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    workdir, outdir = root / "wd", root / "out"
                    design_dir = workdir / "design"
                    design_dir.mkdir(parents=True)
                    old_outputs = {}
                    for name in ("figma-mcp.json", "design_model.json"):
                        path = design_dir / name
                        path.write_text('{"old": true}', encoding="utf-8")
                        old_outputs[path] = path.read_bytes()
                    raw, page = root / "raw.txt", root / "page.json"
                    raw.write_text("synthetic input", encoding="utf-8")
                    page.write_text("{}", encoding="utf-8")
                    calls = []

                    def run(command, **kwargs):
                        name = Path(command[1]).stem
                        calls.append(name)
                        if name == failed_stage:
                            if failure == "missing":
                                raise FileNotFoundError("synthetic missing executable")
                            if failure == "permission":
                                raise PermissionError("synthetic permission denied")
                            if failure == "timeout":
                                raise subprocess.TimeoutExpired(command, 600)
                            return SimpleNamespace(returncode=7 if failure == "nonzero" else 0,
                                                   stdout="", stderr="synthetic failure")
                        if name in ("figma_mcp_to_json", "extract_design_fields"):
                            output = Path(command[command.index("--output") + 1])
                            output.parent.mkdir(parents=True, exist_ok=True)
                            output.write_text('{"fresh": true}', encoding="utf-8")
                        return SimpleNamespace(returncode=0, stdout="", stderr="")

                    rc = self._main(["--workdir", workdir, "--outdir", outdir,
                                     "--figma-mcp-raw", raw, "--page-json", page,
                                     "--page-url", "https://example.test/", "--no-patterns"], run)
                    self.assertEqual(rc, 1, "设计输入失败必须非零退出")
                    expected = ["figma_mcp_to_json"]
                    if failed_stage == "extract_design_fields":
                        expected.append("extract_design_fields")
                    self.assertEqual(calls, expected, "失败后不得再启动页面或下游处理")
                    log = json.loads((outdir / "pipeline-log.json").read_text(encoding="utf-8"))
                    self.assertIn(log[failed_stage]["status"], ("failed", "notRun"))
                    self.assertEqual(log["pipelineStatus"], "degraded")
                    self.assertIn(failed_stage, log["failedStages"])
                    for name in ("design_contrast", "compare_fields", "discover_scenarios"):
                        self.assertEqual(log[name]["status"], "notRun")
                        self.assertIn(failed_stage, log[name]["reason"])
                    for path, old in old_outputs.items():
                        self.assertEqual(path.read_bytes(), old, "失败链路不得覆盖上一轮产物")

    def test_success_promotes_only_current_design_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workdir, outdir = root / "wd", root / "out"
            design_dir = workdir / "design"
            design_dir.mkdir(parents=True)
            for name in ("figma-mcp.json", "design_model.json"):
                (design_dir / name).write_text('{"old": true}', encoding="utf-8")
            calls = []

            def run(command, **kwargs):
                name = Path(command[1]).stem
                calls.append(name)
                if name in ("figma_mcp_to_json", "extract_design_fields"):
                    if name == "extract_design_fields":
                        source = Path(command[command.index("--input") + 1])
                        self.assertEqual(json.loads(source.read_text(encoding="utf-8")), {"fresh": "figma_mcp_to_json"})
                    output = Path(command[command.index("--output") + 1])
                    output.write_text(json.dumps({"fresh": name}), encoding="utf-8")
                if name in ("design_contrast", "compare_fields", "discover_scenarios"):
                    source = Path(command[command.index("--design") + 1])
                    self.assertEqual(source, design_dir / "design_model.json")
                    model = json.loads(source.read_text(encoding="utf-8"))
                    self.assertEqual(model["fresh"], "extract_design_fields")
                    self.assertEqual(model["source"], str(design_dir / "figma-mcp.json"))
                    self.assertEqual(model["meta"]["sourceInput"], str(root / "raw.txt"))
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            page = root / "page.json"
            page.write_text("{}", encoding="utf-8")
            rc = self._main(["--workdir", workdir, "--outdir", outdir,
                             "--figma-mcp-raw", root / "raw.txt", "--page-json", page,
                             "--no-patterns"], run)
            self.assertEqual(rc, 0)
            self.assertEqual(calls, ["figma_mcp_to_json", "extract_design_fields", "plan_run",
                                     "design_contrast", "compare_fields", "discover_scenarios"])
            self.assertEqual(sorted(path.name for path in design_dir.iterdir()),
                             ["design_model.json", "figma-mcp.json"])
            log = json.loads((outdir / "pipeline-log.json").read_text(encoding="utf-8"))
            self.assertTrue(all(log[name]["status"] == "ran" for name in calls))

    def test_real_success_keeps_published_source_and_original_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workdir, outdir = root / "wd", root / "out"
            raw = root / "raw.txt"
            raw.write_text('''NAME: "Synthetic"
GLOBAL_VARS:
ELEMENTS:
EL-root:
  type: FRAME
  layout:
    dimensions: {width: 400, height: 200}
NODES:
[CANVAS] "Page" #0:1
  [FRAME] "Demo" #1:1 template=EL-root
''', encoding="utf-8")
            r = _run_pipeline("--figma-mcp-raw", str(raw), "--no-design-contrast",
                              workdir=workdir, outdir=outdir)
            self.assertEqual(r.returncode, 0, r.stderr)
            model = json.loads((workdir / "design" / "design_model.json").read_text(encoding="utf-8"))
            source = Path(model["source"])
            self.assertEqual(source, workdir / "design" / "figma-mcp.json")
            self.assertTrue(source.is_file(), "模型来源必须在临时目录销毁后仍存在")
            self.assertEqual(model["meta"]["sourceInput"], str(raw))
            self.assertFalse(list((workdir / "design").glob("figma-input-*")))
            log = json.loads((outdir / "pipeline-log.json").read_text(encoding="utf-8"))
            for name in ("figma_mcp_to_json", "extract_design_fields"):
                self.assertEqual(log[name]["status"], "ran")

    def test_dry_run_plans_complete_design_chain_without_io(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            argv = ["--workdir", root / "wd", "--outdir", root / "out",
                    "--figma-mcp-raw", root / "missing.txt", "--page-json", root / "missing.json",
                    "--page-url", "https://example.test/", "--dry-run", "--no-patterns"]
            with mock.patch("builtins.print") as output:
                rc = self._main(argv, AssertionError("dry-run 不得启动外部进程"))
            self.assertEqual(rc, 0)
            plans = [call.args[0] for call in output.call_args_list
                     if call.args and str(call.args[0]).startswith("[plan]")]
            expected = ("figma_mcp_to_json.py", "extract_design_fields.py", "design_contrast.py",
                        "compare_fields.py", "discover_scenarios.py")
            positions = []
            for script in expected:
                found = [i for i, plan in enumerate(plans) if script in plan]
                self.assertEqual(len(found), 1, "缺少计划阶段：" + script)
                positions.append(found[0])
            self.assertEqual(positions, sorted(positions))
            self.assertEqual(list(root.iterdir()), [], "dry-run 不得创建目录或产物")

    def test_conflicting_design_inputs_are_argument_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            r = _run_pipeline("--figma-mcp-raw", str(root / "raw.txt"),
                              "--design-json", str(root / "design.json"), "--dry-run",
                              workdir=root / "wd", outdir=root / "out")
            self.assertEqual(r.returncode, 2)
            self.assertIn("--figma-mcp-raw", r.stderr)
            self.assertIn("--design-json", r.stderr)
            self.assertFalse((root / "wd").exists())

    def test_design_selection_is_mutually_exclusive_and_forwarded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for flag, value, forwarded in (("--figma-node-id", "3:8", "--node-id"),
                                            ("--figma-root-name", "测试画布", "--root-name")):
                with self.subTest(flag=flag), mock.patch("builtins.print") as output:
                    rc = self._main(["--workdir", root / "wd", "--outdir", root / "out",
                                     "--figma-mcp-raw", root / "raw.txt", "--dry-run", flag, value],
                                    AssertionError("dry-run 不得执行外部命令"))
                    self.assertEqual(rc, 0)
                    converter_plan = [call.args[0] for call in output.call_args_list
                                      if call.args and "figma_mcp_to_json.py" in str(call.args[0])]
                    self.assertEqual(len(converter_plan), 1)
                    self.assertIn(forwarded + " " + value, converter_plan[0])
            r = _run_pipeline("--figma-mcp-raw", str(root / "raw.txt"), "--dry-run",
                              "--figma-node-id", "3:8", "--figma-root-name", "测试画布",
                              workdir=root / "wd", outdir=root / "out")
            self.assertEqual(r.returncode, 2)
            self.assertIn("--figma-node-id", r.stderr)
            self.assertIn("--figma-root-name", r.stderr)


class TestPipelinePublishAndShots(unittest.TestCase):
    """合成产物与受控子进程覆盖发布失败和截图接线，不访问网页。"""

    def _main(self, argv, run):
        with mock.patch.object(sys, "argv", ["run_parity_pipeline.py", *map(str, argv)]), \
                mock.patch.object(RPP.subprocess, "run", side_effect=run), \
                mock.patch("builtins.print"):
            return RPP.main()

    def test_publish_failure_is_logged_and_preserves_previous_pair(self):
        for failure in ("converted_json", "model_json", "converted_shape", "model_shape", "model_null", "meta_shape",
                        "read_error", "write_error", "first_replace", "second_replace"):
            for previous in (False, True):
                with self.subTest(failure=failure, previous=previous), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    design_dir, outdir = root / "wd" / "design", root / "out"
                    design_dir.mkdir(parents=True)
                    old = {}
                    for name in ("figma-mcp.json", "design_model.json"):
                        target = design_dir / name
                        if previous:
                            target.write_text(json.dumps({"previous": name}), encoding="utf-8")
                            old[target] = target.read_bytes()
                    calls = []
                    original_read, original_write, original_replace = Path.read_text, Path.write_text, Path.replace

                    def run(command, **kwargs):
                        name = Path(command[1]).stem
                        calls.append(name)
                        if name in ("figma_mcp_to_json", "extract_design_fields"):
                            output = Path(command[command.index("--output") + 1])
                            broken = (failure == "converted_json" and name == "figma_mcp_to_json" or
                                      failure == "model_json" and name == "extract_design_fields")
                            payload = "{broken" if broken else '{"fresh": true}'
                            if (failure == "converted_shape" and name == "figma_mcp_to_json" or
                                    failure == "model_shape" and name == "extract_design_fields"):
                                payload = "[]"
                            if failure == "model_null" and name == "extract_design_fields":
                                payload = "null"
                            if failure == "meta_shape" and name == "extract_design_fields":
                                payload = '{"meta": null}'
                            output.write_text(payload, encoding="utf-8")
                        return SimpleNamespace(returncode=0, stdout="", stderr="")

                    def read(path, *args, **kwargs):
                        if failure == "read_error" and path.name == "design_model.json" and path.parent != design_dir:
                            raise PermissionError("合成读取失败")
                        return original_read(path, *args, **kwargs)

                    def write(path, content, *args, **kwargs):
                        if failure == "write_error" and path.name == "design_model.json" and '"source"' in content:
                            raise PermissionError("合成发布元数据写入失败")
                        return original_write(path, content, *args, **kwargs)

                    def replace(path, target):
                        failing = "figma-mcp.json" if failure == "first_replace" else "design_model.json"
                        if failure in ("first_replace", "second_replace") and path.name == failing and Path(target).parent == design_dir:
                            raise PermissionError("合成发布替换失败")
                        return original_replace(path, target)

                    with mock.patch.object(Path, "read_text", read), mock.patch.object(Path, "write_text", write), \
                            mock.patch.object(Path, "replace", replace):
                        rc = self._main(["--workdir", root / "wd", "--outdir", outdir,
                                         "--figma-mcp-raw", root / "raw.txt", "--page-url", "https://example.test/",
                                         "--no-patterns"], run)
                    self.assertEqual(rc, 1)
                    self.assertEqual(calls, ["figma_mcp_to_json", "extract_design_fields"])
                    log = json.loads((outdir / "pipeline-log.json").read_text(encoding="utf-8"))
                    self.assertEqual(log["design_input_publish"]["status"], "failed")
                    self.assertIn("design_input_publish", log["failedStages"])
                    self.assertEqual(log["pipelineStatus"], "degraded")
                    for name in ("stabilize", "design_contrast", "compare_fields", "discover_scenarios"):
                        self.assertEqual(log[name]["status"], "notRun")
                        self.assertIn("design_input_publish", log[name]["reason"])
                    for name in ("figma-mcp.json", "design_model.json"):
                        target = design_dir / name
                        if previous:
                            self.assertEqual(target.read_bytes(), old[target], "发布失败必须保留成对的旧版本")
                        else:
                            self.assertFalse(target.exists(), "首次发布失败不能留下单个新版本")
                    self.assertFalse(list(design_dir.glob("figma-input-*")))

    def _shots(self, root, *, failure=None, policy="safe", extra=(), producer=None):
        report, outdir = root / "report.json", root / "out"
        report.write_text(json.dumps({"defects": [{"id": "d1", "screenshots": {"page": "old.png"},
                                                   "shotBox": {"x": 1}, "shotNote": "旧截图说明",
                                                   "designNote": "旧设计对比说明"}],
                                      "defectShots": {"status": "ran", "count": 1, "captured": 1}}), encoding="utf-8")
        calls = []

        def run(command, **kwargs):
            name = Path(command[1]).stem
            calls.append(command)
            if name == "capture_defect_shots":
                if producer is not None:
                    return producer(report, root)
                if failure == "missing":
                    raise FileNotFoundError("合成缺失命令")
                if failure == "timeout":
                    raise subprocess.TimeoutExpired(command, 600)
                if failure == "nonzero":
                    return SimpleNamespace(returncode=1, stdout="", stderr="合成截图失败")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        argv = ["--workdir", root / "wd", "--outdir", outdir, "--report", report,
                "--page-url", "https://example.test/", "--figma-png", root / "design.png",
                "--safety-policy", policy, "--align-sidebar", "220", "--no-patterns",
                *(["--test-environment-confirmed"] if policy == "all" else []), *extra]
        rc = self._main(argv, run)
        log = {} if "--dry-run" in extra else json.loads((outdir / "pipeline-log.json").read_text(encoding="utf-8"))
        return rc, log, calls, json.loads(report.read_text(encoding="utf-8"))

    def test_failed_or_unstarted_shots_block_compare_and_remove_stale_references(self):
        for failure in ("nonzero", "missing", "timeout"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                rc, log, calls, report = self._shots(Path(tmp), failure=failure)
                self.assertEqual(rc, 1)
                self.assertNotIn("design_compare", [Path(command[1]).stem for command in calls])
                self.assertEqual(log["design_compare"]["status"], "notRun")
                self.assertIn("capture_defect_shots", log["design_compare"]["reason"])
                self.assertIn("capture_defect_shots", log["failedStages"])
                defect = report["defects"][0]
                self.assertEqual(defect.get("screenshots"), [], "截图失败不得继续引用旧截图")
                for field in ("shotBox", "shotNote", "designNote"):
                    self.assertNotIn(field, defect)
                self.assertIn("capture_defect_shots", defect.get("shotError", ""))
                self.assertEqual(report["defectShots"]["status"], "failed")
                self.assertIn("capture_defect_shots", report["defectShots"]["reason"])
                self.assertEqual(report["defectShots"]["count"], 0)

    def test_partial_capture_preserves_only_current_run_evidence_and_skips_compare(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            observed = {}
            fresh_shot = root / "fresh.crop.png"

            def producer(report_path, _):
                before = json.loads(report_path.read_text(encoding="utf-8"))
                observed.update(before)
                fresh_shot.write_bytes(b"synthetic current image")
                before["defects"] = [
                    {"id": "d1", "screenshots": [{"kind": "crop", "path": str(fresh_shot)}],
                     "shotBox": {"x": 2}, "shotNote": "本轮局部"},
                    {"id": "d2", "screenshots": [], "shotError": "本轮元素未找到"}]
                before["defectShots"] = {"total": 2, "captured": 1, "failed": 1}
                report_path.write_text(json.dumps(before), encoding="utf-8")
                return SimpleNamespace(returncode=3, stdout="部分成功", stderr="")

            rc, log, calls, report = self._shots(root, producer=producer)
            self.assertEqual(observed["defects"][0].get("screenshots"), [], "启动截图前必须撤旧引用")
            self.assertNotIn("shotBox", observed["defects"][0])
            self.assertNotEqual(observed.get("defectShots", {}).get("captured"), 1,
                                "不能让本轮producer继承旧成功摘要")
            self.assertEqual(rc, 1, "部分失败仍须非零退出")
            self.assertNotIn("design_compare", [Path(command[1]).stem for command in calls])
            self.assertIn("capture_defect_shots", log["design_compare"]["reason"])
            self.assertEqual(report["defects"][0]["screenshots"], [{"kind": "crop", "path": str(fresh_shot)}])
            self.assertEqual(report["defects"][0]["shotBox"], {"x": 2})
            self.assertEqual(report["defects"][0]["shotNote"], "本轮局部")
            self.assertEqual(report["defects"][1]["shotError"], "本轮元素未找到")
            self.assertEqual(report["defectShots"]["captured"], 1)
            self.assertEqual(report["defectShots"]["failed"], 1)
            self.assertEqual(report["defectShots"]["status"], "partial")
            self.assertTrue(fresh_shot.exists())

    def test_shots_forward_safety_and_static_only_omits_alignment(self):
        for policy in ("all", "safe", "mockWrites", "staticOnly"):
            with self.subTest(policy=policy), tempfile.TemporaryDirectory() as tmp:
                rc, log, calls, _ = self._shots(Path(tmp), policy=policy)
                self.assertEqual(rc, 0)
                shot = next(command for command in calls if Path(command[1]).stem == "capture_defect_shots")
                self.assertIn("--safety-policy", shot)
                self.assertEqual(shot[shot.index("--safety-policy") + 1], policy)
                self.assertIn("design_compare", [Path(command[1]).stem for command in calls])
                if policy == "staticOnly":
                    self.assertNotIn("--align-sidebar", shot)
                    self.assertIn("staticOnly", log["capture_defect_shots"].get("alignmentReason", ""))
                else:
                    self.assertEqual(shot[shot.index("--align-sidebar") + 1], "220")

    def test_plan_and_gate_use_same_run_root_as_artifacts(self):
        """阻断回归：计划/对账必须与驱动产物同根（workdir），而不是报告子目录。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(RPP, "stage", wraps=RPP.stage) as stages:
                rc, _, _, _ = self._shots(root, policy="staticOnly")
            self.assertEqual(rc, 0)
            planned = {call.args[0]: call.args[1] for call in stages.call_args_list}
            run_root = str(root / "wd")
            self.assertEqual(planned["plan_run"][planned["plan_run"].index("--run-dir") + 1], run_root)
            self.assertEqual(planned["pre_assemble_gate"][planned["pre_assemble_gate"].index("--run-dir") + 1], run_root)
            self.assertEqual(planned["pre_assemble_gate"][planned["pre_assemble_gate"].index("--plan") + 1],
                             str(root / "wd" / "run-plan.json"))

    def test_all_policy_requires_environment_confirmation(self):
        """all 必须带 --test-environment-confirmed：驱动层只拦 danger，caution 会真实点击。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = root / "report.json"
            report.write_text(json.dumps({"defects": []}), encoding="utf-8")
            argv = ["--workdir", root / "wd", "--outdir", root / "out", "--report", report,
                    "--page-url", "https://example.test/", "--safety-policy", "all",
                    "--no-patterns"]
            rc = self._main(argv, lambda command, **kwargs: SimpleNamespace(
                returncode=0, stdout="", stderr=""))
            self.assertEqual(rc, 2)
            self.assertFalse((root / "out" / "pipeline-log.json").exists(),
                             "未确认隔离环境时不得产生任何产物")

    def test_dry_run_plans_compare_without_launching_or_mutating_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(RPP, "stage", wraps=RPP.stage) as stages:
                rc, _, calls, report = self._shots(root, policy="staticOnly", extra=("--dry-run",))
            self.assertEqual(rc, 0)
            self.assertEqual(calls, [])
            self.assertFalse((root / "wd").exists())
            self.assertFalse((root / "out").exists())
            self.assertEqual(report["defects"][0]["screenshots"], {"page": "old.png"})
            planned = {call.args[0]: call.args[1] for call in stages.call_args_list}
            self.assertIn("design_compare", planned)
            self.assertIn("--safety-policy", planned["capture_defect_shots"])
            self.assertNotIn("--align-sidebar", planned["capture_defect_shots"])

    def test_explicit_shot_skip_is_not_a_command_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, log, calls, _ = self._shots(Path(tmp), extra=("--no-defect-shots",))
            self.assertEqual(rc, 0)
            names = [Path(command[1]).stem for command in calls]
            self.assertNotIn("capture_defect_shots", names)
            self.assertNotIn("design_compare", names)
            self.assertEqual(log["capture_defect_shots"]["status"], "notRun")
            self.assertNotIn("failedStages", log)

    def test_real_command_not_run_is_failure_but_optional_not_run_is_not(self):
        # v4.45.0 门禁接线后：注入范围收窄到 diff_score——plan_run 在它之前，
        # 若连门禁一起注入失败，管线在 plan_run 就熔断（rc=2），测不到本例的
        # "非门禁 stage 失败 → rc=1 + degraded" 语义（那由 test_gates_resilience
        # 的门禁熔断用例承接）。
        for failure in (FileNotFoundError("合成缺失"), PermissionError("合成权限"),
                        subprocess.TimeoutExpired(["synthetic"], 600)):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)

                def run(command, **kwargs):
                    if Path(command[1]).stem == "diff_score":
                        raise failure
                    return SimpleNamespace(returncode=0, stdout="", stderr="")

                rc = self._main(["--workdir", root / "wd", "--outdir", root / "out",
                                 "--figma-png", root / "a.png", "--page-png", root / "b.png"], run)
                log = json.loads((root / "out" / "pipeline-log.json").read_text(encoding="utf-8"))
                self.assertEqual(rc, 1)
                self.assertEqual(log["diff_score"]["status"], "notRun")
                self.assertEqual(log["failedStages"], ["diff_score"])
                self.assertEqual(log["generate_reports"]["status"], "notRun")
                self.assertNotIn("generate_reports", log["failedStages"])


if __name__ == "__main__":
    unittest.main()