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


def verify_run(run_dir, fixture=None, redact_ids=False):
    """检查执行目录是否泄漏答案，并核对执行方声明；返回问题清单（空=通过）。

    `redact_ids=True` 时，泄漏报告只给**命中数量与文件位置**，不写答案 ID 本身。
    原因：这份清单要进归档，而常规去敏归档不得含答案 ID——把 ID 写进问题描述
    再脱敏，等于让脱敏只做了一半（正文干净、诊断语里还漏着）。
    """
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
                if redact_ids:
                    issues.append(f"{path.relative_to(run_dir)} 出现 {len(leaked)} 个答案 ID"
                                  "（ID 本身已脱敏）")
                else:
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


ADJUDICATION_DECISIONS = ("valid", "false_positive", "insufficient_evidence")
MATRIX_SCHEMA = "ab-run-matrix/1"


def adjudicate_findings(score_path, adjudication_path):
    """独立裁定：把评测者对「机械未匹配发现」的判断与机械计分并列留痕。

    存在目的：机械评分只认词元命中，未命中可能是措辞差异、也可能是答案键漏了、
    也可能是无效发现——三选一属人工判断，工具不猜。未匹配的发现若无人裁定，
    整体误报率就没有分母，汇总也就无从下手。

    纪律：
    1. 裁定必须**逐条覆盖**该次计分的全部未匹配发现：漏一条即拒（否则裁定不可复算）；
    2. 裁定只针对未匹配发现，命中答案键的发现不得被裁定为无效；
    3. 每条裁定必须有理由，`root_cause` 用于把同一根因的重复发现归并；
    4. 裁定结果写在 score.json 同级的 adjudication.json，与计分分离，不改计分本身。
    """
    score = _read_json(score_path)
    unmatched = score.get("unmatched_findings") or []
    if not isinstance(unmatched, list):
        raise ValueError("score.json 的 unmatched_findings 必须是数组")
    payload = _read_json(adjudication_path)
    items = payload.get("adjudications")
    if not isinstance(items, list):
        raise ValueError("裁定文件必须含 adjudications 数组")
    unmatched_ids = [str(item.get("id")) for item in unmatched
                     if isinstance(item, dict) and item.get("id") is not None]
    if len(unmatched_ids) != len(unmatched):
        raise ValueError("score.json 的 unmatched_findings 结构异常：部分条目缺 id")
    seen = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("adjudications 每项必须为对象")
        finding_id = str(item.get("finding_id") or "")
        if finding_id not in unmatched_ids:
            raise ValueError(f"裁定对象 {finding_id!r} 不在本次未匹配发现列表内："
                             "机械已命中的发现不得被裁定为无效")
        if finding_id in seen:
            raise ValueError(f"发现 {finding_id} 被重复裁定")
        decision = str(item.get("decision") or "")
        if decision not in ADJUDICATION_DECISIONS:
            raise ValueError(f"裁定结论必须为 {'/'.join(ADJUDICATION_DECISIONS)} 之一：got {decision!r}")
        if not str(item.get("reason") or "").strip():
            raise ValueError(f"发现 {finding_id} 的裁定缺理由")
        seen[finding_id] = {"finding_id": finding_id, "decision": decision,
                            "reason": str(item["reason"]).strip(),
                            "root_cause": str(item.get("root_cause") or finding_id)}
    missing = [fid for fid in unmatched_ids if fid not in seen]
    if missing:
        raise ValueError(f"以下未匹配发现未裁定：{missing}——裁定须逐条覆盖，否则分母不可复算")
    counts = {decision: 0 for decision in ADJUDICATION_DECISIONS}
    for entry in seen.values():
        counts[entry["decision"]] += 1
    # 同一根因的重复发现归并：根因去重后的误报数才是误报分子
    fp_roots = {entry["root_cause"] for entry in seen.values()
                if entry["decision"] == "false_positive"}
    valid_roots = {entry["root_cause"] for entry in seen.values()
                   if entry["decision"] == "valid"}
    result = {
        "schema": "blind-adjudication/1",
        "scored_run_id": score.get("run_id"),
        "protocol_sha256": score.get("protocol_sha256"),
        "fixture_sha256": score.get("fixture_sha256"),
        "adjudicated_at": _now(),
        "adjudications": [seen[fid] for fid in unmatched_ids],
        "counts": counts,
        "false_positive_roots": sorted(fp_roots),
        "valid_roots": sorted(valid_roots),
        "false_positives_deduped": len(fp_roots),
        "valid_deduped": len(valid_roots),
    }
    out_path = Path(score_path).with_name("adjudication.json")
    _write_json(out_path, result)
    result["adjudication_path"] = str(out_path)
    return result


