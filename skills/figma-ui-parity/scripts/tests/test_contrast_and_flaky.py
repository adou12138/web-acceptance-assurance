#!/usr/bin/env python3
"""7.42 设计稿对比度与 flaky 统计的行为测试。

为什么单独写：这两个模块的**判定口径**是它们全部的价值所在——
对比度如果不区分「纯色底 / 图片底 / 渐变底 / 半透明祖先」，就会在暗色页和图片横幅上
批量误报；flaky 如果不把「notRun」与「状态翻转」分开，历史越久越容易得出"一直很稳定"。
只断言"文件里有某段字符串"挡不住这类错误，必须驱动真实入口、看真实产物。
"""
import json
import tempfile
import unittest
from pathlib import Path

import sys

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import design_contrast  # noqa: E402
import flaky_report  # noqa: E402


def solid(r, g, b, alpha=1.0):
    return {"type": "SOLID", "visible": True, "opacity": 1,
            "color": {"rgb": [r, g, b], "hex": "#%02x%02x%02x" % (r, g, b),
                      "alpha": alpha, "css": "rgba(%d,%d,%d,%s)" % (r, g, b, alpha)}}


def text_node(name, chars, color, size=14, weight=400, opacity=1.0, visible=True):
    return {"id": "t-" + name, "name": name, "type": "TEXT", "visible": visible, "opacity": opacity,
            "rect": {"x": 0, "y": 0, "w": 100, "h": 20},
            "fills": [color] if color else [],
            "text": {"characters": chars, "fontSize": size, "fontWeight": weight}}


def frame(node_id, name, fills, children=None, opacity=1.0):
    return {"id": node_id, "name": name, "type": "FRAME", "visible": True, "opacity": opacity,
            "rect": {"x": 0, "y": 0, "w": 375, "h": 200}, "fills": fills,
            "children": children or []}


