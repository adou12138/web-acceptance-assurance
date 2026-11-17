"""Trace sourced requirement dependencies to regression candidates, never execute tests."""
import argparse
from collections import deque
import csv
import importlib.util
import json
from pathlib import Path
import sys

_spec = importlib.util.spec_from_file_location("impact_case_data", Path(__file__).with_name("case_data.py"))
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)


def _need(condition, message):
    if not condition:
        raise ValueError(message)


def _references(value, known, label):
    _need(isinstance(value, list) and bool(value), label + ": nonempty list required")
    _need(all(isinstance(item, str) and item in known for item in value), label + ": unknown requirement")
    _need(len(value) == len(set(value)), label + ": duplicate reference")


def analyze(design, changes):
    errors, warnings = _module.validate(design)
    _need(not errors, "Invalid design: " + "; ".join(errors))
    _need(isinstance(changes, dict) and set(changes) == {"schema_version", "changed_requirements", "dependencies"}, "Invalid changes fields")
    _need(changes["schema_version"] == "1.0", "Unsupported changes version")
    requirements = {row["id"]: row for row in design["requirements"]}
    _references(changes["changed_requirements"], requirements, "changed_requirements")
    _need(isinstance(changes["dependencies"], list), "dependencies: list required")
    reverse = {ident: [] for ident in requirements}
    seen = set()
    for edge in changes["dependencies"]:
        _need(isinstance(edge, dict) and set(edge) == {"requirement", "depends_on", "source"}, "Invalid dependency fields")
        child = edge["requirement"]
        _need(isinstance(child, str) and child in requirements and child not in seen, "Unknown or duplicate dependent requirement")
        seen.add(child)
        _references(edge["depends_on"], requirements, "depends_on")
        _need(child not in edge["depends_on"], "Direct self dependency is not allowed")
        _need(isinstance(edge["source"], str) and bool(edge["source"].strip()), "Dependency source required")
        for parent in edge["depends_on"]:
            reverse[parent].append(child)
    paths = {}
    for changed in sorted(changes["changed_requirements"]):
        visited = {changed: [changed]}
        queue = deque([changed])
        while queue:
            current = queue.popleft()
            for dependent in sorted(reverse[current]):
                if dependent not in visited:
                    visited[dependent] = visited[current] + [dependent]
                    queue.append(dependent)
        for ident, path in visited.items():
            paths.setdefault(ident, []).append({"changed": changed, "path": path})
    feature_requirements = {leaf["id"]: leaf["requirement_ids"] for group in design["features"] for leaf in group["children"]}
    acceptance_requirements = {row["id"]: row["requirement_id"] for row in design["acceptance"]}
    impacted = [{"id": ident, "name": requirements[ident]["name"], "paths": paths[ident], "case_ids": []} for ident in sorted(paths)]
    impacted_map = {row["id"]: row for row in impacted}
    candidates = []
    for case in sorted(design["testcases"], key=lambda item: (item["priority"], item["id"])):
        related = {req for feature in case["feature_ids"] for req in feature_requirements[feature]}
        related.update(acceptance_requirements[ac] for ac in case["acceptance_ids"])
        triggers = sorted(related & set(paths))
        if triggers:
            candidates.append({"id": case["id"], "title": case["title"], "priority": case["priority"], "trigger_requirements": triggers, "rerun_candidate": True})
            for ident in triggers:
                impacted_map[ident]["case_ids"].append(case["id"])
    return {
        "design_id": design["meta"]["id"],
        "changed_requirements": sorted(changes["changed_requirements"]),
        "impacted_requirements": impacted,
        "selected_cases": candidates,
        "uncovered_impacted_requirements": [row["id"] for row in impacted if not row["case_ids"]],
        "dependencies": changes["dependencies"],
        "design_warnings": warnings,
        "gaps": (["No dependency edges supplied; indirect effects require investigation."] if not changes["dependencies"] else []),
        "limits": "Candidates from the supplied dependency graph only; graph completeness is unverified. No software execution, status change, acceptance decision or evidence retrieval. A shortest path per change source is shown, not every possible path."
    }


def _safe_cell(value):
    text = str(value)
    if text.lstrip().startswith(("=", "+", "-", "@")):
        text = "'" + text
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("design", type=Path)
    parser.add_argument("changes", type=Path)
    parser.add_argument("--output", type=Path, help="New UTF-8-BOM CSV candidate file; existing paths are never overwritten")
    args = parser.parse_args()
    try:
        result = analyze(json.loads(args.design.read_text(encoding="utf-8-sig")), json.loads(args.changes.read_text(encoding="utf-8-sig")))
        if args.output:
            _need(args.output.suffix.lower() == ".csv", "Output must be CSV")
            _need(args.output.parent.is_dir(), "Output parent directory must exist")
            with args.output.open("x", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["TC ID", "Title", "Priority", "Trigger requirements", "Rerun candidate"])
                for case in result["selected_cases"]:
                    writer.writerow([_safe_cell(case["id"]), _safe_cell(case["title"]), case["priority"], ";".join(case["trigger_requirements"]), "yes"])
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, TypeError, KeyError, OSError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
