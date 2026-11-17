#!/usr/bin/env node
/**
 * 状态场景驱动：7.25 暗色模式 / 7.26 打印视图 / 7.32 页面级状态残留 / 7.33 接口异常
 *
 * 为什么需要这个驱动：discover_scenarios.py 早已生成这四个维度的场景（action 为
 * theme / media / route-back / network），scenario-contract.md 也定义了这些 action，
 * 但执行侧没有对应驱动——这些场景在实际走查里普遍没被执行， report 里只能填 notRun。
 * 本驱动把四类场景变成可执行、可判定、可复现的显式步骤，每个场景产出
 * { dimension, status, findings[] }，status 取值与 coverage_gate.py 的门禁词表一致
 * （pass / fail / pending / notRun）。
 *
 * 安全边界（为什么默认 safe 策略下也能跑）：
 *   - 7.25 / 7.26 只是 page.emulateMedia 切换渲染模式，纯客户端，零副作用；
 *   - 7.32 只做 hash 导航 + history 返回，不提交任何表单；
 *   - 7.33 用 page.route **拦载** XHR/fetch 并直接 fulfill 500——请求根本不到服务端，
 *     不可能产生真实写操作；文档类资源（html/css/img）放行，保证页面能渲染。
 *   - staticOnly 策略下不做任何页面交互，四类场景全部 notRun 并写明原因。
 *
 * 用法：
 *   node run_state_scenarios.cjs --url "https://目标页" [--out "./parity-test/state-scenarios"]
 *     [--profile "资料目录"] [--safety-policy safe] [--only 7.25,7.33] [--headed] [--chrome 路径]
 *
 * 产物：
 *   <out>/state_scenarios.json  —— 场景结果（含 findings 与 status）
 *   <out>/shots/<dimension>.png —— 各场景截图（暗色/打印/异常态）
 */
const fs = require('fs');
const path = require('path');
const { stabilize, resolveChrome } = require('../stabilize.js');
const { verifyPageIdentity } = require('./page_identity.cjs');

function arg(name, def) {
  const i = process.argv.indexOf('--' + name);
  return i >= 0 && process.argv[i + 1] && !process.argv[i + 1].startsWith('--') ? process.argv[i + 1] : def;
}
const URL_ = arg('url');
const OUT = arg('out', path.join(process.cwd(), 'state-scenarios'));
const PROFILE = arg('profile', null);
const CHROME = arg('chrome', null);
const HEADED = process.argv.includes('--headed');
const SAFETY_POLICY = arg('safety-policy', 'safe'); // all | safe | mockWrites | staticOnly
const ONLY = new Set(String(arg('only', '') || '').split(',').map(s => s.trim()).filter(Boolean));
// 【F39】--only 合法值校验：typo（--only 7.3）会让四维度全部静默不执行，连 notRun 都不产。
const VALID_DIMS = ['7.25', '7.26', '7.32', '7.33'];
{
  const bad = Array.from(ONLY).filter(d => !VALID_DIMS.includes(d));
  if (bad.length) {
    console.error('[x] --only 含非法维度: ' + bad.join(', ') + '（合法值: ' + VALID_DIMS.join(', ') + '）');
    process.exit(1);
  }
}
const SHOTS = path.join(OUT, 'shots');

const PROBE_SRC = fs.readFileSync(path.join(__dirname, '..', 'state_probes.js'), 'utf8');

if (!URL_ && require.main === module) { console.error('ERROR: --url is required'); process.exit(1); }

/**
 * 同步解析 Playwright（对齐 run_button_audit.cjs 的驱动模式）。
 * 不用 stabilize.js 的 resolvePlaywright——那个是 async 函数返回 Promise，
 * 漏 await 会拿到 Promise 当模块用（pw.chromium 为 undefined）。
 * 候选取 playwright-core 与 playwright（完整包）两种，兼容只装其一的环境。
 */