class DesignContrastTests(unittest.TestCase):
    def analyse(self, tree):
        return design_contrast.analyse({"tree": tree, "rootId": tree.get("id")})

    def test_gray_on_white_is_confirmed(self):
        tree = frame("root", "页", [solid(255, 255, 255)],
                     [text_node("灰字", "提交订单", solid(153, 153, 153))])
        result = self.analyse(tree)
        confirmed = [f for f in result["findings"] if f["status"] == "confirmed"]
        self.assertEqual(len(confirmed), 1, json.dumps(result["findings"], ensure_ascii=False))
        self.assertEqual(confirmed[0]["dimension"], "7.42")
        self.assertEqual(confirmed[0]["raw"]["ratio"], 2.85)
        self.assertEqual(result["status"], "fail")

    def test_white_on_dark_card_passes(self):
        """反向用例：达标就不该产 finding，否则整个维度是噪音。"""
        card = frame("card", "深色卡片", [solid(30, 30, 30)],
                     [text_node("白字", "立即购买", solid(255, 255, 255))])
        result = self.analyse(frame("root", "页", [solid(255, 255, 255)], [card]))
        # 达标时不产缺陷，但要留一条 pass 证据：否则"跑过且干净"会被当成 notRun
        self.assertEqual([f for f in result["findings"] if f["status"] != "pass"], [])
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(result["findings"][0]["property"], "design-contrast-pass")
        self.assertEqual(result["status"], "pass")

    def test_image_backdrop_is_pending_not_confirmed(self):
        """文字压在图片上：拿不到像素就只能 pending，按白底硬算等于编证据。"""
        banner = frame("banner", "Banner",
                       [{"type": "IMAGE", "visible": True, "opacity": 1, "imageRef": "a", "scaleMode": "FILL"}],
                       [text_node("图上字", "限时特惠", solid(255, 255, 255), 20, 700)])
        result = self.analyse(frame("root", "页", [solid(255, 255, 255)], [banner]))
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(result["findings"][0]["status"], "pending")
        self.assertEqual(result["findings"][0]["raw"]["skipReason"], "image-backdrop")

    def test_gradient_partial_failure_is_pending(self):
        """渐变底色：只有部分色标不达标时不能确认——文字可能根本不落在那一段。"""
        grad = frame("grad", "渐变", [{"type": "GRADIENT_LINEAR", "visible": True, "opacity": 1,
                                       "stops": [{"position": 0, "color": solid(20, 20, 20)["color"]},
                                                 {"position": 1, "color": solid(240, 240, 240)["color"]}]}],
                     [text_node("渐变字", "活动说明", solid(150, 150, 150))])
        result = self.analyse(frame("root", "页", [solid(255, 255, 255)], [grad]))
        hits = [f for f in result["findings"] if f["property"] == "design-contrast"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["status"], "pending")
        self.assertGreater(hits[0]["raw"]["candidateBackdrops"], 1)

    def test_gradient_all_fail_is_confirmed(self):
        """所有色标都不达标 → 无论文字落在哪都是不可读，可以确认。"""
        grad = frame("grad", "渐变", [{"type": "GRADIENT_LINEAR", "visible": True, "opacity": 1,
                                       "stops": [{"position": 0, "color": solid(235, 235, 235)["color"]},
                                                 {"position": 1, "color": solid(245, 245, 245)["color"]}]}],
                     [text_node("渐变字", "活动说明", solid(240, 240, 240))])
        result = self.analyse(frame("root", "页", [solid(255, 255, 255)], [grad]))
        hits = [f for f in result["findings"] if f["property"] == "design-contrast"]
        self.assertEqual(hits[0]["status"], "confirmed")

    def test_transparent_root_is_pending(self):
        """顶层画板无填充：按白底算，但结论必须是 pending——落地底色由实现决定。"""
        result = self.analyse(frame("root", "页", [], [text_node("灰字", "文案", solid(150, 150, 150))]))
        self.assertEqual(result["findings"][0]["status"], "pending")

    def test_semi_transparent_ancestor_is_pending(self):
        ghost = frame("ghost", "半透明层", [solid(255, 255, 255)],
                      [text_node("浅字", "已失效", solid(180, 180, 180))], opacity=0.4)
        result = self.analyse(frame("root", "页", [solid(255, 255, 255)], [ghost]))
        self.assertEqual(result["findings"][0]["status"], "pending")

    def test_hidden_text_not_checked(self):
        tree = frame("root", "页", [solid(255, 255, 255)],
                     [text_node("隐藏", "不显示", solid(200, 200, 200), visible=False)])
        result = self.analyse(tree)
        self.assertEqual(result["findings"], [])

    def test_large_text_uses_three_to_one(self):
        """大字门槛 3:1：白字压 #949494 约 3.0:1，按正文 4.5 会误报。"""
        tree = frame("root", "页", [solid(148, 148, 148)],
                     [text_node("大字", "限时", solid(255, 255, 255), size=26, weight=700)])
        result = self.analyse(tree)
        self.assertEqual([f for f in result["findings"] if f["status"] != "pass"], [],
                         "大字门槛未生效：按 4.5 判了")

    def test_flat_only_model_reports_notrun(self):
        result = design_contrast.analyse({"flat": [text_node("a", "x", solid(0, 0, 0))]})
        self.assertEqual(result["status"], "notRun")
        self.assertIn("tree", result["reason"])

    def test_contract_fields_present(self):
        tree = frame("root", "页", [solid(255, 255, 255)],
                     [text_node("灰字", "提交", solid(153, 153, 153))])
        finding = self.analyse(tree)["findings"][0]
        for key in ("dimension", "status", "property", "severity", "severityHint",
                    "confidence", "symptom", "expected", "actual", "element", "sources"):
            self.assertIn(key, finding, "缺陷条目缺字段 {}".format(key))
        self.assertEqual(finding["element"]["locatorQuality"], "stable")


