"""按条件格汇总页面性能实测数据，判定劣化并导出 CSV 矩阵；不采集、不替代 trace。

单个 trace 只说明一种条件下的表现。本工具把多条件格（网络/CPU/视口/冷热加载）
的实测值放到同一张表里对比，避免"某一格达标"被读成"页面性能达标"。

用法：
    python perf_matrix.py <矩阵数据.json> --output <新矩阵.csv>

约束：
- 阈值表必须给出 source（来源），不允许无出处的阈值。
- 指标值只能是数字或 null；null 表示未采到，不当 0，也不参与达标判定。
- 每格判定分两层：overall 只看指标（exceed/partial/pass），conclusion 结合证据。
  证据未通过校验（无文件、缺哈希、哈希不符）时 conclusion 一律为"待核实"，
  指标超标也不得作为已证实结论。
- 同一条件组合只允许出现一次，重复即报错，防止同条件多样本混算。
- 输出为 UTF-8-sig CSV，父目录需存在，拒绝覆盖；退出码 0 成功、2 数据或工具错误。
"""
import argparse
import copy
import csv
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

SCHEMA_VERSION = "1.0"
METRICS = ("lcp_ms", "fcp_ms", "ttfb_ms", "render_delay_ms", "cls", "inp_ms", "longtask_ms")
LOAD_TYPES = {"cold", "warm"}
EVIDENCE_STATES = {"verified", "no-evidence", "incomplete", "file-missing", "mismatch"}
SCOPE = ("条件格结果只代表所列网络/CPU/视口/冷热组合下的表现，不是真实用户分位数；"
         "单次 lab 采样不外推总体，未采指标保持未知而不是0")
FIELDS = ["cell_id", "viewport_w", "viewport_h", "mobile", "network", "cpu_throttling",
          "load_type", "lcp_ms", "lcp_ms_verdict", "fcp_ms", "fcp_ms_verdict", "ttfb_ms",
          "ttfb_ms_verdict", "render_delay_ms", "render_delay_ms_verdict", "cls", "cls_verdict",
          "inp_ms", "inp_ms_verdict", "longtask_ms", "longtask_ms_verdict", "overall",
          "conclusion", "measured", "evidence_status", "trace_file", "trace_sha256", "notes"]


def _origin(value, label):
    if not isinstance(value, str):
        raise ValueError(f"{label}: 必须是绝对URL字符串")
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError(f"{label}: 必须是http/https绝对URL")
    if parts.username or parts.password:
        raise ValueError(f"{label}: 不能包含凭据")
    return value


def _text(value, label, required=True):
    if not isinstance(value, str):
        raise ValueError(f"{label}: 必须是字符串")
    if required and not value.strip():
        raise ValueError(f"{label}: 不能为空")
    return value


def _number(value, label, minimum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}: 必须是数字")
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError(f"{label}: 不能是NaN或无穷")
    if minimum is not None and value < minimum:
        raise ValueError(f"{label}: 不能小于{minimum}")
    return value


def _thresholds(raw):
    if not isinstance(raw, dict):
        raise ValueError("thresholds: 必须是对象")
    result = {}
    for key, value in raw.items():
        if key == "source":
            continue
        if key not in METRICS:
            raise ValueError(f"thresholds: 未知指标 {key}")
        result[key] = _number(value, f"thresholds.{key}", 0)
    _text(raw.get("source"), "thresholds.source")
    if not result:
        raise ValueError("thresholds: 至少需要一个指标阈值")
    return {"values": result, "source": raw["source"]}


def _metrics(raw, label, limits):
    if not isinstance(raw, dict):
        raise ValueError(f"{label}: 必须是对象")
    values = {}
    verdicts = {}
    for key, value in raw.items():
        if key not in METRICS:
            raise ValueError(f"{label}: 未知指标 {key}")
        if value is None:
            values[key] = None
            verdicts[key] = "not-collected"
            continue
        values[key] = _number(value, f"{label}.{key}", 0)
        if key not in limits:
            verdicts[key] = "not-judged"
        elif values[key] > limits[key]:
            verdicts[key] = "exceed"
        else:
            verdicts[key] = "pass"
    return values, verdicts


def _evidence(raw, label):
    if raw is None:
        return {"status": "no-evidence", "trace_file": None, "sha256": None}
    if not isinstance(raw, dict):
        raise ValueError(f"{label}: 必须是对象")
    trace_file = raw.get("trace_file")
    digest = raw.get("sha256")
    if not trace_file and not digest:
        raise ValueError(f"{label}: 至少提供 trace_file 或 sha256")
    if trace_file is not None:
        _text(trace_file, f"{label}.trace_file")
    if digest is not None:
        _text(digest, f"{label}.sha256")
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest.lower()):
            raise ValueError(f"{label}.sha256: 必须是64位十六进制")
    if trace_file is None or digest is None:
        return {"status": "incomplete", "trace_file": trace_file, "sha256": digest}
    path = Path(trace_file)
    if not path.is_file():
        return {"status": "file-missing", "trace_file": trace_file, "sha256": digest}
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    status = "verified" if actual == digest.lower() else "mismatch"
    return {"status": status, "trace_file": trace_file, "sha256": digest}