function resolvePlaywright() {
  const candidates = [
    'playwright-core',
    'playwright',
    path.join(process.cwd(), 'node_modules', 'playwright-core'),
    path.join(process.cwd(), 'node_modules', 'playwright'),
  ];
  if (process.env.PLAYWRIGHT_CORE_PATH) candidates.push(process.env.PLAYWRIGHT_CORE_PATH);
  for (const c of candidates) { try { return require(c); } catch (e) { /* next */ } }
  return null;
}

/** 解析登录态（可选）：与 run_button_audit.cjs 共用 auth.cjs */
function loadAuth() {
  try { return require('./auth.cjs'); } catch (e) { return null; }
}

async function probe(page, mode, label) {
  // Playwright 的 evaluate 只收**一个**参数：多传会抛 "Too many arguments"，
  // 而且这个异常会被每个维度的 catch 吞成「执行异常」，四个维度一起变 notRun。
  await page.evaluate((cfg) => { window.__vpProbe = { mode: cfg.mode, label: cfg.label }; },
    { mode, label: label || '' });
  const raw = await page.evaluate(PROBE_SRC);
  const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw;
  // 【F51】state_probes 的 F40 契约：读不到 mode 时返回 { error, detail, findings: [] }。
  // 旧版只取 findings——error JSON 的空数组被当成"探测跑完、没发现问题"记 pass：
  // 探测根本没执行，结论却是通过（假通过，且外观完全合理）。error 必须透出，
  // 由调用方记 notRun + detail（与 7.32 goBack 失败、7.33 reload 失败同口径）。
  return parsed && typeof parsed === 'object' ? parsed : { findings: [] };
}

function statusOfFindings(findings) {
  if (!findings.length) return 'pass';
  if (findings.some(f => f.status === 'confirmed')) return 'fail';
  return 'pending';
}

/** 7.32 状态快照：URL / 滚动 / 可见筛选项 / 内容量 */
async function captureState(page) {
  return page.evaluate(() => {
    const WS = new RegExp('[' + String.fromCharCode(32, 9, 10, 13, 12, 11, 0xA0, 0x3000) + ']+', 'g');
    const chips = Array.prototype.slice.call(
      document.querySelectorAll('[class*=filter] [class*=active], [class*=tag] [class*=active], [class*=selected], [aria-selected=true]')
    ).map(el => (el.innerText || '').trim()).filter(Boolean).slice(0, 10);
    return {
      url: location.href,
      hash: location.hash,
      scrollY: Math.round(window.scrollY || 0),
      chips,
      visibleChars: ((document.body && document.body.innerText) || '').replace(WS, '').length,
    };
  });
}

