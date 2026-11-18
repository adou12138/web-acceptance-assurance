"""v3 目标审计 C 批回归：接口迁移退化与外置适配（离线，无真实网络）。"""
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SemanticReviewMigration(unittest.TestCase):
    def setUp(self):
        self.sem = load("sem_c", "skills/api-test/scripts/llm_semantics.py")

    def review(self, text, corrected):
        facts = {"fields": {"field": {"constraints": {}}}}
        disputes = [{"field": "field", "excerpts": [{"text": text}]}]
        review = {"decisions": [{"field": "field", "decision": "correct_llm_value",
                                  "evidence": text, "corrected": corrected}]}
        out, applied, rejected = self.sem.apply_review(review, disputes, facts)
        return applied, rejected

    def test_enum_value_review_accepted(self):
        applied, rejected = self.review("status: 1 enabled; 2 disabled",
                                        {"enum": [[1, "enabled"], [2, "disabled"]]})
        self.assertGreater(len(applied), 0, "合法枚举释义复核应被采纳")
        self.assertEqual(rejected, [])

    def test_bool_review_accepted(self):
        applied, rejected = self.review("required: true", {"required": True})
        self.assertGreater(len(applied), 0, "合法布尔复核应被采纳")
        self.assertEqual(rejected, [])

    def test_crlf_evidence_accepted(self):
        applied, rejected = self.review("state: draft\r\ndraft 草稿", {"enum": [["draft", "草稿"]]})
        self.assertGreater(len(applied), 0, "CRLF 换行证据应归一后采纳")

    def test_baseless_value_still_rejected(self):
        applied, rejected = self.review("name: 名称", {"enum": [[9, "不存在"]]})
        self.assertEqual(applied, [], "原文无据的修订必须拒绝")
        self.assertTrue(rejected)


class ExternalAdapterLoader(unittest.TestCase):
    def setUp(self):
        self.flow = load("flow_c", "skills/api-test/scripts/flow.py")

    def test_external_adapter_dir_is_honored(self):
        # WJX_ADAPTER_DIR 指向包外目录，build_adapter 应能加载其中的站点适配器而不改动安装包
        import os
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        adapter_dir = Path(tmp.name)
        (adapter_dir / "extadapter.py").write_text(
            "class ApiAdapter:\n"
            "    def __init__(self): self.name='ext'\n"
            "    def is_write_allowed(self, env): return True\n",
            encoding="utf-8")
        os.environ["WJX_ADAPTER_DIR"] = str(adapter_dir)
        self.addCleanup(os.environ.pop, "WJX_ADAPTER_DIR", None)
        # 通过 select_adapter_path 或 build_adapter 暴露外部路径解析
        resolved = self.flow.resolve_adapter_path("extadapter", str(adapter_dir))
        self.assertEqual(resolved, adapter_dir / "extadapter.py")


if __name__ == "__main__":
    unittest.main()
