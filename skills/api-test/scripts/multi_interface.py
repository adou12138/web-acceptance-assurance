# -*- coding: utf-8 -*-
"""执行多接口 cases manifest，并汇总每个子运行的真实结果。"""
import argparse
import csv
import hashlib
import json
import os
import secrets
import sys
import time
from pathlib import Path

try:
    import asset_runtime
    import case_coverage
except ImportError:  # 允许从仓库根目录以模块方式加载
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import asset_runtime
    import case_coverage


STATUSES = {"planned", "running", "completed", "failed"}
FIELDS = [
    "mode", "entry", "run_id", "status", "cases", "cases_hash", "passed",
    "failed", "errors", "skipped", "not_run", "verdicts", "reason",
]


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _canonical_cases_hash(doc):
    return asset_runtime.cases_hash(doc)


def load_manifest(path):
    path = Path(path).resolve()
    doc = _read_json(path)
    status = doc.get("status", "planned")
    if status not in STATUSES:
        raise ValueError("manifest.status 必须是 planned/running/completed/failed：%s" % path)
    if doc.get("mode") not in {"acceptance", "regression"}:
        raise ValueError("manifest.mode 必须是 acceptance 或 regression：%s" % path)
    entries = doc.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("manifest.entries 不得为空：%s" % path)
    return doc


def _resolve(base, value, label):
    if not value:
        return None
    result = (base / str(value)).resolve()
    if not result.is_file():
        raise ValueError("%s 文件不存在：%s" % (label, result))
    return result


def _validate_config(config_path, config_doc):
    """复用 flow 的配置校验器，保证预检标准与执行标准同源。"""
    import flow
    flow.validate_exec_config(config_doc, str(config_path))


def _case_dimensions(cases_doc):
    return {str(c.get("dimension") or "") for c in cases_doc.get("cases") or [] if isinstance(c, dict)}


def preflight(manifest_path):
    """一次性加载并校验所有 entries；任何失败都不执行其中一条。"""
    manifest_path = Path(manifest_path).resolve()
    doc = load_manifest(manifest_path)
    base = manifest_path.parent
    prepared, seen = [], set()
    errors = []
    for index, entry in enumerate(doc["entries"], 1):
        if not isinstance(entry, dict):
            errors.append("entries[%d] 不是对象" % index)
            continue
        raw_cases = entry.get("cases")
        try:
            cases_path = _resolve(base, raw_cases, "cases")
            key = str(cases_path).casefold()
            if key in seen:
                raise ValueError("重复引用 cases：%s" % cases_path)
            seen.add(key)
            input_bytes = {"cases": cases_path.read_bytes()}
            cases_doc = json.loads(input_bytes["cases"].decode("utf-8"))
            asset_runtime.validate_cases_doc(cases_doc)
            if not cases_doc["cases"]:
                raise ValueError("cases 不得为空")
            actual_hash = _canonical_cases_hash(cases_doc)
            expected_hash = str(entry.get("cases_hash") or "")
            if not expected_hash:
                raise ValueError("缺少 cases_hash：%s" % cases_path)
            if actual_hash != expected_hash:
                raise ValueError("cases_hash 不匹配：%s（实际 %s）" % (cases_path, actual_hash))
            config_path = _resolve(base, entry.get("config"), "config")
            baseline_path = _resolve(base, entry.get("baseline"), "baseline")
            if config_path:
                input_bytes["config"] = config_path.read_bytes()
                config_doc = json.loads(input_bytes["config"].decode("utf-8"))
                _validate_config(config_path, config_doc)
            if baseline_path:
                input_bytes["baseline"] = baseline_path.read_bytes()
                baseline_doc = json.loads(input_bytes["baseline"].decode("utf-8"))
                baseline_entries = baseline_doc.get("entries") if isinstance(baseline_doc, dict) else None
                if (not isinstance(baseline_entries, list) or not baseline_entries
                        or any(not isinstance(e, dict) or not e.get("case_id") for e in baseline_entries)):
                    raise ValueError("baseline 必须含非空有效 entries")
            only = entry.get("only")
            if isinstance(only, (list, tuple)):
                only = ",".join(str(x) for x in only)
            elif only is not None:
                only = str(only)
            if only:
                wanted = {d.strip() for d in only.split(",") if d.strip()}
                # 族匹配而非等值：AF 族用例的维度是 AF1/AF2/AF3，等值比对会让
                # only=AF 报「没有任何匹配维度」→ 整批零请求、全部 notRun，
                # 真实原因（维度族未认领）却被当成「manifest 配错」。
                _dims = _case_dimensions(cases_doc)
                if not wanted or not [
                        w for w in wanted
                        if any(case_coverage.dim_matches(d, "", w) for d in _dims)]:
                    raise ValueError("only=%s 在 cases 中没有任何匹配维度" % only)
            prepared.append({
                "index": index, "entry": entry, "cases": cases_path,
                "cases_doc": cases_doc, "cases_hash": actual_hash,
                "config": config_path, "baseline": baseline_path, "only": only,
                "input_bytes": input_bytes,
                "input_hashes": {key: hashlib.sha256(raw).hexdigest()
                                 for key, raw in input_bytes.items()},
            })
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            errors.append("entries[%d]: %s" % (index, exc))
    if errors:
        raise ValueError("manifest 预检失败（未执行任何接口）：\n- " + "\n- ".join(errors))
    return doc, prepared


