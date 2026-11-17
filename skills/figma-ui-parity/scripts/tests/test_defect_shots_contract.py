"""缺陷附图的契约：schema 字段、命名规则、报告渲染、CSV 列。

这几处最容易被改坏的顺序是：截图先有、报告后改；或者反过来——
两种都会出现「报告里有附图这一列，但永远是空的」或「文件名与序号对不上」。

实测踩过的两个坑，都在这里锁住：
1. 截图文件名带序号 → 报告渲染前按严重度重排，CSV 第 1 条指向第 13 张图。
2. 无图时不写原因 → 空单元格被读成「这条缺陷没有位置信息」，
   而事实可能是「元素没找到」，处理方式完全不同。
"""

import csv
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
SCRIPTS = ROOT / "scripts"


def run(cmd, cwd):
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=180)


class DefectShotsContractTests(unittest.TestCase):
    def test_schema_declares_screenshot_fields(self):
        d = json.loads((ROOT / "schemas" / "defect.schema.json").read_text(encoding="utf-8"))
        props = d["properties"]
        for name in ("screenshots", "shotError", "shotNote"):
            self.assertIn(name, props, "schema 缺少 {}".format(name))
        kinds = props["screenshots"]["items"]["properties"]["kind"]["enum"]
        self.assertEqual(sorted(kinds), sorted(["crop", "page", "design", "compare", "region", "none"]),
                         "kind 枚举必须包含页面、设计稿、并排对比与区域定位语义")



