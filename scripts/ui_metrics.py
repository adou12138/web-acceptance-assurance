#!/usr/bin/env python3
"""ui_metrics.py — 界面可量化标准的机械校验（对比度 / 目标尺寸 / 文本间距 / 重排）。

为什么需要它：界面类问题最容易退化成"看截图觉得还行"。"按钮偏小""字太淡""小屏挤
在一起"这类判断有公开的量化基准，不该靠感觉——按 WCAG 2.2 的判定口径算出比值再
对照阈值，结论才可复核、可提交、可回归。

判定依据（均为公开规范，不做自定义审美评分）：
- 1.4.3 对比度（最低）AA：普通文本 ≥ 4.5:1；大号文本 ≥ 3:1。
  大号文本 = 字号 ≥ 24px，或字号 ≥ 18.66px 且字重 ≥ 700（14pt 粗体）。
  背景必须声明来源；图片 / 渐变 / 未知背景一律 not_evaluable（见下方纪律）。
- 1.4.11 非文本对比度 AA：界面组件与图形对象（控件边界、图标、焦点指示）≥ 3:1。
- 2.5.8 目标尺寸（最低）AA：可点击区域 ≥ 24×24 CSS px。
  该条有四个例外，全部实现：Inline（位于句子中）、Spacing（24px 圆不相交）、
  User-agent control（尺寸由浏览器决定）、Essential（尺寸对功能必要）。
  命中任一例外 → advisory（记录风险），不判 AA 失败。
  2.5.5（增强）AAA 与主流移动平台建议为 44×44；本脚本按**建议档**输出，不冒充 AA 失败。
- 1.4.12 文本间距 AA：**判定对象是"用户覆盖样式后是否丢失内容"**，不是页面默认行高。
  做法：分别记录覆盖前后被裁剪的元素数，仅当覆盖后新增裁剪才判失败。
  覆盖项 = 行高 1.5 倍、段距 2 倍、字距 0.12 倍、词距 0.16 倍。
  覆盖前就存在的裁剪属别的缺陷（如 1.4.4 缩放文字），单独提示，不计入本条。
- 1.4.10 重排 AA：320 CSS px 宽下不得出现横向滚动（scrollWidth ≤ 视口宽）。
  需要二维布局才有意义的内容（数据表、地图、时间轴）可声明豁免 → advisory。

纪律：
- 算不出来就报 not_evaluable 并写原因，**不得默认通过、也不得默认落成白色**：
  - 取数层必须给出 background_source ∈ solid/image/gradient/unknown；
    未声明时本脚本无法分辨传入颜色是否真实渲染背景，只能 not_evaluable。
  - 图片 / 渐变 / 未知背景上的文字转人工视觉通道（报告里单列 manual_review_required），
    这条是强制的：自动判定放弃的地方必须有人接手，否则这类问题会从系统里消失。
- tool 只判这些可量化项；文案歧义、布局错位、操作反直觉等定性问题仍走探索清单与
  缺陷判定，不由本脚本背书。
- 失败项必须附证据路径，否则降级 pending_evidence —— 没证据的"失败"不能进缺陷账。
- 每条失败须人工裁定 human_ruling：confirmed / exception / false_positive / pending。
  误报率（false_positive / 已裁定）进报告首屏。工具判 fail 只是**候选**，不是缺陷。

用法：
    python scripts/ui_metrics.py --input measurements.json
    python scripts/ui_metrics.py --input measurements.json --emit-json out.json --emit-csv out.csv
    python scripts/ui_metrics.py --input measurements.json --rulings rulings.json --enforce
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path

# —— 阈值：全部来自公开规范，改这里等于改判定口径，须同步 references/visual-usability.md ——
CONTRAST_TEXT_NORMAL = 4.5      # WCAG 1.4.3 AA
CONTRAST_TEXT_LARGE = 3.0       # WCAG 1.4.3 AA（大号文本）
CONTRAST_NON_TEXT = 3.0         # WCAG 1.4.11 AA（组件/图形/焦点指示）
LARGE_TEXT_PX = 24.0            # 18pt
LARGE_BOLD_TEXT_PX = 18.66      # 14pt
LARGE_BOLD_WEIGHT = 700
TARGET_MIN_AA = 24.0            # WCAG 2.5.8 AA
TARGET_COMFORT = 44.0           # WCAG 2.5.5 AAA / 移动平台建议档
LINE_HEIGHT_MIN = 1.5           # WCAG 1.4.12 覆盖值（行高 1.5 倍）
LETTER_SPACING_EM = 0.12        # WCAG 1.4.12 覆盖值
WORD_SPACING_EM = 0.16          # WCAG 1.4.12 覆盖值
PARAGRAPH_SPACING_EM = 2.0      # WCAG 1.4.12 覆盖值
FONT_SIZE_ADVISORY_PX = 12.0    # 无 WCAG 下限，属可读性建议档
REFLOW_VIEWPORT_PX = 320.0      # WCAG 1.4.10
TARGET_SPACING_DIAMETER = 24.0  # 2.5.8 Spacing 例外所用的圆直径

# 2.5.8 四个例外；任一命中即不判 AA 失败
TARGET_EXCEPTIONS = ("inline_in_text", "spacing_clear", "ua_controlled", "essential")

CONTRAST_KINDS = ("text_normal", "text_large", "ui_component", "focus_indicator")
KINDS = CONTRAST_KINDS + ("target", "text_spacing_override", "text_spacing", "reflow")
LEGACY_KINDS = {"text_spacing": (
    "旧口径按页面默认行高判定，不是 WCAG 1.4.12 的判据（该条考的是用户覆盖"
    f"行高 {LINE_HEIGHT_MIN:g} 倍、字距 {LETTER_SPACING_EM:g}em、词距 "
    f"{WORD_SPACING_EM:g}em、段距 {PARAGRAPH_SPACING_EM:g}em 后是否丢失内容）；"
    "请改用 kind=text_spacing_override 记录覆盖前后裁剪数")}

# 背景来源枚举：只有 solid 能直接算对比度
BACKGROUND_SOURCES = ("solid", "image", "gradient", "unknown")
RULINGS = ("confirmed", "exception", "false_positive", "pending")

RGB_RE = re.compile(r"^rgba?\(([^)]+)\)$", re.I)
HEX_RE = re.compile(r"^#([0-9a-f]{3,8})$", re.I)
WEIGHT_WORDS = {"normal": 400, "bold": 700, "bolder": 700, "lighter": 300}


class ColorError(ValueError):
    """颜色无法解析或不适合直接算比值（如半透明叠加）。"""


def parse_color(value):
    """解析 #rgb / #rrggbb / rgb() / rgba()，返回 (r, g, b) 0~255。

    两种"算不出来"必须区分，因为修法不同：
    - 完全透明（alpha=0）：元素本身没画背景，实际背景来自祖先。调用方应向上找到
      最近的不透明祖先背景再填进来（页面里绝大多数段落都是这种情况，若一律报错
      工具就不可用了）。
    - 半透明（0<alpha<1）：必须按最终合成像素取色，凭空算会得出错误比值。
    """
    if not isinstance(value, str) or not value.strip():
        raise ColorError("颜色为空")
    text = value.strip()
    match = HEX_RE.match(text)
    if match:
        digits = match.group(1)
        if len(digits) == 3:
            r, g, b = (int(c * 2, 16) for c in digits)
        elif len(digits) == 6:
            r, g, b = (int(digits[i:i + 2], 16) for i in (0, 2, 4))
        elif len(digits) == 8:
            alpha = int(digits[6:8], 16) / 255
            if alpha == 0:
                raise ColorError(f"背景完全透明（{text}）：须向上取最近的不透明祖先背景")
            if alpha < 0.999:
                raise ColorError(f"半透明颜色 {text}：须按实际渲染像素取色")
            r, g, b = (int(digits[i:i + 2], 16) for i in (0, 2, 4))
        else:
            raise ColorError(f"无法解析颜色 {text}")
        return r, g, b
    match = RGB_RE.match(text)
    if match:
        parts = [p.strip() for p in match.group(1).replace("/", ",").split(",") if p.strip()]
        if len(parts) < 3:
            raise ColorError(f"无法解析颜色 {text}")
        channels = []
        for part in parts[:3]:
            if part.endswith("%"):
                channels.append(float(part[:-1]) * 255 / 100)
            else:
                channels.append(float(part))
        if len(parts) >= 4:
            alpha_text = parts[3]
            alpha = float(alpha_text[:-1]) / 100 if alpha_text.endswith("%") else float(alpha_text)
            if alpha == 0:
                raise ColorError(f"背景完全透明（{text}）：须向上取最近的不透明祖先背景")
            if alpha < 0.999:
                raise ColorError(f"半透明颜色 {text}：须按实际渲染像素取色")
        return tuple(channels)
    raise ColorError(f"无法解析颜色 {text}")


def relative_luminance(rgb):
    """WCAG 相对亮度：通道先做 sRGB 反伽马，再按 0.2126/0.7152/0.0722 加权。"""
    channels = []
    for value in rgb:
        c = value / 255.0
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(foreground, background):
    """WCAG 对比度：(L1+0.05)/(L1 较亮者) / (L2+0.05)。"""
    l1 = relative_luminance(parse_color(foreground))
    l2 = relative_luminance(parse_color(background))
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def normalize_weight(value):
    """字重归一：兼容 700 / "700" / "bold" / "bolder" 等写法。

    取数脚本一般已转成数字，但手写测量表里写 "bold" 很常见——若把它当 400，
    19px 粗体会被按普通文本 4.5:1 判失败，属"假失败"。
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in WEIGHT_WORDS:
            return WEIGHT_WORDS[text]
        try:
            return int(float(text))
        except ValueError:
            return 400
    return 400