def _new_parent(explicit, project=None):
    if explicit:
        path = Path(explicit).expanduser().resolve()
        # prepare_run_dir 同时负责检查空目录和创建目录。
        path, _ = asset_runtime.prepare_run_dir(out_dir=path)
        return path
    root = asset_runtime.default_asset_root()
    project = asset_runtime.safe_component(project or os.environ.get("WJX_PROJECT_NAME") or Path.cwd().name)
    path = root / project / "batches" / (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + secrets.token_hex(4))
    path, _ = asset_runtime.prepare_run_dir(out_dir=path)
    return path


def _flow_main(argv):
    import flow
    runner = getattr(flow, "main", None) or getattr(flow, "_main", None)
    if runner is None:
        raise RuntimeError("flow 未提供 main(argv) 接口")
    result = runner(argv)
    return 0 if result is None else int(result)


def _safe_run_dir(parent, index):
    # flow is the sole owner of the child directory occupancy claim.
    return parent / ("run-%03d" % index)


def _manifest_result(run_manifest, item, flow_code, reason=""):
    row = {"mode": item.get("mode", ""), "entry": item["index"],
           "run_id": "", "status": "failed", "cases": str(item["cases"]),
           "cases_hash": item["cases_hash"], "passed": 0, "failed": 0,
           "errors": 0, "skipped": 0, "not_run": 0, "verdicts": "{}",
           "reason": reason}
    if run_manifest is None:
        row["status"] = "notRun" if not reason else "failed"
        row["not_run"] = 1 if row["status"] == "notRun" else 0
        return row
    doc = _read_json(run_manifest)
    row["run_id"] = doc.get("run_id", "")
    row["status"] = doc.get("status", "failed")
    counts = ((doc.get("results") or {}).get("counts") or {})
    items = ((doc.get("results") or {}).get("items") or [])
    row["passed"] = int(counts.get("PASS", 0) or 0)
    row["failed"] = int(counts.get("FAIL", 0) or 0)
    row["errors"] = int(counts.get("ERROR", 0) or 0)
    row["skipped"] = int(counts.get("SKIPPED", 0) or 0)
    row["verdicts"] = json.dumps(counts, ensure_ascii=False, sort_keys=True)
    selection = doc.get("selection") or {}
    if selection.get("truncated"):
        row["not_run"] = int(selection.get("cap_dropped") or 0) or max(
            int(selection.get("requested") or 0) - int(selection.get("selected") or 0), 1)
    child_hash = str((doc.get("inputs") or {}).get("cases_hash") or "")
    if child_hash and child_hash != item["cases_hash"]:
        row["status"] = "failed"
        row["reason"] = "子运行实际使用的内容与清单哈希不一致"
        return row
    if flow_code or reason:
        row["status"] = "failed"
        if not reason:
            row["reason"] = "flow 返回 %s" % flow_code
    expected_ids = [c["id"] for c in item.get("cases_doc", {}).get("cases", [])]
    # flow owns profile/only/cap selection. Compare its actual selected snapshot,
    # not the unfiltered suite, while rejecting IDs outside the original suite.
    selected_path = Path(run_manifest).parent / "executed_cases.json"
    selection_error = ""
    if selected_path.is_file():
        selected = _read_json(selected_path)
        asset_runtime.validate_cases_doc(selected)
        selected_ids = [c["id"] for c in selected["cases"]]
        if not selected_ids or (expected_ids and not set(selected_ids).issubset(expected_ids)):
            selection_error = "执行快照为空或包含清单外用例"
        expected_ids = selected_ids
    actual_ids = [r.get("case_no") for r in items]
    if row["status"] == "completed" and (selection_error or not expected_ids or
            len(actual_ids) != len(expected_ids) or set(actual_ids) != set(expected_ids)
            or any(r.get("status") != "PASS" for r in items)
            or counts != {"PASS": len(items)}):
        row["status"] = "failed"
        row["reason"] = selection_error or "执行结果与本轮选定用例不完整匹配"
    if row["status"] == "planned":
        row["not_run"] = 1
    elif not items:
        row["not_run"] = 1
    return row


