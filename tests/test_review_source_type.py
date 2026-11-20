"""DEV-1119-09：装置分不清「产品方需求」与「测试者自造需求」。

实测代价：评审材料里 requirement 相关 source 字段如实写了「无独立产品需求文档」等 4 处，
装置读到却不改变任何结果——blocking=[]、go_ahead 放行 15/15、review_check 仍 pass。
于是「评审通过」退化成「自造材料内部自洽 + 格式合规」，整条链的根节点不是产品需求却环环盖章。

修法：requirement 增 source_type；派生型（inferred_from_ui / derived_from_prior）必须
列出 blocking，且 go_ahead 全量放行时告警。字段缺省只 warning，存量数据不失效。
"""

import copy
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "requirement-review" / "scripts" / "review_check.py"


def load():
    spec = importlib.util.spec_from_file_location("review_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def base_review():
    """一份最小合法评审：1 需求 / 1 功能点 / 1 验收条件，go_ahead 放行该功能点。"""
    return {
        "schema_version": "1.0",
        "requirement": {
            "id": "REQ-00", "title": "示例需求", "version": "v1",
            "fingerprint": "sha256:" + "a" * 64,
            "reviewed_at": "2026-11-20T10:00:00+08:00",
        },
        "requirements": [{
            "id": "REQ-01", "name": "需求一", "source": "产品文档",
            "description": "示例", "confirmation": "confirmed"}],
        "features": [{
            "id": "FP-01", "module_code": "M01", "name": "功能一",
            "description": "示例", "source": "需求文档"}],
        "acceptance": [{
            "id": "AC-01", "requirement_id": "REQ-01", "feature_id": "FP-01",
            "description": "示例验收条件", "confirmation": "confirmed",
            "required": True}],
        "modules": [{"code": "M01", "name": "模块一"}],
        "confirmed_rules": [], "blocking": [], "pending": [],
        "go_ahead": ["FP-01"], "coverage_gaps": [], "not_reviewed": [],
    }


class SourceTypeTests(unittest.TestCase):
    def setUp(self):
        self.r = load()

    def _check(self, data):
        errors, warnings, _ = self.r.check(data)
        return errors, warnings

    def test_missing_source_type_is_warning_not_error(self):
        """存量数据（无该字段）不得失效——只提示，不阻断。"""
        errors, warnings = self._check(base_review())
        self.assertEqual([e for e in errors if "source_type" in e], [])
        self.assertTrue([w for w in warnings if "source_type" in w],
                        "缺 source_type 时应给出 warning，否则「未知」与「产品文档」无区分")

    def test_product_doc_is_clean(self):
        data = base_review()
        data["requirement"]["source_type"] = "product_doc"
        errors, warnings = self._check(data)
        self.assertEqual([e for e in errors if "source_type" in e], [])
        self.assertEqual([w for w in warnings if "source_type" in w], [])

    def test_derived_without_blocking_is_error(self):
        """派生需求宣告零阻塞＝拿自造假设当验收标准，必须拒绝。"""
        for stype in ("inferred_from_ui", "derived_from_prior"):
            data = base_review()
            data["requirement"]["source_type"] = stype
            errors, _ = self._check(data)
            self.assertTrue([e for e in errors if "blocking 为空" in e],
                            f"{stype} 且 blocking 为空时应报错")

    def test_derived_with_blocking_passes(self):
        data = base_review()
        data["requirement"]["source_type"] = "derived_from_prior"
        data["blocking"] = [{"id": "B-01", "feature_id": "FP-01",
                             "reason": "无书面需求，缺失败处理约定"}]
        errors, _ = self._check(data)
        self.assertEqual([e for e in errors if "source_type" in e or "blocking 为空" in e], [])

    def test_derived_full_go_ahead_warns(self):
        """派生需求把全部功能点都放行，即使有 blocking 也要提示复核。"""
        data = base_review()
        data["requirement"]["source_type"] = "derived_from_prior"
        data["blocking"] = [{"id": "B-01", "feature_id": "FP-01", "reason": "缺依据"}]
        _, warnings = self._check(data)
        self.assertTrue([w for w in warnings if "放行了全部" in w],
                        "派生型全量放行时应提示，避免默认「全部没问题」")

    def test_invalid_enum_rejected(self):
        data = base_review()
        data["requirement"]["source_type"] = "随便写的"
        errors, _ = self._check(data)
        self.assertTrue([e for e in errors if "source_type" in e])

    def test_schema_declares_field_as_optional(self):
        """schema 不得把 source_type 设为 required——否则存量数据全部失效。"""
        schema = json.loads((ROOT / "skills" / "requirement-review" / "references" /
                             "review-result.schema.json").read_text(encoding="utf-8"))
        req = schema["properties"]["requirement"]
        self.assertNotIn("source_type", req["required"])
        self.assertIn("source_type", req["properties"])


if __name__ == "__main__":
    unittest.main()