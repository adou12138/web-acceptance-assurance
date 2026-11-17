# -*- coding: utf-8 -*-
"""脚本 E 产物落账器：把结构化观察转成缺陷或证据。

`alignment_modal_checks.js` 会产四段：`modals` / `alignments` / `clippedControls` /
`suspiciousText`。此前只有 `modals` 被 `discover_scenarios` 消费，其余三段
**采集后无人读取**——检查能跑、报告里却永远看不到，等于没测。

本模块补上那段消费链，并把口径固定下来：

- `alignments[].pass == false` → confirmed 缺陷（7.13 对齐）；
- `alignments[].pass == true`  → **pass 证据**（检查跑了且通过，不能退化成 notRun）；
- `clippedControls`            → confirmed（7.15 控件内容被遮挡，scrollWidth 已实测溢出）；
- `suspiciousText`             → pending（只是"形似测试数据"的候选，需人工确认，绝不直接判缺陷）。

另有两路同类观察，此前同样"采集后无人读取"：

- 脚本 D 的 `needScrollActions`（7.9 免滚动可达性）→ pending；
- `capture_aria.js` 的结构 diff（7.19 可访问性）→ pending，无基线 → dataMissing。

为什么 suspiciousText / 首屏外主操作 / a11y 结构变化都不判 confirmed：三者都是
"需要人判断是否可接受"的观察。短文案、年份数量、正常编号都会被正则命中；长表单
与长列表本来就需要滚动；纯装饰节点进出可访问性树通常无害。把观察直接写成缺陷，
比漏报更伤报告可信度——报告一旦有几条站不住的缺陷，其余的也不会被信。

7.6（排列顺序）/ 7.7（分隔线）**只在提供 --design-inventory 时产条目**：
设计侧清单由 design_inventory.py 从节点树机械推导。没提供清单时仍然不产——
脚本 D 只有页面侧数据，凭页面数据给出 pass 或 fail 都是编造。
"""
import argparse
import json
import sys
from pathlib import Path

DIM_ALIGN = "7.13"
DIM_CONTROL = "7.15"
DIM_NO_SCROLL = "7.9"
DIM_ORDER = "7.6"
DIM_DIVIDER = "7.7"
# 置信度门槛：低于它的设计侧推导只配 pending（几何推断不足以支撑确定结论）
_DESIGN_CONF_THRESHOLD = 0.8
DIM_A11Y = "7.19"

# kind → (中文说明, 期望, 实测字段)
ALIGN_KINDS = {
    "required-mark": ("必填标识（星号）与标签垂直居中", "星号与标签中心差 ≤2px", "deltaCenterY"),
    "hint-left-align": ("提示文案与输入框左边缘对齐", "左边缘差 ≤2px", "deltaLeft"),
    "icon-text-center": ("按钮内图标与文字垂直居中", "图标与文字中心差 ≤2px", "deltaCenterY"),
}


def _dict_rows(value):
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def _box(row):
    box = row.get("box")
    return box if isinstance(box, dict) else {}


def _element(row):
    return {"selector": str(row.get("sel") or row.get("selector") or ""),
            "bbox": _box(row)}


def _delta(row, key):
    value = row.get(key)
    return value if isinstance(value, (int, float)) else None