def _is_pass(row):
    return (row["status"] == "completed" and row["passed"] > 0 and
            row["failed"] == 0 and row["errors"] == 0 and row["skipped"] == 0 and
            row["not_run"] == 0 and json.loads(row["verdicts"]).keys() == {"PASS"})


def write_csv(rows, output):
    target = Path(output)
    if target.exists():
        raise FileExistsError("拒绝覆盖汇总 CSV：%s" % target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def summarize(manifests, mode="acceptance"):
    """兼容旧 API：读取实际 run_manifest，而非把输入当成已验收结果。"""
    rows = []
    for path in manifests:
        doc = _read_json(path)
        cases = doc.get("inputs", {}).get("cases") or doc.get("cases") or ""
        cases_path = Path(cases) if cases else Path(path).parent / "generated_cases.json"
        if not cases_path.is_file():
            raise ValueError("manifest 缺少存在的 cases 引用：%s" % path)
        cases_doc = _read_json(cases_path)
        item = {"index": len(rows) + 1, "cases": cases_path,
                "cases_doc": cases_doc, "cases_hash": _canonical_cases_hash(cases_doc)}
        rows.append(_manifest_result(Path(path), item, 0))
        rows[-1]["mode"] = mode
    return rows


def _ensure_output_target(target):
    """在发任何请求前确认汇总 CSV 可写，并预留父目录链。"""
    target = Path(target).expanduser().resolve()
    if target.exists():
        raise FileExistsError("拒绝覆盖汇总 CSV：%s" % target)
    probe = target.parent
    while not probe.exists():
        if probe.parent == probe:
            break
        probe = probe.parent
    if not probe.is_dir():
        raise FileExistsError("汇总输出父路径不是目录：%s" % probe)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _freeze_inputs(parent, item):
    """只落盘预检已验证的字节，不再读取可能变动或消失的源文件。"""
    frozen_dir = Path(parent) / "inputs" / ("entry-%03d" % item["index"])
    frozen_dir.mkdir(parents=True, exist_ok=False)
    frozen = {}
    for key, raw in item["input_bytes"].items():
        target = frozen_dir / (key + ".json")
        with target.open("xb") as fh:
            fh.write(raw)
        frozen[key] = target
    return frozen


def _verify_frozen(item, frozen):
    for key, expected in item["input_hashes"].items():
        actual = hashlib.sha256(Path(frozen[key]).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError("entries[%d] 冻结 %s 哈希不一致" % (item["index"], key))


def _not_run_rows(entries, mode, reason):
    return [{"mode": mode, "entry": item["index"], "run_id": "", "status": "notRun",
             "cases": str(item["cases"]), "cases_hash": item["cases_hash"],
             "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "not_run": 1,
             "verdicts": "{}", "reason": reason} for item in entries]


def main(argv=None):
    ap = argparse.ArgumentParser(description="多接口 cases manifest 执行")
    ap.add_argument("--manifest", required=True, help="manifest JSON（单一套件）")
    ap.add_argument("--execute", action="store_true", help="显式真实执行；省略时仅生成计划")
    ap.add_argument("--out-dir", help="新空父目录；每个接口使用唯一子目录")
    ap.add_argument("--out", help="utf-8-sig 汇总 CSV（默认父目录/multi_interface_summary.csv）")
    ap.add_argument("--project", help="默认批次目录的项目名")
    args = ap.parse_args(argv)
    manifest_path = Path(args.manifest).expanduser().resolve()
    if args.out:
        try:
            _ensure_output_target(args.out)
        except OSError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    try:
        doc, entries = preflight(manifest_path)
    except Exception as exc:
        # 预检失败时不调用 flow；若能安全创建输出目录，留下明确 notRun 汇总。
        try:
            parent = _new_parent(args.out_dir, args.project)
            rows = [{"mode": "", "entry": i, "run_id": "", "status": "notRun", "cases": "",
                     "cases_hash": "", "passed": 0, "failed": 0, "errors": 0, "skipped": 0,
                     "not_run": 1, "verdicts": "{}", "reason": str(exc)}
                    for i in range(1, len((_read_json(manifest_path).get("entries") or [])) + 1)]
            write_csv(rows, args.out or parent / "multi_interface_summary.csv")
        except Exception:
            pass
        print(str(exc), file=sys.stderr)
        return 2
    try:
        parent = _new_parent(args.out_dir, args.project)
        output = _ensure_output_target(args.out or (parent / "multi_interface_summary.csv"))
    except OSError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        # 全部冻结、全部校验完成后，才允许第一个 flow 开始。
        frozen_entries = [_freeze_inputs(parent, item) for item in entries]
        for item, frozen in zip(entries, frozen_entries):
            _verify_frozen(item, frozen)
    except BaseException as exc:
        reason = "批次输入冻结失败（未执行任何接口）：%s: %s" % (type(exc).__name__, exc)
        write_csv(_not_run_rows(entries, doc["mode"], reason), output)
        print(reason, file=sys.stderr)
        return 2
    rows = []
    for item, frozen in zip(entries, frozen_entries):
        run_dir, flow_started = None, False
        flow_code, reason = 0, ""
        try:
            run_dir = _safe_run_dir(parent, item["index"])
            _verify_frozen(item, frozen)
            argv_run = ["--cases", str(frozen["cases"]), "--out-dir", str(run_dir)]
            if frozen.get("config"):
                argv_run += ["--config", str(frozen["config"])]
            if frozen.get("baseline"):
                argv_run += ["--retest", str(frozen["baseline"])]
            if item["only"]:
                argv_run += ["--only", item["only"]]
            # 默认只是计划；只有用户显式传入 --execute 才真实执行。
            if args.execute:
                argv_run.append("--execute")
            flow_started = True
            flow_code = _flow_main(argv_run)
        except BaseException as exc:  # 子项异常不能伪装为通过
            flow_code, reason = 2, "%s: %s" % (type(exc).__name__, exc)
        if not flow_started:
            rows.extend(_not_run_rows([item], doc["mode"], "子项准备失败：" + reason))
            continue
        manifest = run_dir / "run_manifest.json"
        try:
            row = _manifest_result(manifest if manifest.is_file() else None,
                                   dict(item, mode=doc["mode"]), flow_code, reason)
        except Exception as exc:  # 单个子项解析失败不得中断整批，也不能伪装为通过
            row = {"mode": doc["mode"], "entry": item["index"], "run_id": "",
                   "status": "failed", "cases": str(item["cases"]),
                   "cases_hash": item["cases_hash"], "passed": 0, "failed": 0,
                   "errors": 0, "skipped": 0, "not_run": 0, "verdicts": "{}",
                   "reason": "汇总解析失败：%s: %s" % (type(exc).__name__, exc)}
        rows.append(row)
    write_csv(rows, output)
    failed = [r for r in rows if r["status"] in {"failed", "notRun"} or (args.execute and not _is_pass(r))]
    print("多接口汇总 -> %s（%d 个接口）" % (output, len(rows)))
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
