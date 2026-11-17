"""技能解析污染防护：备份与旧版缓存不得保留启用态 SKILL.md。

备份目录可由 WAA_BACKUP_DIR 指定；未指定时用本项目默认路径，路径不存在则跳过该分支。
当前版本目录由 plugin.json 决定，允许其保留启用态 SKILL.md。
"""

import json
import os
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = json.loads((ROOT / ".codebuddy-plugin" / "plugin.json").read_text(encoding="utf-8"))


class SkillRegistrationTests(unittest.TestCase):
    def test_plugin_skill_count_matches_agent_statement(self):
        listed = {Path(p).name for p in PLUGIN["skills"]}
        self.assertEqual(listed, {"requirement-review", "case-generator", "api-test", "figma-ui-parity"})
        agent = (ROOT / "agents" / "web-acceptance-assurance.md").read_text(encoding="utf-8")
        self.assertIn("自带四个可独立调用的能力 skill", agent)

    def test_backups_have_no_enabled_skill_md(self):
        backup = Path(os.environ.get("WAA_BACKUP_DIR", r"D:/TestCase/MySharedSkills/backup"))
        if not backup.is_dir():
            self.skipTest("备份目录不存在，跳过本机路径检查")
        enabled = [str(p) for p in backup.rglob("SKILL.md")]
        self.assertEqual(enabled, [], "备份中的 SKILL.md 必须改名为 SKILL.md.disabled")

    def test_old_cache_versions_have_no_enabled_skill_md(self):
        cache = ROOT.parents[3] / "cache" / "my-experts" / "web-acceptance-assurance"
        if not cache.is_dir():
            self.skipTest("插件缓存目录不存在，跳过")
        enabled = [str(p) for p in cache.rglob("SKILL.md")
                   if p.relative_to(cache).parts[0] != PLUGIN["version"]]
        self.assertEqual(enabled, [], "旧版本缓存中的 SKILL.md 必须禁用，避免按名字解析到旧版")


if __name__ == "__main__":
    unittest.main()
