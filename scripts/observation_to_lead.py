#!/usr/bin/env python3
"""专项观察结果 → OBS-ACT- 线索转换（M2/M4 验收修订）。

把 `runtime_observability.js` 的 collect 输出（findings 带 `confirmed`/`pending`
判定）转换为**主流程可消费的 OBS-ACT- 采集记录**：

- **剥离判定**：`status`/`severity`/`confidence`/`raw` 一律不写入记录顶层；
  可观测事实（事件 kind/text/at、请求 method/url/status/ok/at）落入 `observed`；
- **改写线索**：每条 finding 转为 `lead`（只含 `suspicion`/`basis`/`where` 三键），
  依据按维度指向独立通道（console/网络/重复提交/命中测试/载荷快照）——
  **不携带已裁定结论**，由裁决阶段核 `observed_*` 后定性；
- **盲区披露**：`hooksInstalled=false` 或 `collectError` 时 `blind_spot` 如实写明；
- **三类分流**（`--class`）：
  - `expected-4xx`：预期校验失败（4xx）保留为观察，不生成候选；
  - `background-noise`：轮询/取消等后台噪声保留为观察，不生成候选；
  - `real-failure`：5xx / 未捕获异常 / 业务码失败生成**候选**（`verdict=pending`，
    交 finding-triage 复核），不直接落缺陷。

产物（均过 `phase_gate.py scan-collection`）：
- `--out-obs <obs.jsonl>`：OBS-ACT- 采集记录（可入 `complete/revise --collection`）；
- `--out-candidates <candidates.jsonl>`：候选清单（`obs_id`/`finding_key`/`verdict`/
  `basis`/`evidence`，供 finding-triage 复核）；

用法：
    python scripts/observation_to_lead.py --collect <collect.json> \
        --action-id ACT-004 --tc-ref TC-001 --page-url https://test.example/form \
        --evidence evidence/screens/OBS-ACT-004.png \
        --class real-failure --out-obs obs.jsonl --out-candidates candidates.jsonl
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

OBS_SCHEMA_HINT = "OBS-ACT-"
CLASSES = ("expected-4xx", "background-noise", "real-failure")
LEAD_BASIS = {
    "7.35": "独立通道：console 增量 / page error / unhandled rejection 采集",
    "7.36": "独立通道：网络响应采集（非页面回显）",
    "7.37": "独立通道：重复提交计数（请求序列）",
    "7.38": "独立通道：中心点命中测试（遮挡几何）",
    "7.45": "独立通道：请求载荷快照",
}
# finding 上属于「判定」的字段——转换时一律剥离（status/severity/confidence/raw）
STRIPPED_FIELDS = ("status", "severity", "confidence", "raw")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _network_from(requests):
    rows = []
    for req in requests or []:
        rows.append({
            "method": req.get("method"),
            "path": req.get("url"),
            "status": req.get("status"),
            "ok": req.get("ok"),
            "at": req.get("at"),
        })
    return rows


def _facts_from(collect):
    """collect 的 newEvents（增量明细）→ observed 事实；旧版无 newEvents 时退化为空。"""
    new_events = collect.get("newEvents") or {}
    errors = [{"kind": e.get("kind"), "text": e.get("text"),
               "source": e.get("source"), "at": e.get("at")}
              for e in (new_events.get("errors") or [])]
    return {"console": errors, "network": _network_from(new_events.get("requests"))}


def convert(collect, action_id, tc_ref, page_url, evidence, report_class):
    """返回 (obs_record, candidates)。report_class 决定是否生成候选与盲区表述。

    **窗口归属（审核缺陷修复）**：优先使用 `windowFindings`（本窗口新增事件触发的判定，
    由 finding.windowSeq/sourceKind 归属）；旧产物无该字段时回退全量 `findings`——
    回退即表示"窗口归属不可核对"，须在复核时人工核对 cursor 与事件时间（不静默当作
    本窗口）。这防住「累计 findings 每窗口重复转出同一线索」与「动作前事件混入本窗口」。
    """
    if report_class not in CLASSES:
        raise ValueError(f"--class 必须为 {CLASSES} 之一，收到 {report_class!r}")
    findings = collect.get("windowFindings")
    if findings is None:
        findings = collect.get("findings") or []
    obs_id = f"{OBS_SCHEMA_HINT}{action_id}"
    observed = _facts_from(collect)
    blind_spot = []
    if not collect.get("hooksInstalled"):
        blind_spot.append("观察钩子未安装：初始加载期异常未采集")
    if collect.get("collectError"):
        blind_spot.append(f"采集异常：{str(collect.get('collectError'))[:160]}")

    leads, candidates = [], []
    real_failure = report_class == "real-failure"
    for index, finding in enumerate(findings):
        dimension = str(finding.get("dimension") or "?")
        symptom = str(finding.get("symptom") or finding.get("text") or "专项观察命中")
        finding_key = str(finding.get("property") or f"D{dimension}-{index}")
        # 判定剥离：lead 只由可观测症状构造，不带 status/severity/confidence/raw
        leads.append({
            "suspicion": symptom,
            "basis": LEAD_BASIS.get(dimension, "独立通道：专项观察采集"),
            "where": page_url,
        })
        if real_failure:
            candidates.append({
                "id": f"CAND-{action_id}-{index + 1}",
                "obs_id": obs_id,
                "finding_key": finding_key,
                "verdict": "pending",
                "basis": LEAD_BASIS.get(dimension, "独立通道：专项观察采集"),
                "evidence": evidence,
            })

    # 未命中任何 finding 但出现 5xx/网络错误时（real-failure），补一条候选由裁决核
    if real_failure and not findings:
        failures = [r for r in observed["network"] if isinstance(r.get("status"), int) and r["status"] >= 500]
        if failures:
            leads.append({
                "suspicion": f"交互期间出现 {len(failures)} 条 5xx 响应，疑业务失败",
                "basis": LEAD_BASIS["7.36"],
                "where": page_url,
            })
            candidates.append({
                "id": f"CAND-{action_id}-1", "obs_id": obs_id,
                "finding_key": "network-5xx", "verdict": "pending",
                "basis": LEAD_BASIS["7.36"], "evidence": evidence,
            })

    record = {
        "id": obs_id,
        "collection_state": "collected",
        "tc_ref": tc_ref,
        "action_id": action_id,
        "page_url": page_url,
        "report_class": report_class,
        "observed": observed,
        "evidence_paths": [evidence],
        "collected_at": _now(),
    }
    if leads:
        # lead 为三键对象；多条线索按首个入 lead，其余进 acknowledged_unexplored? ——
        # 契约只有 lead 一个字段，多条线索合并 suspicion（保留完整症状清单）。
        record["lead"] = {
            "suspicion": "；".join(l["suspicion"] for l in leads),
            "basis": "；".join(sorted({l["basis"] for l in leads})),
            "where": page_url,
        }
    if blind_spot:
        record["blind_spot"] = blind_spot
    return record, candidates


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="专项 findings → OBS-ACT- 线索（剥离判定 + 三类分流，路径 B 入口转换）")
    parser.add_argument("--collect", required=True, help="runtime_observability collect 输出 JSON")
    parser.add_argument("--action-id", required=True)
    parser.add_argument("--tc-ref", required=True)
    parser.add_argument("--page-url", required=True)
    parser.add_argument("--evidence", required=True, help="截图/DOM 证据路径（OBS 必附）")
    parser.add_argument("--class", dest="report_class", required=True, choices=CLASSES)
    parser.add_argument("--out-obs", required=True, help="OBS-ACT- JSONL 输出")
    parser.add_argument("--out-candidates", help="候选 JSONL 输出（real-failure 时有内容）")
    args = parser.parse_args(argv)

    collect = json.loads(Path(args.collect).read_text(encoding="utf-8-sig"))
    record, candidates = convert(collect, args.action_id, args.tc_ref,
                                 args.page_url, args.evidence, args.report_class)
    Path(args.out_obs).write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.out_candidates:
        text = "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in candidates)
        Path(args.out_candidates).write_text(text, encoding="utf-8")
    print(json.dumps({"obs": args.out_obs, "candidates": len(candidates),
                      "class": args.report_class}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())