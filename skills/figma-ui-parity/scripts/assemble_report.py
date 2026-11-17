#!/usr/bin/env python3
"""把各驱动产物的维度级证据汇总成 report 的 checks 段。

## 为什么需要它

`coverage_gate.py` 要求 39 个维度**每一个**都出现在 `meta.checks` / `checks` /
`dimensionResults` 里，且 `notRun` / `dataMissing` 必须写明原因，否则以非零码退出。
在此之前，这份 39 行的「维度 → 状态 → 原因」表**没有任何生产脚本**——每轮都要由 agent
手工拼装，漏填即被门禁打回，而且手工拼的产物与驱动脚本的真实产出是两张皮。

本脚本把各驱动产物（样式差异 / 探测发现 / 状态场景 / 旅程发现 / 场景执行率）按维度
汇总成 checks，让门禁消费的是**机器产出**而不是手写表。

## 边界纪律（最重要，改代码前先读）

1. **无证据的维度只能填 `notRun`，严禁推断 `pass`。** 「没跑」和「跑了没发现问题」
   是两件事，混为一谈会让覆盖率数字从"诚实记录"变成"装饰"。
2. `notRun` / `dataMissing` **必须带 reason**，且 reason 要能回答"为什么没跑"。
3. 产物里已有的手写 checks **不会被覆盖**：本脚本只补产物能证明的维度，report 里
   已有但产物未覆盖的条目原样保留（人填的比机器猜的优先级高）。
4. 状态优先级（同一维度多份证据时取最严重）：
   `fail`(confirmed) > `pending` > `dataMissing`(notComparable) > `pass`(withinTolerance) > `notRun`
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import defect_keys as DK
import normalize_evidence as NE  # noqa: E402  # 指纹公式的单一来源（F50）
import locator_quality as LQ     # noqa: E402  # 定位可信度的单一来源

# 与 coverage_gate.py 的词表严格一致
EXECUTED = {"pass", "fail", "warn", "pending"}
NOT_EXECUTED = {"notRun", "dataMissing"}
VALID = EXECUTED | NOT_EXECUTED

# 证据严重度排序：越靠前越严重，同维多份证据取最严重。
# warn = 已执行但有告警，必须排在 notRun（没跑）之前，否则「实测到的告警」会被
# 「没跑」覆盖，覆盖率与报告结论同时失真。
SEVERITY_ORDER = ["fail", "pending", "warn", "dataMissing", "pass", "notRun"]

# 本文件生成 checks 时打的指纹。重跑时据此判定「这条是机器派生的」，
# 与人工结论（无此标记）分开：前者必须每轮重算，后者永久保留。
DERIVED_MARK = "assemble_report"

# 兼容存量报告：旧版本写下的派生态没有 derived 标记。只认本文件自己拼出的
# 固定短语——人的描述不会恰好长这样，所以不会误删人工结论。
_DERIVED_REASON_FINGERPRINTS = (
    "无产物覆盖该维度",
    "要求该维度，但本轮未执行",
    "未纳入该维度",
    "本轮未执行原因",
)

VALID_SEVERITY = {"P0", "P1", "P2", "P3"}

# 各 producer 写严重度的历史写法 → schema 词表。中文三个来自 struct_checks，
# 英文档位来自早期检测脚本。新增 producer 应直接用 P0–P3，这里是兼容层。
SEVERITY_ALIASES = {
    "高": "P1", "中": "P2", "低": "P3",
    "critical": "P0", "blocker": "P0", "high": "P1",
    "medium": "P2", "normal": "P2", "low": "P3", "info": "P3", "minor": "P3",
    "p0": "P0", "p1": "P1", "p2": "P2", "p3": "P3",
}


def is_machine_derived(node) -> bool:
    """这条 check 是不是本脚本上一轮派生出来的（可重算），而不是人工结论。"""
    if not isinstance(node, dict):
        return False
    if node.get("derived"):
        return True
    # 档位阶段打的两个标记都只由本文件写入。
    if node.get("profileRequiredButMissing") or node.get("profileExcluded"):
        return True
    reason = str(node.get("reason") or "")
    return any(mark in reason for mark in _DERIVED_REASON_FINGERPRINTS)


def _scenario_ids(items):
    """待确认条目里被阻塞的场景 id。

    字段名在各类产物里写法不统一（scenario / scenarioId / scenario_id 或数组），
    全部收下：漏收的后果是 7.12 说「有待确认项」却指不出卡了哪些场景。
    """
    out = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        for key in ("scenarioId", "scenario", "scenario_id"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                out.add(value.strip())
        for key in ("scenarios", "blockedScenarios"):
            value = item.get(key)
            if isinstance(value, list):
                out.update(str(x).strip() for x in value
                           if isinstance(x, str) and x.strip())
    return out


def _resolution_condition(pending_dims, missing_dims, blocked):
    """解除条件：确认什么、由谁确认、确认后怎么放行。不能写「待确认」。"""
    parts = []
    if pending_dims:
        parts.append("逐条判定 %s 的 pending 项属不属于缺陷：是则改 confirmed 并带证据，"
                     "否则删除并写明判为无害的理由" % "、".join(sorted(pending_dims)))
    if missing_dims:
        parts.append("补齐 %s 缺失的产物（设计稿 / 页面盘点 / 基线）后重跑，"
                     "拿不到数据则标注不可判定" % "、".join(sorted(missing_dims)))
    if blocked:
        parts.append("确认后重跑这 %d 条场景，把结果回填到 scenario_coverage" % len(blocked))
    return "；".join(parts) or "本轮无需解除条件"


def _normalize_severity(item: dict) -> str:
    """把各种严重度写法归一到 schema 允许的 P0–P3。

    中文级别（高/中/低）来自 struct_checks 这类早期 producer，英文档位
    （high/medium/low、critical）来自其它检测脚本。两种都不在 schema 的
    enum 里，原样透传会让整轮报告在校验关卡死。
    """
    raw = str(item.get("severity") or "").strip()
    if raw in VALID_SEVERITY:
        return raw
    alias = SEVERITY_ALIASES.get(raw) or SEVERITY_ALIASES.get(raw.lower())
    if alias:
        return alias
    hint = str(item.get("severityHint") or "").strip()
    if hint in VALID_SEVERITY:
        return hint
    return "P2"


def _ensure_defect_shape(item: dict) -> dict:
    """把 findings 类条目补齐成 ``defect.schema.json`` 的必填形状（缺什么补什么，不覆盖已有值）。

    【F50】findings 类产物（extra_checks / state_probes / journeys / runtime_observability）
    的条目**不带 fingerprint、element 为 null、severity 只有 severityHint**，而
    ``defect.schema.json`` 要求 ``fingerprint``(minLength 8) / ``element``(selector+bbox) /
    ``severity`` 必填。旧版 collect_defects 原样并入 report.defects，下一站
    ``validate_defects --strict`` 必然报「缺少必填字段」rc=6，管线门禁短路
    （P2-9）⇒ 三件套报告零产出。实测复现：assemble rc=0 → validate rc=6 →
    generate_reports 从未执行。

    修法选在**收口处**（assemble_report）而不是逐个 producer 补字段：指纹公式
    只此一份（normalize_evidence.fingerprint，F8 的 dimension|property|raw.url
    口径原样继承），新 producer 自动受益，不会出现第五份手抄键表。
    element 缺省填页面级占位——页面级 finding（暗色/打印/接口异常/旅程落地页）
    本就无元素定位，空 selector 比假 selector 诚实；已有真定位的原样保留。
    severity 取 severityHint（producer 的声明值），两者都缺才落 P2。

    ``reproduction`` / ``suggestion`` 同样缺什么补什么：producer 一律不产这两个
    字段，而 ``--strict`` 要求非空。回填值只从条目自身字段（dimension/symptom/
    expected/actual/page）派生模板，不虚构 producer 没说过的步骤；条目已带的
    真值一律不覆盖。
    """
    out = dict(item)
    if not out.get("fingerprint"):
        out["fingerprint"] = NE.fingerprint(out)
    # severity 归一：producer 用中文级别（高/中/低）或英文档位（high/medium）时，
    # 原样保留会直接撞 schema 的 enum → validate_defects rc=6 → 整轮报告零产出。
    # 只在取值不合法时才改写，合法值一律不动。
    out["severity"] = _normalize_severity(out)
    # confidence 是 schema 必填项，但 producer 普遍不产。缺省值按「状态 + 严重度」
    # 给保守值：已确认的高等级缺陷置信度高，待确认项一律压到 0.4。这不是替页面
    # 下结论，只是让必填字段有一个可解释的来源；producer 自带的真值优先。
    if not isinstance(out.get("confidence"), (int, float)):
        status = str(out.get("status") or "")
        if status == "confirmed":
            out["confidence"] = 0.85 if out["severity"] in ("P0", "P1") else (
                0.75 if out["severity"] == "P2" else 0.6)
        else:
            out["confidence"] = 0.4
    el = out.get("element")
    el = dict(el) if isinstance(el, dict) else {}
    el.setdefault("selector", "")
    el.setdefault("nth", 0)
    bbox = el.get("bbox")
    el["bbox"] = ({"x": 0, "y": 0, "w": 0, "h": 0} if not isinstance(bbox, dict) else
                  {"x": bbox.get("x", 0), "y": bbox.get("y", 0),
                   "w": bbox.get("w", 0), "h": bbox.get("h", 0)})
    el.setdefault("anchor", str(out.get("dimension") or ""))
    # 定位可信度在收口处统一补：producer 各写一份必然漂移（见 defect_keys.py 的教训）。
    # 它不参与判定，只回答"这条定位下次还认不认得出来"。
    out["element"] = LQ.enrich_element(el)
    repro = out.get("reproduction")
    if not (isinstance(repro, list) and any(str(s).strip() for s in repro)):
        page = str(out.get("page") or "").strip()
        symptom = str(out.get("symptom") or "").strip()
        actual = str(out.get("actual") or "").strip()
        dim = str(out.get("dimension") or "").strip()
        steps = ["打开受检页面" + ("（{}）".format(page) if page else "（URL 见本轮产物）")]
        if dim:
            steps.append("触发 {} 对应检查场景（维度定义见 references/check-dimensions.md）".format(dim))
        steps.append("观察：{}".format(symptom + ("；实际：{}".format(actual) if actual else "")))
        out["reproduction"] = [s for s in steps if s.strip()]
    if not str(out.get("suggestion") or "").strip():
        expected = str(out.get("expected") or "").strip()
        out["suggestion"] = "按 expected 修复：{}".format(expected) if expected else \
            "按该维度的预期口径修复（见 references/check-dimensions.md）"
    return out


def load_dimensions(path: Path) -> list:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [d for d in data.get("dimensions", []) if d.get("id")]


def load_profiles(path: Path) -> dict:
    """读执行档位表（assets/run_profiles.json）。读不到就返回空，由调用方报错。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print("[warn] 档位表不可读 {}: {}".format(path, exc), file=sys.stderr)
        return {}
    profiles = data.get("profiles")
    return profiles if isinstance(profiles, dict) else {}


