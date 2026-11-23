"""把五视角台账与采集证据渲染成单文件 HTML 报告；只读数据，不判定缺陷。

报告是阅读视图，数字全部来自 CSV 台账与 JSON 采集结果，不做手工改写。截图以
base64 内联，保证单文件搬移后图片不裂。数据本身仍以 CSV/JSON 原件为准，HTML
不替代台账，也不构成验收通过。

用法：
    python render_report_html.py --output report.html [--title T] [--subject S]
        --dataset "标注::台账.csv::门禁.json" [--dataset ...]
        [--screenshot "图片路径::标题::说明" ...] [--performance 摘要.json ...]
        [--lighthouse report.json ...] [--evidence-dir 证据目录] [--note 补充边界 ...]

约束：
- 数据集、截图、性能摘要、审计报告路径都必须存在，缺失即报错退出 2，不产半成品。
- 输出拒绝覆盖，父目录需存在；成功向 stdout 输出 JSON 摘要。
- 台账中同一记录的多视角合并展示，视角数量之和可大于候选总数，不是重复计数。
- 未采指标与缺失视角原样列出，不用 0 或“达标”代替未知。
"""
import argparse
import base64
import csv
import hashlib
import html
import json
import os
from datetime import datetime
from pathlib import Path
import sys

VIEWS = ("界面样式", "交互易用性", "基础无障碍", "功能", "性能")
RISK_LABELS = {"high": ("t-red", "高"), "medium": ("t-amber", "中"), "low": ("t-gray", "低")}
STATUS_LABELS = {"NOT_RUN": ("t-gray", "未执行"), "PASS": ("t-green", "通过"),
                 "FAIL": ("t-red", "不通过"), "BLOCKED": ("t-amber", "阻塞"),
                 "SKIPPED": ("t-gray", "跳过")}
COVERAGE_LABELS = {"available": ("t-green", "已采"), "partial": ("t-amber", "部分"),
                   "missing": ("t-amber", "缺失"), "not-collected": ("t-gray", "未采")}
# 与 execution_ledger.HEADLINE_SCHEMA 同值：schema 不一致即视为旧摘要，
# 回退推断并标注，不静默按新口径解读旧字段（字段语义可能已变）。
HEADLINE_SCHEMA = "ledger-headline/1"

