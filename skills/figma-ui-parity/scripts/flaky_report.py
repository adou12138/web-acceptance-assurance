#!/usr/bin/env python3
"""跨轮不稳定性（flaky）量化：读历史运行快照，找出反复翻转的维度。

为什么需要它
------------
`baseline_regression.cjs` 每次运行都往 `baseline/runs/` 落一份快照，但回归比较只用
「本次 vs 基线」的一次性差值。于是下面两种报告长得一模一样：

  A 轮 fail=3，B 轮 fail=3  → 同样的 3 项一直没修（稳定的真缺陷）
  A 轮 fail=3，B 轮 fail=3  → 完全不同的 3 项轮流出现（不稳定的判定）

前者该进缺陷台账，后者该先进判定口径修复——混在一起，团队会开始不信任整份报告。
本脚本把「同一维度在多轮之间状态翻转」这件事单独量化，不改任何判定逻辑，只读历史。

判据口径
--------
- 翻转只按 **pass / 非 pass** 二分统计：这是唯一会改变门禁结论的边界。
  `pending → warn` 这种同属「非通过」的漂移不记为翻转，但会记进 `statusDrift`，
  因为它同样说明判定不稳定，只是不影响门禁。
- 一次翻转都没有的维度不会进榜单，即使它常年 fail——那是**稳定**的缺陷。
- 同一 key 内按 `_run` 标签（ISO 时间派生）排序，只对连续两次都有结论的区间计数；
  中途某轮该维度是 `notRun`（换档位/换策略）时跳过该区间，不把「没跑」算成「变了」。

输入兼容
--------
旧版快照没有 `checks` 键。这类 run 记为 `legacyRuns` 并在产物里点名，
**不得**当成「该轮所有维度都无结论」静默跳过——否则历史越久越容易得出「很稳定」。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# 会改变门禁结论的唯一边界：pass 与「其它一切」。
# pending / warn 都在门禁里算未通过，因此它们之间的漂移不算翻转。
GREEN = "pass"
# 这些状态表示「该轮没对这个维度下结论」，不参与翻转计数
NO_VERDICT = {"notRun", "dataMissing", ""}


def _reads_checks(run: dict) -> dict:
    checks = run.get("checks")
    if not isinstance(checks, dict):
        return {}
    out = {}
    for key, value in checks.items():
        if isinstance(value, str) and value.strip():
            out[str(key)] = value.strip()
    return out


def load_runs(runs_dir: Path) -> tuple[dict, list, int]:
    """返回 {safeKey: [run, ...]}、无法解析的文件名、缺 checks 的 run 数。"""
    groups: dict = {}
    broken: list = []
    legacy = 0
    if not runs_dir.exists():
        return groups, broken, legacy
    for path in sorted(runs_dir.glob("*.json")):
        try:
            run = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            broken.append(path.name)
            continue
        if not isinstance(run, dict):
            broken.append(path.name)
            continue
        # 文件名形如 <safeKey>.<runLabel>.json：safeKey 是 16 位 md5 前缀
        safe_key = path.name.split(".")[0]
        if not _reads_checks(run):
            legacy += 1
            continue
        run["_file"] = path.name
        groups.setdefault(safe_key, []).append(run)
    for key in groups:
        groups[key].sort(key=lambda r: str(r.get("_run") or ""))
    return groups, broken, legacy


def dimension_history(runs: list) -> dict:
    """{维度: [status, ...]}，按时间序；缺该维度结论的轮次直接跳过（不填占位）。"""
    history: dict = {}
    for run in runs:
        for dim, status in _reads_checks(run).items():
            if status in NO_VERDICT:
                continue
            history.setdefault(dim, []).append(status)
    return history


def flakiness(statuses: list) -> dict:
    """对单个维度的状态序列算翻转。序列里相邻两项都属于「无结论」时前面已过滤。"""
    flips = 0
    drift = 0
    for prev, cur in zip(statuses, statuses[1:]):
        if prev == cur:
            continue
        drift += 1
        if (prev == GREEN) != (cur == GREEN):
            flips += 1
    observed = len(statuses)
    return {
        "runs": observed,
        "flips": flips,
        "statusDrift": drift,
        "flakyRate": round(flips / (observed - 1), 3) if observed > 1 else 0.0,
        "distinctStatuses": sorted(set(statuses)),
    }


def verdict_of(entry: dict) -> str:
    if entry["flips"] > 0:
        return "flaky"
    distinct = entry["distinctStatuses"]
    if distinct == [GREEN]:
        return "stable-pass"
    if len(distinct) == 1:
        return "stable-" + distinct[0]
    # 全程没跨过 pass 边界，但状态本身在变（如 pending ↔ fail）：判定口径不稳定
    return "drift-only"


def analyze(groups: dict, min_runs: int = 2, top: int = 0) -> dict:
    keys: dict = {}
    insufficient: list = []
    all_flaky: dict = {}
    for safe_key, runs in groups.items():
        if len(runs) < min_runs:
            insufficient.append({"key": safe_key, "runs": len(runs),
                                 "reason": "有效快照少于 {} 轮（{} 轮），翻转率无统计意义".format(min_runs, len(runs))})
            continue
        history = dimension_history(runs)
        dims = []
        for dim, statuses in sorted(history.items(), key=lambda kv: _dim_sort(kv[0])):
            entry = {"dimension": dim, "statuses": statuses}
            entry.update(flakiness(statuses))
            entry["verdict"] = verdict_of(entry)
            dims.append(entry)
            if entry["flips"] > 0:
                best = all_flaky.get(dim)
                if best is None or entry["flips"] > best["flips"]:
                    all_flaky[dim] = {"dimension": dim, "flips": entry["flips"],
                                      "flakyRate": entry["flakyRate"], "key": safe_key,
                                      "statuses": statuses}
        dims.sort(key=lambda d: (-d["flips"], -d["statusDrift"], _dim_float(d["dimension"])))
        keys[safe_key] = {
            "key": runs[0].get("_key") or "",
            "pageUrl": runs[0].get("pageUrl") or "",
            "runs": len(runs),
            "firstRun": runs[0].get("_run") or "",
            "lastRun": runs[-1].get("_run") or "",
            "dimensions": dims[:top] if top else dims,
        }
    ranked = sorted(all_flaky.values(), key=lambda d: (-d["flips"], _dim_float(d["dimension"])))
    return {
        "generatedAt": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "minRuns": min_runs,
        "summary": {
            "keysAnalysed": len(keys),
            "runsTotal": sum(len(v) for v in groups.values()),
            "flakyDimensions": [d["dimension"] for d in ranked],
            "flakyCount": len(ranked),
            "insufficientKeys": len(insufficient),
        },
        "flakyRanking": ranked,
        "insufficient": insufficient,
        "byKey": keys,
    }


def _dim_float(dim: str) -> float:
    """排序用：非数字维度（理论不该有）排到最后。

    用字符判断而不是 try/except：这里没有"异常要兜住"的语义，
    只是把不认识的编号放到末尾；写成 except 会被静默失败审计当成吞异常。
    """
    text = str(dim or "")
    head, _, tail = text.partition(".")
    if head.isdigit() and (tail.isdigit() or tail == ""):
        return float(text)
    return 999.0


def _dim_sort(dim: str):
    return (_dim_float(dim), dim)


def write_csv(payload: dict, path: Path) -> None:
    """CSV（utf-8-sig）：每个（key, 维度）一行，直接可排序筛选。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["key", "pageUrl", "dimension", "runs", "flips", "flakyRate",
                         "statusDrift", "distinctStatuses", "verdict", "statusSequence"])
        for safe_key, block in payload["byKey"].items():
            for dim in block["dimensions"]:
                writer.writerow([
                    block.get("key") or safe_key,
                    block.get("pageUrl") or "",
                    dim["dimension"],
                    dim["runs"],
                    dim["flips"],
                    dim["flakyRate"],
                    dim["statusDrift"],
                    "|".join(dim["distinctStatuses"]),
                    dim["verdict"],
                    " → ".join(dim["statuses"]),
                ])