def is_large_text(font_size_px, font_weight):
    """大号文本判定：≥24px，或 ≥18.66px 且字重 ≥700。"""
    if not isinstance(font_size_px, (int, float)) or isinstance(font_size_px, bool):
        return False
    weight = normalize_weight(font_weight)
    return font_size_px >= LARGE_TEXT_PX or (
        font_size_px >= LARGE_BOLD_TEXT_PX and weight >= LARGE_BOLD_WEIGHT)


def _evidence(record):
    evidence = record.get("evidence")
    if isinstance(evidence, str):
        return [evidence.strip()] if evidence.strip() else []
    if isinstance(evidence, list):
        return [e for e in evidence if isinstance(e, str) and e.strip()]
    return []


def _result(record, status, standard, measured, threshold, note="", ruling="pending",
            manual=False):
    return {"id": record.get("id"), "page": record.get("page"), "element": record.get("element"),
            "kind": record.get("kind"), "status": status, "standard": standard,
            "measured": measured, "threshold": threshold, "note": note,
            "evidence": _evidence(record), "ruling": ruling, "manual_review": manual}


def _background_source(record):
    value = record.get("background_source")
    if value is None:
        return None
    text = str(value).strip().lower()
    return text if text in BACKGROUND_SOURCES else "unknown"