CSS = """
*{box-sizing:border-box}
body{margin:0;background:#f4f5f7;color:#1f2328;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;font-size:14px;line-height:1.65}
.wrap{max-width:1080px;margin:0 auto;padding:36px 24px 64px}
h1{font-size:23px;font-weight:600;margin:0 0 6px;letter-spacing:.2px}
h2{font-size:16px;font-weight:600;margin:34px 0 12px;padding-left:10px;border-left:3px solid #2f6feb}
h3{font-size:14px;font-weight:600;margin:18px 0 8px;color:#3d4349}
p{margin:8px 0}
.sub{color:#656d76;font-size:13px}
.card{background:#fff;border:1px solid #e2e5e9;border-radius:10px;padding:20px 22px;margin:12px 0}
.verdict{display:flex;gap:16px;align-items:flex-start;border-radius:10px;padding:18px 22px;margin:20px 0 8px;border:1px solid}
.v-amber{background:#fff8e6;border-color:#f0d48a}
.v-red{background:#fdeeee;border-color:#f0b4b4}
.v-green{background:#eef8ee;border-color:#b3ddb3}
.verdict .badge{flex:0 0 auto;font-weight:600;font-size:15px;padding:4px 12px;border-radius:6px;background:#8a6100;color:#fff}
.v-red .badge{background:#b32d2d}
.v-green .badge{background:#2c7a2c}
.verdict .body{flex:1}
.kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin:14px 0 4px}
.kpi{background:#fff;border:1px solid #e2e5e9;border-radius:9px;padding:14px}
.kpi .n{font-size:22px;font-weight:600;line-height:1.2}
.kpi .l{font-size:12px;color:#656d76;margin-top:2px}
.kpi.warn .n{color:#8a6100}
.kpi.bad .n{color:#b32d2d}
.kpi.ok .n{color:#2c7a2c}
table{width:100%;border-collapse:collapse;font-size:13px;margin:10px 0 2px}
th{text-align:left;font-weight:600;color:#4a5157;background:#fafbfc;padding:9px 10px;border-bottom:1px solid #e2e5e9;white-space:nowrap}
td{padding:9px 10px;border-bottom:1px solid #eef0f2;vertical-align:top}
tr:last-child td{border-bottom:none}
code{font-family:"SFMono-Regular",Consolas,monospace;font-size:12px;background:#f2f4f6;padding:1px 5px;border-radius:4px}
.tag{display:inline-block;font-size:11px;padding:2px 7px;border-radius:4px;border:1px solid;white-space:nowrap}
.t-red{background:#fdeeee;border-color:#f0b4b4;color:#a12828}
.t-amber{background:#fff8e6;border-color:#f0d48a;color:#8a6100}
.t-green{background:#eef8ee;border-color:#b3ddb3;color:#2c7a2c}
.t-gray{background:#f2f4f6;border-color:#dfe3e7;color:#5a6268}
.t-blue{background:#eef4fd;border-color:#bcd4f5;color:#1f5bb5}
.shot{border:1px solid #dfe3e7;border-radius:8px;overflow:hidden;background:#fff;margin:10px 0}
.shot img{display:block;width:100%;height:auto}
.shot .cap{padding:9px 12px;background:#fafbfc;border-top:1px solid #eef0f2;font-size:12px;color:#5a6268}
.note{background:#fafbfc;border-left:3px solid #cfd4d9;padding:10px 14px;font-size:13px;color:#4a5157;margin:10px 0;border-radius:0 6px 6px 0}
ul{margin:8px 0;padding-left:22px}
li{margin:4px 0}
.foot{margin-top:40px;padding-top:16px;border-top:1px solid #e2e5e9;color:#8a9199;font-size:12px}
@media print{body{background:#fff}.card{break-inside:avoid}h2{break-after:avoid}}
"""


def esc(value):
    return html.escape("" if value is None else str(value), quote=True)


def read_json(path, label):
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"{label}不存在: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label}不是合法JSON: {path} ({exc})") from exc


def read_ledger(path, label):
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"{label}不存在: {path}")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"{label}为空: {path}")
    for column in ("view", "id", "action", "risk", "status"):
        if column not in rows[0]:
            raise ValueError(f"{label}缺少列 {column}: {path}")
    return rows


def b64_image(path):
    with open(path, "rb") as handle:
        return "data:image/png;base64," + base64.b64encode(handle.read()).decode("ascii")


