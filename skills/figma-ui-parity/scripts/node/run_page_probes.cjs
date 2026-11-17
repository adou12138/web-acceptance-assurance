#!/usr/bin/env node
/**
 * 页面侧注入型探针的统一驱动。
 *
 * 为什么需要它：responsive_check / extra_checks / alignment_modal_checks /
 * order_divider_scroll_check / extract_icons / runtime_observability 都是
 * 「IIFE 返回 JSON 字符串、设计上用 browser_evaluate 注入」的页面内脚本。
 * 靠手工逐个注入的代价是 **没有产物就没有维度**：漏注入一次，7.4 / 7.5 / 7.6 /
 * 7.7 / 7.9 / 7.13 / 7.14 / 7.15 / 7.19 / 7.20 / 7.24 / 7.27-7.31 / 7.35-7.38 /
 * 7.45 整片落 notRun，而报告只写「无产物覆盖」——看不出是漏跑还是页面真不适用。
 *
 * 本驱动一次浏览器会话跑完全部探针并落盘，交给 assemble_report 消费。
 *
 * 纪律：
 *   1. 全部探针只读（滚动 / hover / 读计算样式），不点击、不提交、不改状态；
 *   2. 每个探针单独 try：一个炸不能带走其余，失败写进 errors 并标明维度受影响；
 *   3. 探针返回空 findings 时**不伪造 pass**——由产物自带的状态字段决定，
 *      driver 只负责搬运与补 dimension，不替页面下结论。
 *
 * 用法：
 *   node run_page_probes.cjs --url "https://目标页" [--out "./parity-test"]
 *     [--profile "持久化资料目录"] [--headed] [--wait-login]
 *     [--breakpoints "1920,1440,1280,768,375"]
 *     [--probes "responsive,extra,alignment,order,icons,runtime,aria"]
 *     [--chrome 路径]
 */
const fs = require('fs');
const path = require('path');
const { stabilize } = require('../stabilize.js');
const { launch, closeAll } = require('./browser.cjs');
const { createWriteGuard } = require('./write_guard.cjs');
const { verifyPageIdentity } = require('./page_identity.cjs');

function arg(name, def) {
  const i = process.argv.indexOf('--' + name);
  return i >= 0 && process.argv[i + 1] && !process.argv[i + 1].startsWith('--') ? process.argv[i + 1] : def;
}

const URL_ = arg('url');
const OUT = path.resolve(arg('out', path.join(process.cwd(), 'parity-test')));
const PROFILE = arg('profile', null);
const CHROME = arg('chrome', null);
const HEADED = process.argv.includes('--headed');
const WAIT_LOGIN = process.argv.includes('--wait-login');
const BREAKPOINTS = (arg('breakpoints', '1920,1440,1280,768,375') || '')
  .split(',').map(n => parseInt(n, 10)).filter(n => n > 0);
const WANTED = (arg('probes', 'responsive,extra,alignment,order,icons,runtime') || '')
  .split(',').map(s => s.trim()).filter(Boolean);
const SAFETY_POLICY = arg('safety-policy', 'safe');
// 运行期观测（7.35-7.38）只有在页面真的发出请求时才成立：只滚动/悬停的
// interactionsObserved 恒为 0，采集结果按约定必须落 notRun。要拿到证据就得
// 真点几下——点击数上限宁可小，也不做"点遍全页"的冒险动作。
const MAX_CLICKS = Math.max(0, parseInt(arg('max-clicks', '6'), 10));

// 只收「IIFE 返回 JSON 字符串」的页面内脚本。
// capture_aria.js 自带 require 与 CLI（--url/--diff 两种模式），是独立进程而非注入片段，
// 由管线单独起一个 stage 调用；混进来会在页面里抛 "require is not defined"。
const ALL_PROBES = {
  responsive: '../responsive_check.js',
  extra: '../extra_checks.js',
  alignment: '../alignment_modal_checks.js',
  order: '../order_divider_scroll_check.js',
  icons: '../extract_icons.js',
  runtime: '../runtime_observability.js',
};

function log(msg) { console.log('[probe] ' + msg); }
function warn(msg) { console.warn('[probe][warn] ' + msg); }

