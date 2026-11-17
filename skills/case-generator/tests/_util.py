"""测试公共工具：路径解析与 fixture 加载。"""

import json
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = TESTS_DIR.parent
SCRIPTS = SKILL_DIR / "scripts"
REVIEW_SCRIPTS = SKILL_DIR.parent / "requirement-review" / "scripts"
FIXTURES = TESTS_DIR / "fixtures"
PACKAGE_ROOT = SKILL_DIR.parents[1]

for _p in (SCRIPTS, REVIEW_SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

REVIEW_PASS = FIXTURES / "review-sample-pass.json"
REVIEW_BLOCKING = FIXTURES / "review-sample-blocking.json"
REVIEW_INSUFFICIENT = FIXTURES / "review-sample-insufficient.json"
CASES_12 = FIXTURES / "cases-sample-12.json"
BATCHES_SAMPLE = FIXTURES / "batches-sample.json"


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))