def convert(payload, page="", viewport=""):
    """alignment 提取产物 → findings 列表（可直接喂 assemble_report --findings）。"""
    if not isinstance(payload, dict):
        return []
    findings = []

    for row in _dict_rows(payload.get("alignments")):
        kind = str(row.get("kind") or "alignment")
        label, want, delta_key = ALIGN_KINDS.get(kind, (kind, "偏差 ≤2px", "deltaCenterY"))
        delta = _delta(row, delta_key)
        anchor = str(row.get("anchor") or "")[:30]
        element = _element(row)
        if row.get("pass") is True:
            findings.append({
                "dimension": DIM_ALIGN, "status": "pass",
                "category": "alignment|" + kind, "property": kind,
                "severity": "P3", "page": page, "viewport": viewport,
                "detail": "{} 通过（{}={}）".format(label, delta_key, delta),
                "element": element,
            })
            continue
        findings.append({
            "dimension": DIM_ALIGN, "status": "confirmed",
            "category": "alignment|" + kind, "property": kind,
            "severity": "P2", "page": page, "viewport": viewport,
            "symptom": "{}未达成：{}（实测 {}={}）".format(label, want, delta_key, delta),
            "expected": want,
            "actual": "{}={}".format(delta_key, delta),
            "anchor": anchor,
            "element": element,
            "reproduction": ["打开 {} 并定位 {}".format(page or "页面", element["selector"] or "该元素"),
                             "读取元素几何，计算 {}".format(delta_key)],
            "suggestion": "按设计调整布局使{}；偏差随结果输出，边界情况人工复核".format(want),
        })

    before_control = len(findings)
    for row in _dict_rows(payload.get("clippedControls")):
        scroll_w, client_w = row.get("scrollW"), row.get("clientW")
        element = _element(row)
        findings.append({
            "dimension": DIM_CONTROL, "status": "confirmed",
            "category": "clipped-control", "property": "scrollWidth",
            "severity": "P2", "page": page, "viewport": viewport,
            "symptom": "控件内容溢出被遮挡：文案「{}」超出可视宽度".format(
                str(row.get("anchor") or "")[:30]),
            "expected": "scrollWidth ≤ clientWidth",
            "actual": "scrollWidth={} clientWidth={}".format(scroll_w, client_w),
            "element": element,
            "reproduction": ["打开 {} 并定位 {}".format(page or "页面", element["selector"] or "该控件"),
                             "比较 scrollWidth 与 clientWidth"],
            "suggestion": "调整控件宽度或截断策略；确认文案完整可见",
        })

    for row in _dict_rows(payload.get("suspiciousText")):
        element = _element(row)
        findings.append({
            "dimension": DIM_CONTROL, "status": "pending",
            "category": "suspicious-text", "property": "text",
            "severity": "P3", "page": page, "viewport": viewport,
            "symptom": "页面存在形似测试数据的文案：「{}」".format(str(row.get("text") or "")[:60]),
            "expected": "页面不含测试数据/占位文案",
            "actual": str(row.get("text") or "")[:60],
            "element": element,
            "reproduction": ["打开 {} 并定位 {}".format(page or "页面", element["selector"] or "该文案")],
            "suggestion": "人工确认是否为测试数据残留；确认后清理或登记为测试账号数据",
        })
    # 两段都检查过、且都没命中 = 控件完整性通过。
    # 不补这条的话 7.15 会落「无产物覆盖」——把"逐个控件量过、都完整"
    # 误报成"没检查"。判据要求 payload 真的带这两段键：文件缺失或段缺失时
    # 不补 pass，避免"没跑"被当成"跑了且干净"。
    if ("clippedControls" in payload and "suspiciousText" in payload
            and len(findings) == before_control):
        findings.append({
            "dimension": DIM_CONTROL, "status": "pass",
            "category": "control-completeness", "property": "scrollWidth",
            "severity": "P3", "page": page, "viewport": viewport,
            "symptom": "控件内容完整性与可疑文案检查未命中问题",
            "expected": "控件内容完整可见、无测试数据残留",
            "actual": "clippedControls 与 suspiciousText 均为空",
            "element": {"selector": "", "nth": 0,
                        "bbox": {"x": 0, "y": 0, "w": 0, "h": 0},
                        "anchor": "alignment-scan"},
            "reproduction": ["页面稳定后遍历控件，比较 scrollWidth 与 clientWidth"],
            "suggestion": "无需处理",
        })
    return findings


def _is_count(value):
    return isinstance(value, int) and not isinstance(value, bool)


