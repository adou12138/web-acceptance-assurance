#!/usr/bin/env python3
"""enrich_defects.py 契约测试。

覆盖 2026-10-29 用户要求固化的三条纪律：
1) 问题来源三分类自动标注（设计稿证据不足时留空，不猜）；
2) 对比度缺陷现象带设计稿色值对照（文字色/底色/设计稿自身对比度）；
3) 占位复现步骤替换为按键/面板级详细步骤。
外加 generate_reports 对 meta.htmlNote 的注入渲染（根治重跑覆盖手工注入段）。

变异自证： fixture 中设计稿无匹配色值的用例断言「不得标注规范缺陷」——
把匹配逻辑改坏（如容差放开到 255）该用例即红。
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import enrich_defects  # noqa: E402


def _design_tree():
    """迷你设计稿：白底卡片上一条 #7f8e9d 文本 + 一条不相关深色文本。"""
    return {
        "type": "FRAME", "fills": [{"type": "SOLID", "visible": True,
                                    "color": {"hex": "#ffffff"}}],
        "children": [
            {"type": "TEXT",
             "text": {"characters": "会话列表条目标题"},
             "fills": [{"type": "SOLID", "visible": True, "color": {"hex": "#7f8e9d"}}]},
            {"type": "TEXT",
             "text": {"characters": "标题文字"},
             "fills": [{"type": "SOLID", "visible": True, "color": {"hex": "#262626"}}]},
        ],
    }


def _contrast_defect(**over):
    d = {
        "dimension": "7.41", "category": "7.41", "property": "a11y-color-contrast",
        "status": "confirmed", "severity": "P2",
        "symptom": "文本对比度不足：3.36:1（要求 4.5:1）",
        "actual": "前景 rgb(127, 142, 157) / 背景 rgb(255,255,255)（背景按浏览器默认白底推断）",
        "reproduction": ["打开受检页面（URL 见本轮产物）", "触发 7.41 对应检查场景（维度定义见 references/check-dimensions.md）"],
        "element": {"selector": "div.truncate.text-xs", "bbox": {"x": 0, "y": 0, "w": 0, "h": 0}},
    }
    d.update(over)
    return d


