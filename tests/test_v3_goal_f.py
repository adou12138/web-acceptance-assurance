"""v3 目标审计 F 批回归：质量基线硬退化 + 检出效果评测协议（离线夹具）。"""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EvalHardDegradation(unittest.TestCase):
    def setUp(self):
        self.ev = load("ev_f", "scripts/eval_cases.py")
        self.base = {"coverage_layers_missing": 0, "method_variety": 3,
                     "assertion_density": 2.0, "feature_l1_ratio": 1.0,
                     "structural_errors": 0, "vague_or_placeholder": 0, "gaps_open": 0}

    def test_structural_error_increase_is_degradation(self):
        current = dict(self.base, structural_errors=10)
        worse = self.ev.compare(current, self.base)
        self.assertIn("structural_errors", worse, "结构错误增加必须判退化")

    def test_vague_increase_is_degradation(self):
        worse = self.ev.compare(dict(self.base, vague_or_placeholder=5), self.base)
        self.assertIn("vague_or_placeholder", worse)

    def test_gap_increase_is_degradation(self):
        worse = self.ev.compare(dict(self.base, gaps_open=20), self.base)
        self.assertIn("gaps_open", worse)

    def test_clean_current_has_no_degradation(self):
        self.assertEqual(self.ev.compare(dict(self.base), self.base), {})


class DetectionEvalProtocol(unittest.TestCase):
    def test_protocol_fixture_exists_and_is_valid(self):
        protocol = ROOT / "docs" / "evals-baseline" / "detection-protocol.md"
        self.assertTrue(protocol.is_file(), "检出效果评测协议必须落盘")
        text = protocol.read_text(encoding="utf-8")
        for token in ("盲测", "误报", "留出", "必选分支", "检出"):
            self.assertIn(token, text)
        fixtures = ROOT / "docs" / "evals-baseline" / "detection-fixtures"
        self.assertTrue(fixtures.is_dir(), "需有独立检出夹具目录")
        cases = list(fixtures.glob("*.json"))
        self.assertGreaterEqual(len(cases), 4, "至少四个基础场景夹具")


if __name__ == "__main__":
    unittest.main()