def convert_order(payload, page="", viewport=""):
    """脚本 D 产物 → 7.9 条目。7.6 / 7.7 没有设计侧对照，不产条目。

    `needScrollActions` 非空只说明"主操作在首屏外"，长表单和多列表格本来如此，
    因此记 pending 交人判断。空列表要判 pass，必须同时满足两个条件：

    1. `actionCandidates > 0` —— 页面确实有可识别的主操作，否则空列表只意味着
       "没有判定对象"，判 pass 就是把"没得测"说成"测过了"；
    2. 该字段存在 —— 老产物没有计数，无法区分上面两种情况，只能如实降级。

    完全没有 `needScrollActions` 键时不产条目——那不是"检查通过"，是没跑。
    """
    if not isinstance(payload, dict) or "needScrollActions" not in payload:
        return []
    raw = payload.get("needScrollActions")
    if not isinstance(raw, list):
        return [_degraded(DIM_NO_SCROLL, "no-scroll-reachability", page, viewport, "dataMissing",
                          "needScrollActions 形状异常（{}），无法判定 7.9".format(type(raw).__name__))]
    rows = _dict_rows(raw)
    if raw and not rows:
        return [_degraded(DIM_NO_SCROLL, "no-scroll-reachability", page, viewport, "dataMissing",
                          "needScrollActions 内没有任何对象条目，无法判定 7.9")]
    if not rows:
        candidates = payload.get("actionCandidates")
        if _is_count(candidates) and candidates > 0:
            return [{
                "dimension": DIM_NO_SCROLL, "status": "pass",
                "category": "no-scroll-reachability", "property": "fold",
                "severity": "P3", "page": page, "viewport": viewport,
                "detail": "{} 个候选主操作均在首屏内（脚本 D 已执行，无命中）".format(candidates),
            }]
        if _is_count(candidates) and candidates == 0:
            return [_degraded(DIM_NO_SCROLL, "no-scroll-reachability", page, viewport, "notRun",
                              "页面无可识别的主操作按钮（actionCandidates=0），7.9 无判定对象")]
        return [_degraded(DIM_NO_SCROLL, "no-scroll-reachability", page, viewport, "dataMissing",
                          "产物缺少 actionCandidates 计数，无法区分「无主操作」与「全部首屏可达」")]
    findings = []
    for row in rows:
        text = str(row.get("text") or "")[:30]
        reason = str(row.get("reason") or "")
        where = {"below_fold": "首屏下方", "above_fold": "首屏上方",
                 "horizontal": "视口右侧"}.get(reason, reason or "视口外")
        findings.append({
            "dimension": DIM_NO_SCROLL, "status": "pending",
            "category": "no-scroll-reachability", "property": "fold",
            "severity": "P3", "page": page, "viewport": viewport,
            "symptom": "主操作「{}」位于{}，需要滚动才能看到".format(text, where),
            "expected": "主操作在首屏内可见（或确认该页面本就依赖滚动）",
            "actual": "{}（top={} bottom={}）".format(where, row.get("top"), row.get("bottom")),
            "element": _element(row),
            "reason": "首屏外主操作是否为设计预期需人工确认，不直接判缺陷",
            "reproduction": ["打开 {} 且不滚动".format(page or "页面"),
                             "确认「{}」是否在本轮视口内可见".format(text or "该操作")],
            "suggestion": "确认设计是否要求首屏可达；若要求，调整布局或提高信息密度",
        })
    return findings


def _degraded(dimension, category, page, viewport, status, reason):
    """无法判定时的统一形状：必须带 reason，否则 coverage_gate 会拒。"""
    return {
        "dimension": dimension, "status": status,
        "category": category, "property": "fold" if dimension == DIM_NO_SCROLL else "tree",
        "severity": "P3", "page": page, "viewport": viewport,
        "reason": reason,
    }