def _check_contrast(record, kind):
    source = _background_source(record)
    if source is None:
        # 未声明来源 = 无法分辨传入的背景色是元素真实背景还是取数层的兜底值。
        # 实测过的坑：整页是照片时取数脚本落回 #fff，白字算出 1.0:1，全是假失败。
        return _result(record, "not_evaluable", "WCAG 2.2 1.4.3 / 1.4.11", None, None,
                       "缺 background_source（solid/image/gradient/unknown）："
                       "无法确认背景色是否为实际渲染背景，不得直接算比值")
    if source != "solid":
        return _result(record, "not_evaluable", "WCAG 2.2 1.4.3 / 1.4.11", None, None,
                       f"背景来源为 {source}：须按实际渲染像素或人工判读，"
                       "已转人工视觉通道（manual_review_required）", manual=True)
    try:
        ratio = contrast_ratio(record.get("foreground"), record.get("background"))
    except ColorError as exc:
        return _result(record, "not_evaluable", "WCAG 2.2 1.4.3 / 1.4.11", None, None,
                       str(exc), manual=True)
    # 大号文本资格要按实测字号复核，不能凭 kind 自报：否则把 12px 标成
    # text_large 就能拿 3:1 的宽松阈值蒙过去（声明冒充行为）。
    size = record.get("font_size_px")
    measured_large = is_large_text(size, record.get("font_weight"))
    note = ""
    if kind in ("ui_component", "focus_indicator"):
        threshold, standard = CONTRAST_NON_TEXT, "WCAG 2.2 1.4.11 AA（非文本对比度）"
    elif kind == "text_large" and measured_large:
        threshold, standard = CONTRAST_TEXT_LARGE, "WCAG 2.2 1.4.3 AA（大号文本）"
    elif kind == "text_large" and not isinstance(size, (int, float)):
        threshold, standard = CONTRAST_TEXT_LARGE, "WCAG 2.2 1.4.3 AA（大号文本）"
        note = "未提供 font_size_px，大号资格按声明计；补实测字号才算核实"
    elif kind == "text_large":
        threshold = CONTRAST_TEXT_NORMAL
        standard = "WCAG 2.2 1.4.3 AA（普通文本）"
        note = (f"声明为大号文本但实测 {size}px（字重 "
                f"{normalize_weight(record.get('font_weight'))}）未达大号阈值，按普通文本判")
    elif measured_large:
        threshold, standard = CONTRAST_TEXT_LARGE, "WCAG 2.2 1.4.3 AA（按大号文本计）"
    else:
        threshold, standard = CONTRAST_TEXT_NORMAL, "WCAG 2.2 1.4.3 AA（普通文本）"
    measured = {"ratio": round(ratio, 2),
                "font_size_px": record.get("font_size_px"),
                "font_weight": record.get("font_weight"),
                "background_source": source}
    ok = ratio >= threshold
    return _result(record, "pass" if ok else "fail", standard, measured, threshold, note)


