#!/usr/bin/env node
/**
 * 可访问性规则审计驱动：7.41
 *
 * 与 7.19 的分工：7.19 做可访问性树的跨轮结构 diff（首次无基线只能 dataMissing），
 * 本驱动做单次运行的规则合规判定——首次运行就能给出 pass/fail。
 *
 * 两条通道：
 *   builtin —— scripts/a11y_audit.js 内置的 WCAG 2.1 A/AA 子集（纯计算，零依赖）
 *   axe     —— 传入 --axe <axe.min.js 路径> 时额外注入跑一遍，结果合并
 * 产物必须带 rulesCovered 与 coverageNote：内置规则是子集，不得声称全量合规。
 *
 * 用法：
 *   node run_a11y_audit.cjs --url "https://目标页" [--out "./parity-test"]
 *     [--axe "./axe.min.js"] [--wait 600] [--profile 资料目录] [--headed] [--chrome 路径]
 *
 * 产物：<out>/a11y_audit.json
 */
const fs = require('fs');
const path = require('path');
const { launch, closeAll, restoreAuth } = require('./browser.cjs');
const { verifyPageIdentity } = require('./page_identity.cjs');

function arg(name, def) {
  const i = process.argv.indexOf('--' + name);
  return i >= 0 && process.argv[i + 1] && !process.argv[i + 1].startsWith('--') ? process.argv[i + 1] : def;
}
const URL_ = arg('url');
const OUT = arg('out', path.join(process.cwd(), 'parity-test'));
const PROFILE = arg('profile', null);
const CHROME = arg('chrome', null);
const AXE = arg('axe', null);
const HEADED = process.argv.includes('--headed');
const WAIT = parseInt(arg('wait', '600'), 10);

if (!URL_ && require.main === module) { console.error('ERROR: --url is required'); process.exit(1); }

const AUDIT_SRC = fs.readFileSync(path.join(__dirname, '..', 'a11y_audit.js'), 'utf8');

function writeNotRun(reason) {
  fs.mkdirSync(OUT, { recursive: true });
  const payload = {
    url: URL_, engine: 'builtin', error: 'audit-not-executed',
    findings: [{
      dimension: '7.41',
      symptom: '可访问性规则审计未能执行：' + reason,
      expected: '能在页面上跑完内置规则集并给出结论',
      actual: reason,
      severityHint: 'P3', severity: 'P3', confidence: 1, status: 'notRun',
      property: 'a11y-not-executed',
      element: { selector: '', nth: 0, bbox: { x: 0, y: 0, w: 0, h: 0 }, anchor: '' },
      raw: { reason: reason }, evidence: [{ type: 'driver-error' }], sources: ['a11y-driver'],
    }],
    summary: { total: 1, notRun: 1 },
    coverageNote: '未执行，不代表合规也不代表不合规。',
  };
  const outFile = path.join(OUT, 'a11y_audit.json');
  fs.writeFileSync(outFile, JSON.stringify(payload, null, 2), 'utf8');
  console.error('[x] ' + reason);
  console.log(JSON.stringify({ out: outFile, status: 'notRun', reason: reason }, null, 2));
}

/** axe impact → 缺陷等级 */
function severityOfImpact(impact) {
  if (impact === 'critical') return 'P1';
  if (impact === 'serious') return 'P2';
  if (impact === 'moderate') return 'P2';
  return 'P3';
}

/** 把 axe 结果并进内置 findings（同一 findings 契约，规则名带 axe- 前缀） */
function mergeAxe(parsed, axeRaw) {
  const merged = [];
  (axeRaw.violations || []).forEach((v) => {
    (v.nodes || []).forEach((n) => {
      merged.push({
        dimension: '7.41',
        symptom: 'axe 规则违规 [' + v.id + '] ' + v.help + '：' + n.target,
        expected: '符合 ' + ((v.wcag || []).join(',') || 'WCAG 2.1 AA') + ' 规则 ' + v.id,
        actual: String(n.html || '').slice(0, 100),
        severityHint: severityOfImpact(v.impact), severity: severityOfImpact(v.impact),
        confidence: 0.85, status: 'confirmed', property: 'axe-' + v.id,
        wcag: (v.wcag || []).join(','),
        element: { selector: n.target, nth: 0, bbox: { x: 0, y: 0, w: 0, h: 0 }, anchor: v.id },
        evidenceType: 'a11y-axe', rule: 'axe-' + v.id,
        raw: { impact: v.impact, nodeCount: v.nodeCount },
        evidence: [{ type: 'a11y-axe', rule: v.id }], sources: ['a11y-axe'],
      });
    });
  });
  parsed.findings = parsed.findings.concat(merged);
  parsed.axeViolations = (axeRaw.violations || []).length;
  parsed.engine = 'axe+builtin';
  parsed.coverageNote = String(parsed.coverageNote || '')
    + ' 本轮已补充注入 axe-core，覆盖范围以 axe 的 wcag2a/wcag2aa/wcag21a/wcag21aa 规则集为准。';
  return { violations: (axeRaw.violations || []).length, nodeFindings: merged.length };
}

