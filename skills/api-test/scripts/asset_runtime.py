# -*- coding: utf-8 -*-
"""运行资产生命周期：安全目录、运行清单、用例快照与正式基线。"""
import hashlib
import json
import os
import re
import secrets
import time
import unicodedata
from pathlib import Path
from urllib.parse import unquote_plus, urlsplit

STATUS = {"planned", "running", "completed", "partial", "failed"}
_TRANSITIONS = {
    "planned": {"planned", "running", "failed"},
    "running": {"running", "completed", "partial", "failed"},
    "completed": {"completed"},
    "partial": {"partial"},
    "failed": {"failed"},
}
# Match complete key names after punctuation/case normalization.  In particular,
# do not use a substring regex: ``design`` must not be treated as ``sign``.
_SECRET_NAMES = {
    "token", "secret", "password", "passwd", "authorization", "cookie",
    "sign", "apikey", "apisecret", "credential", "credentials",
    "accesstoken", "refreshtoken", "clientsecret", "xapikey", "xauthtoken",
    "proxyauthorization", "setcookie", "sessionid", "csrftoken", "bearer",
}
# Whole-value header/query shapes that must never reach an artifact.
_SECRET_KEY_PATTERN = (r"authorization|proxy-authorization|x-api-key|x-auth-token|"
                       r"api[-_]?key|access[-_]?token|refresh[-_]?token|client[-_]?secret|"
                       r"password|passwd|cookie|set-cookie|session[-_]?id|csrf[-_]?token|sign")
# Header/query-like key/value text, including JSON strings such as
# {"Authorization":"Bearer abc"}. The key boundary prevents ``design`` from
# being mistaken for ``sign``.
_SECRET_TEXT = re.compile(
    r"(?i)(?P<prefix>(?<![a-z0-9])[\"']?(?:%s)(?![a-z0-9])[\"']?\s*[:=]\s*)"
    r"(?P<quote>[\"']?)(?P<scheme>(?:(?:bearer|basic|token|digest)\s+)?)"
    r"(?P<value>[^\s,;&\"'}]+)(?P<close>[\"']?)" % _SECRET_KEY_PATTERN)
_SCHEME_TEXT = re.compile(
    r"(?i)(?P<scheme>\b(?:bearer|basic|digest|token)\s+)"
    r"(?P<value>[^\s,;&\"'}]+)")

_PLACEHOLDER = re.compile(
    r"^(?:<[^>]+>|\$\{[^}]+\}|\*{3,}|xwrong(?:sign|token)x|"
    r"(?:invalid|wrong|fake|dummy|test|bad)[-_ ]?(?:token|secret|key|auth)?|"
    r"(?:no|none|null|missing)[-_ ]?(?:token|secret|key|auth)?)$", re.I)
_SAFE_CHARS = re.compile(r"[^\w.-]+", re.UNICODE)
_RESERVED_WINDOWS = {
    "con", "prn", "aux", "nul", *("com%d" % i for i in range(1, 10)),
    *("lpt%d" % i for i in range(1, 10)),
}
_SAFE_ENV_NAMES = {
    "adapter", "profile", "project", "version", "region", "locale",
    "environment", "baseurl", "host", "timeout", "retries",
    "requestintervalms", "dryrun", "runner", "mode",
}


def _key_norm(key):
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _is_secret_key(key):
    return _key_norm(key) in _SECRET_NAMES


def _secret_placeholder(value):
    if value in (None, "", False):
        return True
    return isinstance(value, str) and bool(_PLACEHOLDER.fullmatch(value.strip()))


def safe_component(value, default="unknown"):
    """Return a filesystem-safe, Unicode-preserving component.

    A short digest is retained whenever sanitization, case-folding, truncation,
    or a Windows reserved name could make two inputs map to one path.
    """
    original = unicodedata.normalize("NFC", str(value or "").strip())
    text = _SAFE_CHARS.sub("-", original).strip(".-_")
    if not text or text in {".", ".."}:
        return (default if not original else default + "-" + hashlib.sha256(original.encode("utf-8")).hexdigest()[:8])
    digest = hashlib.sha256(original.encode("utf-8")).hexdigest()[:8]
    # Keep a readable prefix, but always retain identity entropy.  This also
    # handles Windows' case-insensitive filesystem and reserved device names.
    max_prefix = 110
    text = text[:max_prefix].strip(".-_") or default
    # Keep ordinary lowercase components readable.  A digest is required when
    # normalization changes the spelling, truncation occurs, or case-insensitive
    # Windows/reserved-name semantics could cause a collision.
    needs_digest = (text.casefold() in _RESERVED_WINDOWS or text != original or
                    len(original) > max_prefix or original != original.lower())
    return "%s-%s" % (text, digest) if needs_digest else text


