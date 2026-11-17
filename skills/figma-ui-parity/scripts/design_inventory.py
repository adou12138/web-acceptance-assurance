#!/usr/bin/env python3
"""设计侧盘点引擎：从设计节点树机械推导对照清单（顺序 / 图标 / 分隔线）。

为什么存在：7.5/7.6/7.7 的设计侧对照清单一直依赖外部提供，缺了就整维度永久
notRun。本脚本把"清单"变成流水线中间产物——从 design_model.json 的节点树
按确定性规则推导，每条带证据（节点 id/name/几何）与置信度：

- 顺序：layout.mode ∈ {VERTICAL, HORIZONTAL}（AUTO_LAYOUT）→ 置信度 1.0，
  子项顺序即声明顺序；绝对定位容器按几何阅读流（Y 行聚类 → X 升序）→ 0.6。
  消费方（struct_checks）只对 ≥0.8 的比对出 confirmed，<0.8 一律 pending——
  几何推断永远不足以支撑"顺序错了"的确定结论。
- 图标：三层规则（尺寸证据 > 命名证据 > 兜底）。无子节点的 VECTOR 且 ≤48px
  几乎必是图标（0.9）；命名含 icon/图标的矢量/实例次之（0.8，命名可能挂在
  容器上）；裸叶矢量兜底（0.6）。旧版导出缺 rect 时自然落到命名层。
- 分隔线：极端宽高比 + 浅色纯填充。设计稿确实没有分隔线时输出空清单——
  空清单是诚实的数据点（消费方据此记 dataMissing），不是失败。

容错：单节点解析失败进 unrecognized 列表，绝不中断——历史 MCP 导出常见
缺损节点（rect=None 等），盘点崩溃等于整轮测试瘫痪。

用法：
    python scripts/design_inventory.py --design ./design/design_model.json \
        --output ./design/design_inventory.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_AUTO_LAYOUT_MODES = {"VERTICAL", "HORIZONTAL"}
_ICON_NAME_RE = re.compile(r"icon|图标", re.IGNORECASE)
# 常见 UI 图标尺寸上限（px）
_ICON_MAX_SIDE = 48
_ROW_CLUSTER_TOLERANCE = 8.0


def _rect(n: dict) -> dict:
    r = n.get("rect") or {}
    if not isinstance(r, dict):
        return {}
    return r


def _num(v):
    return v if isinstance(v, (int, float)) else None


def _fill_hex(n: dict) -> str | None:
    for f in (n.get("fills") or []):
        if isinstance(f, dict) and f.get("type") == "SOLID" and f.get("visible", True):
            c = f.get("color") or {}
            if c.get("hex"):
                return str(c["hex"]).lower()
    return None


def _geometric_order(children: list[dict], axis: str) -> list[dict]:
    """几何阅读流：主轴为 X 时先按 Y 行聚类（±8px）再按 X 升序；主轴为 Y 直接按 Y。"""
    def y(c):
        return _num(_rect(c).get("y")) or 0.0

    def x(c):
        return _num(_rect(c).get("x")) or 0.0

    if axis == "horizontal":
        rows: list[list[dict]] = []
        for c in sorted(children, key=y):
            placed = False
            for row in rows:
                if abs(y(c) - y(row[0])) <= _ROW_CLUSTER_TOLERANCE:
                    row.append(c)
                    placed = True
                    break
            if not placed:
                rows.append([c])
        out: list[dict] = []
        for row in sorted(rows, key=lambda r: y(r[0])):
            out.extend(sorted(row, key=x))
        return out
    return sorted(children, key=y)


def derive_order(tree: dict) -> list[dict]:
    """容器子项顺序清单。confidence: auto_layout=1.0 / geometric=0.6。"""
    out: list[dict] = []

    def walk(n: dict) -> None:
        children = [c for c in (n.get("children") or []) if isinstance(c, dict)]
        if len(children) >= 2:
            layout = n.get("layout") or {}
            mode = (layout.get("mode") or "").upper() if isinstance(layout, dict) else ""
            if mode in _AUTO_LAYOUT_MODES:
                ordered, conf, how = children, 1.0, f"auto_layout:{mode.lower()}"
            else:
                axis = "horizontal" if (_num(_rect(n).get("w")) or 0) >= (_num(_rect(n).get("h")) or 0) else "vertical"
                ordered, conf, how = _geometric_order(children, axis), 0.6, f"geometric:{axis}"
            out.append({
                "containerId": n.get("id"),
                "containerName": n.get("name") or "",
                "confidence": conf,
                "derivedBy": how,
                "childCount": len(children),
                "childOrder": [
                    {"nodeId": c.get("id"),
                     "name": (c.get("name") or "")[:40],
                     "type": c.get("type"),
                     "text": ((c.get("text") or {}).get("characters") or "")[:30] if c.get("type") == "TEXT" else ""}
                    for c in ordered
                ],
            })
        for c in children:
            walk(c)

    if isinstance(tree, dict):
        walk(tree)
    return out


def derive_icons(tree: dict) -> list[dict]:
    """图标候选清单。规则分层，尺寸证据（0.9）> 命名证据（0.8）> 叶矢量兜底（0.6）。"""
    out: list[dict] = []

    def walk(n: dict) -> None:
        ntype = n.get("type") or ""
        name = n.get("name") or ""
        kids = [c for c in (n.get("children") or []) if isinstance(c, dict)]
        r = _rect(n)
        w, h = _num(r.get("w")), _num(r.get("h"))
        conf = None
        how = ""
        if ntype in ("VECTOR", "BOOLEAN_OPERATION", "INSTANCE") and _ICON_NAME_RE.search(name):
            conf, how = 0.8, "name-evidence"
        elif ntype in ("VECTOR", "BOOLEAN_OPERATION") and w and h and max(w, h) <= _ICON_MAX_SIDE and not kids:
            conf, how = 0.9, "size-evidence"
        elif ntype == "VECTOR" and not kids:
            conf, how = 0.6, "leaf-vector-fallback"
        if conf is not None:
            out.append({
                "nodeId": n.get("id"),
                "name": name[:40],
                "confidence": conf,
                "derivedBy": how,
                "rect": {"w": w, "h": h},
                "fill": _fill_hex(n),
            })
        for c in kids:
            walk(c)

    if isinstance(tree, dict):
        walk(tree)
    return out


def derive_dividers(tree: dict) -> list[dict]:
    """分隔线候选：极端宽高比（≥10:1 的细长条）+ 非透明纯色填充的 FRAME/RECTANGLE。"""
    out: list[dict] = []

    def walk(n: dict) -> None:
        if (n.get("type") or "") in ("FRAME", "RECTANGLE"):
            r = _rect(n)
            w, h = _num(r.get("w")), _num(r.get("h"))
            if w and h:
                long_side, short_side = max(w, h), min(w, h)
                if short_side <= 2 and long_side >= 20 and long_side / max(short_side, 0.5) >= 10:
                    fill = _fill_hex(n)
                    if fill:
                        out.append({
                            "nodeId": n.get("id"),
                            "name": (n.get("name") or "")[:40],
                            "orientation": "horizontal" if w >= h else "vertical",
                            "rect": {"w": w, "h": h, "x": _num(r.get("x")), "y": _num(r.get("y"))},
                            "fill": fill,
                        })
        for c in (n.get("children") or []):
            if isinstance(c, dict):
                walk(c)

    if isinstance(tree, dict):
        walk(tree)
    return out


def build_inventory(design_payload: dict) -> dict:
    tree = design_payload.get("tree") or design_payload.get("document") or {}
    unrecognized: list[dict] = []
    counters = {"order": 0, "icons": 0, "dividers": 0}

    def safe(fn, *args):
        try:
            result = fn(*args)
            return result
        except Exception as exc:  # noqa: BLE001 —— 单项失败绝不拖垮整份盘点
            unrecognized.append({"step": fn.__name__, "error": str(exc)[:120]})
            return []

    order = safe(derive_order, tree)
    icons = safe(derive_icons, tree)
    dividers = safe(derive_dividers, tree)
    counters = {"order": len(order), "icons": len(icons), "dividers": len(dividers)}

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "source": "design_model.json",
        "order": order,
        "icons": icons,
        "dividers": dividers,
        "unrecognized": unrecognized,
        "stats": counters,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="设计侧盘点：节点树 → 顺序/图标/分隔线清单")
    ap.add_argument("--design", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    args = ap.parse_args()

    try:
        payload = json.loads(args.design.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": f"设计模型读取失败: {exc}"}, ensure_ascii=False))
        return 1

    inventory = build_inventory(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(inventory, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"stats": inventory["stats"],
                      "unrecognized": len(inventory["unrecognized"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
