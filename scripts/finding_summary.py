#!/usr/bin/env python3
"""finding_summary.py — 对抗候选的辅助最小化与聚类汇总（只读输入，纯函数内核）。

定位：读对抗事件（adversarial-events 结构，JSONL）与候选复核（finding-reviews 结构，
JSONL），产出候选归簇、复现率 k/n、最小化状态与预算字段，供人工裁定与报告引用。
本脚本只读输入、不做缺陷判定：unresolved 候选单列，既不计缺陷也不计误报
（口径见 references/finding-triage.md）。schema 之外的扩展字段（version/contract/
entry/role/result_code/reproduced/minimization）在此宽松解析——缺省按空串/缺失处理。

规则（对应 references/finding-triage.md 聚类与最小化一节）：
- 归簇键 = 版本 + 契约/不变量 + 入口 + 角色 + 结果码；组内保留全部样本（含失败尝试）；
- 同结果码但不同入口不自动合并，只标 suspected_duplicate（疑似重复），合并须人工裁定；
- 复现率 k/n：k=复现成功次数，n=同版本/环境/步骤下全部实际尝试（失败尝试保留在 n 中）；
- 最小化状态：只有 verified_minimal 可标"已最小化"；缩减失败（reduce_failed）不撤销首败，
  状态不得标 verified_minimal；
- 预算字段：probes_used（该候选的事件/探针数）、evidence_count（证据路径总数）。

CLI：
    python scripts/finding_summary.py --events events.jsonl [--reviews reviews.jsonl] [--csv out.csv]
成功输出 JSON 摘要并以 0 退出；--csv 以 UTF-8-sig、逗号分隔导出逐候选行。
输入/解析错误以 2 退出。JSONL 解析方式与 scripts/adversarial_gate.py 保持一致
（utf-8-sig、跳过空行、坏行抛 ValueError）。
"""
import argparse
import csv
import json
from pathlib import Path
import sys

CLUSTER_KEY_FIELDS = ("version", "contract", "entry", "role", "result_code")
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


def _field(record, name):
    value = record.get(name)
    return str(value).strip() if value is not None else ""


def cluster_events(events):
    """纯函数：把带 finding_id 的事件按候选归簇，返回 {finding_id: [cluster, ...]}。

    cluster 含键字段、全部样本（事件 ID 保留原顺序）、逐簇复现 k/n。
    不修改输入。
    """
    clusters = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        finding_id = _field(event, "finding_id")
        if not finding_id:
            continue
        key = {name: _field(event, name) for name in CLUSTER_KEY_FIELDS}
        bucket = clusters.setdefault(finding_id, [])
        existing = next((c for c in bucket if c["key"] == key), None)
        if existing is None:
            existing = {"key": key, "samples": [], "reproduced_true": 0, "attempts": 0}
            bucket.append(existing)
        existing["samples"].append(_field(event, "event_id") or f"row{len(existing['samples'])}")
        existing["attempts"] += 1
        if event.get("reproduced") is True:
            existing["reproduced_true"] += 1
    return clusters


def _rate(k, n):
    return round(k / n, 4) if n else "NA"


def mark_suspected_duplicates(candidate_keys):
    """纯函数：同 版本+契约+角色+结果码 但入口不同的候选对，互相标疑似重复（不合并）。"""
    groups = {}
    for finding_id, keys in candidate_keys.items():
        for key in keys:
            signature = (key["version"], key["contract"], key["role"], key["result_code"])
            groups.setdefault(signature, set()).add((finding_id, key["entry"]))
    duplicates = {}
    for members in groups.values():
        entries = {}
        for finding_id, entry in members:
            entries.setdefault(finding_id, set()).add(entry)
        ids = sorted(entries)
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                # 入口集合有交集才可能是同一现象；完全不同入口的也仅并列提示
                duplicates.setdefault(a, set()).add(b)
                duplicates.setdefault(b, set()).add(a)
    return {k: sorted(v) for k, v in duplicates.items()}