def normalize_path(path):
    text = str(path or "/").strip()
    text = re.sub(r"https?://[^/]+", "", text, flags=re.I)
    text = re.sub(r"/{2,}", "/", text)
    if not text.startswith("/"):
        text = "/" + text
    return re.sub(r"/+$", "", text) or "/"


def stable_interface_id(contract=None, action=None, method=None, path=None, endpoint=None):
    contract = contract or {}
    action = action or contract.get("action")
    method = method or contract.get("method") or contract.get("http_method")
    path = path or contract.get("path")
    endpoint = endpoint or contract.get("endpoint") or contract.get("url")
    if action:
        readable = safe_component(action)
        if str(action).isascii() and str(action).isdecimal():
            return "action-" + readable
        return "action-" + readable + "-" + canonical_hash(str(action))[:10]
    if not path and endpoint:
        path = endpoint
    if path:
        raw_path = str(path).strip()
        # urlsplit removes query/fragment while preserving path case.
        parsed = urlsplit(raw_path)
        if parsed.scheme or parsed.netloc:
            raw_path = parsed.path or "/"
        else:
            raw_path = raw_path.split("?", 1)[0].split("#", 1)[0]
        identity = "%s %s" % ((method or "HTTP").upper(), normalize_path(raw_path))
        return safe_component(identity)[:100] + "-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:10]
    return "interface-" + hashlib.sha256(json.dumps(contract, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def canonical_hash(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def cases_hash(cases_doc):
    if isinstance(cases_doc, dict):
        cases_doc = {k: v for k, v in cases_doc.items() if k != "approval"}
    return canonical_hash(cases_doc)


def _secret_text_matches(value):
    """Return credential-shaped text matches whose values are not placeholders."""
    text = str(value)
    matches = []
    for regex in (_SECRET_TEXT, _SCHEME_TEXT):
        for match in regex.finditer(text):
            if not _secret_placeholder(match.group("value")):
                matches.append(match)
    return matches


def _redact_text(value):
    text = str(value)
    # Remove the whole credential, including the scheme that follows the header.
    text = _SECRET_TEXT.sub(
        lambda m: m.group(0) if _secret_placeholder(m.group("value"))
        else m.group("prefix") + m.group("quote") + m.group("scheme") + "***" + m.group("close"), text)
    return _SCHEME_TEXT.sub(
        lambda m: m.group(0) if _secret_placeholder(m.group("value"))
        else m.group("scheme") + "***", text)


def _redact_url(text):
    """Mask credential-shaped query parameters and userinfo, keeping the URL usable."""
    try:
        parsed = urlsplit(text)
    except ValueError:
        return text
    if not parsed.scheme or not parsed.netloc:
        return text
    netloc = parsed.netloc
    if "@" in netloc:
        userinfo, host = netloc.rsplit("@", 1)
        netloc = ("***@" if userinfo else "") + host
    pairs = []
    for chunk in parsed.query.split("&"):
        if not chunk:
            continue
        key, sep, value = chunk.partition("=")
        decoded_key = unquote_plus(key)
        pairs.append("%s=***" % key if sep and _is_secret_key(decoded_key) and not _secret_placeholder(unquote_plus(value)) else chunk)
    return parsed._replace(netloc=netloc, query="&".join(pairs)).geturl()


def _redact(value):
    if isinstance(value, dict):
        return {k: ("***" if _is_secret_key(k) else _redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if isinstance(value, str):
        return _redact_text(_redact_url(value))
    return value


def safe_environment(extra=None):
    allowed = {"WJX_PROFILE", "WJX_REGION", "WJX_ENV", "WJX_PROJECT_NAME", "WJX_VERIFY_TLS"}
    env = {k: v for k, v in os.environ.items() if k in allowed}
    for key, value in (extra or {}).items():
        if not _is_secret_key(key) and _key_norm(key) in _SAFE_ENV_NAMES:
            env[str(key)] = value
    return _redact(env)


def _unique_run_id():
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + secrets.token_hex(4)


def _package_root():
    return Path(__file__).resolve().parents[1]


def default_asset_root(skill_root=None):
    configured = os.environ.get("WJX_API_TEST_ASSET_ROOT")
    candidate = Path(configured).expanduser() if configured else (Path.cwd() / "api-tests")
    candidate = candidate.resolve()
    package = Path(skill_root).resolve() if skill_root else _package_root()
    # Assets must stay outside the skill package to prevent generated secrets
    # and run output from becoming package content.
    if candidate == package or package in candidate.parents:
        raise ValueError("资产根目录必须位于 skill 包外：%s" % candidate)
    return candidate


def _occupy(path):
    marker = Path(path) / ".asset.lock"
    try:
        with marker.open("x", encoding="ascii") as fh:
            fh.write("pid=%s\n" % os.getpid())
    except FileExistsError:
        raise FileExistsError("输出目录已被占用：%s" % path)


def prepare_run_dir(skill_root=None, project=None, interface=None, version=None, out_dir=None, out=None):
    explicit = out_dir or out
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if path.exists() and not path.is_dir():
            raise FileExistsError("输出路径不是目录：%s" % path)
        if path.exists() and any(path.iterdir()):
            raise FileExistsError("拒绝覆盖非空输出目录：%s" % path)
        path.mkdir(parents=True, exist_ok=True)
        # Exclusive marker arbitrates concurrent callers after the emptiness check.
        _occupy(path)
        return path, True
    root = default_asset_root(skill_root)
    project = safe_component(project or os.environ.get("WJX_PROJECT_NAME") or Path.cwd().name)
    interface = safe_component(interface or "interface")
    version = safe_component(version, "unversioned") if version else None
    base = root / project / "runs" / interface
    if version:
        base /= version
    base.mkdir(parents=True, exist_ok=True)
    for _ in range(20):
        path = base / _unique_run_id()
        try:
            path.mkdir()
            _occupy(path)
            return path, False
        except FileExistsError:
            continue
    raise FileExistsError("无法创建唯一运行目录：%s" % base)


def write_json(path, value):
    path = Path(path)
    if path.exists():
        raise FileExistsError("拒绝覆盖已有文件：%s" % path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False, indent=2)


def update_manifest(path, status, **fields):
    if status not in STATUS:
        raise ValueError("未知运行状态：%s" % status)
    path = Path(path)
    doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    previous = doc.get("status")
    if previous and status not in _TRANSITIONS.get(previous, set()):
        # dry-run starts as running in flow for adapter setup, but must finish
        # planned and never become completed.
        if not (previous == "running" and status == "planned" and fields.get("dry_run")):
            raise ValueError("非法运行状态迁移：%s -> %s" % (previous, status))
    if "environment" in fields:
        fields["environment"] = safe_environment(fields["environment"])
    doc.update(_redact(fields))
    doc["status"] = status
    doc["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return doc


def create_manifest(run_dir, interface, contract_hash, cases_doc=None, environment=None, inputs=None):
    run_dir = Path(run_dir)
    manifest = {
        "schema_version": 1, "run_id": run_dir.name, "interface": interface,
        "contract_hash": contract_hash, "environment": safe_environment(environment),
        "status": "planned", "inputs": _redact(inputs or {}), "artifacts": [],
        "executed_cases": [], "results": {},
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if cases_doc is not None:
        manifest["inputs"]["cases_hash"] = cases_hash(cases_doc)
    path = run_dir / "run_manifest.json"
    write_json(path, manifest)
    return path


def _result_item(result):
    return {"case_no": result.get("case_no") or result.get("case_id") or "",
            "status": str(result.get("status") or ""),
            "reason": _redact_text(result.get("reason") or "")}


def finish_manifest(path, results, artifacts, status=None, selection=None):
    results = list(results or [])
    items = [_result_item(r) for r in results]
    known = {"PASS", "FAIL", "ERROR", "SKIPPED", "N/A", "PRECONDITION_UNMET"}
    invalid = (not items or any(not r["case_no"] or not r["status"] or r["status"] not in known for r in items))
    failed = any(r["status"] in {"FAIL", "ERROR", "PRECONDITION_UNMET"} for r in items)
    all_pass = bool(items) and not invalid and not failed and all(r["status"] == "PASS" for r in items)
    truncated = bool((selection or {}).get("truncated"))
    if status is None:
        status = ("partial" if truncated else "completed") if all_pass else "failed"
    if status == "completed" and truncated:
        # A capped run is an explicitly incomplete scope; it must not claim acceptance.
        status = "partial"
    if status in {"completed", "partial"} and not all_pass:
        raise ValueError("未完整通过的结果不能标记为 %s" % status)
    executed = [r["case_no"] for r in items if r["status"] not in {"SKIPPED", "N/A"}]
    fields = {"executed_cases": executed,
              "results": {"counts": {s: sum(1 for r in items if r["status"] == s) for s in sorted({r["status"] for r in items})},
                          "items": items},
              "artifacts": artifacts}
    if selection is not None:
        fields["selection"] = selection
    return update_manifest(path, status, **fields)


def _url_has_credentials(text):
    try:
        parsed = urlsplit(str(text))
    except ValueError:
        return False
    if not parsed.scheme or not parsed.netloc:
        return False
    if parsed.username or parsed.password:
        return True
    for chunk in parsed.query.split("&"):
        key, sep, value = chunk.partition("=")
        if sep and value and _is_secret_key(unquote_plus(key)) and not _secret_placeholder(unquote_plus(value)):
            return True
    return False


def _credential_paths(value, prefix=""):
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            location = prefix + "." + str(key)
            if _is_secret_key(key) and not _secret_placeholder(item):
                found.append(location)
            else:
                found.extend(_credential_paths(item, location))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_credential_paths(item, "%s[%d]" % (prefix, index)))
    elif isinstance(value, str):
        if _url_has_credentials(value):
            found.append(prefix or "<url>")
        elif _secret_text_matches(value):
            found.append(prefix or "<text>")
    return found


def _check_eligibility(case):
    """Reuse case_runner's executable checker registry without import-time cycles."""
    try:
        import case_runner
    except Exception as exc:
        raise ValueError("无法加载检查器注册表：%s" % exc)
    fn = getattr(case_runner, "check_eligibility", None)
    if not callable(fn):
        raise ValueError("case_runner 缺少 check_eligibility，禁止批准")
    reason = fn(case)
    return str(reason or "")


def validate_cases_doc(cases_doc):
    """Validate the basic cases document shape; execution eligibility is separate."""
    if not isinstance(cases_doc, dict):
        raise ValueError("用例文档必须是对象")
    cases = cases_doc.get("cases")
    if not isinstance(cases, list):
        raise ValueError("用例文档 cases 必须是列表")
    ids = [c.get("id") for c in cases if isinstance(c, dict)]
    if any(not isinstance(cid, str) or not cid.strip() for cid in ids) or len(set(ids)) != len(ids):
        raise ValueError("用例编号必须是非空且唯一的字符串")
    approval = cases_doc.get("approval") or {}
    if approval.get("approved") and approval.get("cases_hash") != cases_hash(cases_doc):
        raise ValueError("已批准用例内容发生变化，必须另存重新审批")
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError("cases[%d] 必须是对象" % index)
        if not str(case.get("id") or "").strip():
            raise ValueError("cases[%d] 缺少 id" % index)
        if "expect" in case and not isinstance(case["expect"], dict):
            raise ValueError("cases[%d].expect 必须是对象" % index)
        if isinstance(case.get("expect"), dict) and "success" in case["expect"] and not isinstance(case["expect"]["success"], bool):
            raise ValueError("cases[%d].expect.success 必须是 bool" % index)
    return cases_doc


def validate_approval(cases_doc, approver=None):
    validate_cases_doc(cases_doc)
    cases = cases_doc.get("cases")
    if not approver or not str(approver).strip():
        raise ValueError("批准必须提供 approver")
    if not cases:
        raise ValueError("正式测试集不得为空")
    ids = [str(c.get("id") or "").strip() for c in cases]
    if not all(ids) or len(set(ids)) != len(ids):
        raise ValueError("正式测试集用例编号必须非空且唯一")
    bad = [c.get("id") for c in cases if str(c.get("id", "")).upper().startswith("TODO-") or c.get("needs") == "manual" or c.get("tier") == "manual" or c.get("enabled") is False]
    if bad:
        raise ValueError("存在未完成手工/TODO 用例，禁止批准：%s" % ", ".join(map(str, bad[:10])))
    for case in cases:
        case_id = case.get("id")
        action = case.get("action")
        missing = [field for field in ("name", "dimension")
                   if not str(case.get(field) or "").strip()]
        if not action:
            missing.append("action")
        if not isinstance(case.get("params"), dict):
            missing.append("params")
        if missing:
            raise ValueError("用例 %s 缺少可执行字段，禁止批准：%s" % (case_id, ", ".join(missing)))
        eligibility = _check_eligibility(case)
        if eligibility:
            raise ValueError("用例 %s 检查器不可执行，禁止批准：%s" % (case_id, eligibility))
        expect = case.get("expect") or {}
        if not isinstance(expect, dict) or not isinstance(expect.get("success"), bool):
            raise ValueError("用例 %s 必须声明 expect.success bool" % case_id)
        if expect.get("success") is False:
            keywords = expect.get("err_keywords")
            if (not isinstance(keywords, list) or not keywords
                    or any(not isinstance(k, str) or not k.strip() for k in keywords)):
                raise ValueError("异常用例 %s 必须声明非空 expect.err_keywords 列表" % case_id)
    credentials = _credential_paths(cases_doc)
    if credentials:
        raise ValueError("正式测试集含真实凭据，请外置配置：%s" % ", ".join(credentials[:10]))
    return {"approved": True, "approver": str(approver).strip(), "approved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "cases_hash": cases_hash(cases_doc)}


def approve_cases(path, approver, output=None, project=None):
    src = Path(path).resolve()
    doc = json.loads(src.read_text(encoding="utf-8"))
    if doc.get("approval", {}).get("approved"):
        raise ValueError("正式测试集已批准，修改后必须另存为新版本再批准")
    approval = validate_approval(doc, approver)
    doc["approval"] = approval
    if output:
        target = Path(output).expanduser().resolve()
    else:
        suite_id = doc.get("suite_id") or doc.get("id") or doc.get("name") or doc.get("action") or "suite"
        project = safe_component(project or doc.get("project") or os.environ.get("WJX_PROJECT_NAME") or Path.cwd().name)
        target = (default_asset_root() / project / "suites" / stable_interface_id(doc)
                  / safe_component(suite_id) / approval["cases_hash"] / "cases.json")
    if target == src:
        raise ValueError("批准输出不得覆盖源文件")
    write_json(target, doc)
    return target


def _sanitize_entry(entry):
    if not isinstance(entry, dict):
        return entry
    # Keep regression entry fields, but never carry a response/request body into
    # a durable baseline artifact.
    blocked = {"response", "request", "body", "raw_response", "response_body"}
    return _redact({k: v for k, v in entry.items() if _key_norm(k) not in {_key_norm(x) for x in blocked}})


def promote_baseline(snapshot, target, approver):
    src = Path(snapshot).resolve()
    dst = Path(target).expanduser().resolve()
    if src == dst:
        raise ValueError("批准基线与本轮 snapshot 不得为同一路径")
    doc = json.loads(src.read_text(encoding="utf-8"))
    if doc.get("status") != "snapshot" or doc.get("snapshot") is not True:
        raise ValueError("仅允许从未批准的 snapshot 晋升正式基线")
    if not str(doc.get("cases_hash") or ""):
        raise ValueError("snapshot 必须包含 cases_hash")
    if doc.get("coverage_blocked"):
        raise ValueError("coverage blocked 的 snapshot 不得批准")
    source_cases = doc.get("cases") or []
    entries = doc.get("entries")
    statuses = doc.get("case_statuses")
    source_run = Path(doc.get("source_run") or "").resolve()
    manifest_path = source_run / "run_manifest.json"
    if source_run != src.parent or not manifest_path.is_file():
        raise ValueError("snapshot 必须关联其所在运行目录的真实 manifest")
    run_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if run_manifest.get("status") != "completed" or doc.get("findings_count") != 0:
        raise ValueError("仅完整通过且无待确认/缺陷的运行可以晋升基线")
    if doc["cases_hash"] != cases_hash({"cases": source_cases}):
        raise ValueError("snapshot cases_hash 不匹配")
    executed_path = source_run / "executed_cases.json"
    if not executed_path.is_file():
        raise ValueError("缺少实际执行快照，无法证明 snapshot 来自真实执行")
    executed_doc = json.loads(executed_path.read_text(encoding="utf-8"))
    executed_hash = cases_hash({"cases": executed_doc.get("cases") or []})
    if executed_hash != doc["cases_hash"]:
        raise ValueError("snapshot 内容与实际执行用例不一致")
    selection = run_manifest.get("selection") or {}
    # 【审查修复·范围外 Plus 晋升】`--only A,B` 的运行会把范围外的必测维度记进
    # coverage_excluded 并从 coverage_plan 里摘掉——于是既无覆盖缺口、又能评
    # completed。此前 promote_baseline 完全不看 selection，这种运行可以原样晋升
    # 成「正式基线」，被排除的 C…H 维度被永久固化进去，后续每次 --retest 都以它为
    # 准，而基线 JSON 里没有任何字段记录「本基线只覆盖 A、B」。
    # 同时：档位收窄 / max_cases 截断（truncated=True）的运行同样不代表全量通过。
    excluded = selection.get("coverage_excluded") or []
    if selection.get("truncated"):
        raise ValueError(
            "该运行并非声明范围的完整执行（truncated=true，selection=%s），不得晋升正式基线"
            % json.dumps({k: selection.get(k) for k in
                          ("profile", "only", "cap_dropped", "profile_limited")},
                         ensure_ascii=False))
    if selection.get("only") or excluded:
        raise ValueError(
            "该运行只执行了声明范围的子集（only=%r，coverage_excluded=%d 条），"
            "不得晋升为正式基线——会把「这批维度本轮没测」固化成永久基准"
            % (selection.get("only"), len(excluded)))
    if selection.get("cases_hash") and selection["cases_hash"] != executed_hash:
        raise ValueError("运行清单记录的执行内容与 snapshot 不一致")
    if not isinstance(entries, list) or not entries or not isinstance(statuses, dict) or not statuses:
        raise ValueError("snapshot 必须包含非空 entries 与 case_statuses")
    case_ids = {str(c.get("id")) for c in source_cases}
    entry_ids = {str(e.get("case_id")) for e in entries if isinstance(e, dict)}
    status_ids = {str(k) for k in statuses}
    if (case_ids != entry_ids or entry_ids != status_ids or not case_ids
            or len(entries) != len(case_ids) or len(source_cases) != len(case_ids)):
        raise ValueError("snapshot 的用例、entries、case_statuses 不完整或有重复")
    run_items = (run_manifest.get("results") or {}).get("items") or []
    if (len(run_items) != len(case_ids)
            or {r.get("case_no") for r in run_items} != case_ids
            or any(r.get("status") != "PASS" for r in run_items)):
        raise ValueError("snapshot 与实际运行结果不一致")
    if any(statuses[k] != "PASS" for k in statuses):
        raise ValueError("仅全通过 snapshot 可以批准，跳过/失败不得成为正式基线")
    contract_hash = run_manifest.get("contract_hash")
    cases_by_id = {str(c.get("id")): c for c in source_cases}
    for entry in entries:
        if (not isinstance(entry, dict) or entry.get("last_status") != "PASS"
                or not entry.get("contract_hash")):
            raise ValueError("entries 必须包含实际 PASS 状态与契约哈希")
        if contract_hash and entry["contract_hash"] != contract_hash:
            raise ValueError("entries 契约哈希与本次运行契约不一致")
        case = cases_by_id.get(str(entry.get("case_id"))) or {}
        if (not case or str(entry.get("case_id")) not in cases_by_id):
            raise ValueError("entries 引用了不存在的用例")
        expected_case_hash = canonical_hash(case)
        if entry.get("case_hash") and entry["case_hash"] != expected_case_hash:
            raise ValueError("entries case_hash 与实际执行用例不一致：%s" % entry.get("case_id"))
        if (entry.get("expectations") or {}).get("expect") != (case.get("expect") or {}):
            raise ValueError("entries 期望与实际执行用例不一致")
    approval = validate_approval({"cases": source_cases}, approver)
    baseline = dict(doc)
    baseline["status"] = "approved"
    baseline["approval"] = approval
    baseline["entries"] = [_sanitize_entry(e) for e in entries]
    write_json(dst, baseline)
    return dst
