"""文档引用完整性守卫：agents/SKILL 提到的 references 必须真实存在，且每个 reference 都有回链。"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFERENCES = ROOT / "references"
LINK = re.compile(r"references/([A-Za-z0-9_-]+\.md)")


def markdown_corpus():
    files = [ROOT / "README.md", ROOT / "agents" / "web-acceptance-assurance.md"]
    files += list((ROOT / "references").glob("*.md"))
    files += list((ROOT / "skills").rglob("SKILL.md"))
    files += list((ROOT / "docs").glob("*.md"))
    return [p for p in files if p.is_file()]


def read(path):
    return path.read_text(encoding="utf-8", errors="ignore")


def resolves(name):
    """同名 references 可能属于包级或某个 skill 级目录；任一位置存在即视为可解析。"""
    if (REFERENCES / name).is_file():
        return True
    return any(p.is_file() for p in ROOT.rglob(f"references/{name}"))


class ReferenceLinkTests(unittest.TestCase):
    def test_referenced_files_exist(self):
        missing = {}
        for path in markdown_corpus():
            for line in read(path).splitlines():
                # 溯源/外部路径行（缓存目录、绝对盘符）不属于本包引用，跳过
                if "cache/" in line or "C:/" in line or "C:\\" in line:
                    continue
                for name in LINK.findall(line):
                    if not resolves(name):
                        missing.setdefault(str(path.relative_to(ROOT)), set()).add(name)
        self.assertEqual(missing, {}, f"引用了不存在的 references 文件：{missing}")

    def test_every_reference_has_a_backlink(self):
        corpus = "\n".join(read(p) for p in markdown_corpus())
        orphans = sorted(p.name for p in REFERENCES.glob("*.md") if p.name not in corpus)
        self.assertEqual(orphans, [], f"以下 references 没有任何文档指向，模型执行时不会加载：{orphans}")

    def test_browser_channel_doc_is_routed_from_agent_and_readme(self):
        agent = read(ROOT / "agents" / "web-acceptance-assurance.md")
        readme = read(ROOT / "README.md")
        self.assertIn("references/browser-connection.md", agent, "agents 必须在执行期指向浏览器通道文档")
        self.assertIn("references/browser-connection.md", readme, "README 文件清单需登记浏览器通道文档")

    def test_browser_channel_doc_keeps_actionable_rules(self):
        text = read(REFERENCES / "browser-connection.md")
        for needle in ("Chrome DevTools", "Playwright", "pageId", "uid", "ref", "BLOCKED"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text, f"浏览器通道文档缺少关键约束：{needle}")


if __name__ == "__main__":
    unittest.main()