class FlakyReportTests(unittest.TestCase):
    def write_runs(self, root: Path, safe_key: str, runs):
        runs_dir = root / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        for label, payload in runs:
            (runs_dir / "{}.{}.json".format(safe_key, label)).write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return runs_dir

    def test_flip_detected_and_stable_failure_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = [
                ("2026-10-24T00-00-00", {"_run": "2026-10-24T00-00-00", "checks": {"7.1": "pass", "7.41": "pass", "7.33": "fail"}}),
                ("2026-10-25T00-00-00", {"_run": "2026-10-25T00-00-00", "checks": {"7.1": "pass", "7.41": "fail", "7.33": "fail"}}),
                ("2026-10-26T00-00-00", {"_run": "2026-10-26T00-00-00", "checks": {"7.1": "pass", "7.41": "pass", "7.33": "fail"}}),
            ]
            self.write_runs(root, "k" * 16, runs)
            groups, broken, legacy = flaky_report.load_runs(root / "runs")
            payload = flaky_report.analyze(groups)
            self.assertEqual(payload["summary"]["flakyDimensions"], ["7.41"])
            self.assertEqual(payload["flakyRanking"][0]["flips"], 2)
            # 常年 fail 是稳定缺陷，不是 flaky
            block = payload["byKey"]["k" * 16]
            verdicts = {d["dimension"]: d["verdict"] for d in block["dimensions"]}
            self.assertEqual(verdicts["7.33"], "stable-fail")
            self.assertEqual(verdicts["7.1"], "stable-pass")
            self.assertEqual(broken, [])
            self.assertEqual(legacy, 0)

    def test_notrun_gap_does_not_count_as_flip(self):
        """某轮 notRun（换档位/换策略）不是"变了"，跳过该区间。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            label = lambda d: {"_run": d, "checks": {}}
            runs = [
                ("2026-10-24T00-00-00", {"_run": "2026-10-24T00-00-00", "checks": {"7.43": "pass"}}),
                ("2026-10-25T00-00-00", {"_run": "2026-10-25T00-00-00", "checks": {"7.43": "notRun"}}),
                ("2026-10-26T00-00-00", {"_run": "2026-10-26T00-00-00", "checks": {"7.43": "pass"}}),
            ]
            self.write_runs(root, "a" * 16, runs)
            groups, _, _ = flaky_report.load_runs(root / "runs")
            payload = flaky_report.analyze(groups)
            self.assertEqual(payload["summary"]["flakyCount"], 0)
            entry = payload["byKey"]["a" * 16]["dimensions"][0]
            self.assertEqual(entry["runs"], 2, "notRun 不应进入序列")
            self.assertEqual(entry["flips"], 0)

    def test_legacy_runs_reported_not_silently_ignored(self):
        """缺 checks 的旧快照必须点名：静默跳过会得出"历史一直很稳定"的错误结论。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_runs(root, "b" * 16, [
                ("2026-10-24T00-00-00", {"_run": "2026-10-24T00-00-00"}),
                ("2026-10-25T00-00-00", {"_run": "2026-10-25T00-00-00"}),
            ])
            groups, _, legacy = flaky_report.load_runs(root / "runs")
            self.assertEqual(groups, {})
            self.assertEqual(legacy, 2)

    def test_single_run_key_is_insufficient(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_runs(root, "c" * 16, [
                ("2026-10-24T00-00-00", {"_run": "2026-10-24T00-00-00", "checks": {"7.1": "pass"}}),
            ])
            groups, _, _ = flaky_report.load_runs(root / "runs")
            payload = flaky_report.analyze(groups, min_runs=2)
            self.assertEqual(payload["summary"]["keysAnalysed"], 0)
            self.assertEqual(payload["summary"]["insufficientKeys"], 1)

    def test_drift_without_crossing_pass_boundary(self):
        """pending ↔ fail 不跨 pass 边界：不算 flaky，但要单列 drift-only。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_runs(root, "d" * 16, [
                ("2026-10-24T00-00-00", {"_run": "2026-10-24T00-00-00", "checks": {"7.36": "pending"}}),
                ("2026-10-25T00-00-00", {"_run": "2026-10-25T00-00-00", "checks": {"7.36": "fail"}}),
            ])
            groups, _, _ = flaky_report.load_runs(root / "runs")
            payload = flaky_report.analyze(groups)
            self.assertEqual(payload["summary"]["flakyCount"], 0)
            entry = payload["byKey"]["d" * 16]["dimensions"][0]
            self.assertEqual(entry["flips"], 0)
            self.assertEqual(entry["statusDrift"], 1)
            self.assertEqual(entry["verdict"], "drift-only")

    def test_csv_is_utf8_sig_with_bom(self):
        """交付格式是 CSV（utf-8-sig）：Excel 直接打开不能是乱码。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_runs(root, "e" * 16, [
                ("2026-10-24T00-00-00", {"_run": "2026-10-24T00-00-00", "checks": {"7.41": "pass"}}),
                ("2026-10-25T00-00-00", {"_run": "2026-10-25T00-00-00", "checks": {"7.41": "fail"}}),
            ])
            groups, _, _ = flaky_report.load_runs(root / "runs")
            payload = flaky_report.analyze(groups)
            out = root / "flaky.csv"
            flaky_report.write_csv(payload, out)
            raw = out.read_bytes()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), "CSV 必须带 UTF-8 BOM")
            body = raw.decode("utf-8-sig")
            self.assertIn("7.41", body)
            self.assertIn("flaky", body)


if __name__ == "__main__":
    unittest.main()