function writeJson(file, data) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(data, null, 2), 'utf8');
}

/**
 * 注入一个 IIFE 探针并解析返回值。
 * 探针返回 JSON **字符串**；少数分支可能直接返回对象，两种都接受。
 * 解析失败必须抛出——把字符串原样当产物落盘会在下游变成"无 findings"的假通过。
 */
async function runProbe(page, source, label) {
  const raw = await page.evaluate(source);
  if (raw === null || raw === undefined) {
    throw new Error(label + ' 返回空值（页面可能尚未渲染完成，或探针被 CSP 拦截）');
  }
  if (typeof raw === 'object') return raw;
  try {
    return JSON.parse(raw);
  } catch (e) {
    throw new Error(label + ' 返回值不是合法 JSON：' + String(raw).slice(0, 120));
  }
}

/** 响应式探针的原始 issue → 7.4 的 findings 条目。
 *
 * 按「断点 × 问题类型」聚合而不是逐条铺开：窄屏下一个没做移动端适配的页面
 * 能产出上百条 overflow/clipped，逐条进报告会把真正的缺陷淹没，也会把 7.12
 * 的待确认项堆成噪音。聚合后每条带「共 N 处 + 首个典型元素」，明细仍保留在
 * responsive.json 的 viewports 里可追溯。
 */
function responsiveFindings(perViewport) {
  const SEVERITY_TO_STATUS = {
    high: 'confirmed',
    medium: 'pending',   // 中等问题在单一断点上无法排除是回归还是既有设计取舍
    low: 'pending',
  };
  const TYPE_LABEL = {
    horizontal_overflow: '文档横向溢出',
    overflow_right: '元素超出视口右侧',
    overflow_left: '元素左边界为负',
    text_clipped: '文本被裁剪',
    touch_target_small: '触控目标小于 44×44',
    overlap: '元素重叠',
  };
  const out = [];
  let ranViewports = 0;
  for (const entry of perViewport) {
    if (entry.error) continue;
    ranViewports += 1;
    const groups = new Map();
    for (const issue of entry.issues || []) {
      const key = issue.type || 'unknown';
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(issue);
    }
    for (const [type, items] of groups) {
      const worst = items.slice().sort((a, b) => {
        const rank = { high: 0, medium: 1, low: 2 };
        return (rank[a.severity] ?? 3) - (rank[b.severity] ?? 3);
      })[0];
      const first = items[0];
      out.push({
        dimension: '7.4',
        status: SEVERITY_TO_STATUS[worst.severity] || 'pending',
        symptom: '[' + entry.width + 'px] ' + (TYPE_LABEL[type] || type) + ' 共 ' + items.length + ' 处：'
          + String(first.detail || '').slice(0, 60),
        expected: '该断点下无横向溢出 / 裁剪 / 重叠 / 过小触控目标',
        actual: type + ' × ' + items.length + '（首个元素：' + (first.element || '未知') + '）',
        severityHint: worst.severity === 'high' ? 'P1' : 'P2',
        element: first.element || null,
        evidence: [{ type: 'viewport-probe', result: 'issue', viewport: entry.width, count: items.length }],
        sources: ['responsive-check'],
        raw: { viewport: entry.width, type, count: items.length },
      });
    }
  }
  // 跑过且干净 = pass 证据。没有这条，7.4 会落到「无产物覆盖」，
  // 把"实测过、没问题"误报成"没测"。
  if (ranViewports > 0 && out.length === 0) {
    out.push({
      dimension: '7.4',
      status: 'pass',
      symptom: ranViewports + ' 个断点全部实测，无溢出 / 裁剪 / 重叠',
      expected: '各断点布局稳定',
      actual: '无 issue',
      severityHint: 'P3',
      evidence: [{ type: 'viewport-probe', result: 'clean', viewports: perViewport.map(v => v.width) }],
      sources: ['responsive-check'],
      raw: { viewports: perViewport.map(v => v.width) },
    });
  }
  return out;
}

