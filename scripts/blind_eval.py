#!/usr/bin/env python3
"""blind_eval.py — 独立盲测的执行与打分工具（隔离答案、冻结协议、可复核计分）。

子命令：
    prepare  从含答案的夹具生成"只剩题目"的运行目录（执行方只拿这个目录）
    verify   检查运行目录是否混入答案（文件名/内容/答案 ID），并核对执行方声明
    score    用答案键给执行结果打分（检出/误报/分支/证据/未测义务），结果写 run_dir 同级的 scores/<run>.json
    compare  两份 score.json 对照，标注单次复跑、同一执行方等不可比风险
    ab       A/B 两臂聚合对照（改造前/后各一目录，每目录含多个 run 的 score.json）
             ——按臂聚合检出/误报/复现/成本，输出裁定精确率与每百探针有效发现等
             派生指标（近似口径，须按 ab-protocol.md 解读，不得夸大样本）

设计约束：
- 答案键只在评测者手里；prepare 产物必须不含 hidden_answers / legit_near_misses / match 规则。
- 计分只认可复核输入：证据文件必须真实存在；分支分母必须来自答案键，执行方改名或缩减分母直接报错。
- 协议文档哈希写进每条结果，改判据即产生新哈希，旧结果不能冒充新口径。
- 工具不执行被测网站，也不产生"质量已达标"结论；未真实运行前只能得 fail/incomplete。

用法示例：
    python scripts/blind_eval.py prepare --fixture docs/evals-baseline/detection-fixtures/01-boundary-form.json --out runs/01-A
    python scripts/blind_eval.py verify --run runs/01-A
    python scripts/blind_eval.py score --run runs/01-A --fixture <同一夹具> --findings runs/01-A/findings.json
"""

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "docs" / "evals-baseline" / "detection-protocol.md"
SEVERITIES = ("blocker", "major", "normal")
FINDING_FIELDS = ("id", "title", "observed", "severity", "evidence")
ANSWER_MARKERS = ("hidden_answers", "legit_near_misses", "match_all", "match_any")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_path(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def protocol_hash():
    return sha256_path(PROTOCOL) if PROTOCOL.is_file() else ""


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _text(value):
    return str(value or "")


def spec_from_fixture(fixture):
    """只保留执行方可见的题面：场景、输入、必选分支、交付格式。"""
    return {
        "scenario": fixture.get("scenario", ""),
        "inputs": fixture.get("inputs", ""),
        "required_branches": list(fixture.get("required_branches") or []),
        "holdout": bool(fixture.get("holdout")),
        "constraints": {
            "allowed": "只读操作 + 本轮明确授权范围内的可回滚写操作",
            "forbidden": "不得读取评测答案、不得改动被测数据之外的系统状态、不得伪造证据",
            "evidence": "每条缺陷须给可复核证据（截图/请求响应/读回结果），路径写在 findings[].evidence",
        },
        "deliverable": {
            "file": "findings.json",
            "shape": {
                "executor": {"name": "执行方标识", "tooling": "浏览器/接口工具", "saw_answers": False},
                "budget": {"minutes": 30},
                "branches": [{"name": "必须来自题面 required_branches", "status": "EXECUTED|BLOCKED", "reason": "BLOCKED 必填"}],
                "findings": [{"id": "F-1", "title": "现象",
                              "expected": "按需求应发生什么（结构化三要素之一）",
                              "actual": "实际发生了什么（与 expected 的差异）",
                              "observed_via": "怎么观察到的：DOM/网络响应/截图/接口读回",
                              "observed": "实际观察与预期差（自由文本，兼容旧格式）",
                              "severity": "blocker|major|normal", "evidence": ["相对本目录的证据路径（数组）"],
                              "evidence_grade": "E1（默认）；E2 模拟/E3 未执行不计检出",
                              "reproduction": "可选 {attempts, successes}（k/n，含失败尝试）",
                              "cost": "可选 {probes, minutes}"}],
                "obligations_untested": ["没测到的义务，如实列出"],
                "unresolved_candidates": "可选，未裁定候选数量或条目数组",
                "invalid_actions": 0,
            },
        },
    }


def prepare(fixture_path, out_dir, run_id=None, budget_min=30):
    fixture = _read_json(fixture_path)
    out = Path(out_dir).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"运行目录非空，拒绝覆盖：{out}")
    out.mkdir(parents=True, exist_ok=True)
    spec = spec_from_fixture(fixture)
    run = {
        "run_id": run_id or out.name,
        "created_at": _now(),
        "fixture": Path(fixture_path).name,
        "fixture_sha256": sha256_path(fixture_path),
        "protocol_sha256": protocol_hash(),
        "budget_minutes": budget_min,
        "executor_saw_answers": False,
    }
    _write_json(out / "spec.json", spec)
    _write_json(out / "run.json", run)
    (out / "evidence").mkdir(exist_ok=True)
    return {"run_dir": str(out), "spec": spec, "run": run}


