#!/usr/bin/env python3
"""input_inventory.py — 任务载荷盘点（v1.50）。

只回答三件事，不判断业务、不压结论上限：
1. **识别**：用户提供的材料里有没有高价值信号——Figma 设计稿 URL/导出物、
   接口契约（OpenAPI/Swagger/契约文件）、接口抓包（.har）；
2. **核对**：每一项是否登记了处置（`consumed_by` 指名能力块，或 `declined`+理由）；
3. **区分**：有材料但取不到（环境通道 missing）与有材料但没用（无处置），是两类
   不同缺口——前者记 dataMissing 口径，后者才是静默漏测。

压档不在这里做：`phase_gate.py check-verdict` 读 `gaps` 里的 `unconsumed_input`
把结论上限压到待核实；`declined` 带理由与 `other` 类不压档。

用法：
    python scripts/input_inventory.py --inputs <inputs.json> --out <inventory.json> \
        [--capabilities <runtime-capabilities.json>]

inputs.json：
    {"schema_version": "1.0",
     "items": [{"source": "https://www.figma.com/design/<key>/x", "disposal": null},
               {"source": "<契约文件绝对路径>",
                "disposal": {"declined": true, "reason": "旧版契约，本轮不审"}}]}

退出码：0 盘点完成（含 gaps）；2 输入错误。
"""
import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "input-inventory/1"
HIGH_VALUE = ("figma_design", "api_contract", "api_trace")

FIGMA_URL_RE = re.compile(r"https?://(?:www\.)?figma\.com/(?:file|design)/", re.IGNORECASE)
CONTRACT_HINT_RE = re.compile(r"\b(openapi|swagger|contract)\b", re.IGNORECASE)
CONTRACT_BODY_RE = re.compile(r'"(openapi|swagger|paths)"')
MAX_SCAN_BYTES = 5 * 1024 * 1024


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _classify(source):
    """把一条输入归到 figma_design / api_contract / api_trace / other。

    文件优先读内容（JSON/YAML 里出现 openapi/swagger/paths 才算契约，避免把
    一个恰好叫 api.md 的普通文档误判）；读不到再退回文件名弱信号。
    """
    text = str(source or "").strip()
    if not text:
        return "other"
    if FIGMA_URL_RE.search(text):
        return "figma_design"
    path = Path(text)
    suffix = path.suffix.lower()
    if suffix == ".fig":
        return "figma_design"
    if suffix == ".har":
        return "api_trace"
    if path.is_file():
        try:
            # 大文件只读前缀：OpenAPI 契约常 >5MB，整读会拖慢盘点；但只读前 64KB
            # 已足够命中 "openapi"/"swagger"/"paths" 这几个顶层键。
            if path.stat().st_size <= MAX_SCAN_BYTES:
                body = path.read_text(encoding="utf-8-sig", errors="ignore")
            else:
                with path.open("r", encoding="utf-8-sig", errors="ignore") as stream:
                    body = stream.read(65536)
            if CONTRACT_BODY_RE.search(body) or (
                    suffix in {".yaml", ".yml"} and CONTRACT_HINT_RE.search(body)):
                return "api_contract"
        except OSError:
            pass
        if CONTRACT_HINT_RE.search(path.name):
            return "api_contract"
        return "other"
    if CONTRACT_HINT_RE.search(text):
        return "api_contract"
    return "other"


def _disposal_state(disposal):
    """归一处置：返回 (state, gap)。state ∈ consumed/declined/unconsumed。"""
    if not isinstance(disposal, dict):
        return "unconsumed", None
    if disposal.get("declined") is True:
        reason = str(disposal.get("reason") or "").strip()
        if not reason:
            return "unconsumed", {
                "code": "declined_without_reason",
                "message": "declined 未写理由：不适用/不消费必须有可核对的理由"}
        return "declined", None
    consumer = str(disposal.get("consumed_by") or "").strip()
    if consumer:
        return "consumed", None
    return "unconsumed", None


def build_inventory(inputs, capabilities=None):
    """盘点并返回 inventory 结构；输入结构错误抛 ValueError。"""
    if not isinstance(inputs, dict):
        raise ValueError("inputs 必须为 JSON 对象")
    if inputs.get("schema_version") != "1.0":
        raise ValueError("inputs.schema_version 必须为字符串 1.0")
    items = inputs.get("items")
    if not isinstance(items, list):
        raise ValueError("inputs.items 必须为数组")

    channels = ((capabilities or {}).get("channels") or {})
    design_state = (channels.get("design_data") or {}).get("state")

    rows, gaps = [], []
    for index, item in enumerate(items):
        name = f"items[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{name} 必须为对象")
        source = str(item.get("source") or "").strip()
        if not source:
            raise ValueError(f"{name}.source 必须为非空字符串")
        detected = str(item.get("detected") or "").strip() or _classify(source)
        if detected not in ("figma_design", "api_contract", "api_trace", "other"):
            raise ValueError(f"{name}.detected 非法：{detected!r}")
        state, gap = _disposal_state(item.get("disposal"))
        if state == "unconsumed" and detected in HIGH_VALUE and gap is None:
            gap = {
                "code": "unconsumed_input",
                "message": f"{detected} 材料在场但未登记处置：{source}"
                           "（须指名 consumed_by，或 declined+理由）"}
        if gap:
            gaps.append(dict(gap, source=source, detected=detected))
        # 有设计材料但环境取不到数据：属 dataMissing 口径，披露而不压档
        if detected == "figma_design" and design_state is not None and design_state != "available":
            gaps.append({
                "code": "input_not_retrievable",
                "message": f"设计材料在场但 design_data 通道为 {design_state}："
                           "按 dataMissing 记缺口，不得写「还原度」",
                "source": source, "detected": detected, "severity": "disclose"})
        rows.append({
            "source": source,
            "detected": detected,
            "disposal": item.get("disposal"),
            "state": state,
        })

    return {
        "schema": SCHEMA,
        "generated_at": _now(),
        "items": rows,
        "gaps": gaps,
        "summary": {
            "total": len(rows),
            "recognized": sum(1 for row in rows if row["detected"] in HIGH_VALUE),
            "consumed": sum(1 for row in rows if row["state"] == "consumed"),
            "declined": sum(1 for row in rows if row["state"] == "declined"),
            "unconsumed": sum(1 for row in rows if row["state"] == "unconsumed"),
            "design_channel": design_state,
        },
        "meaning": "只核对材料识别与处置声明；不判定业务，不判结论上限（压档由 phase_gate check-verdict 执行）",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="任务载荷盘点：识别材料并核对处置")
    parser.add_argument("--inputs", required=True, help="inputs.json")
    parser.add_argument("--out", required=True, help="inventory 输出路径（JSON）")
    parser.add_argument("--capabilities", help="runtime-capabilities.json（可选，用于区分取不到与没用）")
    args = parser.parse_args(argv)

    inputs = json.loads(Path(args.inputs).read_text(encoding="utf-8-sig"))
    capabilities = None
    if args.capabilities:
        capabilities = json.loads(Path(args.capabilities).read_text(encoding="utf-8-sig"))
    inventory = build_inventory(inputs, capabilities)
    Path(args.out).write_text(json.dumps(inventory, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print(json.dumps({"out": args.out, "gaps": len(inventory["gaps"]),
                      "unconsumed": inventory["summary"]["unconsumed"]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
