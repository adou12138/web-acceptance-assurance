#!/usr/bin/env python3
"""challenge_summary.py — 断言挑战结果的分账汇总（只读输入，纯函数内核）。

定位：读 challenge-results JSONL（references/schemas/challenge-results.schema.json，
判定口径见 references/assertion-challenges.md），按 kind 分两本账输出指标，绝不混算。
可选读候选复核 JSONL（finding-reviews 结构），联合输出候选裁定统计。

源码变异账（kind=source_mutation）：
- G=基线适用（baseline_green=true）的变异数；baseline_green=false 的记录单列
  baseline_failed，不入分母；
- I=invalid_build、E=equivalent、K=killed、survived、U=tool_error（未裁定）；
- 完整分数 = K/(G−I−E)，仅当 U=0 且分母>0 时给出；
- U>0 时 status="未完成"，不出完整分数，另报 conservative_lower_bound=K/(G−I−E)，
  标注"固定分母的保守检出下界"；
- 分母为零输出 "NA"，不输出 100%。

黑盒注入账（kind=black_box_injection）：
- injected_detections = injected=true 且 outcome=killed 的注入检出数；
- health_restored = 其中 restore_verified=true 的（撤销注入后复测恢复健康对照）；
- false_positives = injected=true、killed 但 restore_verified!=true（撤销注入未确认恢复，
  检出归因存疑，按误报单列待人工裁定）；
- natural_candidates = injected=false 且 outcome=killed 的自然缺陷候选数（仅 injected=false
  才计；注入制造的故障不得记为产品缺陷）；
- injected_missed = injected=true 且 outcome=survived（断言未报警）。

执行器与拦截适配器为接口约定，能力待探测，未实测。

CLI：
    python scripts/challenge_summary.py --challenges challenges.jsonl [--reviews reviews.jsonl] [--csv out.csv]
成功输出 JSON 摘要并以 0 退出；--csv 以 UTF-8-sig、逗号分隔导出逐指标行。
输入/解析错误以 2 退出。JSONL 解析方式与 scripts/adversarial_gate.py 保持一致。
"""
import argparse
import csv
import json
from pathlib import Path
import sys

LOWER_BOUND_LABEL = "固定分母的保守检出下界"
VERDICTS = ("accepted", "rejected", "unresolved")


def load_jsonl(path):
    """逐行解析 JSONL；空行跳过，解析错误抛 ValueError（与 adversarial_gate.load_jsonl 一致）。"""
    records = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} 第 {number} 行解析失败: {exc}") from exc
    return records


def _ratio(k, n):
    return round(k / n, 4) if n else "NA"


def source_mutation_account(records):
    """纯函数：源码变异账。records 为该 kind 的记录列表，不修改输入。"""
    baseline_failed = sum(1 for r in records if r.get("baseline_green") is False)
    applied = [r for r in records if r.get("baseline_green") is True]
    counts = {"killed": 0, "survived": 0, "invalid_build": 0, "equivalent": 0, "tool_error": 0}
    for record in applied:
        outcome = record.get("outcome")
        if outcome in counts:
            counts[outcome] += 1
    k, survived = counts["killed"], counts["survived"]
    i, e, u = counts["invalid_build"], counts["equivalent"], counts["tool_error"]
    denominator = len(applied) - i - e  # G − I − E
    account = {
        "G": len(applied),
        "I": i,
        "E": e,
        "K": k,
        "survived": survived,
        "U": u,
        "baseline_failed": baseline_failed,
        "denominator": denominator,
    }
    if u > 0:
        account["status"] = "未完成"
        account["score"] = None
        account["conservative_lower_bound"] = _ratio(k, denominator)
        account["lower_bound_label"] = LOWER_BOUND_LABEL
    else:
        account["status"] = "完成"
        account["score"] = _ratio(k, denominator)
    return account


