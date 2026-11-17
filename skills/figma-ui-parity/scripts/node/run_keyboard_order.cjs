#!/usr/bin/env node
/**
 * 7.43 键盘可达性与焦点顺序驱动。
 *
 * 分工：页面脚本（keyboard_order.js）负责枚举期望序列与记录 activeElement，
 * 本驱动只负责**按键**。所有判定都在页面脚本的 finish() 里做，驱动不读截图。
 *
 * 安全边界：只按 Tab，不按 Enter/Space，不提交任何表单。因此 safe 策略即可执行；
 * staticOnly 下不启动浏览器，直接 notRun 并写明原因。
 *
 * 用法：
 *   node run_keyboard_order.cjs --url "https://目标页" --out ./parity-test/keyboard
 *     [--profile 资料目录] [--open "CSS 选择器"] [--max-tabs 120] [--headed]
 *
 * 产物：<out>/keyboard_order.json
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
const OUT = arg('out', path.join(process.cwd(), 'keyboard-order'));
const PROFILE = arg('profile', null);
const CHROME = arg('chrome', null);
const HEADED = process.argv.includes('--headed');
const SAFETY_POLICY = arg('safety-policy', 'safe');
const OPEN_SEL = arg('open', null);
const MAX_TABS = parseInt(arg('max-tabs', '120'), 10);
const PAGE_JS_PATH = path.join(__dirname, '..', 'keyboard_order.js');

if (!URL_ && require.main === module) { console.error('ERROR: --url is required'); process.exit(1); }

function writeResult(payload) {
  fs.mkdirSync(OUT, { recursive: true });
  const file = path.join(OUT, 'keyboard_order.json');
  fs.writeFileSync(file, JSON.stringify(payload, null, 2), 'utf8');
  return file;
}

if (require.main === module) (async () => {
  if (!URL_) { console.error('ERROR: --url is required'); process.exit(1); }
  if (!fs.existsSync(PAGE_JS_PATH)) { console.error('ERROR: missing scripts/keyboard_order.js'); process.exit(1); }

  if (SAFETY_POLICY === 'staticOnly') {
    const file = writeResult({
      url: URL_, safetyPolicy: SAFETY_POLICY, status: 'notRun',
      reason: 'staticOnly 策略：不启动浏览器，键盘可达性需真实按键才能观测',
      findings: [], summary: { byStatus: { notRun: 1 } },
    });
    console.log(JSON.stringify({ out: file, status: 'notRun' }, null, 2));
    return;
  }

  const handle = await launch({ chrome: CHROME, headed: HEADED, profile: PROFILE });
  if (handle.error) {
    const file = writeResult({ url: URL_, status: 'notRun', reason: handle.error, findings: [] });
    console.log(JSON.stringify({ out: file, status: 'notRun', reason: handle.error }, null, 2));
    process.exitCode = 2;
    return;
  }

  let payload = null;
  try {
    await restoreAuth(handle.context);
    const page = handle.context.pages()[0] || await handle.context.newPage();
    const journal = [];
    try {
      await page.goto(URL_, { waitUntil: 'domcontentloaded', timeout: 45000 });
    } catch (e) {
      const reason = '起始页不可达：' + String((e && e.message) || e).split('\n')[0].slice(0, 160);
      const file = writeResult({ url: URL_, status: 'notRun', reason, findings: [] });
      console.log(JSON.stringify({ out: file, status: 'notRun', reason }, null, 2));
      process.exitCode = 2;
      return;
    }
    // 页面身份校验：登录页/错误页上的 Tab 顺序不是目标页的，全 notRun 留因。
    const identity = await verifyPageIdentity(page, URL_);
    if (!identity.reachedTarget) {
      const reason = '页面未达目标态（' + (identity.isLoginPage ? '登录页' : '身份校验失败')
        + '，原因: ' + identity.reasons.join('; ') + '）：' + identity.actualUrl;
      const file = writeResult({ url: URL_, status: 'notRun', reason, findings: [] });
      console.log(JSON.stringify({ out: file, status: 'notRun', reason }, null, 2));
      process.exitCode = 5;
      return;
    }
    await page.waitForTimeout(2500);

    // 可选：先打开一个弹层再采样（不然「焦点陷阱」这条只能在页面自带弹层时才生效）
    if (OPEN_SEL) {
      try {
        await page.locator(OPEN_SEL).first().click({ timeout: 6000 });
        await page.waitForTimeout(900);
        journal.push({ stage: 'open', selector: OPEN_SEL, ok: true });
      } catch (e) {
        journal.push({ stage: 'open', selector: OPEN_SEL, ok: false,
          error: String((e && e.message) || e).split('\n')[0].slice(0, 160) });
      }
    }

    const src = fs.readFileSync(PAGE_JS_PATH, 'utf8');
    const planRaw = await page.evaluate(src);
    if (planRaw !== 'installed') {
      throw new Error('页面脚本安装返回异常：' + String(planRaw).slice(0, 120));
    }
    const plan = await page.evaluate(() => window.__vpKb.plan());
    journal.push({ stage: 'plan', total: plan.total, dialogOpen: plan.dialogOpen });

    if (!plan.total) {
      const file = writeResult({
        url: URL_, status: 'notRun', journal,
        reason: '页面里没有可见且 tabindex>=0 的可聚焦元素（可能是登录页/空壳页），键盘顺序无从采样',
        findings: [],
        summary: { planned: 0, observed: 0, byStatus: { notRun: 1 } },
      });
      console.log(JSON.stringify({ out: file, status: 'notRun', planned: 0 }, null, 2));
      return;
    }

    // 采样两轮多一点：第一轮拿顺序，多出来的用于确认能循环回起点
    const presses = Math.min(MAX_TABS, plan.total * 2 + 4);
    const observed = [];
    for (let i = 0; i < presses; i += 1) {
      await page.keyboard.press('Tab');
      observed.push(await page.evaluate(() => window.__vpKb.record()));
    }
    journal.push({ stage: 'tab', presses, observed: observed.length });

    const raw = await page.evaluate((seq) => window.__vpKb.finish(seq), observed);
    const result = typeof raw === 'string' ? JSON.parse(raw) : raw;
    const findings = Array.isArray(result.findings) ? result.findings : [];
    const confirmed = findings.filter((f) => f.status === 'confirmed');
    const status = confirmed.length ? 'fail' : (findings.length ? 'pending' : 'pass');

    payload = {
      url: URL_,
      status,
      safetyPolicy: SAFETY_POLICY,
      startedAt: new Date().toISOString(),
      journal,
      findings,
      summary: {
        planned: result.planned,
        observed: result.observed,
        reached: result.reached,
        unreachable: (result.skipped || []).length,
        positiveTabindex: (result.positiveTabindex || []).length,
        orderBreaks: (result.orderBreaks || []).length,
        dialogOpen: !!result.dialogOpen,
        confirmed: confirmed.length,
        pending: findings.length - confirmed.length,
        byStatus: { fail: confirmed.length, pending: findings.length - confirmed.length },
      },
      coverageNote: '只按 Tab，不按 Enter/Space（无副作用）。未覆盖：焦点可见性（7.29）、'
        + '弹层打开后的焦点归还到触发元素（需先打开弹层，可用 --open 指定）、'
        + 'iframe/shadow DOM 内的可聚焦元素（会记入 kb-unplanned-focusable）。',
    };
    if (!result.dialogOpen) {
      payload.coverageNote += ' 本次页面加载时无弹层打开，焦点陷阱一条未触发。';
    }
  } catch (e) {
    const reason = String((e && e.message) || e).slice(0, 200);
    const file = writeResult({ url: URL_, status: 'notRun', reason: '驱动异常：' + reason, findings: [] });
    console.log(JSON.stringify({ out: file, status: 'notRun', reason }, null, 2));
    process.exitCode = 2;
    return;
  } finally {
    await closeAll(handle);
  }

  const file = writeResult(payload);
  console.log(JSON.stringify({ out: file, status: payload.status, summary: payload.summary }, null, 2));
})();

module.exports = { writeResult };