def verify_run(run_dir, fixture=None):
    """检查执行目录是否泄漏答案，并核对执行方声明；返回问题清单（空=通过）。"""
    run_dir = Path(run_dir)
    issues = []
    spec_path = run_dir / "spec.json"
    run_path = run_dir / "run.json"
    if not spec_path.is_file():
        issues.append("缺 spec.json")
    if not run_path.is_file():
        issues.append("缺 run.json")
    if run_path.is_file():
        run = _read_json(run_path)
        if run.get("executor_saw_answers") is not False:
            issues.append("run.json.executor_saw_answers 必须为 false（看过答案的运行不得计入盲测）")
    if spec_path.is_file():
        spec_text = spec_path.read_text(encoding="utf-8", errors="ignore")
        for marker in ANSWER_MARKERS:
            if marker in spec_text:
                issues.append(f"spec.json 含答案字段标记：{marker}")
    for path in run_dir.rglob("*"):
        if not path.is_file():
            continue
        name = path.name.lower()
        if ".key." in name or name in {"hidden_answers.json", "answers.json"}:
            issues.append(f"运行目录含答案文件：{path.relative_to(run_dir)}")
    if fixture:
        key = _read_json(fixture)
        ids = [a.get("id") for a in key.get("hidden_answers") or []]
        ids += [n.get("id") for n in key.get("legit_near_misses") or []]
        for path in run_dir.rglob("*.json*"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            leaked = [i for i in ids if i and i in text]
            if leaked:
                issues.append(f"{path.relative_to(run_dir)} 出现答案 ID：{leaked}")
    return issues


def _normalize(text):
    return " ".join(_text(text).lower().split())


def _match(rule, text):
    """match_all 全含且 match_any（如有）命中任一即算匹配。"""
    haystack = _normalize(text)
    all_tokens = [_normalize(t) for t in rule.get("match_all") or []]
    any_tokens = [_normalize(t) for t in rule.get("match_any") or []]
    if not all_tokens and not any_tokens:
        return False
    if any(t not in haystack for t in all_tokens):
        return False
    if any_tokens and not any(t in haystack for t in any_tokens):
        return False
    return True


def _finding_text(finding):
    """拼接 finding 的可匹配文本。

    除 title/observed/branch 外，纳入结构化三要素 expected/actual/observed_via——
    执行方把「预期 vs 实际 vs 观察方式」写清时，语义词元才有机会命中；否则
    判据量的是用词重合而不是检出能力（见 docs/evals-baseline/ab-protocol.md）。
    """
    return " ".join(_normalize(finding.get(key)) for key in
                    ("title", "observed", "branch", "expected", "actual", "observed_via"))


def _parse_reproduction(finding):
    """可选扩展字段 reproduction {attempts, successes}；缺省返回 None，非法抛 ValueError。

    仅做形态校验与聚合，不参与 verdict 判定；缺失不得当 0。
    """
    rep = finding.get("reproduction")
    if rep is None:
        return None
    finding_id = finding.get("id")
    if not isinstance(rep, dict):
        raise ValueError(f"findings[{finding_id}].reproduction 必须为对象 {{attempts, successes}}")
    attempts, successes = rep.get("attempts"), rep.get("successes")
    for name, value in (("attempts", attempts), ("successes", successes)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"findings[{finding_id}].reproduction.{name} 必须为非负整数")
    if successes > attempts:
        raise ValueError(f"findings[{finding_id}].reproduction: successes 不得大于 attempts")
    return {"attempts": attempts, "successes": successes}


def _parse_cost(finding):
    """可选扩展字段 cost {probes, minutes}；缺省返回 None，非法抛 ValueError。"""
    cost = finding.get("cost")
    if cost is None:
        return None
    finding_id = finding.get("id")
    if not isinstance(cost, dict):
        raise ValueError(f"findings[{finding_id}].cost 必须为对象 {{probes, minutes}}")
    probes, minutes = cost.get("probes"), cost.get("minutes")
    if not isinstance(probes, int) or isinstance(probes, bool) or probes < 0:
        raise ValueError(f"findings[{finding_id}].cost.probes 必须为非负整数")
    if not isinstance(minutes, (int, float)) or isinstance(minutes, bool) or minutes < 0:
        raise ValueError(f"findings[{finding_id}].cost.minutes 必须为非负数值")
    return {"probes": probes, "minutes": minutes}


def _parse_unresolved_candidates(payload):
    """可选扩展字段 unresolved_candidates（数量或条目数组）；缺省返回 None。"""
    value = payload.get("unresolved_candidates")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, list)):
        raise ValueError("unresolved_candidates 必须为非负整数或条目数组")
    if isinstance(value, int):
        if value < 0:
            raise ValueError("unresolved_candidates 数量不得为负")
        return value
    return len(value)


