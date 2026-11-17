#!/usr/bin/env python3
"""Figma MCP YAML-ish → JSON 转换器契约测试。"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import figma_mcp_to_json as conv  # noqa: E402


SAMPLE = '''NAME: "Demo"

GLOBAL_VARS:
fill_bg:
  - '#EEF3F8'
style_title:
  fontFamily: PingFang SC
  fontStyle: Medium
  fontWeight: 500
  fontSize: 18
  lineHeight: 27px
  textAlignHorizontal: LEFT

ELEMENTS:
EL-root:
  type: FRAME
  layout:
    mode: column
    padding: 10px 20px
    dimensions:
      width: 400
      height: 200
  fills: fill_bg
EL-title:
  type: TEXT
  layout:
    mode: none
    dimensions:
      width: 120
      height: 27
  textStyle: style_title
  fills: fill_bg

NODES:
[CANVAS] "Page 1" #0:1
  [FRAME] "主页面" #1:1 template=EL-root
    [TEXT] #1:2 template=EL-title text="标题"
'''


class ConverterTests(unittest.TestCase):
    def test_resolves_templates_colors_text_and_dimensions(self):
        result = conv.convert(SAMPLE, "主页面")
        doc = result["document"]
        self.assertEqual(doc["name"], "主页面")
        self.assertEqual(doc["layoutMode"], "VERTICAL")
        self.assertEqual(doc["paddingTop"], 10)
        self.assertEqual(doc["paddingRight"], 20)
        self.assertEqual(doc["fills"][0]["color"]["r"], 238 / 255)
        child = doc["children"][0]
        self.assertEqual(child["characters"], "标题")
        self.assertEqual(child["style"]["fontWeight"], 500)
        self.assertEqual(child["style"]["lineHeight"]["unit"], "PIXELS")
        self.assertEqual(child["absoluteBoundingBox"]["width"], 120)

    def test_marks_coordinates_unavailable_instead_of_inventing_absolute_positions(self):
        result = conv.convert(SAMPLE, "主页面")
        self.assertEqual(result["meta"]["coordinateBasis"], "unavailable")
        self.assertEqual(result["document"]["pluginData"]["coordinates"], "unavailable")

    def test_section_titles_inside_values_do_not_split_sections(self):
        text = SAMPLE.replace('NAME: "Demo"', 'NAME: "NODES: Demo"').replace('  type: TEXT', '  text: "NODES: Hello"\n  type: TEXT')
        result = conv.convert(text, "主页面")
        self.assertEqual(result['name'], 'NODES: Demo')
        self.assertEqual(result['document']['children'][0]['characters'], '标题')

    def test_duplicate_and_out_of_order_sections_are_rejected(self):
        for text in (SAMPLE + 'NODES:\n', SAMPLE.replace('GLOBAL_VARS:', 'ELEMENTS:', 1)):
            with self.subTest(text=text), self.assertRaises(ValueError):
                conv.convert(text, '主页面')
        text = 'NAME: Demo\nELEMENTS: {}\nGLOBAL_VARS: {}\nNODES:\n[FRAME] "Only" #1:1\n'
        with self.assertRaises(ValueError):
            conv.convert(text)

    def test_multiple_canvases_require_selection_across_all_pages(self):
        text = SAMPLE + '[CANVAS] "Page 2" #0:2\n  [FRAME] "另一页" #2:1 template=EL-root\n'
        with self.assertRaises(ValueError):
            conv.convert(text)
        self.assertEqual(conv.convert(text, '另一页')['document']['id'], '2:1')
        duplicate = text.replace('"另一页"', '"主页面"')
        with self.assertRaises(ValueError):
            conv.convert(duplicate, '主页面')

    def test_invalid_nodes_are_rejected(self):
        with self.assertRaises(ValueError):
            conv.convert("NAME: Demo\n\nGLOBAL_VARS: {}\n\nELEMENTS: {}\n\nNODES:\n")


if __name__ == "__main__":
    unittest.main()
