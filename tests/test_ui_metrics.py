"""界面可量化标准（ui_metrics.py）的机械行为。

判据全部来自公开规范，不自定义审美评分：
- WCAG 2.2 1.4.3 对比度：普通文本 4.5:1、大号文本 3:1（≥24px 或 ≥18.66px 且字重≥700）；
- WCAG 2.2 1.4.11 非文本对比度：组件/图标/焦点指示 3:1；
- WCAG 2.2 2.5.8 目标尺寸：24×24 CSS px（2.5.5 与移动平台建议 44×44 为建议档）；
- WCAG 2.2 1.4.12 文本间距：行高 ≥1.5 倍字号；
- WCAG 2.2 1.4.10 重排：320 CSS px 视口下无横向滚动。

本文件守住三条底线：算不出来要报 not_evaluable（不得默认通过）、大号文本按规范降阈、
失败项没证据只能算待取证。
"""
import csv
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ui_metrics.py"


def load():
    spec = importlib.util.spec_from_file_location("ui_metrics", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ContrastAlgorithm(unittest.TestCase):
    """对比度算法要对得上公认锚点值，否则后面所有阈值判断都是错的。"""

    def setUp(self):
        self.u = load()

    def test_known_anchor_values(self):
        self.assertAlmostEqual(self.u.contrast_ratio("#000000", "#ffffff"), 21.0, places=2)
        self.assertAlmostEqual(self.u.contrast_ratio("#ffffff", "#ffffff"), 1.0, places=2)
        # 经典边界：#767676 是纯白底上刚好达 4.5 的灰阶，#777777 差一点
        self.assertGreater(self.u.contrast_ratio("#767676", "#ffffff"), 4.5)
        self.assertLess(self.u.contrast_ratio("#777777", "#ffffff"), 4.5)

    def test_color_formats_are_equivalent(self):
        hex_value = self.u.contrast_ratio("#0033cc", "#ffffff")
        self.assertAlmostEqual(self.u.contrast_ratio("rgb(0, 51, 204)", "#ffffff"), hex_value, places=3)
        self.assertAlmostEqual(self.u.contrast_ratio("#03c", "#fff"), hex_value, places=2)

    def test_large_text_definition(self):
        self.assertTrue(self.u.is_large_text(24, 400))
        self.assertTrue(self.u.is_large_text(18.66, 700))
        self.assertFalse(self.u.is_large_text(18.66, 400))
        self.assertFalse(self.u.is_large_text(16, 700))


class ContrastJudgement(unittest.TestCase):
    def setUp(self):
        self.u = load()

    def record(self, **changes):
        record = {"id": "UI-1", "page": "https://example.invalid/a", "element": "p.desc",
                  "kind": "text_normal", "foreground": "#777777", "background": "#ffffff",
                  "font_size_px": 14, "font_weight": 400, "evidence": ["shot.png"]}
        record.update(changes)
        return record

    def test_normal_text_below_4_5_fails(self):
        self.assertEqual(self.u.check_record(self.record())["status"], "fail")
        self.assertEqual(self.u.check_record(self.record())["threshold"], 4.5)

    def test_normal_text_at_threshold_passes(self):
        self.assertEqual(self.u.check_record(self.record(foreground="#767676"))["status"], "pass")

    def test_same_color_passes_when_text_is_large(self):
        """同一颜色：14px 判失败、24px 按大号文本 3:1 判通过——阈值差异要真的生效。"""
        small = self.u.check_record(self.record())
        large = self.u.check_record(self.record(font_size_px=24))
        self.assertEqual(small["status"], "fail")
        self.assertEqual(large["status"], "pass")
        self.assertEqual(large["threshold"], 3.0)
        self.assertIn("大号文本", large["standard"])

    def test_bold_18_66px_counts_as_large(self):
        result = self.u.check_record(self.record(font_size_px=18.66, font_weight=700))
        self.assertEqual(result["threshold"], 3.0)

    def test_word_weight_is_not_read_as_400(self):
        """手写测量表写 "bold" 时不能当 400：否则 19px 粗体被判 4.5:1 失败（假失败）。"""
        self.assertTrue(self.u.is_large_text(19, "bold"))
        self.assertTrue(self.u.is_large_text(19, "700"))
        self.assertFalse(self.u.is_large_text(19, "normal"))
        result = self.u.check_record(self.record(font_size_px=19, font_weight="bold"))
        self.assertEqual(result["threshold"], 3.0)

    def test_declared_large_but_small_size_does_not_get_3_to_1(self):
        """kind=text_large 不能自报豁免：12px 标成大号文本也要按 4.5:1 判（防假绿）。"""
        result = self.u.check_record(self.record(kind="text_large", foreground="#8f8f8f",
                                                 font_size_px=12, font_weight=400))
        self.assertEqual(result["threshold"], 4.5)
        self.assertEqual(result["status"], "fail")
        self.assertIn("未达大号阈值", result["note"])

    def test_declared_large_without_size_keeps_3_to_1_but_flags(self):
        record = self.record(kind="text_large", foreground="#8f8f8f", font_size_px=None)
        result = self.u.check_record(record)
        self.assertEqual(result["threshold"], 3.0)
        self.assertIn("按声明计", result["note"])

    def test_ui_component_uses_3_to_1(self):
        result = self.u.check_record(self.record(kind="ui_component", foreground="#8c8c8c"))
        self.assertEqual(result["threshold"], 3.0)
        self.assertIn("1.4.11", result["standard"])

    def test_focus_indicator_uses_3_to_1(self):
        result = self.u.check_record(self.record(kind="focus_indicator", foreground="#cccccc"))
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["threshold"], 3.0)

    def test_translucent_color_is_not_evaluable_not_pass(self):
        """半透明叠加必须按实际渲染像素取色，不得按纯色算出"通过"。"""
        result = self.u.check_record(self.record(foreground="rgba(0, 0, 0, 0.6)"))
        self.assertEqual(result["status"], "not_evaluable")
        self.assertIn("半透明", result["note"])

    def test_transparent_background_points_to_ancestor(self):
        """页面里段落背景通常是 rgba(0,0,0,0)：要提示向上取祖先背景，而不是笼统报错。"""
        result = self.u.check_record(self.record(background="rgba(0, 0, 0, 0)"))
        self.assertEqual(result["status"], "not_evaluable")
        self.assertIn("祖先", result["note"])
        self.assertNotIn("半透明", result["note"])

    def test_missing_color_is_not_evaluable(self):
        result = self.u.check_record(self.record(background=None))
        self.assertEqual(result["status"], "not_evaluable")

    def test_gradient_background_is_not_evaluable(self):
        result = self.u.check_record(self.record(background="linear-gradient(#fff, #eee)"))
        self.assertEqual(result["status"], "not_evaluable")