def _read_json(path: Path):
    if not path or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print("[warn] 跳过无法解析的产物 {}: {}".format(path, exc), file=sys.stderr)
        return None


def _findings_list(payload) -> list:
    """findings 类产物兼容两种形状：{findings:[...]} 或直接 [...]。

    【审查修复·P1】另认 ``violations`` 键（assert_constraints 的产出形状）：
    此前 constraints.json 经 --findings 传入但只读 findings 键——7.4 的违规
    条目全部丢失，defects 里有 7.4、checks 里 7.4 却 notRun（与本文件
    docstring 记载的历史 bug 同款自相矛盾）。violations 条目自带
    dimension/status/symptom，与 findings 条目同构，直接并入。
    """
    if isinstance(payload, dict):
        for key in ("findings", "violations"):
            items = payload.get(key)
            if isinstance(items, list) and items:
                return items
        return []
    return payload if isinstance(payload, list) else []


def _state_scenario_findings(payload) -> list:
    """run_state_scenarios 产物的缺陷条目：{results:[{findings:[...]}]}。"""
    if not isinstance(payload, dict):
        return []
    out = []
    for result in payload.get("results") or []:
        if not isinstance(result, dict):
            continue
        found = result.get("findings")
        if isinstance(found, list):
            out.extend(f for f in found if isinstance(f, dict))
    return out


