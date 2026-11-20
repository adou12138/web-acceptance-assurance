"""界面可量化标准（ui_metrics.py）的机械行为。

判据全部来自公开规范，不自定义审美评分：
- WCAG 2.2 1.4.3 对比度：普通文本 4.5:1、大号文本 3:1（≥24px 或 ≥18.66px 且字重≥700）；
- WCAG 2.2 1.4.11 非文本对比度：组件/图标/焦点指示 3:1；
- WCAG 2.2 2.5.8 目标尺寸：24×24 CSS px，含 Inline / Spacing / UA / Essential 四个例外；
- WCAG 2.2 1.4.12 文本间距：考「用户覆盖间距后是否丢内容」，不是页面默认行高；
- WCAG 2.2 1.4.10 重排：320 CSS px 视口下无横向滚动。

本文件守住四条底线：
1. 算不出来要报 not_evaluable（不得默认通过，也不得默认落成白色）；
2. 大号文本按规范降阈，且不能靠 kind 自报豁免；
3. 欠尺寸目标必须先走例外判定，否则把合规全判成失败（实测真实页面 6/6 误报）；
4. 工具判 fail 只是候选，未裁定的不进缺陷账；误报率要能被算出来。
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
                  "background_source": "solid",
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

    def test_gradient_string_as_color_is_not_evaluable(self):
        result = self.u.check_record(self.record(background="linear-gradient(#fff, #eee)"))
        self.assertEqual(result["status"], "not_evaluable")

    def test_missing_background_source_is_not_evaluable(self):
        """取数层不说背景怎么来的，工具就无从分辨颜色真假——只能待核实。

        实测过的坑：整页是照片时取数脚本落回 #fff，白字算出 1.0:1，16 条失败里
        5 条是这么来的。宁可报算不出来，也不给假结论。
        """
        payload = self.record()
        payload.pop("background_source")
        result = self.u.check_record(payload)
        self.assertEqual(result["status"], "not_evaluable")
        self.assertIn("background_source", result["note"])

    def test_image_background_is_not_evaluable_and_goes_manual(self):
        result = self.u.check_record(self.record(background=None, background_source="image"))
        self.assertEqual(result["status"], "not_evaluable")
        self.assertTrue(result["manual_review"])
        self.assertIn("人工视觉", result["note"])

    def test_gradient_background_source_is_not_evaluable(self):
        result = self.u.check_record(self.record(background_source="gradient"))
        self.assertEqual(result["status"], "not_evaluable")
        self.assertTrue(result["manual_review"])

    def test_unknown_source_value_is_normalized(self):
        result = self.u.check_record(self.record(background_source="whatever"))
        self.assertEqual(result["status"], "not_evaluable")
        self.assertIn("unknown", result["note"])


class TargetSizeAndSpacing(unittest.TestCase):
    def setUp(self):
        self.u = load()

    def base(self, **changes):
        record = {"id": "UI-2", "page": "https://example.invalid/a", "element": "button.icon",
                  "kind": "target", "target_width_px": 32, "target_height_px": 32,
                  "evidence": ["shot.png"]}
        record.update(changes)
        return record

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

    def test_undersized_without_exception_check_is_pending_evidence(self):
        """18×18 只能说明"欠尺寸"，不能直接判失败——2.5.8 有四个例外。"""
        result = self.u.check_record(self.base(target_width_px=18, target_height_px=18))
        self.assertEqual(result["status"], "pending_evidence")
        self.assertIn("例外", result["note"])

    def test_spacing_exception_downgrades_to_advisory(self):
        """实测真实登录页：6 个欠尺寸目标全部命中 Spacing 例外，原判定全是误报。"""
        result = self.u.check_record(self.base(target_width_px=13, target_height_px=13,
                                               spacing_clear=True))
        self.assertEqual(result["status"], "advisory")
        self.assertEqual(result["measured"]["exceptions"], ["spacing_clear"])

    def test_inline_exception_downgrades_to_advisory(self):
        result = self.u.check_record(self.base(target_width_px=96, target_height_px=16,
                                               inline_in_text=True))
        self.assertEqual(result["status"], "advisory")
        self.assertIn("inline_in_text", result["measured"]["exceptions"])

    def test_spacing_blocked_still_fails(self):
        """例外不成立时必须判失败——修误报不能把真缺陷一起压掉。"""
        result = self.u.check_record(self.base(target_width_px=18, target_height_px=18,
                                               spacing_clear=False))
        self.assertEqual(result["status"], "fail")
        self.assertIn("未命中任何例外", result["note"])

    def test_ua_control_exception(self):
        result = self.u.check_record(self.base(target_width_px=16, target_height_px=16,
                                               ua_controlled=True))
        self.assertEqual(result["status"], "advisory")

    def test_spacing_override_only_new_clip_fails(self):
        """1.4.12 的判据是差分：覆盖间距后新增裁剪才算丢内容。"""
        clean = {"id": "UI-3", "page": "p", "element": "body", "kind": "text_spacing_override",
                 "before_clipped": 0, "after_clipped": 0, "evidence": ["shot.png"]}
        broken = dict(clean, id="UI-4", after_clipped=2)
        self.assertEqual(self.u.check_record(clean)["status"], "pass")
        self.assertEqual(self.u.check_record(broken)["status"], "fail")

    def test_pre_existing_clip_is_advisory_not_spacing_failure(self):
        """页面本来就有裁剪时，不能算到 1.4.12 头上——那属于别的 SC。"""
        record = {"id": "UI-5", "page": "p", "element": "body", "kind": "text_spacing_override",
                  "before_clipped": 2, "after_clipped": 2, "evidence": ["shot.png"]}
        result = self.u.check_record(record)
        self.assertEqual(result["status"], "advisory")
        self.assertIn("1.4.4", result["note"])

    def test_legacy_text_spacing_kind_is_not_evaluable(self):
        """旧的"页面默认行高 ≥1.5"口径不是 1.4.12 的判据，不能再默默给结论。"""
        record = {"id": "UI-6", "page": "p", "element": "p", "kind": "text_spacing",
                  "font_size_px": 14, "line_height_px": 20, "evidence": ["shot.png"]}
        result = self.u.check_record(record)
        self.assertEqual(result["status"], "not_evaluable")
        self.assertIn("text_spacing_override", result["note"])

    def test_spacing_override_missing_fields_not_evaluable(self):
        result = self.u.check_record({"id": "UI-7", "page": "p", "element": "b",
                                      "kind": "text_spacing_override"})
        self.assertEqual(result["status"], "not_evaluable")


class ReflowCheck(unittest.TestCase):
    def setUp(self):
        self.u = load()

    def record(self, **changes):
        base = {"id": "UI-8", "page": "p", "element": "body", "kind": "reflow",
                "viewport_width_px": 320, "scroll_width_px": 320, "evidence": ["shot.png"]}
        base.update(changes)
        return base

    def test_no_horizontal_scroll_passes(self):
        self.assertEqual(self.u.check_record(self.record())["status"], "pass")

    def test_horizontal_scroll_fails(self):
        self.assertEqual(self.u.check_record(self.record(scroll_width_px=420))["status"], "fail")

    def test_two_d_layout_exempt_is_advisory(self):
        result = self.u.check_record(self.record(scroll_width_px=420, two_d_layout_exempt=True))
        self.assertEqual(result["status"], "advisory")
        self.assertIn("二维布局", result["note"])

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

    def _fail_record(self, rid="UI-9", evidence=None):
        return {"id": rid, "page": "p", "element": "p", "kind": "text_normal",
                "foreground": "#aaaaaa", "background": "#ffffff", "background_source": "solid",
                "font_size_px": 14, "evidence": ["shot.png"] if evidence is None else evidence}

    def test_failure_without_evidence_is_downgraded(self):
        report = self.u.evaluate([self._fail_record(evidence=[])])
        self.assertEqual(report["results"][0]["status"], "pending_evidence")
        self.assertEqual(report["hard_failures"], 1)

    def test_unknown_kind_rejected(self):
        with self.assertRaises(ValueError):
            self.u.check_record({"id": "UI-10", "kind": "好看", "page": "p", "element": "e"})

    def test_missing_id_rejected(self):
        with self.assertRaises(ValueError):
            self.u.check_record({"kind": "reflow", "page": "p", "element": "e"})

    def test_false_positive_rate_computed(self):
        report = self.u.evaluate([self._fail_record("A"), self._fail_record("B"),
                                  self._fail_record("C")],
                                 {"A": "confirmed", "B": "false_positive", "C": "pending"})
        self.assertEqual(report["false_positive"], 1)
        self.assertEqual(report["ruled"], 2)          # pending 不算已裁定
        self.assertAlmostEqual(report["false_positive_rate"], 0.5, places=3)

    def test_unconfirmed_fail_does_not_pass_gate(self):
        report = self.u.evaluate([self._fail_record()])
        verdict, reasons = self.u.gate_verdict(report)
        self.assertEqual(verdict, "待核实")
        self.assertTrue(any("人工" in r for r in reasons))

    def test_confirmed_fail_gives_not_pass(self):
        report = self.u.evaluate([self._fail_record()], {"UI-9": "confirmed"})
        verdict, _ = self.u.gate_verdict(report)
        self.assertEqual(verdict, "不通过")

    def test_high_false_positive_rate_blocks_verdict(self):
        """误报率过高时工具本身不可信：此时拦住整轮只会训练出"忽略工具"的习惯。"""
        records = [self._fail_record(f"F{i}") for i in range(4)]
        rulings = {"F0": "confirmed", "F1": "false_positive",
                   "F2": "false_positive", "F3": "false_positive"}
        report = self.u.evaluate(records, rulings)
        verdict, reasons = self.u.gate_verdict(report)
        self.assertEqual(verdict, "待核实")
        self.assertTrue(any("误报率" in r for r in reasons))

    def test_exception_ruling_downgrades_fail(self):
        record = {"id": "T1", "page": "p", "element": "a", "kind": "target",
                  "target_width_px": 18, "target_height_px": 18, "spacing_clear": False,
                  "evidence": ["shot.png"]}
        report = self.u.evaluate([record], {"T1": "exception"})
        self.assertEqual(report["results"][0]["status"], "advisory")

    def test_cli_enforce_and_csv(self):
        payload = self.folder / "m.json"
        payload.write_text(json.dumps([
            self._fail_record("UI-11"),
            {"id": "UI-12", "page": "p", "element": "b", "kind": "ui_component",
             "foreground": "#000000", "background": "#ffffff",
             "background_source": "solid", "evidence": ["shot.png"]},
        ]), encoding="utf-8")
        rulings = self.folder / "r.json"
        rulings.write_text(json.dumps({"UI-11": "confirmed"}), encoding="utf-8")
        out_json, out_csv = self.folder / "o.json", self.folder / "o.csv"
        code = subprocess.run([sys.executable, str(SCRIPT), "--input", str(payload),
                               "--rulings", str(rulings),
                               "--emit-json", str(out_json), "--emit-csv", str(out_csv),
                               "--enforce"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(code.returncode, 2)
        report = json.loads(out_json.read_text(encoding="utf-8"))
        self.assertEqual(report["total"], 2)
        self.assertEqual(report["counts"]["fail"], 1)
        self.assertEqual(report["counts"]["pass"], 1)
        self.assertEqual(report["verdict"], "不通过")
        self.assertIn("不构成 WCAG 合规认证", report["scope"])
        self.assertTrue(out_csv.read_bytes().startswith(b"\xef\xbb\xbf"))
        with out_csv.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 2)
        # 只给判定不给实测值的 CSV 无法复核，measured 必须在列里
        self.assertIn("measured", rows[0])
        self.assertEqual(json.loads(rows[0]["measured"])["ratio"], 2.32)
        self.assertIn("threshold", rows[0])
        self.assertIn("ruling", rows[0])

    def test_cli_clean_run_exits_zero(self):
        payload = self.folder / "clean.jsonl"
        payload.write_text(json.dumps({"id": "UI-13", "page": "p", "element": "p",
                                      "kind": "text_normal", "foreground": "#000000",
                                      "background": "#ffffff", "background_source": "solid",
                                      "font_size_px": 14,
                                      "evidence": ["shot.png"]}) + "\n", encoding="utf-8")
        self.assertEqual(subprocess.run([sys.executable, str(SCRIPT), "--input", str(payload),
                                         "--enforce"], capture_output=True).returncode, 0)

    def test_cli_unruled_failures_do_not_enforce(self):
        """未裁定的失败不拦整轮（否则等于用未验证的判定卡流程），但结论是待核实。"""
        payload = self.folder / "unruled.json"
        payload.write_text(json.dumps([self._fail_record("UI-14")]), encoding="utf-8")
        out_json = self.folder / "unruled-out.json"
        code = subprocess.run([sys.executable, str(SCRIPT), "--input", str(payload),
                               "--emit-json", str(out_json), "--enforce"],
                              capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(code.returncode, 0)
        self.assertEqual(json.loads(out_json.read_text(encoding="utf-8"))["verdict"], "待核实")


class CountsKeyPresenceTests(unittest.TestCase):
    """DEV-1119-02：无证据时 fail 键曾直接消失。

    实测：同一批 5 条真实失败，无 evidence 时 counts 里看不到 fail 键
    （{pass, not_evaluable, pending_evidence, advisory}），只看 counts 会读成「零失败」。
    键缺失比值为 0 更误导：0 表明「检查过且为 0」，缺键表明「这一维没进入视野」。
    """

    def setUp(self):
        self.u = load()

    def _one_pass_record(self):
        return {"id": "t1", "page": "p", "kind": "text_normal",
                "foreground": "rgb(0, 0, 0)", "background": "rgb(255, 255, 255)",
                "background_source": "solid", "font_size_px": 14, "font_weight": 400,
                "evidence": ["shot.png"]}

    def test_fail_key_present_when_zero(self):
        report = self.u.evaluate([self._one_pass_record()])
        self.assertIn("fail", report["counts"])
        self.assertEqual(report["counts"]["fail"], 0)

    def test_all_status_keys_present(self):
        report = self.u.evaluate([self._one_pass_record()])
        for key in self.u.RESULT_STATUSES:
            self.assertIn(key, report["counts"], f"counts 缺 {key} 键")

    def test_pending_evidence_also_present_when_zero(self):
        report = self.u.evaluate([self._one_pass_record()])
        self.assertIn("pending_evidence", report["counts"])
        self.assertEqual(report["counts"]["pending_evidence"], 0)

    def test_false_positive_key_present_when_zero(self):
        """ROUND-2 自查发现：恒显列表漏了 false_positive。

        人工裁定误报后 status 会被改成 false_positive，于是「有误报时该键在、无误报时
        该键消失」——正是本题要修的「键缺失代替 0」，只是换了个状态。误报率是报告首屏
        指标，它的分子项尤其不能隐身。
        """
        report = self.u.evaluate([self._one_pass_record()])
        self.assertIn("false_positive", report["counts"])
        self.assertEqual(report["counts"]["false_positive"], 0)

    def test_status_list_covers_every_written_status(self):
        """RESULT_STATUSES 必须覆盖代码实际会写入的每个 status，否则新增状态又会隐身。"""
        report = self.u.evaluate([self._one_pass_record()], {"t1": "false_positive"})
        self.assertEqual(report["counts"]["false_positive"], 1)
        # counts 的键集合应恰好等于声明集（多一个少一个都说明两边漂移了）
        self.assertEqual(set(report["counts"]), set(self.u.RESULT_STATUSES))


if __name__ == "__main__":
    unittest.main()