class TargetSizeAndSpacing(unittest.TestCase):
    def setUp(self):
        self.u = load()

    def base(self, **changes):
        record = {"id": "UI-2", "page": "https://example.invalid/a", "element": "button.icon",
                  "kind": "target", "target_width_px": 32, "target_height_px": 32,
                  "evidence": ["shot.png"]}
        record.update(changes)
        return record

    def test_below_24_fails(self):
        result = self.u.check_record(self.base(target_width_px=20, target_height_px=20))
        self.assertEqual(result["status"], "fail")
        self.assertIn("2.5.8", result["standard"])

    def test_exactly_24_meets_aa_but_flags_comfort(self):
        """24×24 是 AA 下限：不判失败，但仍按建议档（44×44）给出提示。"""
        result = self.u.check_record(self.base(target_width_px=24, target_height_px=24))
        self.assertEqual(result["status"], "advisory")
        self.assertEqual(result["threshold"], 44.0)

    def test_between_24_and_44_is_advisory(self):
        result = self.u.check_record(self.base())
        self.assertEqual(result["status"], "advisory")
        self.assertEqual(result["threshold"], 44.0)

    def test_missing_dimensions_not_evaluable(self):
        payload = self.base()
        payload.pop("target_width_px")
        self.assertEqual(self.u.check_record(payload)["status"], "not_evaluable")

    def test_line_height_ratio_enforced(self):
        tight = {"id": "UI-3", "page": "p", "element": "p", "kind": "text_spacing",
                 "font_size_px": 14, "line_height_px": 20, "evidence": ["shot.png"]}
        ok = dict(tight, line_height_px=21, id="UI-4")
        self.assertEqual(self.u.check_record(tight)["status"], "fail")
        self.assertEqual(self.u.check_record(ok)["status"], "pass")

    def test_small_font_is_advisory_not_failure(self):
        record = {"id": "UI-5", "page": "p", "element": "p", "kind": "text_spacing",
                  "font_size_px": 11, "line_height_px": 16.5, "evidence": ["shot.png"]}
        result = self.u.check_record(record)
        self.assertEqual(result["status"], "advisory")
        self.assertIn("无 WCAG 下限", result["note"])