def convert_aria(diff=None, snapshot=None, page="", viewport=""):
    """可访问性结构 diff → 7.19 条目。

    - 有 diff、两边都有节点、且 added/removed 均空 → pass（结构未变化的确定性结论）；
    - 有 diff 且有条目 → pending（纯装饰节点进出可访问性树通常无害，需人判断）；
    - 两边节点数为 0 或缺失 → dataMissing：空 diff 只说明"没抓到东西"，不是"没变化"；
    - 只有快照没有基线 → dataMissing 并写明原因，**不能当 pass**。
    """
    if diff is not None:
        if not isinstance(diff, dict):
            return [_degraded(DIM_A11Y, "aria-structure", page, viewport, "dataMissing",
                              "aria diff 顶层不是对象（{}），无法判定 7.19".format(
                                  type(diff).__name__))]
        if "added" not in diff and "removed" not in diff:
            # 传了 --aria-diff 却没有这两个键：一条不产会让 7.19 静默落回 notRun，
            # 看起来像"没跑"，实际是"跑了但产物无效"——必须出声。
            return [_degraded(DIM_A11Y, "aria-structure", page, viewport, "dataMissing",
                              "aria diff 缺少 added/removed 字段，无法判定 7.19")]
        # 键不存在才算"这一侧没有变化"；键存在但值为 null 属形状异常，不能被当成空列表。
        raw_added = diff["added"] if "added" in diff else []
        raw_removed = diff["removed"] if "removed" in diff else []
        if not isinstance(raw_added, list) or not isinstance(raw_removed, list):
            return [_degraded(DIM_A11Y, "aria-structure", page, viewport, "dataMissing",
                              "added/removed 形状异常（{}, {}），无法判定 7.19".format(
                                  type(raw_added).__name__, type(raw_removed).__name__))]
        added = _dict_rows(raw_added)
        removed = _dict_rows(raw_removed)
        if not added and not removed:
            a_count, b_count = diff.get("aCount"), diff.get("bCount")
            if _is_count(a_count) and _is_count(b_count) and a_count > 0 and b_count > 0:
                return [{
                    "dimension": DIM_A11Y, "status": "pass",
                    "category": "aria-structure", "property": "tree",
                    "severity": "P3", "page": page, "viewport": viewport,
                    "detail": "可访问性结构未变化（a={} b={}）".format(a_count, b_count),
                }]
            return [_degraded(DIM_A11Y, "aria-structure", page, viewport, "dataMissing",
                              "两次快照节点数为 0 或缺失（a={} b={}），空 diff 不能证明结构未变".format(
                                  a_count, b_count))]
        findings = []
        for label, rows in (("新增", added), ("消失", removed)):
            for row in rows[:40]:
                role = str(row.get("role") or "")[:20]
                name = str(row.get("name") or "")[:40]
                findings.append({
                    "dimension": DIM_A11Y, "status": "pending",
                    "category": "aria-structure", "property": "tree",
                    "severity": "P3", "page": page, "viewport": viewport,
                    "symptom": "可访问性节点{}：role={} name={}".format(label, role, name),
                    "expected": "可访问性结构与基线一致（或变化属有意调整）",
                    "actual": "{} role={} name={}".format(label, role, name),
                    "element": {"selector": "", "aria": {"role": role, "name": name}},
                    "reason": "结构变化可能是无障碍回归，也可能是无害的装饰节点调整，需人工确认",
                    "reproduction": ["对同一页面重跑 capture_aria.js --diff",
                                     "对比 added/removed 中 role={} name={} 的节点".format(role, name)],
                    "suggestion": "确认该节点是否应有可访问名称/角色；装饰节点可加 aria-hidden",
                })
        return findings
    if snapshot is not None:
        return [{
            "dimension": DIM_A11Y, "status": "dataMissing",
            "category": "aria-structure", "property": "tree",
            "severity": "P3", "page": page, "viewport": viewport,
            "reason": "首次运行无可访问性对比基线（仅存快照），无法判断结构是否变化",
            "detail": "已保存快照；下次运行可产出 diff",
        }]
    return []