def _evidence_ok(run_dir, finding):
    """校验证据可复核。evidence 接受字符串或数组两种形态。

    题面模板早期写作字符串，计分器只认数组，导致照题面执行的真实运行
    全部被判「没有证据路径」、命中答案也不计检出（系统性判低）。此处两种
    形态都接受，题面模板同步改为数组以消除歧义。
    """
    evidence = finding.get("evidence")
    if isinstance(evidence, str):
        evidence = [evidence.strip()] if evidence.strip() else []
    if not isinstance(evidence, list) or not evidence:
        return False, "没有证据路径"
    missing = []
    for item in evidence:
        path = Path(item)
        candidate = path if path.is_absolute() else Path(run_dir) / path
        if not candidate.is_file() or candidate.stat().st_size == 0:
            missing.append(str(item))
    if missing:
        return False, f"证据不存在或为空：{missing}"
    return True, ""


def _score_out_path(run_dir):
    """计分结果输出位置：run_dir 同级的 scores/<run 名>.json。

    不写进 run_dir：score.json 内嵌作业键的答案 ID，留在运行目录会同时触发
    「泄漏答案」误判与二次计分被拒。ab 子命令同时兼容旧位置。
    """
    run_dir = Path(run_dir)
    return run_dir.parent / "scores" / f"{run_dir.name}.json"