def _check_target(record):
    width, height = record.get("target_width_px"), record.get("target_height_px")
    if not isinstance(width, (int, float)) or not isinstance(height, (int, float)):
        return _result(record, "not_evaluable", "WCAG 2.2 2.5.8 AA", None, TARGET_MIN_AA,
                       "缺 target_width_px/target_height_px")
    smallest = min(width, height)
    measured = {"width_px": width, "height_px": height}
    if smallest >= TARGET_COMFORT:
        return _result(record, "pass", "WCAG 2.2 2.5.8 AA（目标尺寸最低）", measured,
                       TARGET_MIN_AA)
    if smallest >= TARGET_MIN_AA:
        return _result(record, "advisory", "WCAG 2.5.5 AAA / 移动平台建议 44×44",
                       measured, TARGET_COMFORT,
                       "达 AA 但低于建议档：密集列表与触摸场景易误触")

    # 欠尺寸：必须先走 2.5.8 的四个例外，否则会把"合规"全判成失败（实测 6/6 误报）
    hit = [name for name in TARGET_EXCEPTIONS if record.get(name) is True]
    measured["exceptions"] = hit
    if hit:
        return _result(record, "advisory", "WCAG 2.2 2.5.8 AA（命中例外）", measured,
                       TARGET_MIN_AA,
                       f"欠 {TARGET_MIN_AA:g}×{TARGET_MIN_AA:g} 但命中例外 {'/'.join(hit)}，"
                       "不判 AA 失败；密集/触摸场景仍属可用性风险，保留为建议")
    # 例外字段全缺 = 没做例外判定，不能凭尺寸直接定论
    if record.get("spacing_clear") is None and not any(
            record.get(name) is not None for name in TARGET_EXCEPTIONS):
        return _result(record, "pending_evidence", "WCAG 2.2 2.5.8 AA", measured,
                       TARGET_MIN_AA,
                       "欠尺寸但未做例外判定（需 spacing_clear：24px 圆是否与相邻目标相交）")
    return _result(record, "fail", "WCAG 2.2 2.5.8 AA（目标尺寸最低）", measured, TARGET_MIN_AA,
                   "欠尺寸且未命中任何例外")


def _check_spacing_override(record):
    """1.4.12 的正确判据：覆盖四项间距后是否新增裁剪。"""
    before, after = record.get("before_clipped"), record.get("after_clipped")
    if not isinstance(before, int) or not isinstance(after, int) or isinstance(before, bool):
        return _result(record, "not_evaluable", "WCAG 2.2 1.4.12 AA", None, 0,
                       "缺 before_clipped/after_clipped（覆盖前后被裁剪元素数）")
    newly = after - before
    measured = {"before_clipped": before, "after_clipped": after, "newly_clipped": newly}
    if newly > 0:
        return _result(record, "fail", "WCAG 2.2 1.4.12 AA（文本间距覆盖）", measured, 0,
                       f"覆盖后新增裁剪 {newly} 处：用户放大行距/字距后内容丢失")
    if before > 0:
        return _result(record, "advisory", "WCAG 2.2 1.4.12 AA（文本间距覆盖）", measured, 0,
                       f"覆盖前后均存在 {before} 处裁剪：不属 1.4.12（间距不恶化），"
                       "但既有裁剪需单独归因，见 1.4.4 缩放文字方向")
    return _result(record, "pass", "WCAG 2.2 1.4.12 AA（文本间距覆盖）", measured, 0)