def _load_matrix(path):
    """读取冻结的运行矩阵。矩阵是本轮实验的「预注册」：出分后不得改。"""
    matrix = _read_json(path)
    if matrix.get("schema") != MATRIX_SCHEMA:
        raise ValueError(f"运行矩阵 schema 必须为 {MATRIX_SCHEMA}")
    fixtures = matrix.get("fixtures")
    arms = matrix.get("arms")
    repeats = matrix.get("repeats")
    runs = matrix.get("runs")
    if not isinstance(fixtures, list) or not fixtures:
        raise ValueError("运行矩阵缺 fixtures")
    if not isinstance(arms, dict) or len(arms) < 2:
        raise ValueError("运行矩阵至少需要两个臂（A/B）")
    if not isinstance(repeats, int) or repeats < 1:
        raise ValueError("运行矩阵缺 repeats")
    if not isinstance(runs, list) or not runs:
        raise ValueError("运行矩阵缺 runs")
    for run in runs:
        for field in ("fixture", "arm", "repeat", "run_id"):
            if not str(run.get(field) or "").strip():
                raise ValueError(f"运行矩阵的 runs 条目缺字段 {field}：{run}")
        if run["fixture"] not in fixtures:
            raise ValueError(f"运行矩阵出现未登记的夹具：{run['fixture']}")
        if run["arm"] not in arms:
            raise ValueError(f"运行矩阵出现未登记的臂：{run['arm']}")
        if not isinstance(run.get("repeat"), int) or run["repeat"] < 1:
            raise ValueError(f"运行矩阵的 repeat 须为正整数：{run}")
    return matrix


def _answers_by_fixture(fixtures_dir):
    """读夹具答案键（评测者侧资产）：id → severity。汇总明细需要它还原命中集合。"""
    mapping = {}
    for path in sorted(Path(fixtures_dir).glob("*.json")):
        fixture = _read_json(path)
        answers = {}
        for answer in fixture.get("hidden_answers") or []:
            if isinstance(answer, dict) and answer.get("id"):
                answers[str(answer["id"])] = str(answer.get("severity") or "normal")
        mapping[path.stem] = {"answers": answers,
                              "near_misses": len(fixture.get("legit_near_misses") or []),
                              "sha256": sha256_path(path)}
    return mapping


def _hit_set(score, answers):
    """从 score.json 还原该 run 命中的答案键集合。

    score.json 只存了各严重度的 missed 列表与总数，未存全部答案 id，
    因此命中集合 = 答案键 − 未命中。答案键不在结果文件里（评测者侧资产），
    故必须由汇总方提供夹具目录。
    """
    missed = set()
    for entry in (score.get("detection") or {}).values():
        for answer_id in (entry or {}).get("missed") or []:
            missed.add(str(answer_id))
    return {answer_id for answer_id in answers if answer_id not in missed}


def _bm_count(score):
    """blocker/major 检出数（主指标的计数口径）。"""
    detection = score.get("detection") or {}
    return sum((detection.get(sev) or {}).get("detected", 0) for sev in ("blocker", "major"))


def _collect_findings_count(score):
    """提交给评测者的发现数（机械三类的和）：命中答案 + 误报近似 + 未匹配。

    这是整体误报率的分母。机械部分无法从 score.json 还原根因去重，
    人工裁定部分按 root_cause 归并——两段口径的差异在汇总里如实标注。
    """
    return (int(score.get("detected_total") or 0)
            + len(score.get("near_miss_hits") or [])
            + len(score.get("unmatched_findings") or []))


