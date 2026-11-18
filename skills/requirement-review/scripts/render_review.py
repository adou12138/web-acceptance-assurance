#!/usr/bin/env python3
"""render_review.py — 从 review-result.json 渲染人读摘要 review-summary.md。

纯派生视图：不新增判定、不承载 JSON 之外的结论；章节固定，条数与 JSON 数组逐项相等。
摘要归档与转发以本文件为准，会话内复述不算交付物。

退出码：0=成功；2=用法/输入错误。
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path


def _fp_short(fp: str) -> str:
    return fp.split(":", 1)[1][:12] if ":" in fp else fp


def _mod_name(modules, module_code):
    for row in modules:
        if isinstance(row, dict) and row.get("module_code") == module_code:
            return f"{module_code} {row.get('name', '')}".strip()
    return module_code


def _fid_mod(data, fid):
    for row in data.get("features") or []:
        if isinstance(row, dict) and row.get("feature_id") == fid:
            return row.get("module_code", "")
    return ""


def render(data):
    req = data.get("requirement") or {}
    modules = data.get("modules") or []
    feats = data.get("features") or []
    lines = []
    lines.append("# 需求评审摘要")
    lines.append("")
    lines.append("<!-- 本文件由 render_review.py 从 review-result.json 渲染，手工修改不作数；条数与 JSON 数组逐项对账 -->")
    lines.append("")
    lines.append("## 需求信息")
    lines.append("")
    lines.append(f"- 需求：{req.get('id', '未知')} {req.get('title', '')}（版本 {req.get('version', '未知')}）")
    lines.append(f"- 评审时间：{req.get('reviewed_at', '未知')}；正文指纹：{ _fp_short(str(req.get('fingerprint', ''))) }")
    lines.append(f"- 实体计数：需求 {len(data.get('requirements') or [])} / 功能点 {len(feats)} / 验收标准 {len(data.get('acceptance') or [])} / 模块 {len(modules)}")
    lines.append("")

    blocking = [r for r in (data.get("blocking") or []) if isinstance(r, dict)]
    lines.append(f"## 阻塞项（{len(blocking)}）")
    lines.append("")
    if blocking:
        groups = {}
        for row in blocking:
            fid = str(row.get("feature_id", ""))
            groups.setdefault(fid, []).append(row)
        for fid in sorted(groups):
            mod = _fid_mod(data, fid)
            fname = next((f.get("name", "") for f in feats if f.get("feature_id") == fid), "")
            lines.append(f"### {fid}（{_mod_name(modules, mod)} · {fname}）")
            lines.append("")
            for row in groups[fid]:
                lines.append(f"- {row.get('issue_id', '')}：{row.get('question', '')}｜影响：{row.get('impact', '')}")
            lines.append("")
    else:
        lines.append("无。")
        lines.append("")

    pending = [r for r in (data.get("pending") or []) if isinstance(r, dict)]
    lines.append(f"## 待确认项（{len(pending)}）")
    lines.append("")
    if pending:
        for row in pending:
            fid = str(row.get("feature_id", ""))
            mod = _fid_mod(data, fid)
            lines.append(f"- {row.get('issue_id', '')}｜{fid}（{_mod_name(modules, mod)}）：{row.get('question', '')}｜暂按假设：{row.get('assumption', '')}")
        lines.append("")
    else:
        lines.append("无。")
        lines.append("")

    go_ahead = data.get("go_ahead") or []
    lines.append(f"## 可直接推进（{len(go_ahead)}）")
    lines.append("")
    if go_ahead:
        for fid in go_ahead:
            mod = _fid_mod(data, fid)
            fname = next((f.get("name", "") for f in feats if f.get("feature_id") == fid), "")
            lines.append(f"- {fid}（{_mod_name(modules, mod)} · {fname}）")
        lines.append("")
    else:
        lines.append("无。")
        lines.append("")

    gaps = [r for r in (data.get("coverage_gaps") or []) if isinstance(r, dict)]
    lines.append(f"## 覆盖缺口（{len(gaps)}）")
    lines.append("")
    if gaps:
        for row in gaps:
            lines.append(f"- {row.get('area', '')}：{row.get('reason', '')}")
        lines.append("")
    else:
        lines.append("无。")
        lines.append("")

    # v3 G07：图片识读结果（含未识读）必须出现在评审摘要，不能只登记不显示
    visuals = [r for r in (data.get("visual_evidence") or []) if isinstance(r, dict)]
    lines.append(f"## 原型/截图证据（{len(visuals)}）")
    lines.append("")
    if visuals:
        for row in visuals:
            understood = row.get("image_understood")
            state = "已识读" if understood else "未识读"
            rules = "、".join(str(x) for x in (row.get("extracted_rules") or [])) or "无"
            lines.append(f"- {row.get('file', '')}（{state}）：{row.get('boundary', '')}；提取规则：{rules}")
        lines.append("")
    else:
        lines.append("无。")
        lines.append("")

    not_rev = [r for r in (data.get("not_reviewed") or []) if isinstance(r, dict)]
    lines.append(f"## 未评审维度（{len(not_rev)}）")
    lines.append("")
    if not_rev:
        for row in not_rev:
            lines.append(f"- {row.get('dimension', '')}：{row.get('reason', '')}")
        lines.append("")
    else:
        lines.append("无。")
        lines.append("")


    # 对账行：渲染条数 == JSON 数组长度（纯派生的机械保证）
    lines.append("---")
    lines.append("")
    lines.append(
        "对账："
        f"阻塞 {len(blocking)}==blocking[{len(data.get('blocking') or [])}]；"
        f"待确认 {len(pending)}==pending[{len(data.get('pending') or [])}]；"
        f"推进 {len(go_ahead)}==go_ahead[{len(go_ahead)}]；"
        f"缺口 {len(gaps)}==coverage_gaps[{len(gaps)}]；"
        f"未评审 {len(not_rev)}==not_reviewed[{len(not_rev)}]"
    )
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description="渲染 review-summary.md（纯派生，不新增判定）")
    ap.add_argument("--input", required=True, help="review-result.json 绝对路径")
    ap.add_argument("--out", help="输出路径（默认与输入同目录 review-summary.md）")
    args = ap.parse_args()

    path = Path(args.input)
    if not path.is_file():
        print(f"输入不存在：{path}", file=sys.stderr)
        sys.exit(2)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"输入解析失败：{exc}", file=sys.stderr)
        sys.exit(2)

    out = Path(args.out) if args.out else path.with_name("review-summary.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(data), encoding="utf-8")
    digest = hashlib.sha256(out.read_bytes()).hexdigest()[:12]
    print(f"rendered: {out} sha256:{digest} rendered_at: {datetime.now().astimezone().isoformat(timespec='seconds')}")
    sys.exit(0)


if __name__ == "__main__":
    main()