/**
 * 按安全策略点击少量「明显只读」的按钮，为运行期观测制造真实交互。
 *
 * 分级沿用 risk_words.cjs 的单源词表：命中 danger 或 caution 的一律不点
 * （caution 含「发送/创建/提交/保存」，正好是这个页面里会真正落库的动作）。
 * 每次点击后按 ESC 关掉可能弹出的浮层，避免后一个按钮被遮挡而误判成不可点。
 * 返回实际点过的按钮（供日志与报告对账）。
 */
async function clickSafeButtons(page, limit) {
  if (!limit) return [];
  // staticOnly 下任何点击都不允许（SKILL「仅静态检查」：不点击、不输入、不提交）。
  // 旧版缺这层判断：本探针是管线默认必跑驱动，staticOnly 下仍会点最多 6 个按钮，
  // 且 safe/staticOnly 不安装写拦截 → 可能真实落库。
  if (SAFETY_POLICY === 'staticOnly') return [];
  let risk;
  try { risk = require('./risk_words.cjs'); } catch (e) { risk = null; }
  const danger = (risk && risk.DANGER_WORDS) || [];
  const caution = (risk && risk.CAUTION_WORDS) || [];

  const candidates = await page.evaluate(() => {
    const nodes = Array.from(document.querySelectorAll('button, [role="button"], a[href="#"], a[href="javascript:;"]'));
    return nodes.map((el, i) => {
      const cs = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      return {
        idx: i,
        text: (el.innerText || el.textContent || el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim().slice(0, 30),
        visible: r.width > 0 && r.height > 0 && cs.display !== 'none' && cs.visibility !== 'hidden',
        disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true',
      };
    }).filter(x => x.visible && !x.disabled && x.text);
  }).catch(() => []);

  const risky = (text) => {
    const low = text.toLowerCase();
    return danger.some(w => text.includes(w) || low.includes(String(w).toLowerCase()))
      || caution.some(w => text.includes(w) || low.includes(String(w).toLowerCase()));
  };
  const pick = candidates.filter(c => !risky(c.text)).slice(0, limit);

  const clicked = [];
  for (const c of pick) {
    try {
      const handle = (await page.$$('button, [role="button"], a[href="#"], a[href="javascript:;"]'))[c.idx];
      if (!handle) continue;
      await handle.click({ timeout: 2500, trial: false });
      clicked.push({ text: c.text });
      await page.waitForTimeout(600);
      // 关掉可能的浮层：不关会让后续按钮被遮挡，制造假"不可点"。
      await page.keyboard.press('Escape').catch(() => null);
      await page.waitForTimeout(200);
    } catch (e) {
      // 点击引发导航/元素失效都属正常：记下原因，继续下一个。
      clicked.push({ text: c.text, error: String((e && e.message) || e).slice(0, 80) });
      await page.waitForTimeout(300);
    }
  }
  return clicked;
}


/**
 * 把 producer 声明的 `checked` 维度展开成 pass 证据。
 *
 * 探测过且没发现问题时 producer 的 findings 为空，下游只看到「无产物」，
 * 于是把「实测干净」判成「没测」。这条把两者的区别补上：声明检查过、
 * 又没有对应 finding 的维度，输出一条 pass。**只对 producer 明确声明过的
 * 维度生效**，不做「没报错就是没问题」的推断。
 */
function passFindingsForChecked(payload, source) {
  const checked = Array.isArray(payload && payload.checked) ? payload.checked : [];
  const found = new Set((payload && payload.findings || []).map(f => String(f.dimension || '')));
  return checked
    .filter(dim => dim && !found.has(dim))
    .map(dim => ({
      dimension: dim,
      status: 'pass',
      symptom: '该维度探测已执行，未发现问题',
      expected: '符合可访问性 / 规范要求',
      actual: '探测未命中问题',
      severityHint: 'P3',
      confidence: 0.6,
      element: null,
      evidence: [{ type: 'probe', result: 'clean', source }],
      sources: [source],
    }));
}


async function main() {
  if (!URL_) {
    console.error('ERROR: --url is required');
    return 2;
  }
  const unknown = WANTED.filter(p => !ALL_PROBES[p]);
  if (unknown.length) {
    console.error('ERROR: --probes 含未知项: ' + unknown.join(', ') + '（可选: ' + Object.keys(ALL_PROBES).join(', ') + '）');
    return 2;
  }

  fs.mkdirSync(OUT, { recursive: true });
  const result = { url: URL_, startedAt: new Date().toISOString(), probes: {}, errors: [], outputs: {} };

  const handle = await launch({ chrome: CHROME, headed: HEADED, profile: PROFILE });
  if (handle.error) {
    // 浏览器起不来不是"页面没问题"：写一份全 notRun 的产物，让下游按真实原因落维度。
    const reason = '浏览器启动失败：' + handle.error;
    writeJson(path.join(OUT, 'page_probes_failed.json'), {
      url: URL_, reason,
      findings: WANTED.map(p => ({
        dimension: probeDimension(p), status: 'notRun', reason,
        symptom: '探针未执行', severityHint: 'P3', sources: ['run_page_probes'],
      })),
    });
    console.error('ERROR: ' + reason);
    return 3;
  }

  const page = await handle.context.newPage();
  const auth = (() => { try { return require('./auth.cjs'); } catch (e) { return null; } })();

  // 写操作防护：按「交互前后」二分。首次 goto 成功前的非只读请求是页面自身
  // 初始化（登录态校验等），放行并记录；之后的业务写按 mockWrites 中断并计数。
  // 这避免此前「goto 前全拦」把认证 POST 掐断、页面停在错误态又被当成网站状态。
  const writeGuard = createWriteGuard(handle.context, SAFETY_POLICY);
  await writeGuard.beforeNavigation();

  try {
    // 登录态必须在 goto **之前**注入：先导航再写 cookie，页面已经在登录页上渲染完了，
    // 探针采到的全是登录页的 DOM（实测 bodyLen 116 / 0 个按钮），
    // 而报告会把它当"首页只有 0 个按钮"落成缺陷。其余驱动都是先注入后 goto。
    if (auth && auth.restoreAuth) {
      const restored = await auth.restoreAuth(handle.context).catch(
        (e) => ({ ok: false, injected: false, reason: String(e).slice(0, 160) }));
      result.auth = restored;
      log('登录态注入: ok=' + (restored && restored.ok) + ' cookies=' + ((restored && restored.cookies) || 0));
    }
    await page.goto(URL_, { waitUntil: 'domcontentloaded', timeout: 60000 });
    if (WAIT_LOGIN) {
      // 只等到出现「已登录」迹象，而不是死等 networkidle：SPA 常驻长连接永不 idle。
      log('检测到登录页时等待人工登录（' + URL_ + '）……');
      const deadline = Date.now() + 180000;
      while (Date.now() < deadline) {
        const onLogin = auth && auth.isLoginPage ? await auth.isLoginPage(page).catch(() => false) : false;
        if (!onLogin) break;
        await page.waitForTimeout(2000);
      }
      await page.goto(URL_, { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(() => null);
    }
    await stabilize(page, {});
    await page.waitForTimeout(800);
    writeGuard.markNavigationSucceeded();

    // 页面身份校验：不只看导航成功，还要确认当前是目标页而非登录页/错误态。
    // 未达目标态时本阶段全部 notRun 并归因，不产出登录页的假缺陷。
    const identity = await verifyPageIdentity(page, URL_);
    result.pageIdentity = identity;
    const writeSummary = writeGuard.finalize(identity.reachedTarget);
    result.writeProtection = writeSummary;
    if (!identity.reachedTarget) {
      const reason = '页面未达目标态（' + (identity.isLoginPage ? '登录页' : '身份校验失败')
        + '，原因: ' + identity.reasons.join('; ') + '），探针未执行：' + identity.actualUrl
        + (writeSummary.reason ? '；' + writeSummary.reasonNote : '');
      writeJson(path.join(OUT, 'page_probes_failed.json'), {
        url: URL_, finalUrl: identity.actualUrl, reason,
        pageIdentity: identity, writeProtection: writeSummary,
        findings: WANTED.map(p => ({
          dimension: probeDimension(p), status: 'notRun', reason,
          symptom: '页面身份校验未通过，未采集', severityHint: 'P3', sources: ['run_page_probes'],
        })),
      });
      console.error('ERROR: ' + reason);
      return 5;
    }

    // （旧「登录页兜底」已由上方 verifyPageIdentity 统一覆盖：URL 登录特征
    // 与可见密码框任一命中即判非目标页，比逐驱动自检更完整且留统一原因。）

    // 探针执行顺序有语义：runtime 的 install 必须最早（否则捕获不到加载期异常），
    // collect 必须最晚（要覆盖完其余探针产生的交互）。
    const order = [];
    if (WANTED.includes('runtime')) order.push('runtime-install');
    for (const p of ['icons', 'alignment', 'order', 'extra', 'responsive']) {
      if (WANTED.includes(p)) order.push(p);
    }
    if (WANTED.includes('runtime')) order.push('runtime-collect');

    for (const step of order) {
      try {
        if (step === 'runtime-install') {
          const src = fs.readFileSync(path.join(__dirname, ALL_PROBES.runtime), 'utf8');
          const installed = await runProbe(page, src, 'runtime_observability(install)');
          result.probes['runtime-install'] = installed;
          log('runtime 钩子已安装: installed=' + installed.installed);
          continue;
        }
        if (step === 'runtime-collect') {
          // 采集前做一轮交互：运行期观测的全部维度（异常 / 失败请求 / 重复提交 /
          // 遮挡 / 载荷完整性）都以"页面真的动了"为前提。
          // 顺序：先无副作用的滚动与悬停，再按策略点击少量安全按钮。
          await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight)).catch(() => null);
          await page.waitForTimeout(500);
          await page.evaluate(() => window.scrollTo(0, 0)).catch(() => null);
          // hover 可能打开浮层/tooltip，属 staticOnly 明确禁止的交互；
          // 只在允许交互的策略下做，滚动（无副作用）仍然保留。
          if (SAFETY_POLICY !== 'staticOnly') {
            const hoverTarget = await page.$('button:visible, [role="button"]:visible, a:visible').catch(() => null);
            if (hoverTarget) await hoverTarget.hover({ timeout: 2000 }).catch(() => null);
            await page.waitForTimeout(400);
          }

          const clicked = await clickSafeButtons(page, MAX_CLICKS);
          result.probes.runtimeClicks = clicked;
          if (clicked.length) {
            log('已点击 ' + clicked.length + ' 个安全按钮：' + clicked.map(c => c.text).join(' / '));
          }
          await page.waitForTimeout(1200);

          const src = fs.readFileSync(path.join(__dirname, ALL_PROBES.runtime), 'utf8');
          const collected = await runProbe(page, src, 'runtime_observability(collect)');
          writeJson(path.join(OUT, 'runtime.json'), collected);
          result.outputs.runtime = path.join(OUT, 'runtime.json');
          result.probes.runtime = {
            hooksInstalled: collected.hooksInstalled,
            interactionsObserved: collected.interactionsObserved,
            findings: (collected.findings || []).length,
            clicked: clicked.length,
          };
          log('runtime 采集完成: findings=' + (collected.findings || []).length
            + ' interactions=' + collected.interactionsObserved);
          continue;
        }

        const src = fs.readFileSync(path.join(__dirname, ALL_PROBES[step]), 'utf8');

        if (step === 'responsive') {
          const perViewport = [];
          const original = page.viewportSize();
          for (const width of BREAKPOINTS) {
            try {
              await page.setViewportSize({ width, height: 900 });
              await page.waitForTimeout(400);
              const data = await runProbe(page, src, 'responsive_check@' + width);
              perViewport.push({ width, issueCount: data.issueCount, issues: data.issues || [] });
            } catch (e) {
              perViewport.push({ width, error: String((e && e.message) || e) });
              result.errors.push({ probe: 'responsive:' + width, error: String((e && e.message) || e) });
            }
          }
          if (original) await page.setViewportSize(original).catch(() => null);
          const payload = {
            url: URL_,
            viewports: perViewport,
            findings: responsiveFindings(perViewport),
            note: '各断点独立注入 responsive_check.js；high→confirmed，medium/low→pending（单断点无法排除既有取舍）',
          };
          writeJson(path.join(OUT, 'responsive.json'), payload);
          result.outputs.responsive = path.join(OUT, 'responsive.json');
          result.probes.responsive = {
            viewports: perViewport.map(v => ({ width: v.width, issues: v.issueCount, error: v.error || null })),
          };
          log('responsive 完成: ' + perViewport.length + ' 个断点');
          continue;
        }

        const data = await runProbe(page, src, step);
        const file = path.join(OUT, step === 'extra' ? 'extra_checks.json' : step + '.json');
        writeJson(file, data);
        result.outputs[step] = file;
        result.probes[step] = {
          keys: Object.keys(data || {}),
          findingCount: Array.isArray(data && data.findings) ? data.findings.length : null,
        };
        log(step + ' 完成 -> ' + path.basename(file));
      } catch (e) {
        const msg = String((e && e.message) || e);
        result.errors.push({ probe: step, error: msg });
        warn(step + ' 失败: ' + msg);
      }
    }

    // 探针失败的维度必须留痕：否则 assemble_report 只会写「无产物覆盖」，
    // 排查方向被带偏到"没接入"，而不是"探针炸了/页面打不开"。
    if (result.errors.length) {
      const failedDims = [...new Set(result.errors.map(e => probeDimension(String(e.probe).split(':')[0])))];
      writeJson(path.join(OUT, 'page_probes_failed.json'), {
        url: URL_,
        reason: result.errors.map(e => e.probe + ': ' + e.error).join('；'),
        findings: failedDims.filter(Boolean).map(dim => ({
          dimension: dim,
          status: 'notRun',
          reason: '探针执行失败：' + result.errors.map(e => e.probe + ' ' + e.error.slice(0, 60)).join('；'),
          symptom: '采集未完成',
          severityHint: 'P3',
          sources: ['run_page_probes'],
        })),
      });
    }
  } finally {
    await closeAll(handle);
  }

  // 汇总「声明检查过但没发现问题」的维度，落成独立的 pass 证据产物。
  // 不复用 producer 自己的文件：那份是 producer 的原始产出，往里补条目会让
  // 「页面说了什么」与「驱动补了什么」混在一起，事后无法对账。
  const passFindings = [];
  for (const file of ['extra_checks.json', 'runtime.json']) {
    const p = path.join(OUT, file);
    if (!fs.existsSync(p)) continue;
    try {
      passFindings.push(...passFindingsForChecked(JSON.parse(fs.readFileSync(p, 'utf8')), file));
    } catch (e) {
      result.errors.push({ probe: 'checked-expand:' + file, error: String((e && e.message) || e) });
    }
  }
  if (passFindings.length) {
    writeJson(path.join(OUT, 'probe_coverage.json'), {
      url: URL_,
      findings: passFindings,
      note: 'producer 声明 checked 但无对应 finding 的维度 = 探测已执行且干净',
    });
    result.outputs.probe_coverage = path.join(OUT, 'probe_coverage.json');
    log('补充 pass 证据 ' + passFindings.length + ' 条：' + passFindings.map(f => f.dimension).join(','));
  }

  result.finishedAt = new Date().toISOString();
  // blockedWrites 由 writeProtection.blockedCount 承接（语义：交互后被中断的业务写）。
  result.blockedWrites = (result.writeProtection && result.writeProtection.blockedCount) || 0;
  writeJson(path.join(OUT, 'page_probes.json'), result);
  log('完成：' + Object.keys(result.outputs).length + ' 份产物，' + result.errors.length + ' 个探针失败');
  return result.errors.length && Object.keys(result.outputs).length === 0 ? 4 : 0;
}

/** 探针名 → 它主要服务的维度（失败留痕用）。 */
function probeDimension(probe) {
  return {
    responsive: '7.4',
    extra: '7.24',
    alignment: '7.13',
    order: '7.9',
    icons: '7.5',
    runtime: '7.35',
    'runtime-install': '7.35',
    'runtime-collect': '7.35',
    aria: '7.19',
  }[probe] || null;
}

if (require.main === module) {
  main().then(code => process.exit(code)).catch(e => {
    console.error('ERROR: ' + String((e && e.stack) || e));
    process.exit(1);
  });
}

module.exports = { main, responsiveFindings, probeDimension };