def _fp_numerator(score, adjudication):
    """误报分子：机械认定的误报（合法近似反例）+ 人工裁定的误报（按根因去重）。"""
    return len(score.get("near_miss_hits") or []) + int(
        (adjudication or {}).get("false_positives_deduped") or 0)


def _load_adjudication(score_path):
    path = Path(score_path).with_name("adjudication.json")
    if not path.is_file():
        return None
    return _read_json(path)


def pair_fixture(matrix, fixture, results_root, answers_by_fixture, arm_a=None, arm_b=None):
    """按夹具配对评分：同一夹具下按重复编号把 A 臂与 B 臂的 run 配成对。

    配对键是（夹具，重复编号），不是臂内聚合——方案要求「按夹具和重复编号配对，
    同夹具两臂使用等价环境、相同授权、版本及重置方式」。臂内聚合会把不同环境的
    运行混在一起，配对差值就不再是「同一条件下换个做法」的差值。

    返回：配对明细 + 同夹具均值 + 缺陷级明细（新增命中/共同命中/A 有 B 无/皆漏）。
    """
    arms = list(matrix["arms"].keys())
    arm_a = arm_a or arms[0]
    arm_b = arm_b or arms[1]
    results_root = Path(results_root)
    registered = [r for r in matrix["runs"] if r["fixture"] == fixture]
    if not registered:
        raise ValueError(f"运行矩阵未登记夹具 {fixture}")
    fixture_info = answers_by_fixture.get(fixture)
    if fixture_info is None:
        raise ValueError(f"夹具目录中找不到 {fixture}")
    answers = fixture_info["answers"]

    def _score_of(run):
        path = results_root / fixture / run["arm"] / str(run["repeat"]) / "score.json"
        if not path.is_file():
            return None, path
        return _read_json(path), path

    pairs, invalid = [], []
    repeats = sorted({r["repeat"] for r in registered})
    for repeat in repeats:
        run_a = next((r for r in registered if r["arm"] == arm_a and r["repeat"] == repeat), None)
        run_b = next((r for r in registered if r["arm"] == arm_b and r["repeat"] == repeat), None)
        if not run_a or not run_b:
            invalid.append({"repeat": repeat,
                            "reason": "矩阵中该重复编号缺少配对的一臂 run"})
            continue
        score_a, path_a = _score_of(run_a)
        score_b, path_b = _score_of(run_b)
        if score_a is None or score_b is None:
            missing = [str(p) for p, s in ((path_a, score_a), (path_b, score_b)) if s is None]
            invalid.append({"repeat": repeat, "reason": "缺少计分结果：" + "、".join(missing)})
            continue
        reasons = []
        if score_a.get("fixture_sha256") != score_b.get("fixture_sha256"):
            reasons.append("两臂夹具指纹不同：不是同一夹具，配对无效")
        if score_a.get("protocol_sha256") != score_b.get("protocol_sha256"):
            reasons.append("两臂协议哈希不同：判据已变，配对无效")
        if score_a.get("budget_minutes") != score_b.get("budget_minutes"):
            reasons.append(f"两臂预算不一致（A={score_a.get('budget_minutes')}, "
                           f"B={score_b.get('budget_minutes')}）：配对无效")
        executor_a = (score_a.get("executor") or {}).get("name")
        executor_b = (score_b.get("executor") or {}).get("name")
        if executor_a and executor_a == executor_b:
            reasons.append(f"两臂同一执行方（{executor_a}）：存在学习效应，配对独立性不成立")
        adj_a = _load_adjudication(path_a)
        adj_b = _load_adjudication(path_b)
        pairs.append({
            "repeat": repeat,
            "run_a": run_a["run_id"], "run_b": run_b["run_id"],
            "executor_a": executor_a, "executor_b": executor_b,
            "bm_a": _bm_count(score_a), "bm_b": _bm_count(score_b),
            "delta_bm": _bm_count(score_b) - _bm_count(score_a),
            "fp_a": _fp_numerator(score_a, adj_a), "fp_b": _fp_numerator(score_b, adj_b),
            "delta_fp": _fp_numerator(score_b, adj_b) - _fp_numerator(score_a, adj_a),
            "findings_a": _collect_findings_count(score_a),
            "findings_b": _collect_findings_count(score_b),
            "hits_a": sorted(_hit_set(score_a, answers)),
            "hits_b": sorted(_hit_set(score_b, answers)),
            "adjudicated_a": bool(adj_a), "adjudicated_b": bool(adj_b),
            "valid": not reasons, "invalid_reasons": reasons,
        })
    valid_pairs = [p for p in pairs if p["valid"]]
    hit_a = set()
    hit_b = set()
    for pair in valid_pairs:
        hit_a |= set(pair["hits_a"])
        hit_b |= set(pair["hits_b"])
    all_ids = set(answers)
    detail = {
        "newly_detected": [{"id": i, "severity": answers.get(i, "normal")}
                           for i in sorted(hit_b - hit_a)],
        "common": [{"id": i, "severity": answers.get(i, "normal")}
                   for i in sorted(hit_a & hit_b)],
        "only_a": [{"id": i, "severity": answers.get(i, "normal")}
                   for i in sorted(hit_a - hit_b)],
        "missed_both": [{"id": i, "severity": answers.get(i, "normal")}
                        for i in sorted(all_ids - hit_a - hit_b)],
    }
    # 新增 blocker 漏检：A 命中、B 未命中的 blocker。冻结阈值里的
    # 「B 无新增已知 blocker 漏检」要拿这个字段判，不是拿总漏检数判。
    newly_missed_blocker = sorted(
        i for i in (hit_a - hit_b) if answers.get(i) == "blocker")
    mean_delta = (sum(p["delta_bm"] for p in valid_pairs) / len(valid_pairs)
                  if valid_pairs else None)
    mean_fp = (sum(p["delta_fp"] for p in valid_pairs) / len(valid_pairs)
               if valid_pairs else None)
    # 有效配对不足 → 标为证据不足：夹具不计入汇总分母。
    # 部分配对无效时，若仍按剩下的一半算均值，那个均值描述的是"另一半运行"，
    # 不是这个夹具——把它当夹具级结论报出去就是在拿残缺数据充数。
    sufficient = bool(valid_pairs) and len(valid_pairs) == len(repeats) and not invalid
    return {
        "fixture": fixture,
        "fixture_sha256": fixture_info["sha256"],
        "answer_total": len(answers),
        "near_miss_total": fixture_info["near_misses"],
        "pairs": pairs,
        "valid_pairs": len(valid_pairs),
        "planned_pairs": len(repeats),
        "invalid_pairs": invalid,
        "mean_delta_bm": mean_delta,
        "mean_delta_fp": mean_fp,
        "detail": detail,
        "newly_missed_blocker": newly_missed_blocker,
        "valid": sufficient,
        "evidence_sufficient": sufficient,
        "insufficient_reason": ("" if sufficient else
                                f"有效配对 {len(valid_pairs)}/{len(repeats)}：证据不足，不计入汇总分母"),
    }