def collect_defects(path: Path, out: list, seen: set) -> int:
    """把某份产物里 status=confirmed 的缺陷条目并入 report 的 defects 列表（按签名去重）。

    【交付修复】本文件的三个 collect_* 此前只产「维度 → 状态」证据，产物里的**缺陷
    条目**全部蒸发：7.25 / 7.26 / 7.32 / 7.33 四类状态场景找到的每条缺陷都到不了报告，
    而它们的 dimension/status 都正常写进了 checks——报告于是呈现「7.32 判定 fail 但
    缺陷清单 0 条」的自相矛盾，coverage_gate 照过。findings 类产物（extra_checks /
    runtime_observability / journeys）同理。
    """
    payload = _read_json(path)
    items: list = []
    if isinstance(payload, list):
        items.extend(payload)
    else:
        for _key, lst in DK.iter_containers(payload):
            items.extend(lst)
    items.extend(_state_scenario_findings(payload))
    added = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "") != "confirmed":
            continue
        # 【F50】收口补齐必填形状（fingerprint/element/severity），
        # 否则 findings 类产物过不了 validate_defects --strict（见 _ensure_defect_shape）
        item = _ensure_defect_shape(item)
        sig = DK.signature(item)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(item)
        added += 1
    return added


def collect_style_diffs(path: Path, evidence: dict) -> None:
    """compare_fields 产物：status 是 confirmed/withinTolerance/notComparable，需映射。

    【F1 端到端断裂修复】compare_fields 顶层键是 ``styleDiffs``（列表形）与
    ``defects``（confirmed 子集），旧代码只读 ``.get("diffs")`` → 恒为 None 直接
    return，**整个样式层（7.1/7.8）的 confirmed 缺陷从不映射成 fail**，对应维度
    退化为 notRun 且门禁照过——核心能力形同虚设。此处按真实键名读取。
    """
    payload = _read_json(path)
    diffs = []
    if isinstance(payload, list):
        diffs = payload
    elif isinstance(payload, dict):
        for key in ("styleDiffs", "diffs"):
            val = payload.get(key)
            if isinstance(val, list):
                diffs = val
                break
    if not isinstance(diffs, list):
        return
    for d in diffs:
        if not isinstance(d, dict):
            continue
        dim = str(d.get("dimension") or "")
        raw = str(d.get("status") or "")
        if not dim:
            continue
        # 映射到门禁词表：容差内=通过、超容差=失败、不可比=数据不可用
        # 【F17】pending 必须显式入表：compare_fields 对**低置信配对**（中文名退化子串
        # 命中）的越界差异降级为 pending 而不是 confirmed。旧表不认 pending → 这些 diff
        # 在这里被 continue 掉，既不进 fail 也不进 pending——**彻底从报告里消失**，
        # 等于把「配对可疑、差异待人工确认」静默吞掉，比直接判 confirmed 更难排查。
        # coverage_gate 的 EXECUTED 已含 pending，入表不会影响覆盖率口径。
        mapped = {"confirmed": "fail", "withinTolerance": "pass",
                  "notComparable": "dataMissing", "pending": "pending"}.get(raw)
        if mapped is None:
            continue
        evidence.setdefault(dim, []).append({
            "status": mapped, "source": path.name,
            "detail": "{} 差异 {}".format(d.get("property"), d.get("delta")),
        })