/** 7.32 状态残留比对：只硬判"返回后白屏"这一确定缺陷，其余（保留/重置）是设计决策 → pending */
function residueFindings(before, after) {
  const out = [];
  if (after.visibleChars < 20) {
    out.push({
      dimension: '7.32', symptom: '从其它路由返回后页面近乎空白，内容没有恢复',
      expected: '返回后页面内容恢复正常渲染', actual: '返回后可见文本仅 ' + after.visibleChars + ' 字符',
      severityHint: 'P1', severity: 'P1', confidence: 0.8, status: 'confirmed',
      element: null, evidence: [{ type: 'state-compare', result: 'mismatch' }],
      sources: ['state-scenarios'], property: 'route-back-blank', raw: { after },
    });
    return out;
  }
  if (after.hash !== before.hash) {
    out.push({
      dimension: '7.32', symptom: '从其它路由返回后 URL/锚点与进入时不一致',
      expected: '返回后回到进入时的路由', actual: '进入 hash=' + before.hash + '，返回后 hash=' + after.hash,
      severityHint: 'P3', severity: 'P3', confidence: 0.5, status: 'pending',
      element: null, evidence: [{ type: 'state-compare', result: 'info' }],
      sources: ['state-scenarios'], property: 'route-back-hash', raw: { before: before.hash, after: after.hash },
    });
  }
  if (Math.abs(after.scrollY - before.scrollY) > 100) {
    out.push({
      dimension: '7.32', symptom: '返回后滚动位置与进入时差异较大，可能状态残留或意外重置',
      expected: '滚动位置按设计保留或重置（二者皆可，但不应随机）',
      actual: '进入 scrollY=' + before.scrollY + '，返回后 scrollY=' + after.scrollY,
      severityHint: 'P3', severity: 'P3', confidence: 0.5, status: 'pending',
      element: null, evidence: [{ type: 'state-compare', result: 'info' }],
      sources: ['state-scenarios'], property: 'route-back-scroll', raw: { before: before.scrollY, after: after.scrollY },
    });
  }
  if (before.chips.join('|') !== after.chips.join('|')) {
    out.push({
      dimension: '7.32', symptom: '返回后可见筛选项与进入时不一致，筛选状态可能残留',
      expected: '筛选态按设计保留或重置', actual: '进入=[' + before.chips.join(',') + ']，返回后=[' + after.chips.join(',') + ']',
      severityHint: 'P3', severity: 'P3', confidence: 0.5, status: 'pending',
      element: null, evidence: [{ type: 'state-compare', result: 'info' }],
      sources: ['state-scenarios'], property: 'route-back-filter', raw: { before: before.chips, after: after.chips },
    });
  }
  return out;
}

async function runDark(page) {
  // 【F41】stabilize.js 注入的 `color-scheme: light !important` 会压过
  // emulateMedia(dark) 的 UA 暗色渲染 → 7.25「背景近白」判定系统性假阳
  // （或掩盖真缺陷）。
  // 【审查修复】旧版把整个含 color-scheme 的 <style> 块删掉——那是 stabilize.js
  // 的 STABILIZE_CSS，除 color-scheme 外还有禁动画/隐藏抖动元素等全部稳定化
  // 规则，且注释声称"探测后由调用方页面恢复流程重建"实际没有任何调用方重建：
  // 后续 7.32/7.33 场景在失去全部稳定化规则的页面上跑，抖动/动画干扰系统性
  // 回归。改为只摘除 color-scheme 声明本身，其余稳定化规则全程在场，无需重建。
  await page.evaluate(() => {
    document.querySelectorAll('style').forEach((s) => {
      if (s.textContent && s.textContent.indexOf('color-scheme') !== -1) {
        s.textContent = s.textContent.replace(/color-scheme\s*:\s*[^;}]+;?/gi, '');
      }
    });
  }).catch(() => {});
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.waitForTimeout(400);
  const shot = path.join(SHOTS, '7.25-dark.png');
  await page.screenshot({ path: shot }).catch(() => {});
  const pr = await probe(page, 'dark');
  // 还原用 null（=取消该覆盖，回到系统默认）；写死 'light' 会在深色默认环境下
  // 污染后续 7.32/7.33 的渲染基准。
  await page.emulateMedia({ colorScheme: null });
  // 【F51】探测未执行（mode 丢失等）≠ 没发现问题：记 notRun 并带上 detail
  if (pr.error) return { dimension: '7.25', status: 'notRun', reason: '探测未执行：' + (pr.detail || pr.error), findings: [] };
  const findings = pr.findings || [];
  return { dimension: '7.25', status: statusOfFindings(findings), findings, screenshot: shot };
}

async function runPrint(page) {
  await page.emulateMedia({ media: 'print' });
  await page.waitForTimeout(400);
  const shot = path.join(SHOTS, '7.26-print.png');
  await page.screenshot({ path: shot }).catch(() => {});
  const pr = await probe(page, 'print');
  await page.emulateMedia({ media: null });
  // 【F51】探测未执行（mode 丢失等）≠ 没发现问题：记 notRun 并带上 detail
  if (pr.error) return { dimension: '7.26', status: 'notRun', reason: '探测未执行：' + (pr.detail || pr.error), findings: [] };
  const findings = pr.findings || [];
  return { dimension: '7.26', status: statusOfFindings(findings), findings, screenshot: shot };
}

