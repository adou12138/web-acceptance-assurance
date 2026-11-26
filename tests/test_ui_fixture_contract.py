"""M6 UI 夹具静态契约测试（补充方案 M6）。

夹具（tests/fixtures/ui/*.html）只验证检测器与流程行为，**不作为真实产品质量证据**。
本测试锁定夹具自身的契约：
- 每个夹具必须带四类注释说明（故意注入 / 合法设计 / 预期观察器输出 / 人工裁定）；
- 注入点与合法例外的结构特征存在（供观察器检测的锚点）；
- 夹具自足（无外部资源引用）；
- 观察器规则引用正确（遮挡 pending / 容器内滚动不误报页面级溢出）。

动态行为验证（真实遮挡生成候选、合法 modal 覆盖不自动判缺陷、320px 横溢出记录）
需要浏览器会话，由 `skills/figma-ui-parity` 观察器采集与 `docs/spikes/001` 承担，
不在本静态契约的覆盖声明内。
"""
import re
import unittest
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "ui"

REQUIRED_SECTIONS = {
    "故意注入": "每夹具必须声明故意注入的问题",
    "合法设计": "每夹具必须声明合法例外（不得判缺陷）",
    "预期观察器输出": "每夹具必须声明预期观察器输出",
    "人工裁定": "每夹具必须声明必须人工裁定的项",
}

EXPECTED_FIXTURES = ("dense-form.html", "responsive-table.html", "modal-focus.html")


class UIFixtureContractTests(unittest.TestCase):
    def load(self, name):
        path = FIXTURE_DIR / name
        self.assertTrue(path.is_file(), f"缺少夹具 {name}")
        return path.read_text(encoding="utf-8")

    def test_all_declared_fixtures_exist(self):
        """方案 M6 要求的三类夹具齐全。"""
        actual = {p.name for p in FIXTURE_DIR.glob("*.html")}
        self.assertEqual(actual, set(EXPECTED_FIXTURES))

    def test_every_fixture_declares_four_sections(self):
        """四类注释说明齐全：故意注入 / 合法设计 / 预期观察器输出 / 人工裁定。"""
        for name in EXPECTED_FIXTURES:
            with self.subTest(fixture=name):
                text = self.load(name)
                for marker, why in REQUIRED_SECTIONS.items():
                    self.assertIn(marker, text, f"{name} 缺少「{marker}」说明：{why}")
                self.assertIn("不作为真实产品质量证据", text,
                              f"{name} 缺少『不作为产品质量证据』声明")

    def test_dense_form_has_injections_and_legal_design(self):
        text = self.load("dense-form.html")
        # 注入 1：标签-输入关联断裂（label 无 for、input 有 aria-label=""）
        self.assertIn("故意注入：label 缺 for", text)
        self.assertIn('aria-label=""', text)
        # 注入 2：错误提示与字段视觉断层（固定底栏内文案）
        self.assertIn("footer-error", text)
        # 合法设计：固定底栏（position:fixed）+ 遮挡候选应 pending 的声明
        self.assertIn("position: fixed", text)
        self.assertIn("occluded-control", text)
        self.assertIn("pending", text)

    def test_responsive_table_distinguishes_container_scroll_from_page_overflow(self):
        text = self.load("responsive-table.html")
        # 合法：容器内横向滚动（overflow-x:auto）
        self.assertIn("overflow-x: auto", text)
        # 注入：整页横溢出（min-width 超视口、无收敛）
        self.assertIn("min-width: 1400px", text)
        self.assertIn("horizontal-overflow", text)
        # 观察器规则引用：容器内滚动不得误报为页面级横溢出
        self.assertIn("不得把容器内滚动报成页面级横溢出", text)

    def test_modal_focus_declares_legal_occlusion_and_focus_gap(self):
        text = self.load("modal-focus.html")
        # 合法：遮罩层结构（z-index 高于背景）+ pending 纪律声明
        self.assertIn("modal-overlay", text)
        self.assertIn("z-index", text)
        self.assertIn("pending", text)
        # 注入：焦点陷阱缺失与焦点不回归的声明
        self.assertIn("焦点陷阱缺失", text)
        self.assertIn("焦点不回归", text)

    def test_fixtures_are_self_contained(self):
        """夹具自足：不引用外部资源（img/css/js 链接），保证离线可复跑。"""
        external = re.compile(
            r'(?:src|href)\s*=\s*["\'](?!#)(https?:)?//[^"\']+|<script[^>]+src', re.I)
        for name in EXPECTED_FIXTURES:
            with self.subTest(fixture=name):
                text = self.load(name)
                hits = external.findall(text)
                self.assertEqual(hits, [], f"{name} 引用了外部资源：{hits}")

    def test_observer_rules_referenced_in_comments(self):
        """夹具注释引用的观察器行为与实现一致：7.38 遮挡候选、EXP-KBD- 键盘遍历。"""
        self.assertIn("7.38", self.load("dense-form.html"))
        self.assertIn("EXP-KBD-", self.load("modal-focus.html"))


if __name__ == "__main__":
    unittest.main()
