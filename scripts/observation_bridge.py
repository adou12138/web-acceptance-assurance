"""把浏览器观察结果转成可校验的探索清单草稿；不执行浏览器操作、不判定缺陷。

观察器输出只说明“看到什么候选”，本工具把候选映射成 NOT_RUN 的待复核条目，供
专家逐项人工复合并回填观察与证据。所有条目都保持未执行状态，不自动重放操作。

除逐个 finding 外，观察结果里已实测到性能数据时还会生成一条 performance 属性的
汇总复核条目，避免“采到了却没进台账”导致门禁误判性能视角缺失。没有任何实测族
（未请求或不可用）时不生成条目，宁可让门禁继续报缺失，也不伪造覆盖。

页面自述为受控夹具（观察结果 page.fixture.declared 为真）时，所有条目的 action
前置统一标记，提示其中的异常可能是预埋项、不得直接作为产品缺陷上报或计入缺陷率。
标记不改变状态与风险等级，也不把条目从清单里剔除，避免用声明代替判断。

用法：
    python observation_bridge.py <观察JSON> --target-url URL --application-version V
        --environment E --requirement REQ [--requirement REQ2] --output <新草稿.json>
        [--role R] [--performance-evidence <trace或审计文件> ...]

约束：
- 观察状态为 blocked 或页面与目标不同源时拒绝生成，避免把错页面候选写进清单。
- page.fixture 结构非法时报错，不猜默认值。
- 未知 finding code、未知性能状态或结构直接报错，不静默丢弃。
- 性能条目只表达“已采到哪些族、还缺哪些”，指标是否达标仍需人工判定。
- 生成后先用 exploration_state.summarize 自检字段与同源约束，通过后才独占写文件。
- 输出为 UTF-8 JSON，父目录需存在，拒绝覆盖；退出码 0 成功、2 数据或工具错误。
"""
import argparse
import importlib.util
from pathlib import Path
import json
import sys
from urllib.parse import urlsplit

FINDING_RULES = {
    "missing-body": {"attributes": ["functional"], "risk": "high",
                     "action": "确认页面主体是否确实缺失，排除加载失败、错误页或路由未渲染"},
    "blank-content": {"attributes": ["functional"], "risk": "high",
                      "action": "确认页面是否只剩框架、正文为空，排除加载中或权限拦截"},
    "horizontal-overflow": {"attributes": ["visual"], "risk": "high",
                            "action": "复现横向滚动，确认越界内容是否导致信息不可见或操作困难"},
    "text-clipping": {"attributes": ["visual"], "risk": "medium",
                      "action": "确认截断文本是否影响信息获取，是否可展开或通过其他方式查看"},
    "broken-image": {"attributes": ["visual"], "risk": "medium",
                     "action": "确认图片资源是否真实失效，排除懒加载未触发或占位图"},
    "unnamed-control": {"attributes": ["accessibility", "usability"], "risk": "high",
                        "action": "复核控件可辨名称，确认读屏与搜索能否识别其用途"},
    "occluded-control": {"attributes": ["visual", "usability"], "risk": "high",
                         "action": "复核控件是否被浮层遮挡导致不可点击，确认遮挡元素与出现条件"},
    "image-aspect-distortion": {"attributes": ["visual"], "risk": "medium",
                                "action": "确认图片拉伸是否失真，核对是否属占位图或预期裁切"},
    "placeholder-link": {"attributes": ["functional"], "risk": "medium",
                         "action": "确认链接是否未实现或占位，核对点击后的实际去向"},
    "missing-viewport-meta": {"attributes": ["visual", "compatibility"], "risk": "medium",
                              "action": "在移动设备或设备模拟下确认是否整体缩放、字号过小或点击区域不足"},
}
DEFAULT_ROLE = "待确认角色"
STATE_TEXT = "页面观察时状态（待确认）"
FIXTURE_MARKER = "【受控夹具预埋项：页面自述为非生产样例或演练站，本项不得作为产品缺陷上报或计入缺陷率，需先确认页面用途】"
PERFORMANCE_FAMILIES = ("navigation", "resources", "loading", "contentful", "interaction", "layout")
PERFORMANCE_STATUSES = {"available", "partial", "unavailable", "not-requested"}
PERFORMANCE_TRACKABLE = {"available", "partial"}


def _module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _origin(value, label):
    if not isinstance(value, str):
        raise ValueError(f"{label}: 必须是绝对URL字符串")
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError(f"{label}: 必须是http/https绝对URL")
    if parts.username or parts.password:
        raise ValueError(f"{label}: 不能包含凭据")
    return parts.scheme, parts.hostname.lower(), parts.port or (443 if parts.scheme == "https" else 80)


def _fixture_declared(page):
    """页面是否自述为受控夹具；缺失视为未声明，结构错误直接报错。"""
    fixture = page.get("fixture") if isinstance(page, dict) else None
    if fixture is None:
        return False
    if not isinstance(fixture, dict) or not isinstance(fixture.get("declared"), bool):
        raise ValueError("观察结果: page.fixture必须包含布尔declared")
    return fixture["declared"]


