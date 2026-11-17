#!/usr/bin/env python3
"""汇总前接线门禁：双向对账 + 深度验真 + assemble 命令生成。

对应 2026-10-29 的两类事故：
1) 【漏传】4 个产物已产出但没接进 assemble 入参——性能/键盘/按钮/设计对比度，
   其中 performance.json 里还躺着一条 confirmed 缺陷被静默吞掉；
   → 反向对账：run 目录里出现了 manifest 登记的 artifactKey 文件，
     却不在 assemble 入参清单里 = 拦截（漏传 100% 阻断）。
2) 【假跑】只查文件存在会放过 0 字节/空壳产物（驱动半路崩溃留壳文件）。
   → 正向验真：每个计划产物必须存在、可解析、且包含 manifest requiredKeys。

用法（在 assemble_report 之前执行）：
    python scripts/pre_assemble_gate.py --plan ./runs/<id>/run-plan.json \
        --run-dir ./runs/<id> \
        --assemble-args ./runs/<id>/assemble-args.json   # assemble 的 --findings 等入参清单
                                                       # （省略则只验真+生成建议命令）

输出：stdout = JSON 结论；生成建议命令写入 <run-dir>/assemble-args.suggested.json。
退出码：0=通过；1=参数错误；2=对账/验真失败（清单见 stderr）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST_PATH = HERE.parent / "assets" / "dimension_manifest.json"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_artifact(run_dir: Path, key: str) -> Path | None:
    """按 manifest 记录的相对路径解析产物；不存在时按文件名在 run 目录内找唯一同名文件。

    为什么需要：产物子目录名可能随调用方漂移（实测 button-audit/ 与 button_audit/
    并存），按硬路径判定会把「实际已产出」误报为缺失、又漏掉真正的漏传。
    """
    direct = run_dir / key
    if direct.exists():
        return direct
    base = Path(key).name
    hits = [p for p in run_dir.rglob(base)
            if p.is_file() and p.name == base]
    return hits[0] if len(hits) == 1 else None


def validate_artifact(path: Path, required_keys: list[str]) -> str | None:
    """返回错误描述；None = 合格。存在≠合格：0 字节/坏 JSON/缺必需键都拦。"""
    if not path.exists():
        return f"计划产物不存在: {path.name}"
    if path.stat().st_size == 0:
        return f"计划产物为空文件（疑似驱动半途崩溃）: {path.name}"
    try:
        payload = load_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        return f"计划产物不是合法 JSON: {path.name}（{exc}）"
    for key in required_keys:
        if key not in payload:
            return f"计划产物缺少必需键 {key!r}（空壳假跑嫌疑）: {path.name}"
    return None


def run_gate(plan: dict, run_dir: Path, assemble_args: list[str] | None,
             manifest: dict, report_path: Path | None = None) -> tuple[list[str], list[str], list[str]]:
    """返回 (errors, warnings, suggested_args)。"""
    errors: list[str] = []
    warnings: list[str] = []
    internal = set(manifest.get("artifactInternalFiles") or [])

    scheduled = plan.get("scheduled") or []

    # 1) 正向：计划产物存在且形状合法
    for item in scheduled:
        artifact = item.get("artifact")
        if not artifact:
            continue
        if item.get("location") == "report":
            # 7.11/7.12 直接回写 report.json，产物不在 run 目录而在上游 report——
            # 用 --report 的真实路径验真；未提供或尚未生成时降级为警告。
            # 「尚未生成」是正常时序：report 由 gate 之后的 assemble/capture 产出。
            if report_path is not None and report_path.exists():
                err = validate_artifact(report_path, item.get("requiredKeys") or [])
            elif report_path is not None:
                warnings.append(f"[{item['dimension']}] report 尚未生成（由 assemble/capture 阶段产出），跳过验真")
                continue
            else:
                warnings.append(f"[{item['dimension']}] 未提供 --report，跳过 report 类产物验真")
                continue
        else:
            resolved = resolve_artifact(run_dir, artifact)
            err = None if resolved else f"计划产物不存在: {artifact}"
            if not err:
                err = validate_artifact(resolved, item.get("requiredKeys") or [])
        if err:
            errors.append(f"[{item['dimension']}] {err}")

    # 2) 反向：run 目录里的已知产物文件必须全部出现在 assemble 入参
    provided = set()
    if assemble_args:
        for a in assemble_args:
            p = Path(a)
            provided.add(p.name)
            provided.add(str(p.relative_to(run_dir)).replace("\\", "/") if p.is_relative_to(run_dir) else p.name)

    for item in scheduled:
        key = item.get("artifactKey")
        if not key:
            continue  # 直接回写 report 的维度（7.11/7.12）不经过 assemble
        f = resolve_artifact(run_dir, key)
        if f is not None and key not in provided and f.name not in provided:
            arg_name = str(item.get("assembleArg") or "findings").lstrip("-")
            errors.append(
                f"[{item['dimension']}] 漏传：{key} 已产出但不在 assemble 入参里"
                f"（本轮须以 --{arg_name} 传入）")

    # 3) 兜底扫描：run 目录（含一级产物子目录）出现 manifest 登记过 artifactKey 的
    #    文件但该维度未排程——排程与现实的另一类脱节
    known_bases = {Path(spec.get("artifactKey")).name
                   for spec in manifest["dimensions"].values() if spec.get("artifactKey")}
    internal_bases = {Path(x).name for x in internal}
    scheduled_bases = {Path(i.get("artifactKey")).name for i in scheduled if i.get("artifactKey")}
    for f in sorted(run_dir.rglob("*.json")):
        if f.name in known_bases and f.name not in internal_bases and f.name not in scheduled_bases:
            warnings.append(f"未排程产物存在于 run 目录: {f.relative_to(run_dir)}（检查 manifest 排程或删除陈旧文件）")

    # 4) 生成建议 assemble 入参（驱动产物全量接线，按 manifest 的 assembleArg）
    suggested: list[str] = []
    for item in scheduled:
        arg = item.get("assembleArg")
        key = item.get("artifactKey")
        if not arg or not key:
            continue
        f = resolve_artifact(run_dir, key)
        if f is not None and not validate_artifact(f, item.get("requiredKeys") or []):
            suggested += [arg, str(f)]
    return errors, warnings, suggested


def main() -> int:
    ap = argparse.ArgumentParser(description="汇总前接线门禁：双向对账+深度验真")
    ap.add_argument("--plan", type=Path, required=True)
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--assemble-args", type=Path, default=None,
                    help="JSON 数组文件：本轮 assemble 的 --findings 等 Path 入参清单；缺省只验真不反向对账")
    ap.add_argument("--report", type=Path, default=None,
                    help="上游 report.json 路径：location=report 的维度（7.11/7.12）对它验真；缺省降级为警告")
    ap.add_argument("--write-suggested", action="store_true",
                    help="把建议入参写为 <run-dir>/assemble-args.suggested.json")
    ap.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    args = ap.parse_args()

    try:
        plan = load_json(args.plan)
        manifest = load_json(args.manifest)
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": f"plan/manifest 读取失败: {exc}"}, ensure_ascii=False))
        return 1

    assemble_args = None
    if args.assemble_args:
        try:
            assemble_args = load_json(args.assemble_args)
        except (OSError, json.JSONDecodeError) as exc:
            print(json.dumps({"error": f"assemble-args 读取失败: {exc}"}, ensure_ascii=False))
            return 1

    errors, warnings, suggested = run_gate(plan, args.run_dir, assemble_args, manifest,
                                           report_path=args.report)

    if args.write_suggested and suggested:
        out = args.run_dir / "assemble-args.suggested.json"
        out.write_text(json.dumps(suggested, ensure_ascii=False, indent=1), encoding="utf-8")

    result = {"errors": errors, "warnings": warnings, "suggestedCount": len(suggested) // 2}
    if errors:
        for e in errors:
            print("[gate-blocked]", e, file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 2 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