async function runResidue(page) {
  const before = await captureState(page);
  await page.evaluate(() => { location.hash = '#vp-state-probe'; });
  await page.waitForTimeout(600);
  const nav = await page.goBack({ waitUntil: 'domcontentloaded' }).catch(() => null);
  if (nav === null) {
    return { dimension: '7.32', status: 'notRun', reason: '无历史记录可返回（goBack 返回 null），状态残留无法实测',
             findings: [] };
  }
  await page.waitForTimeout(600);
  const after = await captureState(page);
  const findings = residueFindings(before, after);
  return { dimension: '7.32', status: statusOfFindings(findings), findings };
}

/* ---------------- 7.33 网络条件矩阵 ----------------
 * 三类条件走的是前端三条**不同的代码路径**，暴露的缺陷也不同：
 *   http-500 : 响应到了但状态码非 2xx   → 走 !resp.ok 分支
 *   offline  : 请求根本发不出去          → 走 catch 分支（TypeError: Failed to fetch）
 *   slow     : 请求慢但最终成功          → 走加载态分支（有没有骨架/转圈）
 * 只测 500 会把「catch 分支没写错误处理」「加载态缺失」这两整片漏掉，
 * 而它们在报告里完全看不出来——7.33 显示 pass，真实的断网表现从没被看过。
 *
 * 安全边界不变：仍然只拦 XHR/fetch，文档/cdn 资源放行（否则页面根本打不开，
 * 测到的是浏览器错误页而不是应用的降级表现）。弱网用 CDP 节流，不拦请求，
 * 因此不会改变任何请求的目标地址。
 */
const NETWORK_CONDITIONS = [
  {
    name: 'http-500', label: '接口返回 500',
    route: async (route) => {
      const type = route.request().resourceType();
      if (type === 'xhr' || type === 'fetch') {
        await route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: 'mocked-500' }) });
      } else {
        await route.continue();
      }
    },
  },
  {
    name: 'offline', label: '断网（请求无法发出）',
    route: async (route) => {
      const type = route.request().resourceType();
      if (type === 'xhr' || type === 'fetch') {
        await route.abort('internetdisconnected');
      } else {
        await route.continue();
      }
    },
  },
  {
    name: 'slow', label: '弱网（1.2s RTT / 1.5Mbps）',
    // 走 CDP 节流，不拦请求：测的是「慢但成功」这条路径
    slow: { latency: 1200, downloadThroughput: Math.round(1.5 * 1024 * 1024 / 8), uploadThroughput: Math.round(750 * 1024 / 8) },
  },
];

const NAMED_NET_CONDITIONS = (() => {
  const raw = String(arg('network-conditions', '500,offline,slow') || '').trim();
  if (!raw || raw === 'all') return NETWORK_CONDITIONS.slice();
  const wanted = raw.split(',').map(s => s.trim()).filter(Boolean)
    .map(s => (s === '500' ? 'http-500' : (s === 'slow3g' ? 'slow' : s)));
  return NETWORK_CONDITIONS.filter(c => wanted.indexOf(c.name) >= 0);
})();

/**
 * 复位 CDP 网络节流。失败必须出声：残留节流会让后续维度（性能/可访问性）
 * 在被人为拖慢的网络下测量，出来的数没法解释，而报告上看不出任何异常。
 */