def _cell(raw, label, limits):
    if not isinstance(raw, dict):
        raise ValueError(f"{label}: 必须是对象")
    ident = _text(raw.get("id"), f"{label}.id")
    viewport = raw.get("viewport")
    if not isinstance(viewport, dict):
        raise ValueError(f"{label}.viewport: 必须是对象")
    width = _number(viewport.get("width"), f"{label}.viewport.width", 1)
    height = _number(viewport.get("height"), f"{label}.viewport.height", 1)
    if type(viewport.get("mobile")) is not bool:
        raise ValueError(f"{label}.viewport.mobile: 必须为布尔值")
    network = _text(raw.get("network"), f"{label}.network")
    cpu = _number(raw.get("cpu_throttling"), f"{label}.cpu_throttling", 1)
    load_type = _text(raw.get("load_type"), f"{label}.load_type")
    if load_type not in LOAD_TYPES:
        raise ValueError(f"{label}.load_type: 只能是 cold 或 warm")
    values, verdicts = _metrics(raw.get("metrics"), f"{label}.metrics", limits)
    evidence = _evidence(raw.get("evidence"), f"{label}.evidence")
    notes = raw.get("notes") or ""
    _text(notes, f"{label}.notes", False)
    measured = evidence["status"] == "verified"
    if "exceed" in verdicts.values():
        overall = "exceed"
    elif "not-collected" in verdicts.values():
        overall = "partial"
    else:
        overall = "pass"
    return {
        "id": ident,
        "viewport": {"width": int(width), "height": int(height), "mobile": viewport["mobile"]},
        "network": network,
        "cpu_throttling": cpu,
        "load_type": load_type,
        "values": values,
        "verdicts": verdicts,
        "evidence": evidence,
        "evidence_status": evidence["status"],
        "measured": measured,
        "overall": overall,
        "conclusion": overall if measured else "待核实",
        "notes": notes,
    }


def summarize(data):
    """校验输入并给出每格判定与整体摘要；不写文件。"""
    if not isinstance(data, dict):
        raise ValueError("输入必须是JSON对象")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"schema_version 必须为 {SCHEMA_VERSION}")
    context = data.get("context")
    if not isinstance(context, dict):
        raise ValueError("context: 必须是对象")
    _origin(context.get("target_url"), "context.target_url")
    for key in ("application_version", "environment", "measured_at"):
        _text(context.get(key), f"context.{key}")
    thresholds = _thresholds(data.get("thresholds"))
    raw_cells = data.get("cells")
    if not isinstance(raw_cells, list) or not raw_cells:
        raise ValueError("cells: 必须是非空数组")
    cells = []
    seen_ids = set()
    seen_conditions = set()
    for index, raw in enumerate(raw_cells):
        item = _cell(raw, f"cells[{index}]", thresholds["values"])
        if item["id"] in seen_ids:
            raise ValueError(f"cells[{index}].id: 重复的 {item['id']}")
        seen_ids.add(item["id"])
        condition = (item["network"], item["cpu_throttling"], item["viewport"]["width"],
                     item["viewport"]["height"], item["load_type"])
        if condition in seen_conditions:
            raise ValueError(f"cells[{index}]: 条件组合重复，同一网络/CPU/视口/冷热只测一次")
        seen_conditions.add(condition)
        cells.append(item)
    not_collected = sorted({key for item in cells for key, verdict in item["verdicts"].items()
                            if verdict == "not-collected"})
    return {
        "schema_version": SCHEMA_VERSION,
        "context": copy.deepcopy(context),
        "thresholds": thresholds,
        "cells_total": len(cells),
        "measured_cells": sum(1 for item in cells if item["measured"]),
        "unverified_cells": sum(1 for item in cells if not item["measured"]),
        "exceed_cells": sum(1 for item in cells if item["measured"] and item["overall"] == "exceed"),
        "partial_cells": sum(1 for item in cells if item["measured"] and item["overall"] == "partial"),
        "pass_cells": sum(1 for item in cells if item["measured"] and item["overall"] == "pass"),
        "evidence_states": dict(sorted({state: sum(1 for item in cells if item["evidence_status"] == state)
                                       for state in EVIDENCE_STATES}.items())),
        "not_collected_metrics": not_collected,
        "cells": cells,
        "scope": SCOPE,
    }


def _guard(value):
    if isinstance(value, str) and (value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n"))):
        return "'" + value
    return value


def export_csv(data, output):
    """先完整校验，再独占新建 CSV；同一条格只出现一行。"""
    report = summarize(data)
    output = Path(output)
    if output.suffix.lower() != ".csv" or not output.parent.is_dir():
        raise ValueError("输出必须为 CSV，父目录必须已存在")
    with output.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        for item in report["cells"]:
            row = {
                "cell_id": item["id"],
                "viewport_w": item["viewport"]["width"],
                "viewport_h": item["viewport"]["height"],
                "mobile": item["viewport"]["mobile"],
                "network": item["network"],
                "cpu_throttling": item["cpu_throttling"],
                "load_type": item["load_type"],
                "overall": item["overall"],
                "conclusion": item["conclusion"],
                "measured": item["measured"],
                "evidence_status": item["evidence_status"],
                "trace_file": item["evidence"]["trace_file"] or "",
                "trace_sha256": item["evidence"]["sha256"] or "",
                "notes": item["notes"],
            }
            for metric in ("lcp_ms", "fcp_ms", "ttfb_ms", "render_delay_ms", "cls", "inp_ms", "longtask_ms"):
                row[metric] = "" if item["values"].get(metric) is None else item["values"][metric]
                row[metric + "_verdict"] = item["verdicts"].get(metric, "")
            writer.writerow({key: _guard(value) for key, value in row.items()})
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="汇总页面性能条件格矩阵并导出 CSV")
    parser.add_argument("matrix")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        data = json.loads(Path(args.matrix).read_text(encoding="utf-8-sig"))
        report = export_csv(data, args.output)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"错误: {error}", file=sys.stderr)
        return 2
    kept = {key: value for key, value in report.items() if key != "cells"}
    print(json.dumps(kept, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