def score_run(run_dir, fixture_path, findings_path, allow_blocked=False):
    run_dir = Path(run_dir)
    key = _read_json(fixture_path)
    payload = _read_json(findings_path)
    problems = verify_run(run_dir, fixture_path)
    if problems:
        raise ValueError("运行目录未通过隔离检查：" + "；".join(problems))

    findings = payload.get("findings")
    if not isinstance(findings, list) or not findings:
        raise ValueError("findings.json.findings 必须为非空数组（未产出任何缺陷时写空缺陷说明，不能留空文件）")

    answers = list(key.get("hidden_answers") or [])
    near_misses = list(key.get("legit_near_misses") or [])
    required = [str(b) for b in key.get("required_branches") or []]

    matched_answers, near_hits, near_pairs, unmatched, evidence_issues = {}, [], [], [], []
    reproduction_parts, cost_parts = [], []
    for finding in findings:
        if not isinstance(finding, dict):
            raise ValueError("findings 每项必须为对象")
        for field in FINDING_FIELDS:
            if field not in finding:
                raise ValueError(f"findings 缺字段 {field}：{finding.get('id')}")
        if finding["severity"] not in SEVERITIES:
            raise ValueError(f"findings.severity 非法：{finding['severity']}")
        reproduction = _parse_reproduction(finding)
        cost = _parse_cost(finding)
        if reproduction:
            reproduction_parts.append(reproduction)
        if cost:
            cost_parts.append(cost)
        text = _finding_text(finding)
        hit = next((a for a in answers
                    if a["id"] not in matched_answers and _match(a, text)), None)
        if hit:
            # 证据等级：E2 模拟 / E3 未执行 不计检出（见 methodology/evidence_grading.md）
            grade = str(finding.get("evidence_grade") or "E1")
            ok, why = _evidence_ok(run_dir, finding)
            if grade != "E1":
                evidence_issues.append({"finding": finding["id"],
                                        "why": f"证据等级 {grade} 非真实执行，不计检出"})
            elif not ok:
                evidence_issues.append({"finding": finding["id"], "why": why})
            else:
                matched_answers[hit["id"]] = finding
        elif any(_match(n, text) for n in near_misses):
            near_hits.append(finding["id"])
            category = next((n.get("category") for n in near_misses if _match(n, text)), "")
            near_pairs.append({"finding": finding["id"], "category": category or "uncategorized"})
        else:
            unmatched.append(finding["id"])

    branches = payload.get("branches")
    if not isinstance(branches, list):
        raise ValueError("findings.json.branches 必须为数组（交代每条必选分支的执行状态）")
    declared = [str(b.get("name") or "") for b in branches if isinstance(b, dict)]
    alien = [name for name in declared if name not in required]
    if alien:
        raise ValueError(f"branches 出现题面之外的分支名（疑似改分母）：{alien}")
    executed = [b for b in branches if isinstance(b, dict) and b.get("status") == "EXECUTED"]
    blocked_ok = [b for b in branches if isinstance(b, dict) and b.get("status") == "BLOCKED"
                  and _text(b.get("reason")).strip()]
    blocked_bad = [b for b in branches if isinstance(b, dict) and b.get("status") == "BLOCKED"
                   and not _text(b.get("reason")).strip()]
    missing_branches = [name for name in required if name not in declared]
    branch_rate = len(executed) / len(required) if required else 0.0

    by_severity = {}
    for severity in SEVERITIES:
        total = [a["id"] for a in answers if a.get("severity") == severity]
        got = [i for i in total if i in matched_answers]
        by_severity[severity] = {"detected": len(got), "total": len(total), "missed": sorted(set(total) - set(got))}

    fp_by_category = {}
    for pair in near_pairs:
        fp_by_category.setdefault(pair["category"], []).append(pair["finding"])

    untested = payload.get("obligations_untested")
    if untested is None:
        untested = []
    if not isinstance(untested, list):
        raise ValueError("obligations_untested 必须为数组")
    invalid_actions = payload.get("invalid_actions", 0)
    if not isinstance(invalid_actions, int) or invalid_actions < 0:
        raise ValueError("invalid_actions 必须为非负整数")

    # 对抗增量聚合（全部可选）：缺省输出 null，缺失不当 0，不参与 verdict 判定
    reproduction = None
    if reproduction_parts:
        attempts = sum(r["attempts"] for r in reproduction_parts)
        successes = sum(r["successes"] for r in reproduction_parts)
        reproduction = {"attempts": attempts, "successes": successes,
                        "rate": round(successes / attempts, 4) if attempts else "NA"}
    cost = None
    if cost_parts:
        cost = {"probes": sum(c["probes"] for c in cost_parts),
                "minutes": sum(c["minutes"] for c in cost_parts)}
    unresolved_candidates = _parse_unresolved_candidates(payload)

    reasons = []
    if by_severity["blocker"]["detected"] != by_severity["blocker"]["total"]:
        reasons.append(f"blocker 未全检出：缺 {by_severity['blocker']['missed']}")
    if by_severity["major"]["detected"] != by_severity["major"]["total"]:
        reasons.append(f"major 未全检出：缺 {by_severity['major']['missed']}")
    if near_hits:
        reasons.append(f"合法近似反例被记成缺陷（误报）：{near_hits}")
    if branch_rate < 1.0:
        reasons.append(f"必选分支执行率 {len(executed)}/{len(required)}，缺 {missing_branches or '未执行项'}")
    if blocked_bad:
        reasons.append(f"BLOCKED 未写原因：{[b.get('name') for b in blocked_bad]}")
    if evidence_issues:
        reasons.append(f"检出缺可复核证据：{evidence_issues}")
    if untested:
        reasons.append(f"仍有未测义务：{untested}")
    if invalid_actions:
        reasons.append(f"无效动作 {invalid_actions} 次")

    verdict = "pass" if not reasons else "fail"
    if verdict == "fail" and allow_blocked and blocked_ok and not (set(reasons) - {
            f"必选分支执行率 {len(executed)}/{len(required)}，缺 {missing_branches or '未执行项'}"}):
        verdict = "conditional"

    # normal 漏检不改变 verdict（协议：blocker/major 全检出即 pass），但必须披露——
    # 否则"全检出"与"只漏轻微"在报告上完全一样，会把部分覆盖误读成完美通过。
    warnings = []
    if by_severity["normal"]["missed"]:
        warnings.append(
            f"normal 级未全检出：缺 {by_severity['normal']['missed']}"
            f"（不影响 verdict，但按「减少漏测」目标应记为覆盖缺口）")
    if unmatched:
        # 不直接判「臆造」：词元未命中可能只是措辞差异，也可能是真缺陷落在
        # 答案键之外。定性属人工复核（finding-triage），工具只报事实清单。
        warnings.append(f"未命中任何答案键，待人工复核（措辞差异/答案键缺失/无效发现，三选一须留痕）：{unmatched}")
        # 仅在确有未命中时提示结构化要素缺失：全检出时不该再多出一条噪音
        unstructured = [f.get("id") for f in findings
                        if not (f.get("expected") and f.get("actual") and f.get("observed_via"))]
        if unstructured:
            warnings.append(
                f"未命中的发现里有未写结构化三要素（expected/actual/observed_via）的：{unstructured}——"
                "建议补齐后复核，匹配召回依赖可读的预期/实际描述")
    result = {
        "run_id": _read_json(run_dir / "run.json").get("run_id") if (run_dir / "run.json").is_file() else run_dir.name,
        "scenario": key.get("scenario", ""),
        "holdout": bool(key.get("holdout")),
        "scored_at": _now(),
        "protocol_sha256": protocol_hash(),
        "fixture_sha256": sha256_path(fixture_path),
        "executor": payload.get("executor") or {},
        "budget_minutes": (payload.get("budget") or {}).get("minutes"),
        "detection": by_severity,
        "detected_total": sum(v["detected"] for v in by_severity.values()),
        "answer_total": len(answers),
        "near_miss_hits": near_hits,
        "false_positive_categories": fp_by_category,
        "unmatched_findings": unmatched,
        "branch_executed": len(executed),
        "branch_required": len(required),
        "branch_rate": round(branch_rate, 4),
        "blocked_justified": [b.get("name") for b in blocked_ok],
        "missing_branches": missing_branches,
        "evidence_issues": evidence_issues,
        "obligations_untested": untested,
        "invalid_actions": invalid_actions,
        "reproduction": reproduction,
        "cost": cost,
        "unresolved_candidates": unresolved_candidates,
        "verdict": verdict,
        "reasons": reasons,
        "warnings": warnings,
        "allow_blocked": bool(allow_blocked),
    }
    # 计分结果写在 run_dir 之外：score.json 含答案 ID，若留在运行目录，
    # 既会被 verify_run 判成「泄漏答案」，又会破坏二次计分的幂等性。
    out_path = _score_out_path(run_dir)
    _write_json(out_path, result)
    result["score_path"] = str(out_path)
    return result