def _check_reflow(record):
    viewport, scroll = record.get("viewport_width_px"), record.get("scroll_width_px")
    if not isinstance(viewport, (int, float)) or not isinstance(scroll, (int, float)):
        return _result(record, "not_evaluable", "WCAG 2.2 1.4.10 AA", None, REFLOW_VIEWPORT_PX,
                       "缺 viewport_width_px/scroll_width_px")
    measured = {"viewport_width_px": viewport, "scroll_width_px": scroll}
    if viewport > REFLOW_VIEWPORT_PX:
        return _result(record, "not_evaluable", "WCAG 2.2 1.4.10 AA", measured,
                       REFLOW_VIEWPORT_PX, f"重排判定须在 ≤{REFLOW_VIEWPORT_PX:g}px 视口下测")
    overflow = scroll > viewport + 0.5
    if record.get("two_d_layout_exempt") is True:
        measured["two_d_layout_exempt"] = True
        return _result(record, "advisory", "WCAG 2.2 1.4.10 AA（声明二维布局豁免）", measured,
                       viewport,
                       ("声明为需二维布局的内容（数据表/地图等）：不计 1.4.10 失败；"
                        "须人工确认二维布局对理解确有必要"))
    if overflow:
        return _result(record, "fail", "WCAG 2.2 1.4.10 AA（重排）", measured, viewport,
                       f"320px 视口下横向溢出 {scroll - viewport:.0f}px")
    return _result(record, "pass", "WCAG 2.2 1.4.10 AA（重排）", measured, viewport)


def check_record(record):
    """单条测量 → 判定结果。status ∈ pass/fail/advisory/not_evaluable/pending_evidence。"""
    if not isinstance(record, dict):
        raise ValueError("测量记录必须为对象")
    kind = str(record.get("kind") or "").strip()
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}: 必须为 {list(KINDS)} 之一")
    if not str(record.get("id") or "").strip():
        raise ValueError("测量记录缺 id")
    if kind in LEGACY_KINDS:
        return _result(record, "not_evaluable", "WCAG 2.2 1.4.12 AA", None, None,
                       LEGACY_KINDS[kind], manual=True)
    if kind in CONTRAST_KINDS:
        return _check_contrast(record, kind)
    if kind == "target":
        return _check_target(record)
    if kind == "text_spacing_override":
        return _check_spacing_override(record)
    return _check_reflow(record)


def apply_rulings(results, rulings):
    """人工裁定回写：confirm / exception / false_positive / pending。"""
    if not isinstance(rulings, dict):
        raise ValueError("裁定表必须为 {记录ID: 裁定} 对象")
    for item in results:
        value = rulings.get(item["id"])
        if value is None:
            continue
        text = str(value).strip().lower()
        if text not in RULINGS:
            raise ValueError(f"未知裁定 {value!r}：必须为 {list(RULINGS)} 之一")
        item["ruling"] = text
        if text == "false_positive":
            item["status"] = "false_positive"
            item["note"] = (item["note"] + "；" if item["note"] else "") + "人工复核为误报"
        elif text == "exception":
            if item["status"] == "fail":
                item["status"] = "advisory"
            item["note"] = (item["note"] + "；" if item["note"] else "") + "人工裁定属规范例外"
        elif text == "confirmed" and item["status"] == "pending_evidence":
            item["note"] = (item["note"] + "；" if item["note"] else "") + \
                "已裁定成立但仍缺证据：补证据后才可进缺陷账"
    return results


