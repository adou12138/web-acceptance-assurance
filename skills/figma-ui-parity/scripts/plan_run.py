#!/usr/bin/env python3
"""跑前计划门禁：45 维度先排程、后开跑。

2026-10-29 两处遗漏（4 个产物已产出未喂汇总、交互型驱动未排程）的根因是
"开跑前没有对 45 个维度逐个做排程决定，跑完才被 coverage_gate 报 notRun"。
本脚本把决定前移：

- 从 assets/dimension_manifest.json 读维度映射（单一事实源，此处不得另抄一份）；
- 结合执行档位（assets/run_profiles.json）生成 run-plan.json：
  档位纳入且 scheduling=auto → scheduled；
  档位纳入且 scheduling=manual → 必须带豁免理由（--exempt 显式给，或用
  manifest 的 defaultExemptReason 预登记——预登记不等于免检，会进报告的
  降级能力说明并逐轮留痕）；
  档位未纳入 → exempt（reason=档位未纳入）；
- 豁免理由禁止留空/待定类话术（黑名单校验），否则退出码 2；
- --design 未提供时，needsDesign 维度自动转为豁免（理由=设计基准未提供）。

退出码：0=计划核准可开跑；1=参数/格式错误；2=必测维度未排程且无合规豁免。

用法：
    python scripts/plan_run.py --profile full --run-dir ./parity-test/runs/<id> \
        [--design ./parity-test/design/design_model.json] [--write]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST_PATH = HERE.parent / "assets" / "dimension_manifest.json"
PROFILES_PATH = HERE.parent / "assets" / "run_profiles.json"

# 豁免理由黑名单：这些话术等于没写理由（"跳过必须留痕"纪律的机器化）
_REASON_BLACKLIST = ("待定", "待补", "稍后", "再说", "可能", "TODO", "tbd", "暂无")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def reason_is_valid(reason: str) -> bool:
    text = str(reason or "").strip()
    if len(text) < 8:
        return False
    lowered = text.lower()
    return not any(mark in text for mark in _REASON_BLACKLIST) and not any(
        mark in lowered for mark in _REASON_BLACKLIST)


def build_plan(profile: str, manifest: dict, profiles: dict,
               design_provided: bool,
               extra_exempt: dict[str, str] | None = None) -> tuple[dict, list[str]]:
    """生成计划。返回 (plan, problems)。problems 非空 → 退出码 2。"""
    extra_exempt = extra_exempt or {}
    profile_def = (profiles.get("profiles") or {}).get(profile)
    problems: list[str] = []
    if not profile_def:
        return {}, [f"未知档位 {profile!r}（可选：{sorted((profiles.get('profiles') or {}))}）"]

    included_raw = profile_def.get("dimensions")
    included = (set(manifest["dimensions"]) if included_raw == "all"
                else set(included_raw or []))
    scheduled: list[dict] = []
    exempt: list[dict] = []

    for dim_id, spec in manifest["dimensions"].items():
        if dim_id not in included:
            exempt.append({"dimension": dim_id, "name": spec.get("name", ""),
                           "reason": "档位未纳入（当前档位不要求该维度）"})
            continue
        if spec.get("scheduling") == "manual":
            reason = extra_exempt.get(dim_id) or spec.get("defaultExemptReason") or ""
            if not reason_is_valid(reason):
                problems.append(
                    f"{dim_id} {spec.get('name', '')}：manual 维度豁免理由缺失或不合规"
                    f"（当前={reason!r}；禁止留空/待定/TODO，须写明为什么本轮不测）")
                continue
            exempt.append({"dimension": dim_id, "name": spec.get("name", ""), "reason": reason})
            continue
        if spec.get("needsDesign") and not design_provided:
            reason = extra_exempt.get(dim_id) or "设计基准未提供（--design 未传），设计对照类维度无法执行"
            exempt.append({"dimension": dim_id, "name": spec.get("name", ""), "reason": reason})
            continue
        if not spec.get("driver") or not spec.get("artifact"):
            problems.append(f"{dim_id}：manifest 标记 auto 但缺少 driver/artifact（单一事实源自相矛盾，须修 manifest）")
            continue
        scheduled.append({
            "dimension": dim_id,
            "name": spec.get("name", ""),
            "driver": spec["driver"],
            "artifact": spec["artifact"],
            "artifactKey": spec.get("artifactKey"),
            "assembleArg": spec.get("assembleArg"),
            "requiredKeys": spec.get("requiredKeys") or [],
            # location=report 的维度（7.11/7.12）产物在上游 report.json 而非 run 目录；
            # 不透传它会让 pre_assemble_gate 按 run 目录验真，report 类通道永不生效。
            "location": spec.get("location"),
        })

    plan = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "profile": profile,
        "profileReason": profile_def.get("reason", ""),
        "designProvided": design_provided,
        "scheduled": scheduled,
        "exempt": exempt,
        "summary": {
            "total": len(manifest["dimensions"]),
            "scheduled": len(scheduled),
            "exempt": len(exempt),
            "problems": len(problems),
        },
    }
    return plan, problems


def main() -> int:
    ap = argparse.ArgumentParser(description="跑前计划门禁：45 维度先排程、后开跑")
    ap.add_argument("--profile", required=True, choices=["smoke", "standard", "full"])
    ap.add_argument("--run-dir", type=Path, required=True, help="本轮 run 目录（run-plan.json 落这里）")
    ap.add_argument("--design", type=Path, default=None, help="设计基准文件路径；未传则 needsDesign 维度转豁免")
    ap.add_argument("--exempt", action="append", default=[],
                    help="显式豁免：--exempt 7.14=理由（可多次），覆盖 manifest 预登记理由")
    ap.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    ap.add_argument("--profiles-file", type=Path, default=PROFILES_PATH)
    ap.add_argument("--write", action="store_true", help="把 run-plan.json 写入 run-dir")
    args = ap.parse_args()

    # --design 传了但文件不存在：needsDesign 维度会被当"设计已提供"排程，
    # 到驱动阶段才炸——设计缺口必须开跑前暴露（这正是本门禁存在的意义）
    if args.design is not None and not args.design.exists():
        print(json.dumps({"error": f"--design 指向的文件不存在: {args.design}"}, ensure_ascii=False))
        return 1

    try:
        manifest = load_json(args.manifest)
        profiles = load_json(args.profiles_file)
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": f"manifest/profiles 读取失败: {exc}"}, ensure_ascii=False))
        return 1

    extra_exempt: dict[str, str] = {}
    for item in args.exempt:
        if "=" not in item:
            print(json.dumps({"error": f"--exempt 格式须为 维度=理由，收到 {item!r}"}, ensure_ascii=False))
            return 1
        dim, reason = item.split("=", 1)
        extra_exempt[dim.strip()] = reason.strip()
    unknown_exempt = sorted(set(extra_exempt) - set(manifest.get("dimensions") or {}))
    if unknown_exempt:
        # 拼错维度号静默吞掉 = 排程缺口被掩盖，必须出声
        print("[plan-warn] --exempt 里的维度不在 manifest 中（疑似拼错）: "
              + ", ".join(unknown_exempt), file=sys.stderr)

    plan, problems = build_plan(args.profile, manifest, profiles,
                                design_provided=bool(args.design), extra_exempt=extra_exempt)

    if problems:
        for p in problems:
            print("[plan-blocked]", p, file=sys.stderr)
        print(json.dumps({"exit": 2, "problems": problems}, ensure_ascii=False, indent=1))
        return 2

    if args.write:
        args.run_dir.mkdir(parents=True, exist_ok=True)
        (args.run_dir / "run-plan.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(plan["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