def summarize_matrix(matrix_path, results_root, fixtures_dir=None):
    """汇总配对差值：先对同夹具重复取均值，再将各夹具等权汇总。

    主指标 = 配对运行中满足证据要求并命中冻结答案键的 blocker/major 缺陷数差值（B−A），
    先对同夹具重复取均值，再将五个夹具等权汇总。

    输出只产事实与派生指标；「改造是否有效」由人按 ab-protocol.md 解读。
    """
    matrix = _load_matrix(matrix_path)
    fixtures_dir = Path(fixtures_dir or (ROOT / "docs" / "evals-baseline" / "detection-fixtures"))
    answers_by_fixture = _answers_by_fixture(fixtures_dir)
    per_fixture = []
    for fixture in matrix["fixtures"]:
        try:
            per_fixture.append(pair_fixture(matrix, fixture, results_root, answers_by_fixture))
        except ValueError as exc:
            per_fixture.append({"fixture": fixture, "valid": False, "pairs": [],
                                "valid_pairs": 0, "invalid_pairs": [{"reason": str(exc)}],
                                "mean_delta_bm": None, "mean_delta_fp": None,
                                "insufficient_reason": str(exc),
                                "detail": {"newly_detected": [], "common": [],
                                           "only_a": [], "missed_both": []}})
    valid_fixtures = [f for f in per_fixture
                      if f.get("valid") and f.get("mean_delta_bm") is not None]
    primary = (sum(f["mean_delta_bm"] for f in valid_fixtures) / len(valid_fixtures)
               if valid_fixtures else None)
    fp_deltas = [f["mean_delta_fp"] for f in valid_fixtures if f.get("mean_delta_fp") is not None]
    mean_fp_delta = (sum(fp_deltas) / len(fp_deltas)) if fp_deltas else None

    # 已知反例误报率：被错报的已知反例数 / 冻结的可评估反例数
    near_miss_hits = 0
    near_miss_total = 0
    for fixture in per_fixture:
        near_miss_total += int(fixture.get("near_miss_total") or 0)
    # 整体误报率：独立裁定的误报数 / 已裁定的去重提交发现数
    fp_total = 0
    findings_total = 0
    insufficient = 0
    for fixture in per_fixture:
        for pair in fixture.get("pairs") or []:
            fp_total += pair["fp_a"] + pair["fp_b"]
            findings_total += pair["findings_a"] + pair["findings_b"]
            insufficient += pair.get("insufficient_a", 0) + pair.get("insufficient_b", 0)

    warnings = []
    invalid_pairs = []
    newly_missed_blocker = []
    for fixture in per_fixture:
        for pair in fixture.get("pairs") or []:
            if not pair.get("valid", True):
                invalid_pairs.append({"fixture": fixture["fixture"], "repeat": pair.get("repeat"),
                                      "reasons": pair.get("invalid_reasons")})
        for item in fixture.get("invalid_pairs") or []:
            invalid_pairs.append({"fixture": fixture["fixture"], "repeat": item.get("repeat"),
                                  "reasons": [item.get("reason")]})
        newly_missed_blocker.extend(
            {"fixture": fixture["fixture"], "id": answer_id}
            for answer_id in (fixture.get("newly_missed_blocker") or []))
    if invalid_pairs:
        warnings.append(f"存在 {len(invalid_pairs)} 个无效配对：其数据不计入汇总，"
                       "无效原因逐条列在 invalid_pairs")
    if len(valid_fixtures) < len(matrix["fixtures"]):
        warnings.append(f"参与汇总的有效夹具数 {len(valid_fixtures)} < 计划 "
                       f"{len(matrix['fixtures'])}：结论只覆盖已汇总部分，不得外推")
    if not valid_fixtures:
        warnings.append("无有效配对：不得给出任何效果结论")

    # 冻结阈值对照：只给逐项事实，不替人下「改造有效/无效」的结论。
    thresholds = matrix.get("thresholds") or {}
    threshold_check = [
        {"name": "primary",
         "requirement": thresholds.get("primary"),
         "observed": primary,
         "observed_text": ("主指标为 None（无有效配对）" if primary is None
                           else f"主指标 = {primary}"),
         "met": None if primary is None else primary > 0},
        {"name": "no_new_blocker_miss",
         "requirement": thresholds.get("no_new_blocker_miss"),
         "observed": newly_missed_blocker,
         "observed_text": (f"B 新增漏检的 blocker：{newly_missed_blocker}"
                           if newly_missed_blocker else "无新增 blocker 漏检"),
         "met": None if not valid_fixtures else not newly_missed_blocker},
        {"name": "false_positive",
         "requirement": thresholds.get("false_positive"),
         "observed": mean_fp_delta,
         "observed_text": ("误报差值为 None（无有效配对）" if mean_fp_delta is None
                           else f"误报差值 = {mean_fp_delta}"),
         "met": None if mean_fp_delta is None else mean_fp_delta <= 0},
    ]
    if all(item["met"] is None for item in threshold_check):
        warnings.append("冻结阈值无法对照：没有有效配对，全部阈值项为 NA")
    return {
        "schema": "ab-matrix-summary/1",
        "matrix_id": matrix.get("matrix_id"),
        "frozen_at": matrix.get("frozen_at"),
        "thresholds": matrix.get("thresholds") or {},
        "per_fixture": per_fixture,
        "primary_metric": {
            "name": "blocker/major 检出数差值（B−A）",
            "value": primary,
            "unit": "个/夹具（同夹具重复取均值后，夹具间等权平均）",
            "denominator": len(valid_fixtures),
            "denominator_note": f"参与汇总的有效夹具数（计划 {len(matrix['fixtures'])}）",
        },
        "false_positive": {
            "mean_delta": mean_fp_delta,
            "false_positives": fp_total,
            "submitted_findings": findings_total,
            "overall_rate": (round(fp_total / findings_total, 4) if findings_total else "NA"),
            "known_near_miss_rate": "NA",
            "known_near_miss_denominator": near_miss_total,
            "known_near_miss_hits": near_miss_hits,
            "insufficient_evidence": insufficient,
            "denominator_note": "机械部分未做根因去重（无法从 score.json 还原），"
                                "人工裁定部分按 root_cause 归并",
        },
        "invalid_pairs": invalid_pairs,
        "newly_missed_blocker": newly_missed_blocker,
        "threshold_check": threshold_check,
        "warnings": warnings,
        "comparable": bool(valid_fixtures) and not invalid_pairs,
        "scope": ("本输出只产事实与派生指标；是否达标由人按 "
                  "docs/evals-baseline/ab-protocol.md 的冻结阈值解读，"
                  "不得由本输出自动推断"),
    }