class ReflowCheck(unittest.TestCase):
    def setUp(self):
        self.u = load()

    def record(self, **changes):
        base = {"id": "UI-6", "page": "p", "element": "body", "kind": "reflow",
                "viewport_width_px": 320, "scroll_width_px": 320, "evidence": ["shot.png"]}
        base.update(changes)
        return base

    def test_no_horizontal_scroll_passes(self):
        self.assertEqual(self.u.check_record(self.record())["status"], "pass")

    def test_horizontal_scroll_fails(self):
        self.assertEqual(self.u.check_record(self.record(scroll_width_px=420))["status"], "fail")

    def test_too_wide_viewport_is_not_evaluable(self):
        result = self.u.check_record(self.record(viewport_width_px=375))
        self.assertEqual(result["status"], "not_evaluable")
        self.assertIn("重排", result["note"])


class EvidenceAndGate(unittest.TestCase):
    def setUp(self):
        self.u = load()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)

    def test_failure_without_evidence_is_downgraded(self):
        report = self.u.evaluate([{"id": "UI-7", "page": "p", "element": "p", "kind": "text_normal",
                                   "foreground": "#aaaaaa", "background": "#ffffff",
                                   "font_size_px": 14, "evidence": []}])
        self.assertEqual(report["results"][0]["status"], "pending_evidence")
        self.assertEqual(report["hard_failures"], 1)

    def test_unknown_kind_rejected(self):
        with self.assertRaises(ValueError):
            self.u.check_record({"id": "UI-8", "kind": "好看", "page": "p", "element": "e"})

    def test_missing_id_rejected(self):
        with self.assertRaises(ValueError):
            self.u.check_record({"kind": "reflow", "page": "p", "element": "e"})

    def test_cli_enforce_and_csv(self):
        payload = self.folder / "m.json"
        payload.write_text(json.dumps([
            {"id": "UI-9", "page": "p", "element": "p", "kind": "text_normal",
             "foreground": "#aaaaaa", "background": "#ffffff", "font_size_px": 14,
             "evidence": ["shot.png"]},
            {"id": "UI-10", "page": "p", "element": "b", "kind": "ui_component",
             "foreground": "#000000", "background": "#ffffff", "evidence": ["shot.png"]},
        ]), encoding="utf-8")
        out_json, out_csv = self.folder / "o.json", self.folder / "o.csv"
        code = subprocess.run([sys.executable, str(SCRIPT), "--input", str(payload),
                               "--emit-json", str(out_json), "--emit-csv", str(out_csv),
                               "--enforce"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(code.returncode, 2)
        report = json.loads(out_json.read_text(encoding="utf-8"))
        self.assertEqual(report["total"], 2)
        self.assertEqual(report["counts"]["fail"], 1)
        self.assertEqual(report["counts"]["pass"], 1)
        self.assertIn("不构成 WCAG 合规认证", report["scope"])
        self.assertTrue(out_csv.read_bytes().startswith(b"\xef\xbb\xbf"))
        with out_csv.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 2)

    def test_cli_clean_run_exits_zero(self):
        payload = self.folder / "clean.jsonl"
        payload.write_text(json.dumps({"id": "UI-11", "page": "p", "element": "p",
                                      "kind": "text_normal", "foreground": "#000000",
                                      "background": "#ffffff", "font_size_px": 14,
                                      "evidence": ["shot.png"]}) + "\n", encoding="utf-8")
        self.assertEqual(subprocess.run([sys.executable, str(SCRIPT), "--input", str(payload),
                                         "--enforce"], capture_output=True).returncode, 0)


if __name__ == "__main__":
    unittest.main()