def convert_design_order(order_payload, inventory, page="", viewport=""):
    """design_inventory 顺序清单 × 页面侧 order.json → 7.6 条目。

    对照口径（证据分级，与 design_inventory 的 confidence 对齐）：
    - 两侧文本集合完全一致但顺序不同 → confirmed（同组文本反序是强证据）；
    - 设计侧为几何推导（confidence < 0.8）→ 只出一条 pending 汇总，待人工；
    - 容器未能与页面侧匹配 → 汇总为一条 dataMissing（不逐容器刷屏）。
    """
    findings = []
    containers = inventory.get("order") if isinstance(inventory, dict) else None
    if not isinstance(containers, list):
        return [_degraded(DIM_ORDER, "design-order", page, viewport, "dataMissing",
                          "design_inventory 缺少 order 清单，无法对照 7.6")]
    page_containers = (order_payload or {}).get("order") or []
    matched = confirmed = 0
    geometric = 0
    for entry in containers:
        conf = entry.get("confidence")
        kids = entry.get("childOrder") or []
        design_texts = [str(k.get("text") or "").strip() for k in kids]
        design_texts = [t for t in design_texts if t]
        if len(design_texts) < 2:
            continue  # 纯图形容器没有可对照的文本序列
        page_match = None
        for pc in page_containers:
            if _is_count(entry.get("childCount")) and len(pc.get("kids") or []) == entry.get("childCount"):
                page_texts = [str(k.get("text") or "").strip() for k in (pc.get("kids") or [])]
                page_texts = [t for t in page_texts if t]
                if page_texts and set(page_texts) == set(design_texts):
                    page_match = page_texts
                    break
        if page_match is None:
            continue
        matched += 1
        if page_match != design_texts:
            if conf is not None and conf >= _DESIGN_CONF_THRESHOLD:
                confirmed += 1
                findings.append({
                    "dimension": DIM_ORDER, "status": "confirmed",
                    "category": "design-order", "property": "child-order-mismatch",
                    "severity": "P2", "page": page, "viewport": viewport,
                    "detail": "容器「{}」子项顺序与设计稿不一致：设计 {} → 页面 {}".format(
                        entry.get("containerName") or entry.get("containerId"),
                        " / ".join(design_texts[:4]), " / ".join(page_match[:4])),
                    "evidenceRef": {"containerId": entry.get("containerId"),
                                    "confidence": conf, "derivedBy": entry.get("derivedBy")},
                })
            else:
                geometric += 1
        else:
            findings.append({
                "dimension": DIM_ORDER, "status": "pass",
                "category": "design-order", "property": "child-order-match",
                "severity": "P3", "page": page, "viewport": viewport,
                "detail": "容器「{}」子项顺序与设计稿一致（{} 项）".format(
                    entry.get("containerName") or entry.get("containerId"), len(design_texts)),
            })
    if confirmed == 0 and geometric:
        findings.append({
            "dimension": DIM_ORDER, "status": "pending",
            "category": "design-order", "property": "geometric-order-unverified",
            "severity": "P3", "page": page, "viewport": viewport,
            "detail": "{} 个容器为几何推导顺序（置信度不足），未发现确定不一致，待人工核对".format(geometric),
        })
    if matched == 0 and not findings:
        findings.append({
            "dimension": DIM_ORDER, "status": "dataMissing",
            "category": "design-order", "property": "no-container-match",
            "severity": "P3", "page": page, "viewport": viewport,
            "detail": "设计稿 {} 个容器均未能与页面侧 DOM 容器匹配（childCount+文本集启发式），无法自动对照 7.6".format(len(containers)),
        })
    return findings


def convert_design_dividers(order_payload, inventory, page="", viewport=""):
    """design_inventory 分隔线清单 × 页面侧 dividers → 7.7 条目。

    设计稿没有分隔线 → dataMissing（诚实的数据点，不是失败——设计上确实没有）。
    两侧都有 → 数量与方向对照；不一致只到 pending（位置级对照需坐标基准，人工复核）。
    """
    dividers = inventory.get("dividers") if isinstance(inventory, dict) else None
    if not isinstance(dividers, list):
        return [_degraded(DIM_DIVIDER, "design-divider", page, viewport, "dataMissing",
                          "design_inventory 缺少 dividers 清单，无法对照 7.7")]
    page_dividers = (order_payload or {}).get("dividers")
    if not isinstance(page_dividers, list):
        return [_degraded(DIM_DIVIDER, "design-divider", page, viewport, "dataMissing",
                          "页面侧产物缺少 dividers 字段，无法对照 7.7")]
    if not dividers and not page_dividers:
        return [{
            "dimension": DIM_DIVIDER, "status": "pass",
            "category": "design-divider", "property": "no-divider",
            "severity": "P3", "page": page, "viewport": viewport,
            "detail": "设计稿与页面侧均无分隔线元素，7.7 无判定对象且两侧一致",
        }]
    if not dividers:
        return [_degraded(DIM_DIVIDER, "design-divider", page, viewport, "pending",
                          "设计稿无分隔线清单条目，但页面侧检出 {} 处（多为 border 实现）——待人工确认是否属设计未标注的实现补充".format(len(page_dividers)))]
    from collections import Counter
    design_orient = Counter(d.get("orientation") for d in dividers)
    page_orient = Counter(str(d.get("kind") or "").startswith("border") and
                          (d.get("w") or 0) >= (d.get("h") or 0) and "horizontal" or "vertical"
                          for d in page_dividers)
    status = "pass" if len(dividers) == len(page_dividers) else "pending"
    return [{
        "dimension": DIM_DIVIDER, "status": status,
        "category": "design-divider", "property": "divider-count",
        "severity": "P3", "page": page, "viewport": viewport,
        "detail": "设计稿 {} 条分隔线（{}）vs 页面侧 {} 条（{}）——{}；位置级对照待人工".format(
            len(dividers), dict(design_orient), len(page_dividers), dict(page_orient),
            "数量一致" if status == "pass" else "数量不一致，待人工确认哪侧为准"),
        "evidenceRef": {"designCount": len(dividers), "pageCount": len(page_dividers)},
    }]


