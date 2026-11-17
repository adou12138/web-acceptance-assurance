"""页面脚本的选择器生成契约。

为什么需要这个测试：选择器拼装逻辑在**页面注入脚本里无法共享代码**（每个脚本被
单独注入，没有 import 通道），所以同一个「标签 + 前两个类名」的拼法被抄了 5 份。
实测后果：修好盘点脚本的 Tailwind 转义后，可访问性审计仍在生成非法选择器
（`span.rounded-[10px]` / `div.mb-1.5`），5 条对比度缺陷因此拿不到位置图，
而报告只显示"未取到图"，看不出是选择器坏了。

既然没法抽单一模块，就用**机械门禁**兜住：任何页面脚本只要按类名拼选择器，
就必须带转义助手；新增脚本绕过这条约定会直接失败。
"""

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
SCRIPTS = ROOT / "scripts"

# 转义助手的可接受形态：有 CSS.escape 就用，没有就丢弃非法类名。
# button_audit.js 自己实现了一份反斜杠转义（esc），也算合规——门禁只要求"存在转义动作"，
# 不强制同名，否则会把已正确的实现判成违规，反而促使别人去改对的地方。
ESCAPE_MARKERS = ("escCls(", "cssEscape(", "esc(")


def page_scripts() -> list:
    """页面注入脚本（scripts 顶层 .js，不含 node/ 下的驱动与测试）。"""
    out = []
    for p in sorted(SCRIPTS.glob("*.js")):
        if p.name.startswith("_"):
            continue
        out.append(p)
    return out


class SelectorEscapeContractTests(unittest.TestCase):
    def test_scripts_joining_class_names_must_escape(self):
        offenders = []
        for p in page_scripts():
            src = p.read_text(encoding="utf-8", errors="ignore")
            if "join('.')" not in src:
                continue
            if not any(m in src for m in ESCAPE_MARKERS):
                offenders.append(p.name)
        self.assertEqual(offenders, [],
                         "这些页面脚本按类名拼选择器却没做转义，Tailwind 任意值类名会让选择器非法：%s"
                         % ", ".join(offenders))

    def test_selector_builder_validates_before_returning(self):
        """拼完必须自检一次：转义解决不了的情况（奇怪 id、伪类冲突）仍要能退到合法形态。"""
        offenders = []
        for p in page_scripts():
            src = p.read_text(encoding="utf-8", errors="ignore")
            if "join('.')" not in src:
                continue
            if "querySelectorAll" not in src:
                offenders.append(p.name)
        self.assertEqual(offenders, [], "选择器拼完没有自检，非法选择器会流到下游：%s" % ", ".join(offenders))

    def test_escape_helper_handles_missing_css_escape_api(self):
        """没有 CSS.escape 的老环境必须降级为丢弃非法类名，而不是原样拼进去。"""
        for name in ("a11y_audit.js", "extra_checks.js", "collect_page_inventory.js",
                     "extract_containers.js", "alignment_modal_checks.js"):
            src = (SCRIPTS / name).read_text(encoding="utf-8", errors="ignore")
            self.assertIn("CSS.escape", src, "{} 必须先尝试 CSS.escape".format(name))
            # 降级分支要给出「丢弃」语义：返回空串或返回原名的安全判定
            self.assertTrue(("isNameChar" in src) or ("SAFE_CLS" in src),
                            "{} 缺少无 CSS.escape 时的安全判定".format(name))


if __name__ == "__main__":
    unittest.main()