def sha256(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def tag(mapping, key, default=None):
    cls, label = mapping.get(key, ("t-gray", default or key or "未知"))
    return f'<span class="tag {cls}">{esc(label)}</span>'


def parse_triple(spec, label):
    parts = spec.split("::")
    if len(parts) != 3 or not all(part.strip() for part in parts):
        raise ValueError(f"{label}格式应为 标注::文件::文件，收到: {spec}")
    return [part.strip() for part in parts]


def merge_records(rows):
    merged, order = {}, []
    for row in rows:
        ident = row["id"]
        if ident not in merged:
            merged[ident] = dict(row)
            merged[ident]["views"] = []
            order.append(ident)
        if row["view"] not in merged[ident]["views"]:
            merged[ident]["views"].append(row["view"])
    return [merged[ident] for ident in order]


CHECK_STATUS_LABELS = {
    "ok": "通过",
    "limited": "受限",
    "missing": "缺失",
    "unknown": "未知",
}
CHECK_STATUS_CLASS = {
    "ok": "v-green",
    "limited": "v-amber",
    "missing": "v-red",
    "unknown": "v-amber",
}


def load_headline(path):
    """读取台账摘要里的首屏事实。返回 (headline 或 None, 回退原因说明)。

    四种情形必须可区分，报告里也要能看出来：
    1. 提供了 summary、含 headline 且 schema 与当前一致 → 消费同一份事实（正常路径）；
    2. 提供了 summary 但缺 headline 字段（旧摘要）→ 回退推断并**标注缺字段**；
    3. 提供了 summary 但 headline.schema 与当前不符 → 回退推断并**标注版本不符**；
    4. 未提供 summary → 回退推断并**标注未提供台账摘要**。

    2/3/4 都必须显式说明，不能静默按渲染器自己的口径解读——否则报告首屏
    看起来照样完整，读者却不知道限制因素是推断的。

    第 3 种是本函数存在的关键理由：字段语义会随版本变（例如某个 status 从两态
    扩成四态），拿旧 schema 的字段按新口径渲染，会得出一个"看起来合规"的错结论。
    版本不符时宁可回退到台账原始记录，也不猜字段含义。
    """
    if not path:
        return None, "未提供台账摘要：首屏的完成范围与限档原因由报告渲染器按台账记录推断，非台账 summary 的口径"
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        return None, f"台账摘要不可读（{exc}）：首屏限制因素由报告渲染器推断"
    if not isinstance(payload, dict):
        return None, "台账摘要格式非法：首屏限制因素由报告渲染器推断"
    headline = payload.get("headline")
    if not isinstance(headline, dict):
        return None, ("台账摘要缺首屏事实字段（headline）："
                      "首屏限制因素由报告渲染器按台账记录推断，非台账 summary 的口径")
    schema = str(headline.get("schema") or "").strip()
    if schema != HEADLINE_SCHEMA:
        shown = schema or "未标注"
        return None, (f"台账摘要的首屏事实版本不符（摘要 {shown}，本渲染器要求 {HEADLINE_SCHEMA}）："
                      "字段语义可能已变，首屏限制因素改由报告渲染器按台账记录推断，"
                      "请用同一版本重新生成 summary 后再渲染首屏")
    return headline, ""


def build(args):
    datasets = [parse_triple(spec, "--dataset") for spec in args.dataset]
    if not datasets:
        raise ValueError("至少需要一个 --dataset")
    loaded = []
    for label, ledger_path, gate_path in datasets:
        rows = read_ledger(ledger_path, f"台账({label})")
        gate = read_json(gate_path, f"门禁({label})")
        if not isinstance(gate.get("gate"), dict):
            raise ValueError(f"门禁({label})缺少 gate 字段")
        loaded.append({"label": label, "rows": rows, "gate": gate})

    records = merge_records([row for item in loaded for row in item["rows"]])
    if not records:
        raise ValueError("台账中没有任何记录")

    high_risk = sum(1 for row in records if row.get("risk") == "high")
    failed = sum(1 for row in records if row.get("status") == "FAIL")
    pending = sum(1 for row in records if row.get("status") == "NOT_RUN")
    view_counts = {}
    for row in records:
        for view in row["views"]:
            view_counts[view] = view_counts.get(view, 0) + 1
    missing_views = []
    per_dataset_missing = [set(item["gate"]["gate"].get("missing_views") or []) for item in loaded]
    if per_dataset_missing:
        common = set.intersection(*per_dataset_missing) if len(per_dataset_missing) > 1 else per_dataset_missing[0]
        missing_views = [view for view in VIEWS if view in common]
    # 覆盖偏薄的视角：有台账记录但页面×状态单元不足，报告必须区分「有记录」与
    # 「已按下限覆盖」——两者混为一谈会把「清点过控件」表述成「已验收该视角」。
    per_dataset_thin = [set(item["gate"]["gate"].get("thin_views") or []) for item in loaded]
    thin_views = []
    if per_dataset_thin:
        common_thin = (set.intersection(*per_dataset_thin)
                       if len(per_dataset_thin) > 1 else per_dataset_thin[0])
        thin_views = [view for view in VIEWS if view in common_thin]
    view_cells = {}
    # 旧版门禁产物没有 view_cells：此时整列都必须缺席，不能按 0 渲染——「0 个单元」
    # 与「该字段没采」在读者眼里都是 0，会把「有 8 条台账记录」读成「零覆盖」。
    cells_available = False
    for item in loaded:
        gate = item["gate"]["gate"]
        if "view_cells" in gate:
            cells_available = True
        for view, cells in (gate.get("view_cells") or {}).items():
            view_cells.setdefault(view, set()).update(cells)
    ceilings = []
    for item in loaded:
        ceiling = item["gate"]["gate"].get("verdict_ceiling")
        if ceiling and ceiling not in ceilings:
            ceilings.append(ceiling)

    performance_runs, not_available = [], []
    for spec in args.performance:
        payload = read_json(spec, "性能摘要")
        for run in payload.get("runs") or []:
            performance_runs.append(run)
        for entry in payload.get("notAvailable") or []:
            if entry not in not_available:
                not_available.append(entry)

    audits = []
    for spec in args.lighthouse:
        payload = read_json(spec, "审计报告")
        categories = payload.get("categories") or {}
        scores = []
        for key, label in (("accessibility", "无障碍"), ("best-practices", "最佳实践"), ("seo", "SEO")):
            if key in categories:
                raw = categories[key].get("score")
                scores.append((label, None if raw is None else int(round(raw * 100))))
        failed_audits = []
        for audit_id, audit in (payload.get("audits") or {}).items():
            if audit.get("score") == 0 and audit.get("scoreDisplayMode") not in ("notApplicable", "manual", "informative"):
                node = ""
                items = ((audit.get("details") or {}).get("items") or [])
                if items:
                    node = (items[0].get("node") or {}).get("selector") or ""
                failed_audits.append({"id": audit_id, "title": audit.get("title") or audit_id, "node": node})
        audits.append({"scores": scores, "failed": failed_audits})

    parts = []
    add = parts.append

    add('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">')
    add('<meta name="viewport" content="width=device-width,initial-scale=1">')
    add(f"<title>{esc(args.title)}</title><style>{CSS}</style></head><body><div class='wrap'>")
    add(f"<h1>{esc(args.title)}</h1>")
    add(f"<p class='sub'>{esc(args.subject)}　·　生成时间 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        f"　·　被测地址 {esc(records[0].get('page_url', ''))}</p>")

    ceiling_text = " / ".join(ceilings) if ceilings else "未给出"
    headline, headline_note = load_headline(getattr(args, "summary", None))
    reason_bits = []
    if pending:
        reason_bits.append(f"{pending} 项记录仍处于未执行状态")
    if missing_views:
        reason_bits.append("以下视角本轮无台账记录：" + "、".join(missing_views))
    if headline:
        # 正常路径：首屏直接消费台账摘要的首屏事实（完成范围 / 必查项 / 限档原因），
        # 不再由渲染器另算一套——各算一次必然漂移，且漂移方向通常是报好看的那个。
        scope = headline.get("completion_scope") or {}
        checks = headline.get("required_checks") or []
        limits = headline.get("limitation_reasons") or []
        ceiling_text = str(headline.get("verdict_ceiling") or ceiling_text)
        verdict_cls = ("v-red" if "不通过" in ceiling_text
                       else ("v-green" if ceiling_text == "通过" else "v-amber"))
        add(f"<div class='verdict {verdict_cls}'><div class='badge'>{esc(ceiling_text)}</div><div class='body'>")
        add(f"<div><b>技术结论上限为「{esc(ceiling_text)}」。</b></div>")
        add(f"<div><b>完成范围：</b>{esc(scope.get('text') or '未提供完成范围')}</div>")
        if scope.get("can_claim_readonly_complete"):
            add("<div><b>可写「只读范围内已完成」</b>（必查项实际完成且无其他缺口）。</div>")
        limit_text = "；".join(str(item.get("text") or item) for item in limits) \
            or "未见未完成项或未提供证据的限制因素"
        add(f"<div class='sub'><b>限档原因：</b>{esc(limit_text)}。</div>")
        if checks:
            add("<table><tr><th style='width:150px'>必查项</th><th style='width:80px'>状态</th>"
                "<th>说明</th></tr>")
            for check in checks:
                status = str(check.get("status") or "unknown")
                add(f"<tr><td>{esc(check.get('label') or check.get('id'))}</td>"
                    f"<td class='{CHECK_STATUS_CLASS.get(status, 'v-amber')}'>"
                    f"{esc(CHECK_STATUS_LABELS.get(status, status))}</td>"
                    f"<td>{esc(check.get('detail') or '')}</td></tr>")
            add("</table>")
        add("<div class='sub'>台账中的候选未经复核前不计缺陷。</div></div></div>")
    else:
        verdict_cls = "v-red" if "不通过" in ceilings else ("v-green" if ceilings and "通过" in ceiling_text and "待核实" not in ceiling_text else "v-amber")
        add(f"<div class='verdict {verdict_cls}'><div class='badge'>{esc(ceiling_text)}</div><div class='body'>")
        add(f"<div><b>技术结论上限为「{esc(ceiling_text)}」。</b></div>")
        add(f"<div class='sub'>{esc('；'.join(reason_bits) or '未见未执行记录或无记录视角，结论仍需人工断言复核')}。"
            "台账中的候选未经复核前不计缺陷。</div></div></div>")
    if headline_note:
        add(f"<div class='card'><b>首屏口径说明：</b>{esc(headline_note)}</div>")

    add("<div class='kpis'>")
    add(f"<div class='kpi'><div class='n'>{len(records)}</div><div class='l'>观察候选（去重）</div></div>")
    add(f"<div class='kpi warn'><div class='n'>{high_risk}</div><div class='l'>其中高风险</div></div>")
    add(f"<div class='kpi{' bad' if failed else ''}'><div class='n'>{failed}</div><div class='l'>台账记为不通过</div></div>")
    add(f"<div class='kpi'><div class='n'>{pending}</div><div class='l'>待执行复核</div></div>")
    first_score = next((score for audit in audits for label, score in audit["scores"] if label == "无障碍"), None)
    add(f"<div class='kpi{' warn' if first_score is not None and first_score < 100 else ''}'>"
        f"<div class='n'>{first_score if first_score is not None else '—'}</div><div class='l'>Lighthouse 无障碍</div></div>")
    add("</div>")

    add("<h2>一、测试范围与环境</h2><div class='card'><table>")
    add("<tr><th style='width:150px'>项目</th><th>实际值</th></tr>")
    rows = [
        ("被测地址", records[0].get("page_url", "")),
        ("应用版本标记", records[0].get("application_version", "")),
        ("执行环境", records[0].get("environment", "")),
        ("台账数据集", "；".join(item["label"] for item in loaded)),
        ("记录条数", f"{len(records)} 条（按 ID 去重）"),
    ]
    for key, value in rows:
        add(f"<tr><td style='color:#656d76'>{esc(key)}</td><td>{esc(value)}</td></tr>")
    add("</table></div>")

    add("<h2>二、五视角覆盖与门禁</h2><div class='card'>")
    add("<p class='sub'>同一记录可同时属于多个视角，因此各视角数量之和大于候选总数，这是设计如此，不是重复计数。</p>")
    add("<table><tr><th>视角</th><th style='width:90px'>本轮条目</th>"
        + ("<th style='width:130px'>页面×状态单元</th>" if cells_available else "")
        + "<th>状态</th></tr>")
    for view in VIEWS:
        count = view_counts.get(view, 0)
        if view in view_cells:
            cells_text = str(len(view_cells[view]))
        else:
            cells_text = "—"  # 该视角未采单元数，与「确实为 0 个」区分
        if count:
            if view in thin_views:
                state = "<span class='tag t-amber'>记录偏薄</span>"
            else:
                state = "<span class='tag t-blue'>有台账记录</span>"
        elif view in missing_views:
            state = "<span class='tag t-red'>本轮无记录</span>"
        else:
            state = "<span class='tag t-gray'>未纳入本次范围</span>"
        row = f"<tr><td>{esc(view)}</td><td>{count}</td>"
        if cells_available:
            row += f"<td>{cells_text}</td>"
        add(row + f"<td>{state}</td></tr>")
    add("</table>")
    add("<div class='note'>门禁规则：可执行候选未清空、或必查视角无记录时，技术结论上限只能是「待核实」；"
        "「记录偏薄」表示该视角有台账记录但页面×状态单元不足，相关结论不得按「已验收」表述。"
        + ("" if cells_available else "本报告的依据产物未提供页面×状态单元数据，该列不显示。") + "</div>")
    add("<h3>各数据集的门禁结果</h3><table><tr><th>数据集</th><th>候选数</th><th>待执行</th>"
        "<th>高风险待执行</th><th>缺失视角</th><th>门禁自身结论上限</th></tr>")
    for item in loaded:
        gate = item["gate"]["gate"]
        add(f"<tr><td>{esc(item['label'])}</td><td>{esc(item['gate'].get('planned', ''))}</td>"
            f"<td>{esc(gate.get('ready_pending', ''))}</td><td>{esc(gate.get('ready_high_risk', ''))}</td>"
            f"<td>{esc('、'.join(gate.get('missing_views') or []) or '无')}</td>"
            f"<td>{esc(gate.get('verdict_ceiling', ''))}</td></tr>")
    add("</table>")
    add("<div class='note'>本表是<b>各数据集门禁自身</b>的判定，不是本页的技术结论上限。"
        "技术结论上限取自台账摘要的 headline（综合各门禁与执行事实），显示在首屏；"
        "两者口径不同，出现差异时<b>以首屏为准</b>。</div></div>")

    if args.screenshot:
        add("<h2>三、视觉证据</h2>")
        for spec in args.screenshot:
            path, title, note = parse_triple(spec, "--screenshot")
            image = Path(path)
            if not image.is_file():
                raise ValueError(f"截图不存在: {path}")
            if image.stat().st_size == 0:
                raise ValueError(f"截图为空文件: {path}")
            add(f"<div class='shot'><img src='{b64_image(image)}' alt='{esc(title)}'>")
            add(f"<div class='cap'><b>{esc(title)}</b>　{esc(note)}　·　文件 <code>{esc(image.name)}</code>"
                f"　·　SHA-256 <code>{sha256(image)[:16]}…</code></div></div>")

    add("<h2>四、记录明细</h2><div class='card'>")
    add("<p class='sub'>以下为台账中的待复核线索，action 为下一步应执行的复核动作；未执行前不计缺陷。</p>")
    add("<table><tr><th style='width:190px'>记录 ID</th><th>所属视角</th><th style='width:56px'>风险</th>"
        "<th style='width:76px'>状态</th><th>下一步复核动作</th></tr>")
    for row in records:
        add(f"<tr><td><code>{esc(row['id'])}</code></td><td>{esc('、'.join(row['views']))}</td>"
            f"<td>{tag(RISK_LABELS, row.get('risk'))}</td><td>{tag(STATUS_LABELS, row.get('status'))}</td>"
            f"<td>{esc(row.get('action', ''))}</td></tr>")
    add("</table></div>")

    if audits:
        add("<h2>五、基础无障碍（外部审计）</h2><div class='card'>")
        for audit in audits:
            if audit["scores"]:
                add("<table><tr><th>类别</th><th style='width:120px'>得分</th></tr>")
                for label, score in audit["scores"]:
                    cls = "t-green" if score is not None and score >= 90 else ("t-amber" if score is not None and score >= 50 else "t-red")
                    add(f"<tr><td>{esc(label)}</td><td><span class='tag {cls}'>{score if score is not None else '—'}</span></td></tr>")
                add("</table>")
            if audit["failed"]:
                add("<h3>未通过项</h3><table><tr><th style='width:180px'>审计项</th><th>说明</th><th>命中节点</th></tr>")
                for entry in audit["failed"]:
                    node = f"<code>{esc(entry['node'])}</code>" if entry["node"] else "—"
                    add(f"<tr><td><code>{esc(entry['id'])}</code></td><td>{esc(entry['title'])}</td><td>{node}</td></tr>")
                add("</table>")
        add("<div class='note'>外部审计与只读脚本是两套独立证据，同一元素被两边同时命中时可互相印证，但仍需真实操作复核后才可定性。</div>")
        add("</div>")

    if performance_runs or not_available:
        add("<h2>六、性能（分族实测）</h2><div class='card'>")
        for run in performance_runs:
            add(f"<h3>{esc(run.get('kind', '未标注场景'))}</h3>")
            add("<table><tr><th style='width:200px'>指标</th><th style='width:120px'>观测值</th><th>说明</th></tr>")
            for metric in run.get("metrics") or []:
                add(f"<tr><td>{esc(metric.get('name', ''))}</td><td><b>{esc(metric.get('value', ''))}</b></td>"
                    f"<td>{esc(metric.get('note', ''))}</td></tr>")
            add("</table>")
        if not_available:
            add("<h3>未取得的数据</h3><ul>")
            for entry in not_available:
                add(f"<li>{esc(entry)}</li>")
            add("</ul>")
        add("<div class='note'>未采指标保持未知，不用 0 或“达标”代替；单次实验不外推真实用户分布。</div>")
        add("</div>")

    if args.evidence_dir:
        folder = Path(args.evidence_dir)
        if not folder.is_dir():
            raise ValueError(f"证据目录不存在: {folder}")
        files = sorted(name for name in os.listdir(folder) if (folder / name).is_file())
        add("<h2>七、证据文件与指纹</h2><div class='card'><table>")
        add("<tr><th>文件</th><th style='width:170px'>大小</th><th>SHA-256</th></tr>")
        for name in files:
            path = folder / name
            add(f"<tr><td><code>{esc(name)}</code></td><td>{path.stat().st_size:,} 字节</td>"
                f"<td><code>{sha256(path)[:32]}…</code></td></tr>")
        add("</table></div>")

    add("<h2>八、未测项与边界</h2><div class='card'><ul>")
    if missing_views:
        add(f"<li>以下视角本轮无台账记录：{esc('、'.join(missing_views))}，相关结论一概不成立。</li>")
    if thin_views:
        add(f"<li>以下视角有台账记录但覆盖偏薄（页面×状态单元不足）：{esc('、'.join(thin_views))}，"
            "不得表述为「已验收」，只能表述为「有记录、深度不足」。</li>")
    if pending:
        add(f"<li>{pending} 项记录仍为未执行，候选未经交互复核前不能认定为缺陷。</li>")
    for entry in not_available:
        add(f"<li>性能数据缺口：{esc(entry)}</li>")
    for note in args.note:
        add(f"<li>{esc(note)}</li>")
    add("<li>台账只记录观察与执行状态，业务结论仍以实际断言与证据内容复核为准。</li>")
    add("</ul></div>")

    add(f"<div class='foot'>本报告由台账 CSV 与采集 JSON 自动渲染，页面数字与台账一致，未手工改写。"
        f"报告为阅读视图，数据以 CSV 与 JSON 原件为准。生成于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}。</div>")
    add("</div></body></html>")
    return "\n".join(parts), len(records)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="把台账与采集证据渲染成单文件 HTML 报告")
    parser.add_argument("--output", required=True)
    parser.add_argument("--title", default="网站验收测试报告")
    parser.add_argument("--subject", default="")
    parser.add_argument("--dataset", action="append", default=[],
                        help="标注::台账CSV::门禁JSON，可多次；门禁JSON须为探索清单门禁"
                             "（exploration_state.py 输出的整份报告，或含顶层 gate 字段的 JSON），"
                             "攻击覆盖结论等其它门禁产物不适用")
    parser.add_argument("--screenshot", action="append", default=[],
                        help="图片路径::标题::说明，可多次；图片以base64内联")
    parser.add_argument("--performance", action="append", default=[], help="性能摘要JSON，可多次")
    parser.add_argument("--lighthouse", action="append", default=[], help="外部审计报告JSON，可多次")
    parser.add_argument("--evidence-dir", default=None)
    parser.add_argument("--summary", default=None,
                        help="台账 summary JSON（execution_ledger.py summary 的输出）。"
                             "提供时首屏直接消费其 headline（完成范围/必查项状态/限档原因）；"
                             "未提供或缺 headline 字段时回退到渲染器推断，并在首屏如实标注")
    parser.add_argument("--note", action="append", default=[], help="本次特有的未测项说明，可多次")
    args = parser.parse_args(argv)
    try:
        document, count = build(args)
        output = Path(args.output)
        if not output.parent.is_dir():
            raise ValueError("输出父目录不存在")
        with output.open("x", encoding="utf-8") as handle:
            handle.write(document)
        print(json.dumps({"output": str(output), "records": count,
                          "bytes": output.stat().st_size,
                          "inline_images": "data:image/png;base64," in document},
                         ensure_ascii=False))
        return 0
    except (ValueError, OSError, TypeError) as exc:
        print(f"报告渲染输入/工具错误: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