class EnrichDefectsTests(unittest.TestCase):
    def test_contrast_defect_gets_source_and_design_colors(self):
        payload = {"defects": [_contrast_defect()]}
        stats = enrich_defects.enrich(payload, enrich_defects.collect_design_texts(_design_tree()))
        d = payload["defects"][0]
        self.assertEqual(d["sourceClass"], "规范缺陷")
        self.assertIn("#7f8e9d", d["sourceNote"])
        self.assertEqual(stats["sourceLabeled"], 1)
        # 现象与指标必须带设计稿色值对照（用户 2026-10-29 要求固化）
        self.assertIn("设计稿同款配色", d["symptom"])
        self.assertIn("#7f8e9d", d["symptom"])
        self.assertIn("#ffffff", d["symptom"])
        self.assertIn("3.36", d["symptom"])
        self.assertEqual(stats["symptomEnriched"], 1)

    def test_placeholder_repro_replaced_with_detailed_steps(self):
        payload = {"defects": [_contrast_defect()]}
        enrich_defects.enrich(payload, enrich_defects.collect_design_texts(_design_tree()))
        steps = payload["defects"][0]["reproduction"]
        self.assertGreaterEqual(len(steps), 5, "详细复现步骤至少 5 步")
        self.assertNotIn("打开受检页面", "".join(steps), "占位话术必须被替换")
        self.assertNotIn("对应检查场景", "".join(steps))
        joined = "".join(steps)
        for must in ("F12", "Ctrl+Shift+C", "4.5:1", "WebAIM"):
            self.assertIn(must, joined, f"详细步骤缺少关键操作说明：{must}")

    def test_no_design_match_leaves_source_empty(self):
        """设计稿无匹配色值 → 不得标注规范缺陷（防假分类；容差改坏此用例即红）。"""
        payload = {"defects": [_contrast_defect()] }
        # 设计稿只有深灰文本，无近似 #7f8e9d
        dark_only = {"type": "TEXT", "text": {"characters": "标题"},
                     "fills": [{"type": "SOLID", "visible": True, "color": {"hex": "#262626"}}]}
        stats = enrich_defects.enrich(payload, enrich_defects.collect_design_texts(
            {"type": "FRAME", "fills": [{"type": "SOLID", "visible": True, "color": {"hex": "#ffffff"}}],
             "children": [dark_only]}))
        d = payload["defects"][0]
        self.assertIsNone(d.get("sourceClass"), "无对照数据必须留空，不猜")
        self.assertEqual(stats["sourceLabeled"], 0)
        self.assertNotIn("设计稿同款配色", d["symptom"])

    def test_dark_defect_without_design_variant(self):
        payload = {"defects": [_contrast_defect(
            property="prefers-color-scheme", dimension="7.25", category="7.25",
            symptom="页面未适配深色模式", actual="同源样式表中未找到 dark 媒体查询")]}
        enrich_defects.enrich(payload, enrich_defects.collect_design_texts(_design_tree()))
        d = payload["defects"][0]
        self.assertEqual(d["sourceClass"], "规范+实现共同缺失")
        self.assertIn("深色变体", d["sourceNote"])

    def test_behavior_defect_is_implementation(self):
        payload = {"defects": [_contrast_defect(
            property="failure-stuck-loading", dimension="7.33", category="7.33",
            symptom="接口返回 500时页面停留在加载态", actual="可见文本仅 8 字符")]}
        enrich_defects.enrich(payload, None)
        d = payload["defects"][0]
        self.assertEqual(d["sourceClass"], "实现缺陷")
        # 无设计稿也能给出详细复现（状态场景类不依赖设计稿）
        self.assertNotIn("打开受检页面", "".join(d["reproduction"]))
        self.assertIn("Offline", "".join(d["reproduction"]))

    def test_idempotent_manual_source_wins(self):
        """人工已标注的缺陷整体跳过——symptom 不被机器改写。"""
        d0 = _contrast_defect(sourceClass="实现缺陷", sourceNote="人工判定",
                              symptom="人工写好的现象")
        payload = {"defects": [d0]}
        stats = enrich_defects.enrich(payload, enrich_defects.collect_design_texts(_design_tree()))
        self.assertEqual(payload["defects"][0]["symptom"], "人工写好的现象")
        self.assertEqual(payload["defects"][0]["sourceClass"], "实现缺陷")
        self.assertEqual(stats["sourceLabeled"], 0)

    def test_main_writes_report_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            rp = Path(td) / "report.json"
            rp.write_text(json.dumps({"defects": [_contrast_defect()]}, ensure_ascii=False), encoding="utf-8")
            dp = Path(td) / "design_model.json"
            dp.write_text(json.dumps({"tree": _design_tree()}, ensure_ascii=False), encoding="utf-8")
            with patch.object(sys, "argv", ["enrich_defects.py", "--report", str(rp), "--design", str(dp)]):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(enrich_defects.main(), 0)
            first = json.loads(rp.read_text(encoding="utf-8"))["defects"][0]["symptom"]
            with patch.object(sys, "argv", ["enrich_defects.py", "--report", str(rp), "--design", str(dp)]):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(enrich_defects.main(), 0)
            second = json.loads(rp.read_text(encoding="utf-8"))["defects"][0]["symptom"]
            self.assertEqual(first, second, "重复运行不得追加重复内容")


class HtmlNoteRenderTests(unittest.TestCase):
    """generate_reports 必须渲染 meta.htmlNote（2026-10-29 问题8：重跑覆盖手工注入段）。"""

    def test_html_note_survives_regeneration(self):
        import generate_reports as report
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src, out = root / "input", root / "output"
            src.mkdir(); out.mkdir()
            defect = {"fingerprint": "n1", "severity": "P2", "status": "confirmed",
                      "symptom": "占位", "sourceClass": "规范缺陷"}
            note = "<p class=\"note\"><strong>像素层量化</strong>：diffScore=28.06%</p>"
            ip = src / "upstream.json"
            ip.write_text(json.dumps({"defects": [defect], "meta": {"htmlNote": note}},
                                     ensure_ascii=False), encoding="utf-8")
            with patch.object(sys, "argv", ["generate_reports.py", "--input", str(ip), "--outdir", str(out)]):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(report.main(), 0)
            html = (out / "report.html").read_text(encoding="utf-8")
            self.assertIn("diffScore=28.06%", html, "meta.htmlNote 必须出现在报告中")
            # 再生成一次仍存在（幂等，不依赖手工补注）
            with patch.object(sys, "argv", ["generate_reports.py", "--input", str(ip), "--outdir", str(out)]):
                with contextlib.redirect_stdout(io.StringIO()):
                    report.main()
            html2 = (out / "report.html").read_text(encoding="utf-8")
            self.assertIn("diffScore=28.06%", html2)


if __name__ == "__main__":
    unittest.main()