def evaluate(records, rulings=None):
    """批量校验并汇总。失败项无证据 → 降级 pending_evidence（不得直接进缺陷账）。"""
    results = [check_record(record) for record in records]
    for item in results:
        if item["status"] == "fail" and not item["evidence"]:
            item["note"] = (item["note"] + "；" if item["note"] else "") \
                + "失败项缺证据，降级待取证"
            item["status"] = "pending_evidence"
    if rulings:
        apply_rulings(results, rulings)
    counts = {}
    for item in results:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    judged = sum(1 for item in results if item["ruling"] in ("confirmed", "exception",
                                                            "false_positive"))
    false_positive = sum(1 for item in results if item["ruling"] == "false_positive")
    fp_rate = round(false_positive / judged, 4) if judged else None
    hard_fail = counts.get("fail", 0) + counts.get("pending_evidence", 0)
    return {
        "total": len(results),
        "counts": counts,
        "hard_failures": hard_fail,
        "ruled": judged,
        "false_positive": false_positive,
        "false_positive_rate": fp_rate,
        "manual_review_required": [item["id"] for item in results if item["manual_review"]],
        "results": results,
        "standards": {
            "contrast_text_normal": CONTRAST_TEXT_NORMAL,
            "contrast_text_large": CONTRAST_TEXT_LARGE,
            "contrast_non_text": CONTRAST_NON_TEXT,
            "target_min_aa": TARGET_MIN_AA,
            "target_comfort": TARGET_COMFORT,
            "target_spacing_diameter": TARGET_SPACING_DIAMETER,
            "line_height_min": LINE_HEIGHT_MIN,
            "letter_spacing_em": LETTER_SPACING_EM,
            "word_spacing_em": WORD_SPACING_EM,
            "paragraph_spacing_em": PARAGRAPH_SPACING_EM,
            "reflow_viewport_px": REFLOW_VIEWPORT_PX,
        },
        "scope": ("只判可量化项（对比度/目标尺寸/间距覆盖/重排）并给出规范出处；"
                  "不判文案语义、布局错位、操作逻辑等定性问题，不构成 WCAG 合规认证"),
    }


def _load(path):
    text = Path(path).read_text(encoding="utf-8-sig")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("measurements"), list):
        return data["measurements"]
    if isinstance(data, dict) and "kind" in data:
        # 单条测量对象（JSONL 只有一行时会被整体解析成对象）
        return [data]
    raise ValueError("输入需为测量数组、JSONL 或 {\"measurements\": [...]}")


def _emit_csv(report, path):
    # measured 必须在列里：只给判定不给实测值，复核者无法验证这个判定是怎么来的
    fields = ["id", "page", "element", "kind", "status", "ruling", "standard", "threshold",
              "measured", "manual_review", "note", "evidence"]
    with Path(path).open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in report["results"]:
            row = dict(item)
            row["measured"] = json.dumps(item["measured"], ensure_ascii=False)
            row["evidence"] = json.dumps(item["evidence"], ensure_ascii=False)
            writer.writerow({key: row.get(key) for key in fields})


def gate_verdict(report, max_fp_rate=0.15):
    """enforce 判定：有成立缺陷且误报率可控才算"该拦"。

    误报率过高时工具本身不可信，此时拦住整轮只会训练出"忽略工具输出"的习惯，
    应当先修测量装置——所以高误报率返回待核实而不是 fail。
    """
    if report["false_positive_rate"] is not None and report["false_positive_rate"] > max_fp_rate:
        return "待核实", [f"工具误报率 {report['false_positive_rate']:.2%} 超过上限 "
                         f"{max_fp_rate:.0%}：先修测量装置，其结论不可作为缺陷依据"]
    confirmed = sum(1 for item in report["results"] if item["ruling"] == "confirmed")
    reasons = []
    if confirmed == 0:
        reasons.append("无经人工确认的成立缺陷（工具判 fail 只是候选）")
    if report["hard_failures"] and confirmed == 0:
        reasons.append(f"{report['hard_failures']} 项未裁定的失败/待取证：需人工裁定")
    if confirmed:
        return "不通过", reasons or [f"{confirmed} 项经确认的界面缺陷"]
    return "待核实", reasons


def main(argv=None):
    parser = argparse.ArgumentParser(description="界面可量化标准机械校验")
    parser.add_argument("--input", required=True, help="测量结果（JSON 数组 / JSONL）")
    parser.add_argument("--rulings", help="人工裁定表 {记录ID: confirmed/exception/false_positive/pending}")
    parser.add_argument("--emit-json", help="结论输出路径")
    parser.add_argument("--emit-csv", help="CSV 导出路径（UTF-8-sig）")
    parser.add_argument("--max-fp-rate", type=float, default=0.15,
                        help="允许的工具误报率上限（默认 0.15）")
    parser.add_argument("--enforce", action="store_true",
                        help="存在经确认的失败项时以退出码 2 结束")
    args = parser.parse_args(argv)
    rulings = None
    if args.rulings:
        rulings = json.loads(Path(args.rulings).read_text(encoding="utf-8-sig"))
    report = evaluate(_load(args.input), rulings)
    verdict, reasons = gate_verdict(report, args.max_fp_rate)
    report["verdict"] = verdict
    report["verdict_reasons"] = reasons
    if args.emit_json:
        Path(args.emit_json).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    if args.emit_csv:
        _emit_csv(report, args.emit_csv)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.enforce and verdict == "不通过":
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