async function runAxe(page) {
  await page.addScriptTag({ path: AXE });
  return page.evaluate(async () => {
    if (typeof window.axe === 'undefined' || !window.axe.run) {
      return { error: 'axe-not-loaded' };
    }
    const result = await window.axe.run(document, {
      resultTypes: ['violations'],
      runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] },
    });
    return {
      violations: (result.violations || []).map((v) => ({
        id: v.id, impact: v.impact, help: v.help,
        wcag: (v.tags || []).filter((t) => t.indexOf('wcag') === 0),
        nodeCount: (v.nodes || []).length,
        nodes: (v.nodes || []).slice(0, 5).map((n) => ({
          target: (n.target || []).join(' '),
          html: String(n.html || '').slice(0, 120),
        })),
      })),
    };
  });
}

async function main() {
  if (!URL_) { console.error('ERROR: --url is required'); process.exit(1); }
  const handle = await launch({ profile: PROFILE, headed: HEADED, chrome: CHROME });
  if (handle.error) {
    writeNotRun(handle.error);
    process.exit(2);
  }

  try {
    await restoreAuth(handle.context);
    const page = handle.context.pages()[0] || await handle.context.newPage();
    // 不用 process.exit：它跳过 finally，浏览器不会被关（见 browser.cjs 的说明）
    let gotoError = null;
    try {
      await page.goto(URL_, { waitUntil: 'load', timeout: 60000 });
    } catch (e) {
      gotoError = String(e).slice(0, 140);
    }
    if (gotoError) {
      writeNotRun('起始页不可达：' + gotoError);
      process.exitCode = 2;
      return;
    }
    // 页面身份校验：未达目标态（登录页/错误页/空壳）时不产出审计结论，
    // 全 notRun 留因——登录页的 a11y 问题不是目标页的。
    const identity = await verifyPageIdentity(page, URL_);
    if (!identity.reachedTarget) {
      writeNotRun('页面未达目标态（' + (identity.isLoginPage ? '登录页' : '身份校验失败')
        + '，原因: ' + identity.reasons.join('; ') + '）：' + identity.actualUrl);
      process.exitCode = 5;
      return;
    }
    // 不做 stabilize：稳定化会隐藏抖动元素，改变可见性判定，与"真实渲染态"不符
    await page.waitForTimeout(isNaN(WAIT) ? 600 : WAIT);

    const raw = await page.evaluate(AUDIT_SRC);
    const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw;
    if (!parsed || typeof parsed !== 'object' || !Array.isArray(parsed.findings)) {
      writeNotRun('审计脚本返回形状异常（既不是字符串也不是含 findings 的对象）');
      process.exitCode = 2;
      return;
    }

    // ---------- 可选 axe 通道：补强挂了不能拖垮内置结论 ----------
    let axeState = { requested: !!AXE, status: 'not-requested' };
    if (AXE) {
      if (!fs.existsSync(AXE)) {
        axeState = { requested: true, status: 'missing', reason: '文件不存在: ' + AXE };
      } else {
        try {
          const axeRaw = await runAxe(page);
          if (axeRaw && axeRaw.error) {
            axeState = { requested: true, status: 'failed', reason: axeRaw.error };
          } else {
            const stat = mergeAxe(parsed, axeRaw);
            axeState = { requested: true, status: 'ran', violations: stat.violations,
                         nodeFindings: stat.nodeFindings };
          }
        } catch (e) {
          axeState = { requested: true, status: 'failed', reason: String(e).slice(0, 160) };
          console.warn('[warn] axe 补充通道失败（内置结论仍然有效）: ' + axeState.reason);
        }
      }
    }

    const findings = parsed.findings;
    parsed.axe = axeState;
    parsed.startedAt = new Date().toISOString();
    parsed.summary = Object.assign({}, parsed.summary, {
      total: findings.length,
      confirmed: findings.filter((f) => f.status === 'confirmed').length,
      pending: findings.filter((f) => f.status === 'pending').length,
      pass: findings.filter((f) => f.status === 'pass').length,
      byStatus: findings.reduce((acc, f) => { acc[f.status] = (acc[f.status] || 0) + 1; return acc; }, {}),
    });

    fs.mkdirSync(OUT, { recursive: true });
    const outFile = path.join(OUT, 'a11y_audit.json');
    fs.writeFileSync(outFile, JSON.stringify(parsed, null, 2), 'utf8');
    console.log(JSON.stringify({
      out: outFile,
      engine: parsed.engine,
      axe: axeState,
      rulesCovered: parsed.summary.rulesCoveredCount,
      findings: parsed.summary.total,
      byStatus: parsed.summary.byStatus,
    }, null, 2));
  } finally {
    await closeAll(handle);
  }
}

if (require.main === module) {
  main().catch((e) => { console.error('FATAL', String(e)); process.exit(1); });
}

module.exports = { severityOfImpact, mergeAxe };
