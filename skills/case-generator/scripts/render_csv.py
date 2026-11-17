#!/usr/bin/env python3
"""render_csv.py — 五视角用例 CSV 渲染（从 cases JSON 派生，只渲染不判定）。

视角映射直接复用专家包 scripts/exploration_state.py 的 VIEWS，避免与执行期门禁漂移；
列头与顺序复用 policy.json views.columns，与 19 列 Excel 逐字一致。

输出：<outdir>/测试用例_<task-id>_<视角>.csv 共 5 个文件（utf-8-sig）。
--verify：渲染后逐文件读回，核对 BOM、表头、列数与 case_id 集合；不一致退出码 1。
默认拒绝覆盖已有文件；--force 显式允许。task_id 限字母数字与 . _ -（作文件名片段）。

退出码：0=成功；1=回读一致性失败；2=用法/输入错误。
"""

import argparse
import csv
import importlib.util
import json
import re
import sys
from pathlib import Path


class UsageError(Exception):
    """用法或输入不合法（退出码 2）。"""


class VerifyError(Exception):
    """渲染后回读校验不一致（退出码 1）。"""


TASK_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")

SCRIPTS = Path(__file__).resolve().parent
SKILL_DIR = SCRIPTS.parent
PACKAGE_ROOT = SCRIPTS.parents[2]
DEFAULT_POLICY = SKILL_DIR / "config" / "policy.json"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
from render_xlsx import load, row_for  # noqa: E402

_SPEC = importlib.util.spec_from_file_location("exploration_state", PACKAGE_ROOT / "scripts" / "exploration_state.py")
es = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(es)


def select(cases, attributes):
    return [tc for tc in cases.get("testcases", []) if set(tc.get("attributes") or []) & set(attributes)]


def verify_view(path, columns, expected):
    """回读单个视图：BOM、表头、列数、case_id 列存在与 id 集合五项校验。"""
    path = Path(path)
    if not path.read_bytes().startswith(b"\xef\xbb\xbf"):
        raise VerifyError(f"{path.name}: 缺少 utf-8-sig BOM")
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    headers = [c["header"] for c in columns]
    if not rows or rows[0] != headers or any(len(r) != len(headers) for r in rows[1:]):
        raise VerifyError(f"{path.name}: 表头或列数不一致")
    id_columns = [i for i, c in enumerate(columns) if c["field"] == "case_id"]
    if not id_columns:
        raise VerifyError(f"{path.name}: 列契约缺少 case_id 字段，无法回读校验")
    id_index = id_columns[0]
    actual = {r[id_index] for r in rows[1:]}
    if actual != expected:
        raise VerifyError(f"{path.name}: case_id 集合不一致（回读 {len(actual)} / 预期 {len(expected)}）")


def totals(cases):
    """视图行数与去重用例数分开统计：同一用例跨视角只计一次。"""
    rows = 0
    unique = set()
    for attributes in es.VIEWS.values():
        picked = select(cases, attributes)
        rows += len(picked)
        unique |= {str(tc.get("id", "")) for tc in picked}
    return {"view_rows": rows, "unique_cases": len(unique)}


def render(cases, task_id, outdir, columns, force=False, verify=False):
    if not isinstance(task_id, str) or not TASK_ID.fullmatch(task_id):
        raise UsageError(f"task_id 仅允许字母数字与 . _ -（最长 64 位）：{task_id!r}")
    outdir = Path(outdir)
    if not outdir.parent.is_dir():
        raise ValueError("输出目录的父目录必须已存在")
    outdir.mkdir(exist_ok=True)
    headers = [c["header"] for c in columns]
    result = {}
    for view, attributes in es.VIEWS.items():
        path = outdir / f"测试用例_{task_id}_{view}.csv"
        if path.exists() and not force:
            raise FileExistsError(f"拒绝覆盖已有视图：{path}（如确认重渲染请加 --force）")
        selected = select(cases, attributes)
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(headers)
            for tc in selected:
                writer.writerow(row_for(tc, columns))
        expected = {str(tc.get("id", "")) for tc in selected}
        if verify:
            verify_view(path, columns, expected)
        result[view] = {"path": str(path), "count": len(selected)}
    return result


def main():
    ap = argparse.ArgumentParser(description="从 cases JSON 渲染五视角 CSV")
    ap.add_argument("--cases", required=True)
    ap.add_argument("--out", required=True, dest="outdir", help="输出目录")
    ap.add_argument("--task-id", default="task")
    ap.add_argument("--policy", default=str(DEFAULT_POLICY))
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--force", action="store_true", help="允许覆盖同名视图")
    args = ap.parse_args()
    try:
        cases = load(args.cases)
        policy = load(args.policy)
        columns = policy.get("views", {}).get("columns", [])
        if len(columns) != 19:
            raise UsageError(f"policy.views.columns 应为 19 列，当前 {len(columns)}")
        result = render(cases, args.task_id, args.outdir, columns, args.force, args.verify)
    except VerifyError as exc:
        print(f"回读校验失败：{exc}", file=sys.stderr)
        sys.exit(1)
    except (UsageError, ValueError, OSError) as exc:
        print(f"渲染失败：{exc}", file=sys.stderr)
        sys.exit(2)
    print(json.dumps({"views": result, **totals(cases)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