def _performance_item(payload, requirement_ids, page, viewport, role, evidence_paths, fixture_declared=False):
    performance = payload.get("performance")
    if performance is None:
        return None
    if not isinstance(performance, dict):
        raise ValueError("观察结果: performance必须是对象")
    status = performance.get("status")
    if status not in PERFORMANCE_STATUSES:
        raise ValueError(f"观察结果: performance.status {status!r} 未知，需先补充处理再生成清单")
    if status not in PERFORMANCE_TRACKABLE:
        return None
    coverage = performance.get("coverage")
    if not isinstance(coverage, dict):
        raise ValueError("观察结果: performance.coverage缺失，无法判断已采到哪些性能族")
    available = [family for family in PERFORMANCE_FAMILIES if coverage.get(family) == "available"]
    if not available:
        return None
    missing = [family for family in PERFORMANCE_FAMILIES if coverage.get(family) != "available"]
    uncollected = performance.get("notCollected")
    if not isinstance(uncollected, list) or not uncollected or not all(isinstance(entry, str) and entry.strip() for entry in uncollected):
        uncollected = missing
    if missing:
        action = ("补采未覆盖的性能族（" + "、".join(uncollected) + "），再核对已采指标是否满足验收阈值；"
                  "已采族：" + "、".join(available))
        risk = "medium"
    else:
        action = "核对已采性能指标是否满足验收阈值，并记录与真实用户分布之间的差距"
        risk = "low"
    paths = []
    for path in evidence_paths:
        resolved = Path(path).resolve()
        if resolved not in paths:
            paths.append(str(resolved))
    return {
        "id": "OBS-performance",
        "requirement_ids": list(requirement_ids),
        "page_url": page["url"],
        "role": role,
        "viewport": f"{int(viewport['width'])}x{int(viewport['height'])}",
        "state": STATE_TEXT,
        "action": (FIXTURE_MARKER + action) if fixture_declared else action,
        "risk": risk,
        "status": "NOT_RUN",
        "observation": "",
        "evidence_paths": paths,
        "blocked_reason": "",
        "side_effect_pending": False,
        "attributes": ["performance"],
    }


def build_items(payload, target_url, requirement_ids, role, source_path, performance_evidence=()):
    if not isinstance(payload, dict):
        raise ValueError("观察结果: 必须是JSON对象")
    if payload.get("schemaVersion") != "1.0":
        raise ValueError("观察结果: schemaVersion必须为1.0")
    status = payload.get("status")
    if status not in {"observed", "incomplete"}:
        raise ValueError(f"观察结果: 状态{status!r}不能用于生成清单，阻塞或未采集需先恢复采集")
    page = payload.get("page")
    if not isinstance(page, dict) or not isinstance(page.get("url"), str):
        raise ValueError("观察结果: page.url缺失")
    if _origin(page["url"], "page.url") != _origin(target_url, "target_url"):
        raise ValueError("观察结果: page.url与target_url不同源，拒绝把其他页面候选写入清单")
    viewport = page.get("viewport")
    if not isinstance(viewport, dict) or not all(isinstance(viewport.get(k), (int, float)) and viewport[k] > 0
                                                 for k in ("width", "height")):
        raise ValueError("观察结果: page.viewport缺少有效宽高")
    findings = payload.get("findings")
    if not isinstance(findings, list):
        raise ValueError("观察结果: findings必须是数组")
    fixture_declared = _fixture_declared(page)
    items, used = [], {}
    for finding in findings:
        if not isinstance(finding, dict) or not isinstance(finding.get("code"), str):
            raise ValueError("观察结果: findings条目必须含字符串code")
        code = finding["code"]
        if code not in FINDING_RULES:
            raise ValueError(f"观察结果: 未映射的finding code {code!r}，需先补充映射再生成清单")
        rule = FINDING_RULES[code]
        used[code] = used.get(code, 0) + 1
        ident = f"OBS-{code}" if used[code] == 1 else f"OBS-{code}-{used[code]}"
        items.append({
            "id": ident,
            "requirement_ids": list(requirement_ids),
            "page_url": page["url"],
            "role": role,
            "viewport": f"{int(viewport['width'])}x{int(viewport['height'])}",
            "state": STATE_TEXT,
            "action": (FIXTURE_MARKER + rule["action"]) if fixture_declared else rule["action"],
            "risk": rule["risk"],
            "status": "NOT_RUN",
            "observation": "",
            "evidence_paths": [str(source_path.resolve())],
            "blocked_reason": "",
            "side_effect_pending": False,
            "attributes": list(rule["attributes"]),
        })
    performance_item = _performance_item(payload, requirement_ids, page, viewport, role,
                                        [source_path, *performance_evidence], fixture_declared)
    if performance_item is not None:
        items.append(performance_item)
    return items


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="把只读观察结果转成待复核探索清单；不执行操作")
    parser.add_argument("observation")
    parser.add_argument("--target-url", required=True)
    parser.add_argument("--application-version", required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--requirement", action="append", required=True)
    parser.add_argument("--role", default=DEFAULT_ROLE)
    parser.add_argument("--performance-evidence", action="append", default=[],
                        help="宿主性能追踪或审计产物路径，可多次；归入性能条目的证据链")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        store = _module("exploration_state", "exploration_state.py")
        source = Path(args.observation)
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
        performance_evidence = []
        for value in args.performance_evidence:
            path = Path(value).resolve()
            if not path.is_file():
                raise ValueError(f"性能证据文件不存在: {value}")
            if path not in performance_evidence:
                performance_evidence.append(path)
        items = build_items(payload, args.target_url, args.requirement, args.role, source, performance_evidence)
        context = {"target_url": args.target_url, "application_version": args.application_version,
                   "environment": args.environment}
        draft = {"schema_version": "1.0", "context": context, "items": items}
        report = store.summarize(draft)
        output = Path(args.output)
        if not output.parent.is_dir():
            raise ValueError("输出父目录不存在")
        with output.open("x", encoding="utf-8") as stream:
            json.dump(draft, stream, ensure_ascii=False, indent=1)
            stream.write("\n")
        print(json.dumps({"generated": len(items), "item_ids": [item["id"] for item in items],
                          "observation_status": payload["status"],
                          "fixture_declared": _fixture_declared(payload.get("page")),
                          "gate": report["gate"]},
                         ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, OSError, TypeError) as exc:
        print(f"观察桥接输入/工具错误: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