def compare_scores(path_a, path_b):
    a, b = _read_json(path_a), _read_json(path_b)
    warnings = []
    if a.get("fixture_sha256") != b.get("fixture_sha256"):
        warnings.append("两份结果使用的夹具不同（fixture_sha256 不一致），不能直接对比")
    if a.get("protocol_sha256") != b.get("protocol_sha256"):
        warnings.append("协议哈希不同：判据已变，旧结果不可与新结果混算")
    name_a = (a.get("executor") or {}).get("name")
    name_b = (b.get("executor") or {}).get("name")
    if name_a and name_a == name_b:
        warnings.append("两次运行同一执行方：存在学习效应，需独立执行方复跑或交错顺序")
    delta = {
        "detected_total": (b.get("detected_total") or 0) - (a.get("detected_total") or 0),
        "answer_total": (b.get("answer_total") or 0) - (a.get("answer_total") or 0),
        "branch_rate": round((b.get("branch_rate") or 0) - (a.get("branch_rate") or 0), 4),
        "near_miss_hits": len(b.get("near_miss_hits") or []) - len(a.get("near_miss_hits") or []),
        "obligations_untested": len(b.get("obligations_untested") or []) - len(a.get("obligations_untested") or []),
    }
    return {
        "a": {"run_id": a.get("run_id"), "verdict": a.get("verdict"), "detected": a.get("detected_total")},
        "b": {"run_id": b.get("run_id"), "verdict": b.get("verdict"), "detected": b.get("detected_total")},
        "delta": delta,
        "warnings": warnings,
        "comparable": not any("不能直接对比" in w or "不可与" in w for w in warnings),
    }