def summarize(events, reviews):
    """纯函数内核：events/reviews 为已解析记录列表，返回汇总 dict。不修改输入。"""
    events = [e for e in events or [] if isinstance(e, dict)]
    reviews = [r for r in reviews or [] if isinstance(r, dict)]
    clusters = cluster_events(events)

    verdict_by_finding = {}
    for review in reviews:
        finding_id = _field(review, "finding_id")
        if finding_id and review.get("verdict") in VERDICTS:
            verdict_by_finding.setdefault(finding_id, review.get("verdict"))

    candidates = []
    for finding_id in sorted(clusters):
        rows = [e for e in events if _field(e, "finding_id") == finding_id]
        minimized = ""
        reduce_failed = False
        probes_used = len(rows)
        evidence_count = sum(len(e.get("evidence_paths") or []) for e in rows)
        attempts = len(rows)
        successes = sum(1 for e in rows if e.get("reproduced") is True)
        for event in rows:
            status = _field(event, "minimization")
            if status == "verified_minimal":
                minimized = "verified_minimal"
            elif status == "reduce_failed":
                reduce_failed = True
        if reduce_failed:
            # 缩减失败不撤销首败：状态不得标 verified_minimal，首败样本已保留在簇内
            minimized = "reduce_failed" if minimized != "verified_minimal" else minimized
        candidate = {
            "finding_id": finding_id,
            "verdict": verdict_by_finding.get(finding_id, "unreviewed"),
            "clusters": [
                {
                    "key": c["key"],
                    "samples": list(c["samples"]),
                    "reproduction": {
                        "successes": c["reproduced_true"],
                        "attempts": c["attempts"],
                        "rate": _rate(c["reproduced_true"], c["attempts"]),
                    },
                }
                for c in sorted(clusters[finding_id], key=lambda c: tuple(c["key"].values()))
            ],
            "reproduction": {"successes": successes, "attempts": attempts, "rate": _rate(successes, attempts)},
            "minimization": minimized or None,
            "budget": {"probes_used": probes_used, "evidence_count": evidence_count},
        }
        candidates.append(candidate)

    dup_map = mark_suspected_duplicates(
        {c["finding_id"]: [cl["key"] for cl in c["clusters"]] for c in candidates})
    for candidate in candidates:
        candidate["suspected_duplicate_of"] = dup_map.get(candidate["finding_id"], [])

    verdict_counts = {v: 0 for v in VERDICTS}
    verdict_counts["unreviewed"] = 0
    for candidate in candidates:
        verdict_counts[candidate["verdict"]] += 1
    unresolved_ids = [c["finding_id"] for c in candidates if c["verdict"] == "unresolved"]

    return {
        "candidates": candidates,
        "cluster_count": sum(len(c["clusters"]) for c in candidates),
        "verdict_counts": verdict_counts,
        "unresolved_ids": unresolved_ids,
        "notes": [
            "unresolved 候选单列：不计有效缺陷也不计误报（finding-triage.md 口径）",
            "疑似重复仅并列提示，不自动合并销号",
            "复现率分母含失败尝试；缩减失败不撤销首败",
        ],
    }


CSV_COLUMNS = ("finding_id", "verdict", "version", "contract", "entry", "role", "result_code",
               "samples", "successes", "attempts", "reproduction_rate", "minimization",
               "probes_used", "evidence_count", "suspected_duplicate_of")


def to_csv_rows(summary):
    """纯函数：把汇总展开为逐簇 CSV 行（list[dict]，键为 CSV_COLUMNS）。"""
    rows = []
    for candidate in summary.get("candidates") or []:
        for cluster in candidate.get("clusters") or []:
            key = cluster["key"]
            rows.append({
                "finding_id": candidate["finding_id"],
                "verdict": candidate["verdict"],
                "version": key["version"],
                "contract": key["contract"],
                "entry": key["entry"],
                "role": key["role"],
                "result_code": key["result_code"],
                "samples": ";".join(cluster["samples"]),
                "successes": cluster["reproduction"]["successes"],
                "attempts": cluster["reproduction"]["attempts"],
                "reproduction_rate": cluster["reproduction"]["rate"],
                "minimization": candidate.get("minimization") or "",
                "probes_used": candidate["budget"]["probes_used"],
                "evidence_count": candidate["budget"]["evidence_count"],
                "suspected_duplicate_of": ";".join(candidate.get("suspected_duplicate_of") or []),
            })
    return rows


def write_csv(summary, path):
    """以 UTF-8-sig（带 BOM）、逗号分隔导出逐簇行；返回写入行数。"""
    rows = to_csv_rows(summary)
    with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="对抗候选聚类与复现率汇总（只读输入）；unresolved 单列不计缺陷不计误报")
    parser.add_argument("--events", help="对抗事件 JSONL 路径（adversarial-events 结构）")
    parser.add_argument("--reviews", help="候选复核 JSONL 路径（finding-reviews 结构）")
    parser.add_argument("--csv", help="逐簇 CSV 导出路径（UTF-8-sig、逗号分隔）")
    args = parser.parse_args(argv)
    try:
        events = load_jsonl(args.events) if args.events else []
        reviews = load_jsonl(args.reviews) if args.reviews else []
        summary = summarize(events, reviews)
        if args.csv:
            summary["csv_rows_written"] = write_csv(summary, args.csv)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError) as exc:
        print(f"finding_summary 输入/解析错误: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
