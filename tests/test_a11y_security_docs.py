"""无障碍人工审计与安全防护验证清单的一致性约束。

防的是三类回归：
1. 清单条数与声明不符（改表忘改标题）；
2. 标注“内置已覆盖”但 figma 规则集里根本没有对应条款（虚报机检覆盖）；
3. 安全清单只剩标题，丢了“怎么验 / 不算缺陷”这类可执行内容。
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
A11Y = ROOT / "references" / "a11y-audit.md"
SECURITY = ROOT / "references" / "security-verification.md"
BUILTIN = ROOT / "skills" / "figma-ui-parity" / "references" / "a11y-rules.md"

CRITERION = re.compile(r"^\|\s*(\d+\.\d+\.\d+)\s[^|]*\|\s*(A|AA)\s*\|\s*([^|]+)\|\s*(内置|人工|双)\s*\|", re.M)
BUILTIN_ROW = re.compile(r"^\|\s*`[a-z0-9-]+`\s*\|\s*([^|]+)\|", re.M)


def criteria():
    return CRITERION.findall(A11Y.read_text(encoding="utf-8"))


class A11yChecklist(unittest.TestCase):
    def test_declared_count_matches_table(self):
        text = A11Y.read_text(encoding="utf-8")
        rows = criteria()
        declared = re.search(r"A/AA 清单（(\d+) 条）", text)
        self.assertIsNotNone(declared, "缺少条数声明")
        self.assertEqual(int(declared.group(1)), len(rows))
        self.assertEqual(len(rows), 37)

    def test_no_duplicate_criteria(self):
        ids = [r[0] for r in criteria()]
        self.assertEqual(len(ids), len(set(ids)), f"重复条款: {ids}")

    def test_builtin_claims_exist_in_rule_set(self):
        """标注内置/双的条款，必须在 figma 15 条规则里能找到同号条款。"""
        builtin_numbers = set()
        for cell in BUILTIN_ROW.findall(BUILTIN.read_text(encoding="utf-8")):
            builtin_numbers.update(re.findall(r"\d+\.\d+\.\d+", cell))
        self.assertTrue(builtin_numbers, "未能解析出内置规则条款号")
        claimed = [r[0] for r in criteria() if r[3] in ("内置", "双")]
        self.assertTrue(claimed)
        missing = [c for c in claimed if c not in builtin_numbers]
        self.assertEqual(missing, [], f"虚报机检覆盖: {missing}")

    def test_figures_do_not_claim_full_compliance(self):
        text = A11Y.read_text(encoding="utf-8")
        self.assertIn("**禁止表述**", text)
        self.assertIn("不得合并成", text)
        self.assertIn("VO+U", text)       # VoiceOver 命令
        self.assertIn("Insert+F7", text)  # NVDA 命令
        self.assertIn("320", text)        # 重排阈值


class SecurityVerification(unittest.TestCase):
    def test_twelve_items_with_verification_and_false_positive_guard(self):
        text = SECURITY.read_text(encoding="utf-8")
        blocks = re.split(r"^### (\d+)\. ", text, flags=re.M)[1:]
        numbers = [int(n) for n in blocks[0::2]]
        bodies = blocks[1::2]
        self.assertEqual(numbers, list(range(1, 13)))
        for num, body in zip(numbers, bodies):
            with self.subTest(item=num):
                self.assertIn("**怎么验**", body)
                self.assertIn("**不算缺陷**", body)

    def test_no_attack_instructions(self):
        """清单只允许防护验证；不得出现扫描/爆破类操作指引。"""
        text = SECURITY.read_text(encoding="utf-8")
        for forbidden in ("sqlmap", "nmap", "dirb", "hydra", "burp", "fuzz 字典"):
            self.assertNotIn(forbidden, text.lower())
        self.assertIn("不做自动化扫描", text)


if __name__ == "__main__":
    unittest.main()