# 浏览器与页面只替换外部边界，报告读写、截图命名、错误处理均执行真实脚本。
CAPTURE_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const cfg = JSON.parse(process.argv[1]);
const events = [];
let routeHandler = null;
let sidebarWidth = 48;
function locator(selector, nth = 0) {
  const count = selector.includes('password') ? (cfg.password ? 1 : 0)
    : selector === 'span.badge' ? (cfg.count === undefined ? 1 : cfg.count)
    : cfg.expandButton ? 1 : 0;
  const check = () => { if (nth < 0 || nth >= count) throw new Error('nth 越界'); };
  return {
    count: async () => count,
    first: () => locator(selector, 0),
    nth: n => locator(selector, n),
    filter: () => locator(selector, nth),
    click: async () => { check(); events.push('click'); sidebarWidth = 231; },
    scrollIntoViewIfNeeded: async () => { check(); },
    boundingBox: async () => { check(); return { x: 10, y: 10, width: 40, height: 16 }; },
    evaluate: async () => { check(); return { outline: '', offset: '' }; },
    screenshot: async opts => { check(); events.push('crop'); if (cfg.shotFail) throw new Error('截图失败'); fs.writeFileSync(opts.path, 'new'); },
  };
}
const page = {
  url: () => cfg.finalUrl || cfg.url,
  goto: async () => {
    events.push('goto');
    if (routeHandler) for (const method of ['GET', 'HEAD', 'OPTIONS', 'POST', 'PUT', 'PATCH', 'DELETE', 'PROPFIND']) {
      await routeHandler({ request: () => ({ method: () => method, url: () => cfg.url }),
        continue: async () => events.push('allow:' + method),
        abort: async () => events.push('block:' + method),
        fulfill: async () => events.push('mock:' + method) });
    }
    if (cfg.gotoFail) throw new Error('导航超时');
  },
  locator,
  waitForLoadState: async () => {},
  waitForTimeout: async () => {
    // 模拟「导航成功之后」的业务写请求：此时防护应按 mockWrites 拦截。
    if (routeHandler && !events.includes('postnav-dispatch')) {
      events.push('postnav-dispatch');
      for (const method of ['POST', 'PUT', 'PATCH', 'DELETE', 'PROPFIND']) {
        await routeHandler({ request: () => ({ method: () => method, url: () => cfg.url }),
          continue: async () => events.push('allow:' + method),
          abort: async () => events.push('block:' + method),
          fulfill: async () => events.push('mock:' + method) });
      }
    }
  },
  evaluate: async fn => String(fn).includes('viewportH') ? { width: sidebarWidth, height: 900 }
    : String(fn).includes('input[type=password]') ? (cfg.password === true)
    : String(fn).includes('innerText') ? (cfg.thinPage ? 30 : 1500)
    : String(fn).includes('querySelectorAll') ? { x: 20, y: 20 } : 0,
  mouse: { click: async () => { events.push('coordinate-click'); sidebarWidth = 231; } },
  screenshot: async opts => { events.push('page'); if (cfg.shotFail) throw new Error('截图失败'); fs.writeFileSync(opts.path, 'new'); },
};
const context = {
  pages: () => [page], newPage: async () => page,
  route: async (_pattern, handler) => { events.push('route'); routeHandler = handler; },
};
const browser = {
  launch: async () => { events.push('launch'); if (cfg.launchThrows) throw new Error('启动异常'); return cfg.launchFail ? { error: '启动失败' } : { context }; },
  restoreAuth: async () => ({ ok: true }),
  closeAll: async () => { events.push('closed'); },
};
const fakeProcess = {
  argv: ['node', cfg.script, '--report', cfg.report, '--url', cfg.url, '--out', cfg.out, '--settle', '0', ...(cfg.args || [])],
  cwd: () => cfg.out, exitCode: 0,
  exit: code => { fakeProcess.exitCode = code; events.push('process-exit'); throw new Error('process.exit:' + code); },
};
(async () => {
  const scriptDir = require('path').dirname(cfg.script);
  const path = require('path');
  await vm.runInNewContext(fs.readFileSync(cfg.script, 'utf8'), {
    require: name => {
      if (name === './browser.cjs') return browser;
      if (name.startsWith('./')) return require(path.join(scriptDir, name));
      return require(name);
    },
    process: fakeProcess, console: { log() {}, warn() {}, error() {} }, URL, setTimeout,
  }, { filename: cfg.script });
  process.stdout.write(JSON.stringify({ events, code: fakeProcess.exitCode }));
})().catch(error => {
  if (String(error.message).startsWith('process.exit:')) process.stdout.write(JSON.stringify({ events, code: fakeProcess.exitCode }));
  else { console.error(error); process.exitCode = 99; }
});
"""


class CaptureBehaviorTests(unittest.TestCase):
    def capture(self, cfg=None, defects=None, args=None, setup=None):
        cfg = dict(cfg or {})
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            default = {"fingerprint": "abc12345", "dimension": "7.41",
                       "element": {"selector": "span.badge", "nth": 0}}
            report = tmp / "report.json"
            report.write_text(json.dumps({"url": "https://x.test/", "defects": defects if defects is not None else [default]}), encoding="utf-8")
            if setup:
                setup(tmp)
            cfg.update(script=str(SCRIPTS / "node" / "capture_defect_shots.cjs"),
                       report=str(report), out=str(tmp), url="https://x.test/", args=args or [])
            result = run(["node", "-e", CAPTURE_HARNESS, json.dumps(cfg)], ROOT)
            self.assertEqual(result.returncode, 0, result.stderr)
            state = json.loads(result.stdout)
            data = json.loads(report.read_text(encoding="utf-8"))
            state["files"] = [str(p.relative_to(tmp)) for p in tmp.rglob("*.png")]
            return state, data

    def test_names_are_fingerprint_based_and_unique(self):
        defects = [{"fingerprint": "abc12345", "dimension": "7.41", "element": {"selector": "span.badge"}} for _ in range(2)]
        state, data = self.capture(defects=defects)
        self.assertEqual(state["code"], 0)
        names = [pathlib.Path(d["screenshots"][0]["path"]).name for d in data["defects"]]
        self.assertEqual(names, ["abc12345-7.41.crop.png", "abc12345-7.41-2.crop.png"])

    def test_default_static_only_does_not_click_sidebar(self):
        state, data = self.capture({"expandButton": True}, args=["--align-sidebar", "231"])
        self.assertNotIn("click", state["events"])
        self.assertFalse(data["defectShots"]["sidebar"]["ok"])
        self.assertIn("staticOnly", data["defectShots"]["sidebar"]["reason"])

    def test_mock_writes_routes_before_navigation_and_blocks_every_write_method(self):
        state, _ = self.capture(args=["--safety-policy", "mockWrites"])
        self.assertIn("route", state["events"])
        self.assertLess(state["events"].index("route"), state["events"].index("goto"))
        for method in ("GET", "HEAD", "OPTIONS"):
            self.assertIn("allow:" + method, state["events"])
        for method in ("POST", "PUT", "PATCH", "DELETE", "PROPFIND"):
            self.assertTrue(any(prefix + method in state["events"] for prefix in ("block:", "mock:")), method)

    def test_sidebar_never_falls_back_to_coordinate_click(self):
        state, data = self.capture(args=["--safety-policy", "mockWrites", "--align-sidebar", "231"])
        self.assertNotIn("coordinate-click", state["events"])
        self.assertFalse(data["defectShots"]["sidebar"]["acted"])
        self.assertTrue(data["defectShots"]["sidebar"]["reason"])

    def test_explicit_expand_candidate_remains_available_with_mock_writes(self):
        state, data = self.capture({"expandButton": True}, args=["--safety-policy", "mockWrites", "--align-sidebar", "231"])
        self.assertIn("click", state["events"])
        self.assertTrue(data["defectShots"]["sidebar"]["ok"])

    def test_pipeline_safe_and_all_policies_allow_explicit_expand(self):
        for policy in ("safe", "all"):
            with self.subTest(policy=policy):
                state, data = self.capture({"expandButton": True}, args=["--safety-policy", policy, "--align-sidebar", "231"])
                self.assertEqual(state["code"], 0)
                self.assertIn("click", state["events"])
                self.assertTrue(data["defectShots"]["sidebar"]["ok"])

    def test_one_rejected_defect_makes_partial_capture_nonzero(self):
        state, data = self.capture(defects=[{"element": {"selector": "span.badge"}},
                                                   {"page": "https://x.test/elsewhere", "element": {}}])
        self.assertNotEqual(state["code"], 0)
        self.assertEqual(data["defectShots"]["captured"], 1)
        self.assertEqual(data["defectShots"]["failed"], 1)
        self.assertIn("closed", state["events"])
        self.assertNotIn("process-exit", state["events"])

    def test_page_level_shot_and_max_limit_keep_fresh_reason(self):
        state, data = self.capture(defects=[{"element": {}}, {"element": {}, "shotBox": {"w": 99},
                                                   "screenshots": [{"path": "old.png"}], "designNote": "旧设计备注"}],
                                   args=["--max", "1"])
        self.assertEqual(state["code"], 0)
        self.assertEqual(data["defects"][0]["screenshots"][0]["kind"], "page")
        self.assertFalse(data["defects"][1]["screenshots"])
        self.assertIn("--max", data["defects"][1]["shotNote"])
        self.assertNotIn("shotBox", data["defects"][1])
        self.assertNotIn("designNote", data["defects"][1])

    def test_launch_failure_removes_all_old_attachment_fields_and_persists_reason(self):
        old = {"fingerprint": "abc12345", "element": {}, "screenshots": [{"kind": "crop", "path": "old.png"}],
               "shotBox": {"w": 9}, "shotError": "旧错误", "shotNote": "旧备注", "designNote": "旧设计备注"}
        for failure in ("launchFail", "launchThrows"):
            with self.subTest(failure=failure):
                state, data = self.capture({failure: True}, defects=[old])
                self.assertNotEqual(state["code"], 0)
                d = data["defects"][0]
                self.assertFalse(d.get("screenshots"))
                for key in ("shotBox", "shotNote", "designNote"):
                    self.assertNotIn(key, d)
                self.assertIn("启动", d["shotError"])
                self.assertNotEqual(d["shotError"], "旧错误")

    def test_goto_failure_is_reported_without_screenshots_and_closes_browser(self):
        state, data = self.capture({"gotoFail": True})
        self.assertNotEqual(state["code"], 0)
        self.assertNotIn("page", state["events"])
        self.assertNotIn("crop", state["events"])
        self.assertIn("页面打开失败", data["defects"][0]["shotError"])
        self.assertIn("closed", state["events"])

    def test_login_url_or_password_refuses_capture(self):
        for cfg in ({"finalUrl": "https://x.test/login"}, {"finalUrl": "https://x.test/signin"},
                    {"finalUrl": "https://passport.x.test/"}, {"password": True}):
            with self.subTest(cfg=cfg):
                state, data = self.capture(cfg)
                self.assertNotEqual(state["code"], 0)
                self.assertNotIn("page", state["events"])
                self.assertNotIn("crop", state["events"])
                self.assertIn("登录", data["defects"][0]["shotError"])
                self.assertIn("closed", state["events"])

    def test_missing_and_out_of_range_elements_never_get_page_fallback(self):
        for count, nth in ((0, 0), (1, 1), (1, -1), (1, "broken")):
            with self.subTest(count=count, nth=nth):
                state, data = self.capture({"count": count}, defects=[{"element": {"selector": "span.badge", "nth": nth}}])
                self.assertNotEqual(state["code"], 0)
                self.assertNotIn("page", state["events"])
                self.assertNotIn("crop", state["events"])
                self.assertTrue(data["defects"][0]["screenshots"], "定位失败必须留下none原因条目")
                self.assertEqual(data["defects"][0]["screenshots"][0]["kind"], "none")
                self.assertTrue(data["defects"][0]["shotError"])
                self.assertIn("closed", state["events"])
                self.assertNotIn("process-exit", state["events"])

    def test_foreign_page_or_frame_is_not_captured_from_current_page(self):
        for origin in ({"page": "https://x.test/another"}, {"pageUrl": "https://other.test/"},
                       {"element": {"selector": "span.badge", "frame": "iframe#checkout"}},
                       {"frame": "child"}, {"element": {"selector": "span.badge", "isMainFrame": False}}):
            with self.subTest(origin=origin):
                defect = {"element": {"selector": "span.badge"}, **origin}
                state, data = self.capture(defects=[defect])
                self.assertNotEqual(state["code"], 0)
                self.assertNotIn("page", state["events"])
                self.assertNotIn("crop", state["events"])
                self.assertTrue(data["defects"][0]["shotError"])

    def test_known_nondefault_states_never_capture_default_matching_selector(self):
        known_states = ("dialog-open", "hover", "focus", "disabled", "loading", "error", "print", "dark", "打印", "暗色")
        for field in ("state", "scenario"):
            for value in known_states:
                with self.subTest(field=field, value=value):
                    state, data = self.capture(defects=[{field: value, "element": {"selector": "span.badge"}}])
                    self.assertNotEqual(state["code"], 0)
                    self.assertNotIn("crop", state["events"])
                    self.assertNotIn("page", state["events"])
                    self.assertEqual(data["defects"][0]["screenshots"][0]["kind"], "none")
                    self.assertIn("未复现", data["defects"][0]["shotError"])
                    self.assertIn(value, data["defects"][0]["shotError"])

    def test_default_state_and_ordinary_scenario_ids_remain_capturable(self):
        for values in ({"state": "default"}, {"state": "normal"}, {"state": "默认"},
                       {"scenario": "SC-101"}, {"scenario": "checkout-error-handling"},
                       {"state": "default", "scenario": "overview"}):
            with self.subTest(values=values):
                state, data = self.capture(defects=[{**values, "element": {"selector": "span.badge"}}])
                self.assertEqual(state["code"], 0)
                self.assertIn("crop", state["events"])
                self.assertEqual(data["defects"][0]["screenshots"][0]["kind"], "crop")

    def test_old_png_survives_but_failed_current_shot_never_uses_it(self):
        def setup(tmp):
            folder = tmp / "shots" / "defects"
            folder.mkdir(parents=True)
            for name in ("abc12345-7.41.crop.png", "abc12345-7.41.page.png"):
                (folder / name).write_bytes(b"old")
        state, data = self.capture({"shotFail": True}, setup=setup)
        self.assertNotEqual(state["code"], 0)
        self.assertEqual(len(state["files"]), 2)
        self.assertFalse(any(s.get("path") for s in data["defects"][0]["screenshots"]))
        self.assertIn("closed", state["events"])

    def test_successful_runs_use_isolated_output_directory(self):
        state, data = self.capture()
        self.assertEqual(state["code"], 0)
        shot = pathlib.Path(data["defects"][0]["screenshots"][0]["path"])
        self.assertEqual(shot.parent.parent.name, "defects")


class ReportShotRenderingTests(unittest.TestCase):
    def _report(self, tmp: pathlib.Path, shots: list) -> pathlib.Path:
        shots_dir = tmp / "shots"
        shots_dir.mkdir(parents=True, exist_ok=True)
        defect = {
            "fingerprint": "abc12345", "dimension": "7.41", "severity": "P2",
            "confidence": 0.8, "status": "confirmed",
            "symptom": "文本对比度不足：2.30:1", "expected": ">= 4.5:1", "actual": "2.30:1",
            "element": {"selector": "span.badge", "nth": 0, "bbox": {"x": 10, "y": 10, "w": 40, "h": 16}},
            "screenshots": shots,
        }
        payload = {"url": "https://x.test/", "defects": [defect]}
        p = tmp / "report.json"
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return p

    def test_html_inlines_small_crop_and_csv_exposes_path(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            img = tmp / "shots" / "abc12345-7.41.crop.png"
            img.parent.mkdir(parents=True, exist_ok=True)
            # 1x1 PNG 足够验证内联路径
            img.write_bytes(bytes.fromhex(
                "89504e470d0a1a0a0000000d494844520000000100000001080600000"
                "01f15c4890000000a49444154789c6300010000050001"
                "0d0a2db40000000049454e44ae426082"))
            rep = self._report(tmp, [{"kind": "crop", "path": str(img), "note": "缺陷元素局部"}])
            r = run([sys.executable, str(SCRIPTS / "generate_reports.py"),
                     "--input", str(rep), "--outdir", str(tmp / "reports")], ROOT)
            self.assertEqual(r.returncode, 0, (r.stderr or "")[-400:])
            html = (tmp / "reports" / "report.html").read_text(encoding="utf-8")
            self.assertIn("data:image/png;base64", html, "小图必须内联，否则报告不能单文件传阅")
            with (tmp / "reports" / "defects.csv").open(encoding="utf-8-sig") as fh:
                rows = list(csv.DictReader(fh))
            self.assertIn("截图", rows[0], "CSV 必须暴露截图列")
            self.assertIn("shots/", rows[0]["截图"].replace("\\", "/"))

    def test_missing_image_file_is_reported_not_silently_blank(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            rep = self._report(tmp, [{"kind": "crop", "path": str(tmp / "nope.png")}])
            r = run([sys.executable, str(SCRIPTS / "generate_reports.py"),
                     "--input", str(rep), "--outdir", str(tmp / "reports")], ROOT)
            self.assertEqual(r.returncode, 0, (r.stderr or "")[-400:])
            html = (tmp / "reports" / "report.html").read_text(encoding="utf-8")
            self.assertIn("图片缺失", html, "路径写了但文件不在，必须显式说明")

    def test_no_shot_shows_reason(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            rep = self._report(tmp, [])
            data = json.loads(rep.read_text(encoding="utf-8"))
            data["defects"][0]["shotError"] = "元素未找到（span.badge）"
            rep.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            r = run([sys.executable, str(SCRIPTS / "generate_reports.py"),
                     "--input", str(rep), "--outdir", str(tmp / "reports")], ROOT)
            self.assertEqual(r.returncode, 0, (r.stderr or "")[-400:])
            html = (tmp / "reports" / "report.html").read_text(encoding="utf-8")
            self.assertIn("无图：元素未找到", html, "没有图就要说清原因，不能留空")


if __name__ == "__main__":
    unittest.main()