def main() -> int:
    here = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description="跨轮不稳定性（flaky）量化：只读历史快照，不改判定")
    ap.add_argument("--baseline-dir", type=Path, default=Path.cwd() / "parity-test" / "baseline",
                    help="baseline_regression.cjs 的 --baseline-dir（读其 runs/ 子目录）")
    ap.add_argument("--out", type=Path, default=None, help="JSON 产物路径，缺省 <baseline-dir>/flaky.json")
    ap.add_argument("--csv", type=Path, default=None, help="CSV 产物路径，缺省 <baseline-dir>/flaky.csv")
    ap.add_argument("--min-runs", type=int, default=2, help="至少几轮才参与统计（默认 2）")
    ap.add_argument("--top", type=int, default=0, help="每个 key 只保留翻转最多的前 N 个维度（0=全部）")
    ap.add_argument("--fail-on-flaky", type=int, default=None, metavar="N",
                    help="任一维度翻转次数 >= N 时以 rc=2 退出（不给该参数则永不因 flaky 失败）")
    args = ap.parse_args()

    runs_dir = args.baseline_dir / "runs"
    groups, broken, legacy = load_runs(runs_dir)
    if not groups and not legacy:
        print(json.dumps({
            "status": "notRun",
            "reason": "{} 下没有可用的运行快照；先跑一次 baseline_regression.cjs".format(runs_dir),
        }, ensure_ascii=False))
        return 0

    payload = analyze(groups, args.min_runs, args.top)
    payload["source"] = {"runsDir": str(runs_dir), "legacyRuns": legacy, "unreadableFiles": broken[:10],
                         "unreadableCount": len(broken)}
    # 旧快照缺 checks 是「统计样本变少」，必须显式告知，否则结论会被读成「历史一直很稳定」
    if legacy:
        payload["warnings"] = [
            "{} 份快照没有每维度结论（旧版本产物），已排除在统计外：这些轮次无法判断维度是否翻转".format(legacy)
        ]
    if broken:
        payload.setdefault("warnings", []).append(
            "{} 份快照读不出来，已跳过：{}".format(len(broken), ", ".join(broken[:5])))

    out = args.out or (args.baseline_dir / "flaky.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    csv_path = args.csv or (args.baseline_dir / "flaky.csv")
    write_csv(payload, csv_path)

    print(json.dumps({
        "out": str(out),
        "csv": str(csv_path),
        "keysAnalysed": payload["summary"]["keysAnalysed"],
        "runsTotal": payload["summary"]["runsTotal"],
        "flakyCount": payload["summary"]["flakyCount"],
        "flakyRanking": payload["flakyRanking"][:5],
        "legacyRuns": legacy,
        "insufficientKeys": payload["summary"]["insufficientKeys"],
    }, ensure_ascii=False, indent=2))

    if args.fail_on_flaky is not None:
        worst = [d for d in payload["flakyRanking"] if d["flips"] >= args.fail_on_flaky]
        if worst:
            print("[x] {} 个维度翻转次数 >= {}：{}".format(
                len(worst), args.fail_on_flaky,
                ", ".join("{}({}次)".format(d["dimension"], d["flips"]) for d in worst[:8])),
                file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