# defect 词表 → 门禁词表。findings 类产物（extra_checks / runtime_observability /
# journeys）里的 status 是缺陷判定词（confirmed/pending/dataMissing），与
# coverage_gate 的 EXECUTED/NOT_EXECUTED 词表**不是一套**，必须映射后才能进 checks。
DEFECT_TO_GATE = {"confirmed": "fail", "pending": "pending", "dataMissing": "dataMissing",
                  # findings 也可能**断言通过**（如脚本 E 的对齐检查 pass 项）。不映射的话
                  # 「检查跑了且通过」会被丢掉，该维度退化成 notRun——把实测过判成没测。
                  "pass": "pass",
                  # 有证据但不确定是否缺陷：门禁词表里有 warn，producer 也会用它
                  # （性能维度在未施加网络节流时只能给 warn，不能确认成缺陷）。
                  "warn": "warn",
                  # driver 自己失败时产出的 notRun 条目：不映射会既进不了 checks 也丢了
                  # 原因，维度只能落"无产物覆盖"——与"页面打不开"这个真实原因不是一回事。
                  "notRun": "notRun"}


def collect_findings(path: Path, evidence: dict) -> None:
    """extra_checks / runtime_observability / journeys 的 findings 产物。"""
    payload = _read_json(path)
    items = _findings_list(payload)
    # 【审查修复·P1】assert_constraints 跑了且无违规（violations 空列表）≠ 没跑：
    # 不补 pass 证据的话 7.4 仍落 notRun（"无产物覆盖该维度"），与实际执行矛盾。
    # 产物条目的 dimension 固定为 "7.4"（assert_constraints 生产侧同款硬编码）。
    if (isinstance(payload, dict) and isinstance(payload.get("violations"), list)
            and not items):
        evidence.setdefault("7.4", []).append({
            "status": "pass", "source": path.name,
            "detail": "约束断言已执行，无违规条目",
        })
        return
    for f in items:
        if not isinstance(f, dict):
            continue
        dim = str(f.get("dimension") or "")
        status = DEFECT_TO_GATE.get(str(f.get("status") or ""))
        if not dim or status is None:
            continue
        evidence.setdefault(dim, []).append({
            "status": status, "source": path.name,
            # reason 是 notRun/dataMissing 的必填项（门禁要求能回答"为什么没跑"）。
            # 只带 symptom 会让失败原因在第 2 层就丢失，门禁再补一条泛化的
            # "无产物覆盖"，排查方向被带偏到"没接入"而不是"页面打不开"。
            "reason": f.get("reason") or f.get("actual"),
            "detail": str(f.get("symptom") or "")[:60],
        })


