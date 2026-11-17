#!/usr/bin/env python3
"""render_xlsx.py — 19 列 Excel 渲染（随包内置，只渲染不判定）。

列头与顺序逐字对齐 policy.json views.columns（源：export_excel.py COLUMNS，frozen 4.17.0）。
不得引入额外判定：质检一律由 gate_checks.py 承担；本脚本只做 cases JSON → 视图投影。
openpyxl 缺失时降级输出 Markdown 表格并明确标注（不自动安装）。
--verify：渲染后读回比对 case_id 集合与 JSON 完全相等，不一致即作废（退出码 1）。

退出码：0=成功；1=一致性校验失败；2=用法/输入错误；3=依赖缺失且已降级或不可降级。
"""

import argparse
import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
DEFAULT_POLICY = SKILL_DIR.parent / "config" / "policy.json"


def load(path, code=2):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"输入解析失败：{exc}", file=sys.stderr)
        sys.exit(code)


def row_for(tc, columns):
    suites = tc.get("suites") or []
    attrs = tc.get("attributes") or []
    pre = "\n".join(str(p) for p in tc.get("preconditions", []))
    steps = "\n".join(f"{i}. {s.get('action', '')} → {s.get('expected', '')}"
                      for i, s in enumerate(tc.get("steps", []), 1))
    expected = "\n".join(str(a.get("expect", "")) for a in tc.get("assertions", []))
    is_smoke = "是" if "smoke" in suites else "否"
    project = str(tc.get("project", ""))
    values = {
        "project": project,
        "case_type": str(tc.get("layer", "")),
        "case_id": str(tc.get("id", "")),
        "requirement": project,  # 抽取源即取 project 值，勿按列名直取
        "priority": str(tc.get("priority", "")),
        "title": str(tc.get("title", "")),
        "menu_path": str(tc.get("menu_path", "")),
        "preconditions": pre,
        "steps": steps,
        "expected_results": expected,
        "is_smoke": is_smoke,
        "creator": "AI",
        "assignee": str(tc.get("assignee", "")),
        "test_case_type": ",".join(str(a) for a in attrs),
        "test_category": ",".join(str(s) for s in suites),
        "status": "",  # 生成阶段留空；执行事实在台账，不回写视图
        "screenshot": "",
        "test_suite": str(tc.get("test_suite", "")),
        "remarks": str(tc.get("remarks", "")),
    }
    return [values.get(col["field"], "") for col in columns]


def render_markdown(cases, columns):
    headers = [c["header"] for c in columns]
    lines = ["<!-- 降级输出：环境缺 openpyxl，未自动安装；此 Markdown 表为 19 列视图降级件 -->", ""]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "|".join(["---"] * len(headers)) + "|")
    for tc in cases.get("testcases", []):
        cells = [str(v).replace("\n", "<br>").replace("|", "\\|") for v in row_for(tc, columns)]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description="19 列 Excel 渲染（只渲染不判定）")
    ap.add_argument("--cases", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--task-id", default="task")
    ap.add_argument("--policy", default=str(DEFAULT_POLICY))
    ap.add_argument("--verify", action="store_true", help="渲染后读回比对 case_id 集合")
    ap.add_argument("--verify-only", action="store_true",
                    help="不渲染：只读回现有 xlsx 与 JSON 比对 case_id 集合（防手工改后回流）")
    args = ap.parse_args()
    if args.verify_only and args.verify:
        print("--verify 与 --verify-only 互斥", file=sys.stderr)
        sys.exit(2)

    cases = load(args.cases)
    policy = load(args.policy)
    columns = policy.get("views", {}).get("columns", [])
    if len(columns) != 19:
        print(f"policy.views.columns 应为 19 列，当前 {len(columns)}", file=sys.stderr)
        sys.exit(2)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    json_ids = [tc.get("id") for tc in cases.get("testcases", []) if isinstance(tc, dict)]

    def compare_ids(xlsx_path):
        """借鉴 wjx M3 产物回读：表头逐字 / 列数 / 行数 / case_id 集合四项校验，任一 error 即渲染失败。
        BOM 由导出代码保证（CSV 派生一律 utf-8-sig；xlsx 为二进制无 BOM 概念）。"""
        problems = []
        wb2 = load_workbook(xlsx_path, read_only=True)
        ws2 = wb2[str(policy.get("views", {}).get("artifact", {}).get("sheet_name", "测试用例"))]
        rows = list(ws2.iter_rows(min_row=2, values_only=True))
        header_row = [str(c.value) if c.value is not None else "" for c in next(ws2.iter_rows(min_row=1, max_row=1))]
        expect_headers = [c["header"] for c in columns]
        if header_row != expect_headers:
            diff = [(i, e, a) for i, (e, a) in enumerate(zip(expect_headers, header_row)) if e != a][:3]
            problems.append(f"表头与 policy.views.columns 不一致：{diff}")
        if ws2.max_column != len(columns):
            problems.append(f"列数 {ws2.max_column} != {len(columns)}")
        col_idx = [c["field"] for c in columns].index("case_id")
        xlsx_ids = [str(r[col_idx]) for r in rows if r]
        if len(rows) != len(json_ids):
            problems.append(f"数据行数 {len(rows)} != 用例数 {len(json_ids)}")
        if sorted(xlsx_ids) != sorted(map(str, json_ids)):
            problems.append(f"case_id 集合与 JSON 不等（xlsx {len(xlsx_ids)} 条）")
        wb2.close()
        if problems:
            print("视图一致性失败：" + "；".join(problems) + "；视图作废重渲", file=sys.stderr)
            sys.exit(1)
        print(f"视图一致性通过：表头逐字 / 列数 {len(columns)} / 行数 {len(rows)} / {len(xlsx_ids)} 条 case_id 全部一致")

    if args.verify_only:
        if not out.is_file():
            print(f"待比对文件不存在：{out}", file=sys.stderr)
            sys.exit(2)
        from openpyxl import load_workbook
        compare_ids(out)
        sys.exit(0)

    try:
        from openpyxl import Workbook, load_workbook
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    except ImportError:
        md_path = out.with_suffix(".md")
        md_path.write_text(render_markdown(cases, columns), encoding="utf-8")
        print(f"工具阻塞：缺 openpyxl，不自动安装。已降级输出 Markdown 表格 -> {md_path}")
        sys.exit(3)

    wb = Workbook()
    ws = wb.active
    ws.title = str(policy.get("views", {}).get("artifact", {}).get("sheet_name", "测试用例"))
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    header_font = Font(name="微软雅黑", size=11, bold=True, color="FFFFFF")
    body_font = Font(name="微软雅黑", size=10)
    thin = Side(style="thin")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    wrap = Alignment(wrap_text=True, vertical="top")

    ws.append([c["header"] for c in columns])
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = wrap
        cell.border = border
    for tc in cases.get("testcases", []):
        ws.append(row_for(tc, columns))
        for cell in ws[ws.max_row]:
            cell.font = body_font
            cell.alignment = wrap
            cell.border = border
    for idx, col in enumerate(columns, 1):
        width = 12 if col["field"] not in {"steps", "expected_results", "preconditions"} else 40
        ws.column_dimensions[ws.cell(row=1, column=idx).column_letter].width = width
    wb.save(out)

    if args.verify:
        compare_ids(out)
    print(f"rendered -> {out}")
    sys.exit(0)


if __name__ == "__main__":
    main()