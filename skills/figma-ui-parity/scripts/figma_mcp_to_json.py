#!/usr/bin/env python3
"""将 Figma MCP 的 YAML-ish 文本安全地转换成 Figma-like JSON。"""
from __future__ import annotations

import argparse
import ast
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any

from extract_design_fields import font_weight

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

NODE_RE = re.compile(r'^(?P<indent> *)\[(?P<kind>[^]]+)\]\s+(?:(?P<name>"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')\s+)?#(?P<id>\S+)(?P<attrs>.*)$')
ATTR_START_RE = re.compile(r"(?P<key>[A-Za-z][\w-]*)=")
HEX_RE = re.compile(r"^#([0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
RGBA_RE = re.compile(r"^rgba?\(([^)]+)\)$", re.I)
IDENT_RE = re.compile(r"^[A-Za-z_][\w-]*$")


def _yaml(text: str) -> Any:
    if yaml is None:
        raise RuntimeError("需要 PyYAML 才能解析 Figma MCP 的 YAML-ish 返回")

    # SafeLoader 的 bool resolver 会把文案 on/off 转成 True/False；只移除
    # bool 隐式解析，保留安全 loader 的其它类型解析能力。
    class TextSafeLoader(yaml.SafeLoader):
        pass
    TextSafeLoader.yaml_implicit_resolvers = copy.deepcopy(yaml.SafeLoader.yaml_implicit_resolvers)
    for ch, resolvers in list(TextSafeLoader.yaml_implicit_resolvers.items()):
        TextSafeLoader.yaml_implicit_resolvers[ch] = [
            (tag, rx) for tag, rx in resolvers if tag != "tag:yaml.org,2002:bool"
        ]
    return yaml.load(text, Loader=TextSafeLoader)


def _section(text: str, name: str, next_name: str | None = None) -> str:
    headers = list(re.finditer(r"^(NAME|GLOBAL_VARS|ELEMENTS|NODES):[^\S\r\n]*", text, re.M))
    names = [m.group(1) for m in headers]
    order = {key: i for i, key in enumerate(("NAME", "GLOBAL_VARS", "ELEMENTS", "NODES"))}
    if len(names) != len(set(names)) or any(order[a] >= order[b] for a, b in zip(names, names[1:])):
        raise ValueError("分段标题重复或顺序错误")
    for index, header in enumerate(headers):
        if header.group(1) == name:
            end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
            return text[header.end():end].strip("\r\n")
    return ""


def _split_attrs(text: str) -> list[tuple[str, str]]:
    """只在引号、括号外切分 key=value 属性。"""
    starts: list[tuple[int, str, int]] = []
    quote: str | None = None
    escaped = False
    depth = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            i += 1
            continue
        if ch in "([{":
            depth += 1
            i += 1
            continue
        if ch in ")]}":
            depth = max(0, depth - 1)
            i += 1
            continue
        if depth == 0:
            match = ATTR_START_RE.match(text, i)
            if match:
                starts.append((i, match.group("key"), match.end()))
                i = match.end()
                continue
        i += 1
    out: list[tuple[str, str]] = []
    for idx, (_, key, value_start) in enumerate(starts):
        value_end = starts[idx + 1][0] if idx + 1 < len(starts) else len(text)
        out.append((key, text[value_start:value_end].strip()))
    return out


def _parse_value(raw: str) -> Any:
    raw = raw.strip()
    if not raw:
        return None
    if raw.startswith("{") or raw.startswith("["):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            try:
                return ast.literal_eval(raw)
            except (ValueError, SyntaxError):
                return raw
    if raw.startswith(('"', "'")) and raw.endswith(("\"", "'")):
        try:
            return json.loads(raw) if raw.startswith('"') else ast.literal_eval(raw)
        except (json.JSONDecodeError, ValueError, SyntaxError):
            return raw[1:-1]
    return raw


def _clean_name(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    return _parse_value(value) if value[:1] in ('"', "'") else value


def _parse_node_line(line: str) -> tuple[int, str, str | None, str, str]:
    indent_match = re.match(r"^(\s*)\[([^]]+)\](.*)$", line)
    if not indent_match:
        raise ValueError(f"节点行格式错误: {line.strip()}")
    indent = len(indent_match.group(1))
    kind = indent_match.group(2).strip()
    rest = indent_match.group(3)
    quote: str | None = None
    escaped = False
    hash_pos = None
    i = 0
    while i < len(rest):
        ch = rest[i]
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
        elif ch in "'\\\"":
            quote = ch
        elif ch == "#" and (i == 0 or rest[i - 1].isspace()):
            candidate = re.match(r"#([^\s]+)", rest[i:])
            if candidate:
                hash_pos = i
                break
        i += 1
    if hash_pos is None:
        raise ValueError(f"节点行缺少 ID: {line.strip()}")
    before = rest[:hash_pos].strip()
    id_match = re.match(r"#([^\s]+)(.*)$", rest[hash_pos:])
    if not id_match:
        raise ValueError(f"节点行 ID 格式错误: {line.strip()}")
    node_id = id_match.group(1)
    attrs = id_match.group(2)
    name = _clean_name(before) if before else None
    return indent, kind, name, node_id, attrs


def parse_nodes(text: str) -> list[dict]:
    roots: list[dict] = []
    stack: list[tuple[int, dict]] = []
    seen: set[str] = set()
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        indent, kind, name, node_id, attrs = _parse_node_line(line)
        if node_id in seen:
            raise ValueError(f"节点 ID 重复: {node_id}")
        seen.add(node_id)
        node = {"id": node_id, "type": kind, "name": name, "_line": lineno}
        for key, raw in _split_attrs(attrs or ""):
            node[key] = _parse_value(raw)
        node.setdefault("children", [])
        while stack and indent <= stack[-1][0]:
            stack.pop()
        if stack:
            stack[-1][1]["children"].append(node)
        else:
            roots.append(node)
        stack.append((indent, node))
    return roots


def _css_num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.match(r"^(-?\d+(?:\.\d+)?)(?:px)?$", str(value).strip(), re.I)
    return float(m.group(1)) if m else None


def _css_unit(value: Any) -> tuple[str, float] | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "PIXELS", float(value)
    raw = str(value).strip().lower()
    m = re.match(r"^(-?\d+(?:\.\d+)?)(px|%|em)$", raw)
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2)
    return {"px": "PIXELS", "%": "PERCENT", "em": "EM"}[unit], n


def _padding(value: Any) -> tuple[float | None, float | None, float | None, float | None]:
    if value is None:
        return None, None, None, None
    parts = str(value).split()
    nums = [_css_num(x) for x in parts]
    if not 1 <= len(nums) <= 4 or any(n is None for n in nums):
        return None, None, None, None
    if len(nums) == 1:
        return nums[0], nums[0], nums[0], nums[0]
    if len(nums) == 2:
        return nums[0], nums[1], nums[0], nums[1]
    if len(nums) == 3:
        return nums[0], nums[1], nums[2], nums[1]
    if len(nums) >= 4:
        return tuple(nums[:4])
    return None, None, None, None


def _color(value: str) -> dict | None:
    value = str(value).strip()
    m = HEX_RE.match(value)
    if m:
        raw = m.group(1)
        alpha = int(raw[6:], 16) / 255 if len(raw) == 8 else 1
        return {"r": int(raw[:2], 16) / 255, "g": int(raw[2:4], 16) / 255,
                "b": int(raw[4:6], 16) / 255, "a": round(alpha, 4)}
    m = RGBA_RE.match(value)
    if m:
        parts = [x.strip() for x in m.group(1).split(",")]
        if len(parts) >= 3:
            nums = [float(x[:-1]) * 2.55 if x.endswith("%") else float(x) for x in parts[:3]]
            alpha = float(parts[3][:-1]) / 100 if len(parts) > 3 and parts[3].endswith("%") else (float(parts[3]) if len(parts) > 3 else 1)
            return {"r": nums[0] / 255, "g": nums[1] / 255, "b": nums[2] / 255, "a": round(alpha, 4)}
    return None


def _resolve(ref: Any, globals_: dict[str, Any], field: str = "") -> Any:
    if isinstance(ref, str) and ref in globals_:
        value = globals_[ref]
        return value[0] if isinstance(value, list) and len(value) == 1 else value
    if isinstance(ref, str) and IDENT_RE.match(ref) and field:
        raise ValueError(f"缺失全局引用: {ref}")
    return ref


def _paint(value: Any, globals_: dict[str, Any], uncertainties: list[str]) -> list[dict]:
    value = _resolve(value, globals_, "fills/strokes")
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    out: list[dict] = []
    for item in values:
        if isinstance(item, str):
            color = _color(item)
            if color:
                alpha = color.pop("a", 1)
                color["a"] = 1
                out.append({"type": "SOLID", "color": color, "opacity": alpha, "visible": True})
            else:
                out.append({"type": "UNSUPPORTED", "raw": item, "visible": True})
                uncertainties.append(f"unsupported paint: {item}")
        elif isinstance(item, dict):
            kind = str(item.get("type", "")).upper()
            if kind == "IMAGE":
                out.append(copy.deepcopy(item))
            elif isinstance(item.get("color"), dict):
                paint = copy.deepcopy(item)
                color = paint["color"]
                alpha = color.get("a", 1)
                color["a"] = 1
                paint["opacity"] = paint.get("opacity", 1) * alpha
                out.append(paint)
            elif kind in {"SOLID", "GRADIENT_LINEAR", "GRADIENT_RADIAL", "GRADIENT_ANGULAR", "GRADIENT_DIAMOND", "IMAGE"} or item:
                out.append({"type": "UNSUPPORTED", "raw": copy.deepcopy(item), "visible": item.get("visible", True)})
                uncertainties.append(f"unsupported paint type: {kind or 'unknown'}")
        else:
            out.append({"type": "UNSUPPORTED", "raw": item, "visible": True})
            uncertainties.append("unsupported paint value")
    for paint in out:
        if isinstance(paint.get("visible"), str) and paint["visible"].lower() in ("true", "false"):
            paint["visible"] = paint["visible"].lower() == "true"
    return out


def _style(value: Any, globals_: dict[str, Any], font_size: float | None = None) -> dict:
    value = _resolve(value, globals_, "textStyle") if value is not None else {}
    if not isinstance(value, dict):
        raise ValueError("textStyle 必须是对象")
    out = copy.deepcopy(value)
    if "fontSize" in out:
        out["fontSize"] = _css_num(out["fontSize"])
    if "fontStyle" in out:
        font_name = out.get("fontName") if isinstance(out.get("fontName"), dict) else {}
        font_name = dict(font_name)
        font_name.setdefault("style", out["fontStyle"])
        if "fontFamily" in out:
            font_name.setdefault("family", out["fontFamily"])
        out["fontName"] = font_name
    if str(out.get("lineHeight", "")).lower() == "auto":
        out["lineHeight"] = {"unit": "AUTO"}
    if "lineHeight" in out:
        parsed = _css_unit(out["lineHeight"])
        if parsed:
            unit, number = parsed
            if unit == "EM":
                if font_size is not None:
                    out["lineHeight"] = {"unit": "PIXELS", "value": round(number * font_size, 4)}
            else:
                out["lineHeight"] = {"unit": unit, "value": number}
    if "letterSpacing" in out:
        parsed = _css_unit(out["letterSpacing"])
        if parsed:
            unit, number = parsed
            if unit == "EM":
                out["letterSpacing"] = {"unit": "PERCENT", "value": number * 100}
            else:
                out["letterSpacing"] = {"unit": unit, "value": number}
    for key in ("lineHeight", "letterSpacing"):
        if key in out and (not isinstance(out[key], dict) or out[key].get("unit") not in ("PIXELS", "PERCENT", "AUTO")):
            out.pop(key)
    if "textAlignHorizontal" in out:
        out["textAlignHorizontal"] = str(out["textAlignHorizontal"]).upper()
    return out


def _layout(value: Any, globals_: dict[str, Any]) -> dict:
    value = _resolve(value, globals_, "layout") if value is not None else {}
    if not isinstance(value, dict):
        raise ValueError("layout 必须是对象")
    out = copy.deepcopy(value)
    mode = str(out.pop("mode", "none")).lower()
    out["layoutMode"] = {"row": "HORIZONTAL", "column": "VERTICAL"}.get(mode, "NONE")
    if "padding" in out:
        top, right, bottom, left = _padding(out.pop("padding"))
        out.update({"paddingTop": top, "paddingRight": right, "paddingBottom": bottom, "paddingLeft": left})
    if "gap" in out:
        gap = _css_num(out.pop("gap"))
        if gap is not None:
            out["itemSpacing"] = gap
    if "alignItems" in out:
        align = str(out.pop("alignItems")).upper()
        out["counterAxisAlignItems"] = {"CENTER": "CENTER", "FLEX-START": "MIN", "FLEX-END": "MAX"}.get(align, align)
    dims = out.pop("dimensions", None)
    out["_dimensions"] = {}
    if isinstance(dims, dict):
        for key in ("width", "height"):
            if key in dims:
                number = _css_num(dims[key])
                if number is not None:
                    out["_dimensions"][key] = number
    out.pop("sizing", None)
    return out


def _effects(value: Any, globals_: dict[str, Any], uncertainties: list[str]) -> list[dict]:
    value = _resolve(value, globals_, "effects") if value is not None else []
    values = value if isinstance(value, list) else [value]
    out = []
    for item in values:
        if isinstance(item, dict) and item.get("type") in ("DROP_SHADOW", "INNER_SHADOW"):
            out.append(copy.deepcopy(item))
        elif item is not None:
            out.append({"type": "UNSUPPORTED", "raw": item})
            uncertainties.append(f"unsupported effect: {item}")
    for effect in out:
        if isinstance(effect.get("visible"), str) and effect["visible"].lower() in ("true", "false"):
            effect["visible"] = effect["visible"].lower() == "true"
    return out


def convert_node(node: dict, globals_: dict[str, Any], elements: dict[str, Any], parent_origin: dict | None = None) -> dict:
    template_name = node.get("template")
    if template_name:
        if template_name not in elements or not isinstance(elements[template_name], dict):
            raise ValueError(f"缺失模板: {template_name}")
        template = copy.deepcopy(elements[template_name])
    else:
        template = {}
    merged = template
    for key, value in node.items():
        if key not in ("children", "template") and value is not None:
            merged[key] = value
    merged["children"] = node.get("children", [])
    uncertainties: list[str] = []
    out = {k: v for k, v in merged.items() if not k.startswith("_") and k not in ("template", "children", "layout", "textStyle", "fills", "strokes", "effects", "text")}
    out["type"] = {"IMAGE-SVG": "VECTOR"}.get(str(merged.get("type")), merged.get("type"))
    layout = _layout(merged.get("layout"), globals_)
    out.update({k: v for k, v in layout.items() if k != "_dimensions"})
    dims = layout.get("_dimensions", {})
    if dims:
        out["absoluteBoundingBox"] = {**dims}
    style_input = _resolve(merged.get("textStyle"), globals_, "textStyle")
    font_size = _css_num(style_input.get("fontSize")) if isinstance(style_input, dict) else None
    style = _style(style_input, globals_, font_size)
    if style:
        out["style"] = style
    out["fills"] = _paint(merged.get("fills"), globals_, uncertainties)
    out["strokes"] = _paint(merged.get("strokes"), globals_, uncertainties)
    out["effects"] = _effects(merged.get("effects"), globals_, uncertainties)
    for key in ("borderRadius", "cornerRadius", "strokeWeight", "opacity", "rotation"):
        if key in out:
            parsed = _css_num(out[key])
            out[key] = parsed
            if parsed is None:
                uncertainties.append(f"unsupported numeric value: {key}={merged.get(key)}")
    if isinstance(out.get("visible"), str) and out["visible"].lower() in ("true", "false"):
        out["visible"] = out["visible"].lower() == "true"
    if merged.get("text") is not None:
        out["characters"] = str(merged["text"])
    elif isinstance(merged.get("content"), list):
        texts = [x.get("text", "") for x in merged["content"] if isinstance(x, dict) and "text" in x]
        if texts:
            out["characters"] = "".join(texts)
    if "borderRadius" in out:
        out["cornerRadius"] = out.pop("borderRadius")
    layout_input = _resolve(merged.get("layout"), globals_, "layout") or {}
    availability: dict[str, bool] = {
        "text.characters": "characters" in out,
        "rect.x": False, "rect.y": False,
        "rect.w": "width" in dims, "rect.h": "height" in dims,
        "fills": "fills" in merged, "strokes": "strokes" in merged, "effects": "effects" in merged,
        "layout.padding": all(layout.get(k) is not None for k in ("paddingTop", "paddingRight", "paddingBottom", "paddingLeft")),
        "layout.itemSpacing": _css_num(layout.get("itemSpacing")) is not None,
    }
    for key in ("opacity", "rotation", "visible"):
        availability[key] = key in merged and out.get(key) is not None
    availability["radius"] = _css_num(out.get("cornerRadius")) is not None
    if isinstance(style_input, dict):
        for key in ("fontSize", "fontWeight", "fontFamily", "fontName", "lineHeight", "letterSpacing", "textAlignHorizontal", "textCase", "fontPostScriptName", "textAlignVertical", "paragraphSpacing"):
            availability["text." + key] = key in style_input or (key == "fontName" and "fontStyle" in style_input)
        availability["text.fontSize"] = style.get("fontSize") is not None
        availability["text.fontWeight"] = font_weight(style) is not None
        availability["text.fontFamily"] = bool(style.get("fontFamily") or (style.get("fontName") or {}).get("family"))
        for key in ("lineHeight", "letterSpacing"):
            availability["text." + key] = key in style and not (
                key == "letterSpacing" and style[key].get("unit") == "PERCENT" and not font_size)
            if key in style_input and not availability["text." + key]:
                uncertainties.append(f"unsupported text unit: {key}={style_input[key]}")
    if isinstance(layout_input, dict):
        for key, field in (("padding", "layout.padding"), ("gap", "layout.itemSpacing")):
            if key in layout_input and not availability[field]:
                uncertainties.append(f"unsupported layout unit: {key}={layout_input[key]}")
    if isinstance(style_input, dict) and "fontSize" in style_input and not availability["text.fontSize"]:
        uncertainties.append(f"unsupported fontSize: {style_input['fontSize']}")
    for field in ("fills", "strokes", "effects"):
        if any(p.get("type") == "UNSUPPORTED" for p in out[field]):
            availability[field] = False
    out["pluginData"] = {"source": "figma-mcp-yaml-ish", "evidenceLine": node.get("_line"),
                          "coordinates": "unavailable", "geometry": "unavailable",
                          "propertyAvailability": availability, "uncertainties": uncertainties}
    children = [convert_node(c, globals_, elements) for c in merged.get("children", [])]
    if children:
        out["children"] = children
    return out


def _find_nodes(roots: list[dict], node_id: str) -> list[dict]:
    found = []
    def visit(node: dict):
        if node.get("id") == node_id:
            found.append(node)
        for child in node.get("children", []):
            visit(child)
    for root in roots:
        visit(root)
    return found


def convert(text: str, root_name: str | None = None, node_id: str | None = None) -> dict:
    global_vars = _yaml(_section(text, "GLOBAL_VARS", "ELEMENTS")) or {}
    elements = _yaml(_section(text, "ELEMENTS", "NODES")) or {}
    if not isinstance(global_vars, dict) or not isinstance(elements, dict):
        raise ValueError("GLOBAL_VARS/ELEMENTS 必须是对象")
    roots = parse_nodes(_section(text, "NODES"))
    if not roots:
        raise ValueError("NODES 段没有可解析的节点")
    if root_name and node_id:
        raise ValueError("--root-name 与 --node-id 不能同时使用")
    if node_id:
        selected = _find_nodes(roots, node_id)
        if len(selected) != 1:
            raise ValueError(f"节点 ID 不存在或不唯一: {node_id}")
        root = selected[0]
    else:
        frames = []
        for candidate in roots:
            frames.extend(candidate.get("children", []) if candidate.get("type") == "CANVAS" else [candidate])
        frames = [n for n in frames if n.get("type") == "FRAME"]
        if root_name:
            selected = [n for n in frames if n.get("name") == root_name]
            if len(selected) != 1:
                raise ValueError(f"root-name 不存在或不唯一: {root_name}")
            root = selected[0]
        elif len(frames) != 1:
            raise ValueError("存在多个或零个 Frame，请使用 --root-name 或 --node-id")
        else:
            root = frames[0]
    document = convert_node(root, global_vars, elements)
    return {"name": _yaml(_section(text, "NAME", "GLOBAL_VARS")), "document": document,
            "meta": {"sourceFormat": "figma-mcp-yaml-ish", "coordinateBasis": "unavailable",
                     "globalVars": len(global_vars), "parsedRoots": len(roots)}}


def main() -> int:
    ap = argparse.ArgumentParser(description="把 Figma MCP YAML-ish 返回转成 Figma-like JSON")
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--root-name", default=None, help="选择页面级 Frame 名称")
    ap.add_argument("--node-id", default=None, help="选择任意节点 ID")
    args = ap.parse_args()
    try:
        payload = convert(args.input.read_text(encoding="utf-8"), args.root_name, args.node_id)
    except (OSError, ValueError, RuntimeError, TypeError) as exc:
        print("转换失败: {}".format(exc), file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(args.output), "root": payload["document"].get("name"), "meta": payload["meta"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