def matrix_to_csv(summary):
    """把汇总转成可复算 CSV（UTF-8 BOM）：一行一个夹具一列一项事实。"""
    lines = ["fixture,answer_total,near_miss_total,pairs,valid_pairs,"
             "mean_delta_bm,mean_delta_fp,newly_detected,common,only_a,missed_both"]
    for fixture in summary.get("per_fixture") or []:
        detail = fixture.get("detail") or {}
        lines.append(",".join([
            str(fixture.get("fixture")),
            str(fixture.get("answer_total", "")),
            str(fixture.get("near_miss_total", "")),
            str(len(fixture.get("pairs") or [])),
            str(fixture.get("valid_pairs", 0)),
            ("" if fixture.get("mean_delta_bm") is None else str(fixture["mean_delta_bm"])),
            ("" if fixture.get("mean_delta_fp") is None else str(fixture["mean_delta_fp"])),
            str(len(detail.get("newly_detected") or [])),
            str(len(detail.get("common") or [])),
            str(len(detail.get("only_a") or [])),
            str(len(detail.get("missed_both") or [])),
        ]))
    return "\ufeff" + "\n".join(lines) + "\n"


def _sanitize_summary(summary):
    """去掉汇总里的答案 ID，只留计数与严重度分布。

    答案 ID 属受限资产（执行方目录里混入答案 ID 即判泄漏），而常规归档要能
    随报告流转。因此去敏版只保留：条目数、严重度、限档原因与无效记录原因——
    这些足以复算指标，但拼不回答案键。
    """
    out = json.loads(json.dumps(summary, ensure_ascii=False))
    for fixture in out.get("per_fixture") or []:
        detail = fixture.get("detail") or {}
        for key in ("newly_detected", "common", "only_a", "missed_both"):
            entries = detail.get(key) or []
            detail[key] = [{"severity": e.get("severity")} for e in entries]
        fixture["newly_missed_blocker"] = [
            {"severity": "blocker"} for _ in (fixture.get("newly_missed_blocker") or [])]
    out["newly_missed_blocker"] = [
        {"fixture": item.get("fixture"), "severity": "blocker"}
        for item in (out.get("newly_missed_blocker") or [])]
    for fixture_entry in out.get("per_fixture") or []:
        for pair in fixture_entry.get("pairs") or []:
            pair.pop("hits_a", None)
            pair.pop("hits_b", None)
    out["sanitized"] = True
    out["sanitized_note"] = ("答案 ID 已去除；本件为常规去敏归档，"
                              "含答案 ID 的完整记录留在受限区。")
    return out