async function resetThrottle(client) {
  try {
    await client.send('Network.emulateNetworkConditions',
      { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
    await client.detach();
  } catch (e) {
    console.warn('[warn] CDP 节流复位失败，后续维度可能在错误网络下测量: '
      + String((e && e.message) || e).slice(0, 140));
  }
}

async function runNetworkCondition(page, cond) {
  const out = { condition: cond.name, label: cond.label, findings: [], status: 'notRun' };
  let client = null;
  if (cond.route) {
    await page.route('**/*', cond.route);
  } else if (cond.slow) {
    try {
      client = await page.context().newCDPSession(page);
      await client.send('Network.enable');
      await client.send('Network.emulateNetworkConditions', {
        offline: false,
        latency: cond.slow.latency,
        downloadThroughput: cond.slow.downloadThroughput,
        uploadThroughput: cond.slow.uploadThroughput,
      });
    } catch (e) {
      out.reason = 'CDP 节流失效（' + String((e && e.message) || e).slice(0, 120) + '），弱网条件未生效';
      if (client) { await resetThrottle(client); }
      return out;
    }
  }

  try {
    // 【F39b】reload 失败 = 没真正进入该条件，照常 probe 会把旧态当条件态产 pass/pending
    let reloaded = true;
    await page.reload({ waitUntil: 'domcontentloaded' }).catch(() => { reloaded = false; });
    if (!reloaded) {
      out.reason = 'reload 失败，未能进入「' + cond.label + '」状态（未验证，不得视为通过）';
      return out;
    }

    if (cond.slow) {
      // 弱网两段采样：先用 loading 探针看「等的时候用户看到什么」，再等渲染完看最终态。
      // 只采最终态会永远看不到加载反馈缺失——那时骨架早就被内容替换了。
      await page.waitForTimeout(900);
      const mid = await probe(page, 'loading', cond.label);
      if (mid.error) { out.reason = '加载中探测未执行：' + (mid.detail || mid.error); return out; }
      out.loadingFindings = mid.findings || [];
      await page.waitForLoadState('networkidle', { timeout: 20000 }).catch((e) => {
        out.settleNote = '等待 networkidle 超时（' + String((e && e.message) || e).slice(0, 80) + '），按当前已渲染状态判定';
      });
      // 最终态只取「骨架没消失」这一条：弱网的最终状态是**成功**，拿 failureFindings
      // 去判会把「加载成功但页面本来就短」误报成「接口失败白屏」——标签直接说错原因。
      const fin = await probe(page, 'failure', cond.label + '加载完成时');
      if (fin.error) { out.reason = '最终态探测未执行：' + (fin.detail || fin.error); return out; }
      const residue = (fin.findings || []).filter(f => f.property === 'failure-skeleton-residue');
      out.findings = (mid.findings || []).concat(residue);
      out.finalFindings = fin.findings || [];
    } else {
      await page.waitForTimeout(2500); // 等骨架屏/错误提示渲染稳定
      const pr = await probe(page, 'failure', cond.label);
      if (pr.error) { out.reason = '探测未执行：' + (pr.detail || pr.error); return out; }
      out.findings = pr.findings || [];
    }

    const shot = path.join(SHOTS, '7.33-' + cond.name + '.png');
    await page.screenshot({ path: shot }).catch(() => {});
    out.screenshot = shot;
    out.status = statusOfFindings(out.findings);
    return out;
  } finally {
    if (cond.route) await page.unroute('**/*', cond.route).catch(() => {});
    if (client) {
      // 节流必须显式复位：CDP 会话与页面绑定，残留会让后续维度在错误网络下测量
      await resetThrottle(client);
    }
    await page.reload({ waitUntil: 'domcontentloaded' }).catch(() => {}); // 恢复正常网络
  }
}

async function runFailure(page) {
  const conditions = [];
  for (const cond of NAMED_NET_CONDITIONS) {
    conditions.push(await runNetworkCondition(page, cond));
  }
  const executed = conditions.filter(c => c.status !== 'notRun');
  const findings = conditions.reduce((acc, c) => acc.concat(c.findings || []), []);
  const skipped = conditions.filter(c => c.status === 'notRun');
  const reasons = skipped.map(c => '「' + c.condition + '」未执行：' + (c.reason || '原因缺失'));

  if (!executed.length) {
    return { dimension: '7.33', status: 'notRun',
      reason: '全部网络条件均未执行：' + reasons.join('；'), findings: [], conditions };
  }
  let status = executed.some(c => c.status === 'fail') ? 'fail'
    : (executed.some(c => c.status === 'pending') ? 'pending' : 'pass');
  // 部分条件没跑成：不能按剩余条件判 pass——那等于把「没测过的网络条件」记成通过。
  // 降 pending 并把缺口写进 reason，聚合层才不会认为该维度已完整覆盖。
  if (skipped.length && status === 'pass') status = 'pending';
  const res = {
    dimension: '7.33', status, findings, conditions,
    conditionsExecuted: executed.map(c => c.condition),
    conditionsSkipped: skipped.map(c => c.condition),
    screenshot: (conditions.filter(c => c.screenshot)[0] || {}).screenshot,
  };
  if (reasons.length) res.reason = reasons.join('；');
  return res;
}

const RUNNERS = {
  '7.25': runDark,
  '7.26': runPrint,
  '7.32': runResidue,
  '7.33': runFailure,
};

async function main() {
  if (!URL_) { console.error('ERROR: --url is required'); process.exit(1); }
  fs.mkdirSync(SHOTS, { recursive: true });

  // staticOnly：四类场景全是页面交互（媒体仿真/路由拦截/导航往返），
  // 不启动浏览器，直接按维度产出 notRun 并写明原因（门禁要求原因必填）。
  if (SAFETY_POLICY === 'staticOnly') {
    const results = ['7.25', '7.26', '7.32', '7.33']
      .filter(d => !ONLY.size || ONLY.has(d))
      .map(d => ({ dimension: d, status: 'notRun',
                   reason: 'staticOnly 策略：不做任何页面交互（含媒体仿真与路由拦截）', findings: [] }));
    const payload = {
      url: URL_, safetyPolicy: SAFETY_POLICY,
      startedAt: new Date().toISOString(), results,
      summary: { byStatus: { notRun: results.length }, findingsTotal: 0 },
    };
    const outFile = path.join(OUT, 'state_scenarios.json');
    fs.writeFileSync(outFile, JSON.stringify(payload, null, 2), 'utf8');
    console.log(JSON.stringify({ out: outFile, ...payload.summary,
      detail: results.map(r => ({ dimension: r.dimension, status: r.status, findings: 0, reason: r.reason })) }, null, 2));
    return;
  }

  const pw = resolvePlaywright();
  if (!pw) { console.error('ERROR: playwright-core not found. npm i playwright-core'); process.exit(1); }
  const chrome = CHROME && fs.existsSync(CHROME) ? CHROME : resolveChrome();
  if (!chrome) { console.error('ERROR: chrome.exe not found under ms-playwright'); process.exit(1); }
  const auth = loadAuth();

  let context;
  let browser = null;
  if (PROFILE) context = await pw.chromium.launchPersistentContext(PROFILE, { headless: !HEADED, executablePath: chrome, args: ['--no-sandbox'], viewport: { width: 1440, height: 900 } });
  else {
    browser = await pw.chromium.launch({ headless: !HEADED, executablePath: chrome, args: ['--no-sandbox'] });
    context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  }
  try {
    // 【F57】同上：restorAuth 注入失败必须留痕（不影响执行，但产物与日志要点名）。
    let authRestore = null;
    if (auth && auth.restoreAuth) {
      authRestore = await auth.restoreAuth(context).catch(
        (e) => ({ ok: false, injected: false, reason: 'restoreAuth 异常: ' + String(e).slice(0, 160) }));
      if (authRestore && authRestore.ok === false && !authRestore.noAuthFile
          && !authRestore.badStorage) {
        console.error('[warn] ' + (authRestore.reason || '登录态恢复失败'));
      }
    }
    const page = context.pages()[0] || await context.newPage();
    // 【F9】起始页不可达时不得继续：在 about:blank 上探测会把 7.25/7.26 判成 pass（假绿）。
    try {
      await page.goto(URL_, { waitUntil: 'domcontentloaded', timeout: 45000 });
    } catch (e) {
      console.error('[fatal] 起始页不可达，状态场景无法执行:', String(e).slice(0, 200));
      const out = path.join(OUT, 'state_scenarios.json');
      fs.mkdirSync(OUT, { recursive: true });
      const notrun = ['7.25', '7.26', '7.32', '7.33']
        .filter(d => !ONLY.size || ONLY.has(d))
        .map(d => ({ dimension: d, status: 'notRun', reason: '起始页不可达：' + String(e).slice(0, 120), findings: [] }));
      fs.writeFileSync(out, JSON.stringify({ url: URL_, error: 'start-url-unreachable', results: notrun }, null, 2));
      process.exit(2);
    }
    await stabilize(page, {});

    // 页面身份校验（本轮假缺陷根因驱动）：state 场景曾用旧凭据落到登录页，
    // 登录页样式被当成工作台产出 4 条假缺陷。未达目标态时四维全部 notRun
    // 并留原因，绝不把登录页/错误页的样式当产品结论。
    const identity = await verifyPageIdentity(page, URL_);
    if (!identity.reachedTarget) {
      const reason = '页面未达目标态（' + (identity.isLoginPage ? '登录页' : '身份校验失败')
        + '，原因: ' + identity.reasons.join('; ') + '），状态场景未执行：' + identity.actualUrl;
      const notrun = ['7.25', '7.26', '7.32', '7.33']
        .filter(d => !ONLY.size || ONLY.has(d))
        .map(d => ({ dimension: d, status: 'notRun', reason, findings: [] }));
      const out = path.join(OUT, 'state_scenarios.json');
      fs.writeFileSync(out, JSON.stringify({
        url: URL_, finalUrl: identity.actualUrl, pageIdentity: identity,
        error: 'page-identity-unverified', results: notrun,
      }, null, 2));
      console.error('[fatal] ' + reason);
      process.exit(5);
    }

    const results = [];
    for (const dim of ['7.25', '7.26', '7.32', '7.33']) {
      if (ONLY.size && !ONLY.has(dim)) continue;
      try {
        results.push(await RUNNERS[dim](page));
      } catch (e) {
        results.push({ dimension: dim, status: 'notRun', reason: '执行异常：' + String(e).slice(0, 160), findings: [] });
      }
    }

    const byStatus = {};
    results.forEach(r => { byStatus[r.status] = (byStatus[r.status] || 0) + 1; });
    const payload = {
      url: URL_, safetyPolicy: SAFETY_POLICY,
      startedAt: new Date().toISOString(),
      authRestore,
      results, summary: { byStatus, findingsTotal: results.reduce((n, r) => n + (r.findings ? r.findings.length : 0), 0) },
    };
    const outFile = path.join(OUT, 'state_scenarios.json');
    fs.writeFileSync(outFile, JSON.stringify(payload, null, 2), 'utf8');
    console.log(JSON.stringify({ out: outFile, ...payload.summary,
      detail: results.map(r => ({ dimension: r.dimension, status: r.status, findings: (r.findings || []).length, reason: r.reason })) }, null, 2));
  } finally {
    await context.close().catch(() => {});
    // 只关 context 会留下 chrome 子进程，node 不退出——管线只能等到超时，
    // 日志里只有一句"没有输出"。浏览器必须显式关。
    if (browser) await browser.close().catch(e => console.warn('[warn] browser 关闭失败: ' + String(e).slice(0, 120)));
  }
}

if (require.main === module) {
  main().catch(e => { console.error('FATAL', String(e)); process.exit(1); });
}

module.exports = { statusOfFindings, residueFindings, captureState, probe, RUNNERS };