def collect_state_scenarios(path: Path, evidence: dict) -> None:
    """run_state_scenarios 产物：元素本身就是 {dimension,status,reason?}，直接采用。"""
    payload = _read_json(path)
    items = payload if isinstance(payload, list) else (payload or {}).get("results") \
        if isinstance(payload, dict) else []
    if not isinstance(items, list):
        return
    for s in items:
        if not isinstance(s, dict):
            continue
        dim = str(s.get("dimension") or "")
        status = str(s.get("status") or "")
        if not dim or status not in VALID:
            continue
        evidence.setdefault(dim, []).append({
            "status": status, "source": path.name,
            "reason": s.get("reason"), "detail": "状态场景驱动",
        })


def collect_scenario_coverage(path: Path, evidence: dict) -> None:
    """scenario_coverage 产物：只用来回答「这个维度有场景但没跑」，不产生 pass。"""
    payload = _read_json(path)
    if not isinstance(payload, dict):
        return
    executed_by_dim = ((payload.get("executed") or {}).get("byDimension") or {})
    notrun_by_dim = ((payload.get("notExecuted") or {}).get("byDimension") or {})
    for dim, cnt in notrun_by_dim.items():
        if executed_by_dim.get(dim):
            continue  # 该维度有场景也跑过，交给别的证据说话
        evidence.setdefault(str(dim), []).append({
            "status": "notRun", "source": path.name,
            "reason": "该维度有 {} 条场景但未执行（场景执行率见 scenario_coverage.json）".format(cnt),
            "detail": "场景未执行",
        })


def summarize(dim_evidence: list) -> dict:
    """同维多份证据取最严重状态，并带上来源与原因。"""
    best = min(dim_evidence, key=lambda e: SEVERITY_ORDER.index(e["status"])
               if e["status"] in SEVERITY_ORDER else len(SEVERITY_ORDER))
    entry = {"status": best["status"]}
    # reason 只对"未执行"有意义：给 pass/warn 也塞一段 reason，报告表格里每行都会
    # 拖一串实际值文本，读的人反而找不到真正需要解释的 notRun 行。
    reason = best.get("reason") if best["status"] in NOT_EXECUTED else None
    # 【F24 配套】coverage_gate 对 notRun/dataMissing 强制要求 reason，缺了以 5 退出。
    # 样式层 notComparable 证据天然没有 reason 字段——在此兜底补一条可回答
    # 「为什么没跑成」的原因，避免修好 F1 后门禁反而假失败。
    if not reason and best["status"] in NOT_EXECUTED:
        reason = {
            "dataMissing": "证据存在但不可比对（notComparable：视口/配对/数据缺失），无法给出判定",
            "notRun": "该维度的驱动产物未产生任何证据",
        }.get(best["status"], "无证据（原因未由产物给出）")
    if reason:
        entry["reason"] = reason
    sources = sorted({e["source"] for e in dim_evidence})
    entry["evidence"] = {"sources": sources, "count": len(dim_evidence)}
    if best.get("detail"):
        entry["note"] = best["detail"]
    return entry