def archive_run(matrix_path, results_root, out_dir, fixtures_dir=None, sanitized=False):
    """归档一次正式运行：协议、矩阵、原始计分、裁定、隔离检查、无效记录与汇总。

    归档原则（方案 §5.6）：
    - **答案与敏感原始证据留在受限区**：归档不含夹具答案键，也不含证据文件本体，
      只留证据索引（发现里声明的证据路径）；
    - **原始记录保留**：无效配对也进归档（连同无效原因），不得只留有效的那部分；
    - **可复算**：同时给 summary.json 与 UTF-8 BOM 的 summary.csv；
    - 归档写入**新目录**，已存在即拒，避免覆盖既有归档。

    隔离检查在此重跑一次并留档：`prepare` 产物当时通过了检查，不代表执行完成后
    的目录仍干净——执行方可能事后把答案写回目录。这类泄漏必须进归档。

    `sanitized=True` 产出**常规去敏归档**：不含原始计分（score.json 含答案 ID）、
    不含答案 ID，只留汇总事实、去敏明细、可复算 CSV 与协议副本。
    完整件（含答案 ID 与原始计分）留在受限区。
    """
    matrix = _load_matrix(matrix_path)
    results_root = Path(results_root)
    out_dir = Path(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ValueError(f"归档目录非空，拒绝覆盖：{out_dir}")
    fixtures_dir = Path(fixtures_dir or (ROOT / "docs" / "evals-baseline" / "detection-fixtures"))
    out_dir.mkdir(parents=True, exist_ok=True)
    for sub in ("protocol", "scores", "adjudications", "isolation"):
        (out_dir / sub).mkdir(parents=True, exist_ok=True)

    # 协议与矩阵：冻结时的副本，事后不得靠现网文件解释历史结果
    for name in ("detection-protocol.md", "ab-protocol.md"):
        source = ROOT / "docs" / "evals-baseline" / name
        if source.is_file():
            shutil.copy2(source, out_dir / "protocol" / name)
    shutil.copy2(matrix_path, out_dir / "protocol" / "run-matrix.json")

    isolation = []
    for run in matrix["runs"]:
        score_path = (results_root / run["fixture"] / run["arm"] / str(run["repeat"])
                      / "score.json")
        if not score_path.is_file():
            isolation.append({"run_id": run["run_id"], "fixture": run["fixture"],
                              "arm": run["arm"], "repeat": run["repeat"],
                              "checked": False, "issues": ["缺少计分结果"]})
            continue
        score = _read_json(score_path)
        target = out_dir / "scores" / f"{run['fixture']}__{run['arm']}__{run['repeat']}.json"
        if not sanitized:
            shutil.copy2(score_path, target)
            adjudication = score_path.with_name("adjudication.json")
            if adjudication.is_file():
                shutil.copy2(adjudication,
                             out_dir / "adjudications" / target.name)
        issues = []
        fixture_path = fixtures_dir / f"{run['fixture']}.json"
        if fixture_path.is_file():
            issues = verify_run(score_path.parent, fixture_path, redact_ids=sanitized)
        isolation.append({"run_id": run["run_id"], "fixture": run["fixture"],
                          "arm": run["arm"], "repeat": run["repeat"],
                          "checked": True, "issues": issues,
                          "executor_saw_answers": (score.get("executor") or {}).get("saw_answers")})
    (out_dir / "isolation" / "isolation-checks.json").write_text(
        json.dumps(isolation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary = summarize_matrix(matrix_path, results_root, fixtures_dir)
    if sanitized:
        summary = _sanitize_summary(summary)
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "summary.csv").write_text(matrix_to_csv(summary), encoding="utf-8")
    (out_dir / "invalid-records.json").write_text(
        json.dumps({"invalid_pairs": summary.get("invalid_pairs") or [],
                    "warnings": summary.get("warnings") or []},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    leaked = [item for item in isolation if item.get("issues")]
    index = {
        "schema": "ab-archive-index/1",
        "matrix_id": summary.get("matrix_id"),
        "frozen_at": summary.get("frozen_at"),
        "archived_at": _now(),
        "sanitized": bool(sanitized),
        "runs_archived": len(isolation),
        "isolation_issues": len(leaked),
        "isolation_issue_detail": leaked,
        "contains_answer_key": False,
        "contains_answer_ids": not sanitized,
        "contains_raw_evidence": False,
        "note": ("常规去敏归档：不含答案 ID 与原始计分。"
                 if sanitized else
                 "受限区完整归档：含原始计分与答案 ID，不随报告流转。"),
        "files": sorted(str(p.relative_to(out_dir)).replace("\\", "/")
                        for p in out_dir.rglob("*") if p.is_file()),
    }
    (out_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"archive_dir": str(out_dir), "index": index, "summary": summary}


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

    p_adj = sub.add_parser("adjudicate", help="独立裁定：判定机械未匹配的发现")
    p_adj.add_argument("--score", required=True, help="score.json 路径")
    p_adj.add_argument("--adjudication", required=True,
                       help="裁定 JSON：{adjudications:[{finding_id,decision,reason,root_cause}]}，"
                            "decision ∈ valid/false_positive/insufficient_evidence")

    p_pair = sub.add_parser("pair", help="按夹具配对评分（同一夹具下按重复编号配对两臂）")
    p_pair.add_argument("--matrix", required=True, help="冻结的运行矩阵 JSON")
    p_pair.add_argument("--fixture", required=True, help="夹具名（不含 .json）")
    p_pair.add_argument("--results", required=True,
                        help="结果根目录，结构 <fixture>/<arm>/<repeat>/score.json")
    p_pair.add_argument("--fixtures-dir", default=None, help="夹具目录（评测者侧资产）")

    p_matrix = sub.add_parser("matrix", help="汇总配对差值（先夹具内取均值，再夹具间等权汇总）")
    p_matrix.add_argument("--matrix", required=True, help="冻结的运行矩阵 JSON")
    p_matrix.add_argument("--results", required=True,
                          help="结果根目录，结构 <fixture>/<arm>/<repeat>/score.json")
    p_matrix.add_argument("--fixtures-dir", default=None, help="夹具目录（评测者侧资产）")
    p_matrix.add_argument("--out-csv", default=None, help="可复算 CSV 输出路径（UTF-8 BOM）")

    p_archive = sub.add_parser("archive", help="归档一次正式运行（不含答案键与原始证据）")
    p_archive.add_argument("--matrix", required=True, help="冻结的运行矩阵 JSON")
    p_archive.add_argument("--results", required=True,
                           help="结果根目录，结构 <fixture>/<arm>/<repeat>/score.json")
    p_archive.add_argument("--out", required=True, help="归档目录（须不存在或为空）")
    p_archive.add_argument("--fixtures-dir", default=None, help="夹具目录（评测者侧资产）")
    p_archive.add_argument("--sanitized", action="store_true",
                            help="产出常规去敏归档：不含原始计分与答案 ID（完整件留在受限区）")

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
        elif args.command == "adjudicate":
            print(json.dumps(adjudicate_findings(args.score, args.adjudication),
                             ensure_ascii=False, indent=2))
        elif args.command == "pair":
            matrix = _load_matrix(args.matrix)
            fixtures_dir = Path(args.fixtures_dir or (ROOT / "docs" / "evals-baseline" / "detection-fixtures"))
            print(json.dumps(pair_fixture(matrix, args.fixture, args.results,
                                          _answers_by_fixture(fixtures_dir)),
                             ensure_ascii=False, indent=2))
        elif args.command == "matrix":
            summary = summarize_matrix(args.matrix, args.results, args.fixtures_dir)
            if args.out_csv:
                out_csv = Path(args.out_csv)
                if not out_csv.parent.is_dir():
                    raise ValueError("CSV 输出父目录不存在")
                out_csv.write_text(matrix_to_csv(summary), encoding="utf-8")
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        elif args.command == "archive":
            result = archive_run(args.matrix, args.results, args.out, args.fixtures_dir,
                                 args.sanitized)
            print(json.dumps({"archive_dir": result["archive_dir"],
                              "index": result["index"]},
                             ensure_ascii=False, indent=2))
        else:
            print(json.dumps(compare_scores(args.a, args.b), ensure_ascii=False, indent=2))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"盲测工具错误：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