def black_box_account(records):
    """纯函数：黑盒注入账。口径见模块 docstring；不修改输入。"""
    injected_killed = [r for r in records
                       if r.get("injected") is True and r.get("outcome") == "killed"]
    natural_killed = [r for r in records
                      if r.get("injected") is False and r.get("outcome") == "killed"]
    return {
        "injected_detections": len(injected_killed),
        "health_restored": sum(1 for r in injected_killed if r.get("restore_verified") is True),
        "false_positives": sum(1 for r in injected_killed if r.get("restore_verified") is not True),
        "natural_candidates": len(natural_killed),
        "injected_missed": sum(1 for r in records
                               if r.get("injected") is True and r.get("outcome") == "survived"),
        "health_alarms": sum(1 for r in records
                             if r.get("injected") is False and r.get("outcome") == "survived"),
    }


def verdict_counts(reviews):
    """纯函数：候选裁定统计，accepted/rejected/unresolved 分列（另计未复核）。"""
    counts = {v: 0 for v in VERDICTS}
    counts["unreviewed"] = 0
    for review in reviews or []:
        if not isinstance(review, dict):
            continue
        verdict = review.get("verdict")
        if verdict in VERDICTS:
            counts[verdict] += 1
    return counts


def summarize(challenges, reviews):
    """纯函数内核：challenges/reviews 为已解析记录列表，返回分账汇总 dict。不修改输入。"""
    challenges = [c for c in challenges or [] if isinstance(c, dict)]
    reviews = [r for r in reviews or [] if isinstance(r, dict)]
    source_records = [c for c in challenges if c.get("kind") == "source_mutation"]
    black_records = [c for c in challenges if c.get("kind") == "black_box_injection"]
    return {
        "source_mutation": source_mutation_account(source_records),
        "black_box_injection": black_box_account(black_records),
        "verdict_counts": verdict_counts(reviews),
        "notes": [
            "两账分列：黑盒检出不计入源码变异 K/(G−I−E)，一律不称源码变异杀伤率",
            "U>0 时完整分数标未完成，仅报固定分母的保守检出下界",
            "零分母输出 NA，不输出 100%",
            "natural_candidates 仅统计 injected=false 的记录；注入制造的故障不得记为产品缺陷",
            "false_positives 为撤销注入未确认恢复的注入检出，归因存疑单列，待人工裁定",
        ],
    }


def to_csv_rows(summary):
    """纯函数：把汇总展开为 (account, metric, value) 逐指标行，便于导出复核。"""
    rows = []
    for metric, value in (summary.get("source_mutation") or {}).items():
        rows.append({"account": "source_mutation", "metric": metric, "value": _cell(value)})
    for metric, value in (summary.get("black_box_injection") or {}).items():
        rows.append({"account": "black_box_injection", "metric": metric, "value": _cell(value)})
    for verdict, count in (summary.get("verdict_counts") or {}).items():
        rows.append({"account": "verdict_counts", "metric": verdict, "value": str(count)})
    for note in summary.get("notes") or []:
        rows.append({"account": "notes", "metric": "note", "value": note})
    return rows


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def write_csv(summary, path):
    """以 UTF-8-sig（带 BOM）、逗号分隔导出逐指标行；返回写入行数。"""
    rows = to_csv_rows(summary)
    with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["account", "metric", "value"])
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="断言挑战分账汇总（源码变异/黑盒注入两本账，只读输入）")
    parser.add_argument("--challenges", required=True,
                        help="challenge-results JSONL 路径（CHL- 记录）")
    parser.add_argument("--reviews", help="候选复核 JSONL 路径（finding-reviews 结构），联合输出裁定统计")
    parser.add_argument("--csv", help="逐指标 CSV 导出路径（UTF-8-sig、逗号分隔）")
    args = parser.parse_args(argv)
    try:
        challenges = load_jsonl(args.challenges)
        reviews = load_jsonl(args.reviews) if args.reviews else []
        summary = summarize(challenges, reviews)
        if args.csv:
            summary["csv_rows_written"] = write_csv(summary, args.csv)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError) as exc:
        print(f"challenge_summary 输入/解析错误: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
