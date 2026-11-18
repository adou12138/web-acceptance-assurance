"""文档引用完整性守卫：agents/SKILL 提到的 references 必须真实存在，且每个 reference 都有回链。"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFERENCES = ROOT / "references"
# 只匹配"裸 references/..."，显式全路径（skills/x/references/y、../references/y）不算裸引用，
# 由路径本身表达层级，避免把跨级资料链接误判为写错目录。
LINK = re.compile(r"(?<![\w/.-])references/([A-Za-z0-9_/-]+\.md)")
# 有意隔离的目录不参与回链检查（legacy-schemas/README 已声明"未实现、仅供参考"）
BACKLINK_EXCLUDED = {"legacy-schemas"}


def markdown_corpus():
    files = [ROOT / "README.md", ROOT / "agents" / "web-acceptance-assurance.md"]
    files += list((ROOT / "references").rglob("*.md"))
    files += list((ROOT / "skills").rglob("SKILL.md"))
    # v3 G17：skill 级 references 也是可读语料，纳入回链语料与其自身索引检查
    files += [p for skill in (ROOT / "skills").iterdir() if skill.is_dir()
              for p in (skill / "references").rglob("*.md") if p.is_file()]
    files += list((ROOT / "docs").glob("*.md"))
    return [p for p in files if p.is_file()]


def read(path):
    return path.read_text(encoding="utf-8", errors="ignore")


def all_reference_files():
    """包级 + 各 skill 级 references 树下全部文档（含子目录），legacy-schemas 整目录隔离不查。"""
    files = [p for p in REFERENCES.rglob("*.md")
             if not (set(p.relative_to(REFERENCES).parts) & BACKLINK_EXCLUDED)]
    for skill in (ROOT / "skills").iterdir():
        d = skill / "references"
        if d.is_dir():
            files += [p for p in d.rglob("*.md")
                      if not (set(p.relative_to(d).parts) & BACKLINK_EXCLUDED)]
    return files


def corpus_root(path):
    """引用方所属根：skill 文档只在本 skill 内解析，其余按包根解析。

    同名文件在别处存在不算解析成功——那正是"写错层级、执行期加载不到"的典型形态。
    """
    parts = path.relative_to(ROOT).parts
    if parts and parts[0] == "skills" and len(parts) > 1:
        return ROOT / "skills" / parts[1]
    return ROOT


# 跨层引用白名单：资料性链接（如包级对照文档指引 skill 内的维度清单），
# 必须显式登记，不能靠"同目录找不到就去别处找"兜底。
CROSS_LEVEL_LINKS = {
    # 三个 skill 的 SKILL.md 用"专家包 references/..."指向包级检出维度全景图（阅读导向，不参与执行加载）
    ("skills/api-test/SKILL.md", "bug-detection-map.md"),
    ("skills/case-generator/SKILL.md", "bug-detection-map.md"),
    ("skills/figma-ui-parity/SKILL.md", "bug-detection-map.md"),
    # 生成侧方法论指向包级业务坑点库（跨层资料链接：模块坑点不属于单个 skill）
    ("skills/case-generator/references/methodology/defect_patterns.md", "business-pitfalls.md"),
    # figma 内置无障碍规则声明未覆盖项时，指向包级人工审计清单（跨层资料链接）
    ("skills/figma-ui-parity/references/a11y-rules.md", "a11y-audit.md"),
}


def resolves(path, rel):
    """按引用方所在目录解析 references/<rel>；白名单条目才允许跨层。"""
    if (corpus_root(path) / "references" / rel).is_file():
        return True
    if (path.relative_to(ROOT).as_posix(), rel) in CROSS_LEVEL_LINKS:
        return any(p.is_file() for p in ROOT.rglob(f"references/{rel}"))
    return False


class ReferenceLinkTests(unittest.TestCase):
    def test_referenced_files_exist(self):
        missing = {}
        for path in markdown_corpus():
            for line in read(path).splitlines():
                # 溯源/外部路径行（缓存目录、绝对盘符、外部开源项目出处）不属于本包引用，跳过
                if "cache/" in line or "C:/" in line or "C:\\" in line:
                    continue
                if "github.com" in line or "出处" in line or "MIT License" in line:
                    continue
                for name in LINK.findall(line):
                    if name.endswith("SKILL.md"):
                        # 指向其它 skill 文档的资料性链接（如 source-comparison.md 的知识来源清单），
                        # 不属于本包 references 树，不做存在性校验
                        continue
                    if not resolves(path, name):
                        missing.setdefault(str(path.relative_to(ROOT)), set()).add(name)
        self.assertEqual(missing, {}, f"引用了不存在的 references 文件：{missing}")

    def test_every_reference_has_a_backlink(self):
        corpus = "\n".join(read(p) for p in markdown_corpus())
        orphans = sorted(str(p.relative_to(ROOT))
                         for p in all_reference_files() if p.name not in corpus)
        self.assertEqual(orphans, [], f"以下 references 没有任何文档指向，模型执行时不会加载：{orphans}")

    def test_a11y_and_security_docs_are_routed(self):
        """人工审计与防护验证清单必须被执行期入口指向，否则模型不会加载。"""
        expects = {
            "references/a11y-audit.md": ["agents/web-acceptance-assurance.md",
                                         "references/bug-detection-map.md",
                                         "references/visual-usability.md",
                                         "references/business-pitfalls.md",
                                         "README.md"],
            "references/security-verification.md": ["agents/web-acceptance-assurance.md",
                                                    "references/bug-detection-map.md",
                                                    "references/business-pitfalls.md",
                                                    "README.md"],
        }
        for doc, carriers in expects.items():
            for rel in carriers:
                with self.subTest(doc=doc, carrier=rel):
                    self.assertIn(doc, read(ROOT / rel))

    def test_business_pitfalls_routed_from_agent_map_and_readme(self):
        """模块坑点库必须被执行期路由到，否则模型不会加载。"""
        for rel in ("agents/web-acceptance-assurance.md", "references/bug-detection-map.md", "README.md"):
            with self.subTest(doc=rel):
                self.assertIn("references/business-pitfalls.md", read(ROOT / rel))

    def test_browser_channel_doc_is_routed_from_agent_and_readme(self):
        agent = read(ROOT / "agents" / "web-acceptance-assurance.md")
        readme = read(ROOT / "README.md")
        for name in ("browser-connection.md", "design-data.md"):
            with self.subTest(doc=name):
                self.assertIn(f"references/{name}", agent, f"agents 必须在执行期指向 {name}")
                self.assertIn(f"references/{name}", readme, f"README 文件清单需登记 {name}")

    def test_design_data_doc_keeps_actionable_rules(self):
        text = read(REFERENCES / "design-data.md")
        for needle in ("dataMissing", "get_figma_data", "FIGMA_API_KEY", "还原度", "nodeId"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text, f"设计数据文档缺少关键约束：{needle}")

    def test_browser_channel_doc_keeps_actionable_rules(self):
        text = read(REFERENCES / "browser-connection.md")
        for needle in ("Chrome DevTools", "Playwright", "pageId", "uid", "ref", "BLOCKED"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text, f"浏览器通道文档缺少关键约束：{needle}")


if __name__ == "__main__":
    unittest.main()