def _aggregate_arm(scores):
    """聚合一臂（同臂多个 run 的 score 列表）为可对比的计数字段。

    只做计数汇总，不产生"质量达标"类结论；复现/成本缺省时输出 None（缺失不当 0）。
    """
    detected = {}
    for severity in SEVERITIES:
        detected[severity] = sum((s.get("detection") or {}).get(severity, {}).get("detected", 0)
                                 for s in scores)
    answer_total = sum(s.get("answer_total") or 0 for s in scores)
    false_positives = sum(len(s.get("near_miss_hits") or []) for s in scores)
    fp_categories = {}
    for s in scores:
        for category, findings in (s.get("false_positive_categories") or {}).items():
            fp_categories[category] = fp_categories.get(category, 0) + len(findings or [])
    repro_parts = [s.get("reproduction") for s in scores if s.get("reproduction")]
    reproduction = None
    if repro_parts:
        attempts = sum(r.get("attempts", 0) for r in repro_parts)
        successes = sum(r.get("successes", 0) for r in repro_parts)
        reproduction = {"attempts": attempts, "successes": successes,
                        "rate": round(successes / attempts, 4) if attempts else "NA"}
    cost_parts = [s.get("cost") for s in scores if s.get("cost")]
    cost = None
    if cost_parts:
        cost = {"probes": sum(c.get("probes", 0) or 0 for c in cost_parts),
                "minutes": sum(c.get("minutes", 0) or 0 for c in cost_parts)}
    return {
        "runs": len(scores),
        "run_ids": [s.get("run_id") for s in scores],
        "verdicts": sorted(str(s.get("verdict") or "") for s in scores),
        "detected": detected,
        "detected_total": sum(detected.values()),
        "answer_total": answer_total,
        "false_positives": false_positives,
        "false_positive_categories": fp_categories,
        "unmatched_findings": sum(len(s.get("unmatched_findings") or []) for s in scores),
        "obligations_untested": sum(len(s.get("obligations_untested") or []) for s in scores),
        "invalid_actions": sum(s.get("invalid_actions") or 0 for s in scores),
        "reproduction": reproduction,
        "cost": cost,
        "unresolved_candidates": sum(s.get("unresolved_candidates") or 0
                                     for s in scores if isinstance(s.get("unresolved_candidates"), int)),
        "fixture_sha256": sorted({s.get("fixture_sha256") or "" for s in scores}),
        "protocol_sha256": sorted({s.get("protocol_sha256") or "" for s in scores}),
        "executors": sorted({(s.get("executor") or {}).get("name") or ""
                             for s in scores}),
    }