def main() -> int:
    ap = argparse.ArgumentParser(description="把驱动产物的维度证据汇总成 report 的 checks 段")
    ap.add_argument("--report", required=True, type=Path, help="主 report.json（读写）")
    ap.add_argument("--style-diffs", type=Path, default=None, help="compare_fields 产物")
    ap.add_argument("--findings", type=Path, action="append", default=None,
                    help="findings 类产物（extra_checks / runtime_observability / journeys），可多份")
    ap.add_argument("--state-scenarios", type=Path, default=None, help="run_state_scenarios 产物")
    ap.add_argument("--scenario-coverage", type=Path, default=None, help="scenario_coverage 产物")
    ap.add_argument("--dimensions", type=Path,
                    default=Path(__file__).resolve().parent.parent / "schemas" / "check-dimensions.json")
    ap.add_argument("--profile", default=None,
                    help="执行档位 smoke / standard / full（见 assets/run_profiles.json）。"
                         "只改『未执行维度』的记账口径：把 notRun 的原因写成「档位未纳入」，"
                         "不改判定、不抬高覆盖率。不传时行为与之前完全一致")
    ap.add_argument("--profiles-file", type=Path,
                    default=Path(__file__).resolve().parent.parent / "assets" / "run_profiles.json")
    ap.add_argument("--design-provided", choices=["true", "false"], default=None,
                    help="本轮是否有设计稿（写入 meta.designProvided，供还原度措辞门禁判断）；不传则不做该断言")
    ap.add_argument("--write", action="store_true", help="把 checks 写回 report")
    args = ap.parse_args()

    if not args.report.exists():
        print("报告不存在: {}".format(args.report), file=sys.stderr)
        return 3
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print("报告无法解析: {}（{}）".format(args.report, exc), file=sys.stderr)
        return 3
    dims = load_dimensions(args.dimensions)
    if not dims:
        print("维度表为空: {}".format(args.dimensions), file=sys.stderr)
        return 3

    evidence: dict = {}
    if args.style_diffs:
        collect_style_diffs(args.style_diffs, evidence)
    for f in (args.findings or []):
        collect_findings(f, evidence)
    if args.state_scenarios:
        collect_state_scenarios(args.state_scenarios, evidence)
    if args.scenario_coverage:
        collect_scenario_coverage(args.scenario_coverage, evidence)

    # 【交付修复】把三份产物里 status=confirmed 的缺陷条目并入 report 的 defects。
    # 去重集合先纳入 report 顶层已有的各容器键（含 defects/styleDiffs），避免与
    # generate_reports 的「取第一路非空」策略叠加成重复计数。
    defects = report.get("defects")
    if not isinstance(defects, list):
        defects = []
        report["defects"] = defects
    # 存量缺陷同样要过收口补齐：defects 只增不减，上一轮写下的条目会带着旧格式
    # 一直留在报告里。producer 侧修好 severity 词表、补上 confidence 之后，
    # 老条目仍然是 severity="中"、没有 confidence —— validate_defects 每次都
    # 在同一批历史条目上报同样的错，看起来像"改了没生效"。
    # 对所有条目（不只新收的）跑一遍，缺什么补什么、不覆盖已有值。
    for idx, item in enumerate(defects):
        if isinstance(item, dict):
            defects[idx] = _ensure_defect_shape(item)
    seen = set()
    for _key, items in DK.iter_containers(report):
        for item in items:
            if isinstance(item, dict):
                seen.add(DK.signature(item))
    for source in [args.style_diffs] + list(args.findings or []) + [args.state_scenarios]:
        if source:
            collect_defects(source, defects, seen)

    # 已有手写 checks 原样保留：人填的比机器猜的优先级高
    existing = {}
    for container in (report.get("meta"), report):
        if not isinstance(container, dict):
            continue
        for key in ("checks", "dimensionResults", "checklist"):
            value = container.get(key)
            if isinstance(value, dict):
                existing = {str(k): v for k, v in value.items()}
            elif isinstance(value, list):
                existing = {str(e["id"]): e for e in value
                            if isinstance(e, dict) and e.get("id")}
            # 【审查修复·P1】同一 container 内多键并存时首命中即停，与
            # coverage_gate.extract_checks 的读取序（checks 优先）严格同向——
            # 此后命中者无条件覆盖，手写 checks 会被 dimensionResults 整体
            # 顶掉，与两处门禁的优先级判定互相矛盾。
            if existing:
                break
        if existing:
            break

    # 【重复运行】上一轮由本脚本派生的结论不能冒充"手写结论"：`--report` 只重出报告
    # 是官方用法，若把派生结果当成人的判断保留，第一次算出的状态会被永久冻结。
    # 这不只是 7.12 的问题——**每一轮机器写下的 notRun 都会冻结**：第二轮补齐了
    # 驱动产物、重跑管线，checks 里 35 条 notRun 原样留着，覆盖率永远停在第一轮，
    # 报告呈现"再怎么跑也没覆盖"的假象。带 derived 标记的一律剔除重算。
    #
    # 兼容存量报告：旧版本写下的派生态没有 derived 标记，用脚本文案指纹识别。
    # 判据只认本文件自己拼出的固定短语，人的描述不会恰好是这样，因此不会误删人工结论。
    checks = {k: v for k, v in existing.items() if not is_machine_derived(v)}
    produced, kept_manual, not_run = 0, 0, 0
    manual_ids = set(checks)
    for dim in dims:
        dim_id = str(dim["id"])
        if dim_id in checks:
            kept_manual += 1
            continue
        if dim_id in evidence:
            checks[dim_id] = summarize(evidence[dim_id])
            # 标记来源：下一轮重跑必须能识别「这条可以重算」。
            checks[dim_id]["derived"] = DERIVED_MARK
            produced += 1
            if checks[dim_id]["status"] in NOT_EXECUTED:
                not_run += 1
        else:
            # 边界纪律：无证据 → notRun + 可回答「为什么没跑」的原因
            checks[dim_id] = {
                "status": "notRun",
                "reason": "无产物覆盖该维度（未提供对应驱动产物，或该维度无适用场景）",
                "evidence": {"sources": [], "count": 0},
                "derived": DERIVED_MARK,
            }
            produced += 1
            not_run += 1

    # ---- 执行档位：只裁"本轮没打算跑"的维度，不改任何已执行的结论 ----
    # 为什么放在 checks 生成之后：档位是**记账口径**，不是筛选器。已有证据的维度
    # 跑过就是跑过（哪怕不在档位里），只有 notRun/dataMissing 才允许被改写成
    # "档位未纳入"——这样覆盖率不会因为换档位而虚高，报告也能一眼看出
    # "这一轮本来就只打算跑这些"，而不是"漏跑了 20 个维度"。
    applied_profile = {"name": "unset", "applied": False,
                       "note": "未指定 --profile，按实际产物记账"}
    if args.profile:
        profiles = load_profiles(args.profiles_file)
        entry = profiles.get(args.profile)
        if not isinstance(entry, dict):
            print("错误：未知档位 {}（可用：{}）".format(
                args.profile, "、".join(sorted(profiles)) or "档位表为空"), file=sys.stderr)
            return 3
        raw_dims = entry.get("dimensions")
        all_ids = {str(d["id"]) for d in dims}
        if raw_dims == "all":
            included = set(all_ids)
        elif isinstance(raw_dims, list):
            included = {str(x) for x in raw_dims}
        else:
            print("错误：档位 {} 的 dimensions 必须是维度数组或 \"all\"".format(args.profile),
                  file=sys.stderr)
            return 3
        unknown = sorted(d for d in included if d not in all_ids)
        if unknown:
            print("错误：档位 {} 含维度表里不存在的编号：{}".format(args.profile, "、".join(unknown)),
                  file=sys.stderr)
            return 3
        excluded = []
        missing = []
        for dim in dims:
            dim_id = str(dim["id"])
            if dim_id in manual_ids:
                continue
            # 7.12 是横切派生维度（由待确认项推导，不由档位决定），跳过：
            # 否则它会出现在 excludedDimensions 里，而实际结论是派生出来的，自相矛盾。
            if dim_id == "7.12":
                continue
            current = checks.get(dim_id)
            if not isinstance(current, dict) or current.get("status") not in NOT_EXECUTED:
                continue
            previous_reason = str(current.get("reason") or "").strip()
            if dim_id in included:
                # 档位要求了却没跑：这是**漏跑**，必须与"档位外不打算跑"分开写。
                # 两者都是 notRun，但前者意味着这一轮的结论不完整，需要补跑；
                # 混成同一句"无产物覆盖"会让冒烟档漏跑关键维度也看不出来。
                current["reason"] = "档位 {} 要求该维度，但本轮未执行：{}".format(
                    args.profile, previous_reason or "未提供对应驱动产物")
                current["profileRequiredButMissing"] = True
                missing.append(dim_id)
                continue
            current["reason"] = "档位 {}（{}）未纳入该维度：{}{}".format(
                args.profile, entry.get("label") or "", entry.get("reason") or "",
                ("；本轮未执行原因：" + previous_reason) if previous_reason else "")
            current["profileExcluded"] = True
            excluded.append(dim_id)
        applied_profile = {
            "name": args.profile,
            "applied": True,
            "label": entry.get("label") or "",
            "reason": entry.get("reason") or "",
            "includedCount": len(included),
            "totalDimensions": len(all_ids),
            "excludedDimensions": sorted(excluded),
            "requiredButMissing": sorted(missing),
            "note": "档位只影响未执行维度的原因写法，不改变任何已执行的判定与覆盖率口径；"
                    "requiredButMissing 表示档位要求但本轮无产物（漏跑，需补跑）",
        }

    # 7.12「待确认问题记录」是横切维度：它衡量的是**报告有没有把待确认项记下来**，
    # 不是某个脚本跑没跑。此前全仓没有这一维的任何代码路径——报告里明明有 pending
    # 条目，7.12 却显示 notRun，覆盖率与"待确认已记录"的事实相反。
    # 手写 checks 优先，不覆盖人的结论。
    if "7.12" not in manual_ids:
        previous_712 = checks.get("7.12", {}).get("status")
        # 口径必须与报告「待确认问题」表同源（见 defect_keys.PENDING_*）：
        # 缺陷容器里的 pending/dataMissing、证据层同状态条目、以及 pendingChecks 容器，
        # 三者任一存在都意味着"本轮有待确认项"。只统计 defects 会漏掉按模板记录的
        # pendingChecks，让 7.12 在表里明明有 5 条待确认时仍写"无待确认项"。
        # 状态词表取自共享常量，避免两处各写一份后漂移。
        _PENDING, _MISSING = DK.PENDING_STATUSES
        pending_dims = set()
        missing_dims = set()
        blocked = set()
        for dim, rows in evidence.items():
            statuses = {r.get("status") for r in rows}
            if _PENDING in statuses:
                pending_dims.add(dim)
            if _MISSING in statuses:
                missing_dims.add(dim)
            for row in rows:
                if row.get("status") in DK.PENDING_STATUSES:
                    blocked |= _scenario_ids([row])
        for _key, items in DK.iter_containers(report):
            for item in items:
                if not isinstance(item, dict) or not item.get("dimension"):
                    continue
                state = str(item.get("status") or "")
                if state == _PENDING:
                    pending_dims.add(str(item["dimension"]))
                elif state == _MISSING:
                    missing_dims.add(str(item["dimension"]))
                if state in DK.PENDING_STATUSES:
                    blocked |= _scenario_ids([item])
        pending_checks = 0
        for key in DK.PENDING_CONTAINER_KEYS:
            value = report.get(key)
            if isinstance(value, list):
                pending_checks += sum(1 for x in value if isinstance(x, dict))
        if pending_dims or missing_dims or pending_checks:
            # 两类必须分开写：待确认是"有结论但需人定"，数据缺失是"没取到、判不了"。
            # 混成一句"存在待确认项"会让人以为问题都有结论，也让覆盖率看起来更饱满。
            parts = []
            if pending_dims:
                parts.append("{} 个维度存在待确认项（{}）".format(
                    len(pending_dims), "、".join(sorted(pending_dims))))
            if missing_dims:
                parts.append("{} 个维度数据缺失、无法判定（{}）".format(
                    len(missing_dims), "、".join(sorted(missing_dims))))
            if pending_checks:
                parts.append("pendingChecks 记录 {} 条".format(pending_checks))
            checks["7.12"] = {
                "status": "pending",
                "reason": "本轮{}，结论需人工复核后定论".format("；".join(parts)),
                "evidence": {"sources": ["checks"],
                             "count": len(pending_dims | missing_dims) + pending_checks},
                # 只写「待确认」等于把问题往人手里一推：不知道卡住了哪些场景、
                # 也不知道确认什么才能放行。这两项让待确认项可以直接派活。
                "blocked_scenarios": sorted(blocked),
                "resolution_condition": _resolution_condition(pending_dims, missing_dims, blocked),
                # 派生标记：下次运行 assemble 时据此剔除并重算，不当成手写结论保留。
                "derived": "pending-from-findings",
            }
            # 派生把 notRun 改成了 pending，统计必须同步减，否则 stdout 的 notRunTotal
            # 与 checks 实际内容自相矛盾（打印说 N 个未跑，报告里是 N-1 个）。
            if previous_712 in NOT_EXECUTED:
                not_run -= 1
        else:
            checks["7.12"] = {
                "status": "notRun",
                "reason": "本轮无待确认项（无可确认对象），该维度无需记录",
                "evidence": {"sources": [], "count": 0},
                "derived": "pending-from-findings",
            }

    # 【审查修复·P2】meta 为显式 null 时 setdefault 不覆盖 → checks 静默不写入
    # （--write 落盘缺 checks，下游 coverage_gate 读到全维度缺失）。
    if not isinstance(report.get("meta"), dict):
        report["meta"] = {}
    report["meta"]["checks"] = checks
    report["meta"]["runProfile"] = applied_profile
    if args.design_provided is not None:
        report["meta"]["designProvided"] = (args.design_provided == "true")
    # 措辞门禁：只记录不阻断。「正常展示」这类描述无法复核，但把它判成缺陷会
    # 让报告里多出一批"文案不合格"的噪音——它的正确去向是让人改写，而不是进台账。
    try:
        import wording_gate
        report["meta"]["wording_gate"] = wording_gate.scan(report)
        report["meta"]["restoreClaimGate"] = wording_gate.scan_restore_claims(report)
    except Exception as exc:                      # pragma: no cover - 兜底不阻断出报告
        report["meta"]["wording_gate"] = {"checked": 0, "flagged": [],
                                          "pass": True, "error": str(exc)}
        report["meta"]["restoreClaimGate"] = {"checked": 0, "flagged": [],
                                              "pass": True, "error": str(exc)}
    if args.write:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "dimensions": len(dims),
        "producedByArtifacts": produced,
        "keptManual": kept_manual,
        "notRunTotal": not_run,
        "evidenceDimensions": sorted(evidence.keys()),
        "runProfile": applied_profile,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