def main(argv=None):
    ap = argparse.ArgumentParser(description="结构化观察产物 → findings")
    ap.add_argument("--alignment", type=Path, default=None,
                    help="alignment_modal_checks.js 的 JSON 产物（7.13 / 7.15）")
    ap.add_argument("--order", type=Path, default=None,
                    help="order_divider_scroll_check.js 的 JSON 产物（7.9）")
    ap.add_argument("--aria-diff", dest="aria_diff", type=Path, default=None,
                    help="capture_aria.js --diff 的 JSON 产物（7.19）")
    ap.add_argument("--aria-snapshot", dest="aria_snapshot", type=Path, default=None,
                    help="仅快照、无基线时使用：记 dataMissing 并写明原因")
    ap.add_argument("--design-inventory", dest="design_inventory", type=Path, default=None,
                    help="design_inventory.json（设计侧盘点产物）：提供后产出 7.6/7.7 对照条目")
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--page", default="")
    ap.add_argument("--viewport", default="")
    args = ap.parse_args(argv)
    if not any((args.alignment, args.order, args.aria_diff, args.aria_snapshot)):
        # 一个源都不给却退出 0，会产出一份空 findings 冒充"检查已执行"。
        print("至少需要一个输入：--alignment / --order / --aria-diff / --aria-snapshot",
              file=sys.stderr)
        return 2
    if args.design_inventory and not args.order:
        print("--design-inventory 依赖 --order（页面侧顺序/分隔线数据）", file=sys.stderr)
        return 2

    findings = []
    for label, path, handler in (
            ("alignment", args.alignment, lambda p: convert(p, args.page, args.viewport)),
            ("order", args.order, lambda p: convert_order(p, args.page, args.viewport))):
        if path is None:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            print("读取 %s 产物失败：%s" % (label, exc), file=sys.stderr)
            return 2
        if not isinstance(payload, dict):
            print("%s 产物顶层必须是对象，实际 %s" % (label, type(payload).__name__),
                  file=sys.stderr)
            return 2
        findings.extend(handler(payload))

    if args.aria_diff is not None or args.aria_snapshot is not None:
        diff = snapshot = None
        for label, path in (("aria_diff", args.aria_diff), ("aria_snapshot", args.aria_snapshot)):
            if path is None:
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError) as exc:
                print("读取 %s 产物失败：%s" % (label, exc), file=sys.stderr)
                return 2
            if label == "aria_diff":
                diff = payload
            else:
                snapshot = payload if payload is not None else {}
        findings.extend(convert_aria(diff, snapshot, args.page, args.viewport))

    if args.design_inventory is not None:
        order_payload = None
        if args.order is not None:
            try:
                order_payload = json.loads(args.order.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError) as exc:
                print("读取 order 产物失败：%s" % exc, file=sys.stderr)
                return 2
        try:
            inventory = json.loads(args.design_inventory.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            print("读取 design_inventory 失败：%s" % exc, file=sys.stderr)
            return 2
        findings.extend(convert_design_order(order_payload, inventory, args.page, args.viewport))
        findings.extend(convert_design_dividers(order_payload, inventory, args.page, args.viewport))

    doc = {"findings": findings, "count": len(findings),
           "counts": {"confirmed": sum(1 for f in findings if f["status"] == "confirmed"),
                      "pending": sum(1 for f in findings if f["status"] == "pending"),
                      "pass": sum(1 for f in findings if f["status"] == "pass"),
                      "dataMissing": sum(1 for f in findings if f["status"] == "dataMissing")}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print("结构观察 -> %s（confirmed=%d pending=%d pass=%d dataMissing=%d）"
          % (args.output, doc["counts"]["confirmed"], doc["counts"]["pending"],
             doc["counts"]["pass"], doc["counts"]["dataMissing"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