def _derived_metrics(arm):
    """派生指标（近似口径）：裁定精确率、每百探针有效发现、复现率。

    分母不足（探针/样本过少）时输出 NA，不编造比例；解读纪律见
    docs/evals-baseline/ab-protocol.md。
    """
    precision = "NA"
    denominator = arm["detected_total"] + arm["false_positives"]
    # 小样本不给比例：分母 < 10 时输出 NA 并附分母，避免「精确率 1.0」被误读
    if denominator >= 10:
        precision = round(arm["detected_total"] / denominator, 4)
    per_100_probes = "NA"
    if arm.get("cost") and (arm["cost"].get("probes") or 0) > 0:
        per_100_probes = round(arm["detected_total"] / arm["cost"]["probes"] * 100, 2)
    repro_rate = (arm.get("reproduction") or {}).get("rate") if arm.get("reproduction") else "NA"
    return {"precision_proxy": precision, "precision_proxy_denominator": denominator,
            "effective_findings_per_100_probes": per_100_probes,
            "reproduction_rate": repro_rate}


def compare_ab(arm_a_dir, arm_b_dir):
    """审核四 A2：对抗改造前后 A/B 两臂聚合对照。

    两臂各为一个目录，递归收集其中全部 score.json（同臂多 run）。可比性检查：
    协议/夹具哈希须同臂一致且跨臂一致；执行方跨臂须不同（同执行方存在学习效应）。
    本函数只产事实与派生指标，不产"改造有效/无效"结论。
    """
    def _collect(arm_dir):
        arm_dir = Path(arm_dir)
        if not arm_dir.is_dir():
            raise ValueError(f"臂目录不存在：{arm_dir}")
        found = sorted(set(arm_dir.glob("**/score.json")) | set(arm_dir.glob("**/scores/*.json")))
        if not found:
            raise ValueError(f"臂目录内未找到任何 score.json（须先对各 run 执行 score 子命令）：{arm_dir}")
        return [_read_json(p) for p in found]

    scores_a = _collect(arm_a_dir)
    scores_b = _collect(arm_b_dir)
    arm_a, arm_b = _aggregate_arm(scores_a), _aggregate_arm(scores_b)
    warnings = []
    for label, arm in (("A", arm_a), ("B", arm_b)):
        if len(arm["protocol_sha256"]) > 1:
            warnings.append(f"臂 {label} 内协议哈希不一致：判据在臂内发生变更，混算无效")
        if len(arm["fixture_sha256"]) > 1:
            warnings.append(f"臂 {label} 内夹具哈希不一致：同臂内使用了不同夹具，聚合失真")
    if arm_a["protocol_sha256"] != arm_b["protocol_sha256"]:
        warnings.append("两臂协议哈希不同：判据已变，A/B 不可直接对比")
    if arm_a["fixture_sha256"] != arm_b["fixture_sha256"]:
        warnings.append("两臂夹具不同：A/B 不可直接对比")
    if arm_a["answer_total"] != arm_b["answer_total"]:
        warnings.append(f"两臂答案分母不同（A={arm_a['answer_total']}, B={arm_b['answer_total']}）："
                        "检出计数不可直接相减")
    overlap = set(arm_a["executors"]) & set(arm_b["executors"]) - {""}
    if overlap:
        warnings.append(f"两臂存在同一执行方：{sorted(overlap)}——存在学习效应，须独立执行方或交错顺序")

    delta = {
        "detected_total": arm_b["detected_total"] - arm_a["detected_total"],
        "false_positives": arm_b["false_positives"] - arm_a["false_positives"],
        "unresolved_candidates": arm_b["unresolved_candidates"] - arm_a["unresolved_candidates"],
        "obligations_untested": arm_b["obligations_untested"] - arm_a["obligations_untested"],
        "by_severity": {sev: arm_b["detected"][sev] - arm_a["detected"][sev]
                        for sev in SEVERITIES},
    }
    derived = {"A": _derived_metrics(arm_a), "B": _derived_metrics(arm_b)}
    return {
        "arm_a": arm_a,
        "arm_b": arm_b,
        "delta": delta,
        "derived": derived,
        "warnings": warnings,
        "comparable": not any("不可直接" in w or "混算无效" in w or "聚合失真" in w
                              for w in warnings),
        "scope": ("A/B 聚合对照只产事实与派生指标；判定改造是否有效须结合 "
                  "docs/evals-baseline/ab-protocol.md 的样本与解读纪律，"
                  "不得由本输出自动推断"),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="独立盲测：准备/隔离检查/计分/对照")
    sub = parser.add_subparsers(dest="command", required=True)

    p_prepare = sub.add_parser("prepare", help="生成只含题面的运行目录")
    p_prepare.add_argument("--fixture", required=True)
    p_prepare.add_argument("--out", required=True)
    p_prepare.add_argument("--run-id")
    p_prepare.add_argument("--budget-min", type=int, default=30)

    p_verify = sub.add_parser("verify", help="检查运行目录是否泄漏答案")
    p_verify.add_argument("--run", required=True)
    p_verify.add_argument("--fixture")

    p_score = sub.add_parser("score", help="按答案键计分")
    p_score.add_argument("--run", required=True)
    p_score.add_argument("--fixture", required=True)
    p_score.add_argument("--findings", required=True)
    p_score.add_argument("--allow-blocked", action="store_true")

    p_cmp = sub.add_parser("compare", help="两份结果对照")
    p_cmp.add_argument("--a", required=True)
    p_cmp.add_argument("--b", required=True)

    p_ab = sub.add_parser("ab", help="A/B 两臂聚合对照（各为一目录，含多 run 的 score.json）")
    p_ab.add_argument("--arm-a", required=True, help="改造前（基线）臂目录")
    p_ab.add_argument("--arm-b", required=True, help="改造后（对抗）臂目录")

    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            print(json.dumps(prepare(args.fixture, args.out, args.run_id, args.budget_min),
                             ensure_ascii=False, indent=2))
        elif args.command == "verify":
            issues = verify_run(args.run, args.fixture)
            print(json.dumps({"run": args.run, "issues": issues, "ok": not issues}, ensure_ascii=False, indent=2))
            return 0 if not issues else 1
        elif args.command == "score":
            print(json.dumps(score_run(args.run, args.fixture, args.findings, args.allow_blocked),
                             ensure_ascii=False, indent=2))
        elif args.command == "ab":
            print(json.dumps(compare_ab(args.arm_a, args.arm_b), ensure_ascii=False, indent=2))
        else:
            print(json.dumps(compare_scores(args.a, args.b), ensure_ascii=False, indent=2))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"盲测工具错误：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
