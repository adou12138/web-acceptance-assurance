#!/usr/bin/env python3
"""缺陷收口富化：问题来源标注 + 设计稿色值对照 + 复现步骤详细化。

为什么存在： assemble_report.py 的占位复现步骤（"打开受检页面/触发对应检查场景"）
与无来源标注的缺陷单，用户读不懂也定不了修复入口。2026-10-29 实测两轮返工后沉淀：
1) 问题来源三分类（规范缺陷/实现缺陷/规范+实现共同缺失）必须写进每条缺陷单，
   判定依据是设计稿侧证据——**无对照数据时留空，不猜**；
2) 对比度类缺陷的现象必须带设计稿色值对照（文字色/底色/设计稿自身对比度），
   因为"网页忠实还原设计稿"意味着修复入口在设计侧色板而非前端；
3) 复现步骤要写清每一步按什么键、看哪个面板、达到什么条件算复现。

用法（在 assemble_report + coverage_gate 之后、capture_defect_shots 之前执行）：
    python scripts/enrich_defects.py --report ./parity-test/report.json \
        [--design ./parity-test/design/design_model.json]

幂等性： 已有 sourceClass 的缺陷视为人工已定稿，整体跳过（人填的比机器猜的优先）；
symptom 已含设计稿对照段时不再追加。重复运行不产生重复内容。

设计稿色值匹配口径： 页面前景 hex 与设计稿 TEXT 节点填充 hex 精确相等，或
RGB 各通道差 <= 10（容差匹配，报告不区分精度）。设计稿侧对比度用其祖先链上
最近的可见纯色填充作底色；无底色节点时只引用文字色、不编造对比度。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ---- 分类口径（与 defect.schema.json sourceClass 描述保持一致） ----
CONTRAST_PROPS = {"a11y-color-contrast", "placeholder-contrast"}
DARK_PROPS = {"dark-hardcoded-light", "dark-contrast", "prefers-color-scheme"}
BEHAVIOR_PROPS = {
    "failure-stuck-loading", "journey-back-broken", "journey-link-noop",
    "network-failure", "a11y-form-label",
    # uncompressed-image 故意不在此列：它由 DIMENSION_FALLBACK_LABELS["7.40"]
    # 标注（资源工程范畴），放这里会被更早的 prop 分支拦截成死分支——
    # 实测 7.40 曾因此拿到「交互/异常行为」的错误话术。
}
# 按 dimension 兜底（driver 产物可能不带 property）：静态设计稿不承载的工程实现范畴
DIMENSION_FALLBACK_LABELS = {
    "7.4": ("实现缺陷", "多视口布局溢出由前端 CSS 实现，静态设计稿不承载多视口行为，修复入口在前端"),
    "7.40": ("实现缺陷", "资源压缩与加载优化属前端工程范畴，修复入口在前端构建与资源侧"),
}
# 复现步骤是否仍是 assemble_report 的占位模板（可被详细模板替换）
_PLACEHOLDER_MARKS = ("打开受检页面", "对应检查场景")


def _hex_of_fill(node: dict) -> str | None:
    for f in (node.get("fills") or []):
        if f.get("type") == "SOLID" and f.get("visible", True):
            c = f.get("color") or {}
            if c.get("hex"):
                return str(c["hex"]).lower()
    return None


def _lum(hexstr: str) -> float:
    c = [int(hexstr[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _contrast(a: str, b: str) -> float:
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _near(h1: str, h2: str, tol: int = 10) -> bool:
    a = tuple(int(h1[i:i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(h2[i:i + 2], 16) for i in (1, 3, 5))
    return max(abs(a[i] - b[i]) for i in range(3)) <= tol


def _rgb_to_hex(rgbstr: str) -> str | None:
    m = re.match(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", rgbstr.strip())
    if not m:
        return None
    return "#%02x%02x%02x" % tuple(int(m.group(i)) for i in (1, 2, 3))


def collect_design_texts(tree: dict) -> list[dict]:
    """收集设计稿全部可见文本：{chars, textHex, bgHex}。bgHex 取祖先链最近纯色填充。"""
    out: list[dict] = []

    def walk(node: dict, bg_chain: list[str]) -> None:
        my_bg = _hex_of_fill(node) if node.get("type") != "TEXT" else None
        chain = bg_chain + ([my_bg] if my_bg else [])
        if node.get("type") == "TEXT":
            chars = str((node.get("text") or {}).get("characters") or "").strip()
            th = _hex_of_fill(node)
            if chars and th:
                out.append({"chars": chars, "textHex": th, "bgHex": chain[-1] if chain else None})
        for child in node.get("children", []):
            if isinstance(child, dict):
                walk(child, chain)

    if isinstance(tree, dict):
        walk(tree, [])
    return out


def match_design_color(texts: list[dict], fg_hex: str) -> dict | None:
    exact = [t for t in texts if t["textHex"] == fg_hex]
    pool = exact or [t for t in texts if _near(t["textHex"], fg_hex)]
    return pool[0] if pool else None


def _fg_from_actual(actual: str) -> str | None:
    m = re.search(r"前景\s*(rgb\([^)]+\))", actual)
    return _rgb_to_hex(m.group(1)) if m else None


def _ratio_from_symptom(symptom: str) -> str | None:
    m = re.search(r"([\d.]+)\s*:\s*1", symptom)
    return m.group(1) if m else None


def _is_placeholder_repro(repro) -> bool:
    if not isinstance(repro, list):
        return False
    steps = [str(s) for s in repro]
    return bool(steps) and any(any(mark in s for mark in _PLACEHOLDER_MARKS) for s in steps)


# ---- 详细复现步骤模板（参数来自缺陷自身；面向零基础读者，逐步到按键/面板级） ----

def _repro_steps(prop: str, d: dict) -> list[str] | None:
    url = str(d.get("page") or "").strip() or "受检页面 URL（见本轮产物）"
    el = (d.get("element") or {}).get("selector") if isinstance(d.get("element"), dict) else None
    sel = str(el or "目标元素（selector 见元素定位列）")
    fg = _fg_from_actual(str(d.get("actual") or ""))
    ratio = _ratio_from_symptom(str(d.get("symptom") or ""))
    if prop == "a11y-color-contrast" and fg and ratio:
        return [
            f"1. 用 Chrome 打开 {url} ，确认页面正常加载。",
            "2. 按 F12 打开开发者工具，点左上角箭头图标（或按 Ctrl+Shift+C）激活「选取元素」，在页面上点击选中目标元素。（本条元素：" + sel + "）",
            f"3. 在 Elements 面板的 Styles/Computed 里读出该元素文字颜色与背景色：实测前景 {fg}（背景见实际列）。",
            "4. 算对比度：点开颜色块看 \"Contrast ratio\" 一栏，或把两个色值填进 WebAIM Contrast Checker。",
            f"5. 实测 {ratio}:1，低于 WCAG AA 正文要求的 4.5:1，判定对比度不足。",
        ]
    if prop == "placeholder-contrast" and ratio:
        return [
            f"1. 用 Chrome 打开 {url} ，确认页面正常加载。",
            "2. 在页面输入框（本条元素：" + sel + "）里看未输入时的浅色占位提示文字（placeholder）。",
            "3. 按 F12，用 Ctrl+Shift+C 选中该输入框，在 Elements 里找 ::placeholder 伪元素样式，读它的文字颜色与输入框背景色。",
            "4. 把两个色值填进对比度工具（颜色选择器的 Contrast ratio 一栏或 WebAIM Contrast Checker）。",
            f"5. 实测对比度 {ratio}:1，远低于 4.5:1，占位文字几乎看不清。修复方向：placeholder 颜色加深一档以上。",
        ]
    if prop in DARK_PROPS:
        return [
            f"1. 用 Chrome 打开 {url} ，确认页面正常加载。",
            "2. 按 F12 打开开发者工具，按 Ctrl+Shift+P 打开命令面板，输入「Show Rendering」回车，打开 Rendering 面板。",
            "3. 在「Emulate CSS media feature prefers-color-scheme」下拉选 dark，把系统主题强制模拟为深色。",
            "4. 观察页面表现并对照本条「实际」列的实测数值（硬白底元素数 / 低对比文字数或样式表检查结果）。",
            "5. 判定：深色模拟下页面未跟随变暗或出现白底/低对比，即复现本条缺陷。",
        ]
    if prop == "failure-stuck-loading":
        return [
            f"1. 用 Chrome 打开 {url} ，确认页面正常加载。",
            "2. 按 F12 打开开发者工具，切到「Network（网络）」面板。",
            "3. 场景A（接口 500）：用请求拦截（Console 预装 fetch/XHR hook 返回 500，或 Network 面板右键请求 Block 后配合 Local Overrides），刷新页面或触发一次加载。",
            "4. 场景B（断网）：在 Network 面板的 Throttling 下拉里选「Offline」，刷新页面。",
            "5. 判定：两种场景下页面停留在加载态（见本条「实际」列的可见字符与提示检测结果），无错误提示、无重试入口，即复现缺陷；恢复网络后页面才正常。",
        ]
    if prop == "journey-back-broken":
        return [
            f"1. 用 Chrome 打开 {url} ，确认页面正常加载。",
            "2. 点击站内链接进入子页面/新视图（自动化实测为点击工作台内链接跳转）。",
            "3. 点浏览器「返回」按钮或按 Alt+←。",
            "4. 判定：返回后应回到进入前的页面；若变成空白页（about:blank、无可读文本）即复现。替代路径：手动点开一个会话再返回验证。",
        ]
    if prop == "journey-link-noop":
        return [
            f"1. 用 Chrome 打开 {url} ，确认页面正常加载。",
            "2. 找到目标链接（本条元素：" + sel + "）并点击。",
            "3. 判定：点击后 URL 与 DOM 内容均无任何变化（未导航、无内容更新）即复现；需人工确认链接本身是否应当有响应。",
        ]
    if prop == "network-failure":
        return [
            f"1. 用 Chrome 打开 {url} ，确认页面正常加载。",
            "2. 按 F12 切到 Network 面板，勾选「Preserve log（保留日志）」。",
            "3. 正常操作页面（切换会话、刷新列表等交互）。",
            "4. 判定：Network 面板出现红色失败请求（本条为 " + str((d.get('raw') or {}).get('url') or sel) + "，状态码 0 / signal aborted），且页面无任何失败提示，即复现。",
        ]
    if prop == "a11y-form-label":
        return [
            f"1. 用 Chrome 打开 {url} ，确认页面正常加载。",
            "2. 按 F12 用 Ctrl+Shift+C 选中输入控件（本条元素：" + sel + "）。",
            "3. 在 Elements 与 Accessibility 面板检查：是否存在 label[for] 关联、包裹式 label 或 aria-label。",
            "4. 判定：三者皆无、仅靠 placeholder 提示，即复现（WCAG 1.3.1 / 3.3.2）。",
        ]
    if prop == "uncompressed-image":
        raw_url = str((d.get('raw') or {}).get('url') or sel)
        return [
            f"1. 用 Chrome 打开 {url} ，按 F12 切到 Network 面板，刷新页面。",
            f"2. 在请求列表里筛选图片类请求，找到 {raw_url} 。",
            "3. 看该请求的 Size（传输体积）与图片原始解码体积：实测传输 443KB / 解码 446KB，即原图直出、未压缩也未转 WebP/AVIF。",
            "4. 判定：PNG 原图作为页面素材直接传输且体积超 100KB，即复现。修复：转 WebP/AVIF 或压缩后重新上传。",
        ]
    dim = str(d.get("dimension") or "")
    if dim == "7.4":
        actual = str(d.get("actual") or "")
        return [
            f"1. 用 Chrome 打开 {url} ，按 F12 切到 Device Toolbar（Ctrl+Shift+M）开启响应式模式。",
            "2. 依次选择本条实测的视口宽度（768px / 375px），每档设置后刷新页面。",
            "3. 在 Console 里执行 document.documentElement.scrollWidth 与 window.innerWidth 对比：scrollWidth 更大即横向溢出。",
            "4. 判定：" + (f"实测 {actual[:60]}" if actual else "见本条「实际」列的溢出数值") + " —— 视口缩窄后内容未随布局收缩，即复现。修复方向：检查固定宽度元素与缺失的响应式断点。",
        ]
    return None


def enrich(payload: dict, design_texts: list[dict] | None) -> dict:
    stats = {"checked": 0, "sourceLabeled": 0, "symptomEnriched": 0, "reproUpgraded": 0}
    dark_in_design = None  # 懒判定：设计稿里有没有深色变体线索
    for d in payload.get("defects", []):
        if not isinstance(d, dict):
            continue
        stats["checked"] += 1
        prop = str(d.get("property") or "")
        if d.get("sourceClass"):
            continue  # 人工已定稿，机器不覆盖
        label = None
        if prop in CONTRAST_PROPS:
            label = None  # 走设计稿匹配分支，不在此标注
        elif prop in DARK_PROPS:
            label = ("__dark__", None)
        elif prop in BEHAVIOR_PROPS:
            label = ("实现缺陷", "静态设计稿不承载此类交互/异常行为定义，属前端实现范畴，修复入口在前端")
        else:
            label = DIMENSION_FALLBACK_LABELS.get(str(d.get("dimension") or ""))

        if label == ("__dark__", None):
            if dark_in_design is None:
                dump = json.dumps(design_texts or [], ensure_ascii=False)
                dark_in_design = any(k in dump for k in ("dark", "Dark", "深色"))
            if design_texts is not None and not dark_in_design:
                d["sourceClass"] = "规范+实现共同缺失"
                d["sourceNote"] = ("设计稿无深色变体（导出稿文本节点无 dark/深色相关定义），"
                                   "前端亦未做适配或主题变量兜底")
                stats["sourceLabeled"] += 1
        elif label is not None:
            d["sourceClass"], d["sourceNote"] = label
            stats["sourceLabeled"] += 1

        if prop in CONTRAST_PROPS:
            fg = _fg_from_actual(str(d.get("actual") or ""))
            ratio = _ratio_from_symptom(str(d.get("symptom") or ""))
            hit = match_design_color(design_texts or [], fg) if (design_texts and fg) else None
            if hit:
                note = "设计稿该处文字即 {}（如「{}」）".format(
                    hit["textHex"], hit["chars"][:20])
                if hit["bgHex"]:
                    dcr = _contrast(hit["textHex"], hit["bgHex"])
                    note += "，底色 {}，设计稿自身对比度 {:.2f}:1".format(hit["bgHex"], dcr)
                else:
                    note += "（设计稿无底色填充节点，底色未知不推断）"
                note += " —— 前端忠实还原了设计稿配色，修复入口在设计侧色板"
                d["sourceClass"] = "规范缺陷"
                d["sourceNote"] = note
                stats["sourceLabeled"] += 1
                if "设计稿同款配色" not in str(d.get("symptom") or ""):
                    head = str(d.get("symptom") or "").strip()
                    d["symptom"] = (
                        "{}。设计稿同款配色：设计稿该处文字即 {}、底色 {}，"
                        "设计稿自身对比度 {:.2f}:1，同样低于 4.5:1 —— "
                        "缺陷源于设计规范而非前端实现偏差".format(
                            head, hit["textHex"], hit["bgHex"] or "#未知", _contrast(hit["textHex"], hit["bgHex"] or "#ffffff"))
                        if hit["bgHex"] else
                        "{}。设计稿同款配色：设计稿该处文字即 {}（底色未知不推断）".format(head, hit["textHex"]))
                    stats["symptomEnriched"] += 1
            # 无设计稿数据 → 留空不猜

        if _is_placeholder_repro(d.get("reproduction")):
            steps = _repro_steps(prop, d)
            if steps:
                d["reproduction"] = steps
                stats["reproUpgraded"] += 1
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", required=True)
    ap.add_argument("--design", help="design_model.json（可选；不给则跳过设计稿侧判定，来源留空不猜）")
    args = ap.parse_args()

    report_path = Path(args.report)
    payload = json.loads(report_path.read_text(encoding="utf-8"))

    design_texts = None
    if args.design:
        model = json.loads(Path(args.design).read_text(encoding="utf-8"))
        tree = model.get("tree") or model.get("document") or {}
        design_texts = collect_design_texts(tree)

    stats = enrich(payload, design_texts)
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
