/** 缺陷附图只记录本轮当前页面可复现的位置，失败时保留原因。 */
const fs = require('fs');
const path = require('path');
const { launch, closeAll, restoreAuth } = require('./browser.cjs');
const { createWriteGuard } = require('./write_guard.cjs');
const { verifyPageIdentity } = require('./page_identity.cjs');

function arg(name, def) {
  const i = process.argv.indexOf('--' + name);
  return i >= 0 && process.argv[i + 1] && !process.argv[i + 1].startsWith('--') ? process.argv[i + 1] : def;
}

const REPORT = arg('report', null);
const URL_ = arg('url', null);
const OUT = arg('out', path.join(process.cwd(), 'parity-test'));
const PROFILE = arg('profile', null);
const CHROME = arg('chrome', null);
const HEADED = process.argv.includes('--headed');
const MAX = parseInt(arg('max', '60'), 10);
const SETTLE = parseInt(arg('settle', '2500'), 10);
const SKIP_CONTEXT = process.argv.includes('--no-page-shot');
const SAFETY = arg('safety-policy', 'staticOnly');
const ALIGN_SIDEBAR = parseInt(arg('align-sidebar', '0'), 10) || 0;

function vp() {
  const m = String(arg('viewport', '1440x900')).match(/^(\d+)x(\d+)$/);
  return m ? { width: Number(m[1]), height: Number(m[2]) } : { width: 1440, height: 900 };
}

async function measureSidebar(page) {
  return page.evaluate(() => {
    const viewportH = window.innerHeight;
    let best = null;
    for (const el of document.querySelectorAll('aside, nav, div, section')) {
      const r = el.getBoundingClientRect();
      if (r.left > 8 || r.top > 120 || r.height < viewportH * 0.6 || r.width < 30 || r.width > 400) continue;
      if (!best || r.width > best.width) best = { width: Math.round(r.width), height: Math.round(r.height) };
    }
    return best;
  }).catch(() => null);
}

async function alignSidebar(page, target) {
  const before = await measureSidebar(page);
  if (before && Math.abs(before.width - target) <= 8) return { ok: true, acted: false, before: before.width, target };
  if (SAFETY === 'staticOnly') {
    return { ok: false, acted: false, before: before && before.width, target, reason: 'staticOnly 禁止点击展开侧栏，保留当前页面状态' };
  }
  const clicked = [];
  const candidates = [
    'button[aria-label*="展开"]', 'button[title*="展开"]',
    '[role="button"][aria-label*="展开"]', '[role="button"][title*="展开"]',
    'button[aria-label*="expand" i]', 'button[title*="expand" i]',
    '[role="button"][aria-label*="expand" i]', '[role="button"][title*="expand" i]',
  ];
  for (const selector of candidates) {
    const locator = page.locator(selector).first();
    if (await locator.count().catch(() => 0)) {
      if (await locator.click({ timeout: 4000 }).then(() => true).catch(() => false)) {
        clicked.push(selector);
        break;
      }
    }
  }
  if (!clicked.length) {
    return { ok: false, acted: false, before: before && before.width, target, clicked, reason: '未找到可点击且明确标注展开含义的侧栏按钮，保留当前页面状态' };
  }
  await page.waitForTimeout(900);
  const after = await measureSidebar(page);
  const ok = !!(after && Math.abs(after.width - target) <= 8);
  return { ok, acted: true, before: before && before.width, after: after && after.width, target, clicked,
    reason: ok ? '' : '点击展开按钮后侧栏宽度仍未匹配设计稿，保留实际测量状态' };
}

// 指纹跨排序稳定；重复证据用后缀去重，避免序号与报告重排错位。
function stemFor(defect, used) {
  const dim = String(defect.dimension || 'x').replace(/[^0-9.]/g, '') || 'x';
  const fp = String(defect.fingerprint || '').replace(/[^A-Za-z0-9_-]/g, '').slice(0, 8);
  const base = (fp || 'nofp') + '-' + dim;
  let name = base;
  let n = 2;
  while (used.has(name)) name = base + '-' + n++;
  used.add(name);
  return name;
}

async function outlineOn(locator) {
  const prev = await locator.evaluate(el => {
    const previous = { outline: el.style.getPropertyValue('outline'), offset: el.style.getPropertyValue('outline-offset') };
    el.style.setProperty('outline', '3px solid #FF3B30', 'important');
    el.style.setProperty('outline-offset', '2px', 'important');
    return previous;
  });
  return { locator, prev };
}

async function outlineOff(handle) {
  if (!handle) return;
  await handle.locator.evaluate((el, prev) => {
    el.style.setProperty('outline', prev.outline || '');
    el.style.setProperty('outline-offset', prev.offset || '');
  }, handle.prev).catch(e => console.warn('[warn] 红框还原失败: ' + String(e.message || e).slice(0, 100)));
}

function canonicalUrl(value) {
  try { return new URL(String(value), URL_).href; } catch (_) { return ''; }
}

function originError(defect, currentUrl) {
  for (const field of ['state', 'scenario']) {
    const value = defect[field];
    const state = typeof value === 'string' ? value.trim().toLowerCase().replace(/[\s_]+/g, '-') : '';
    // 只识别完整的已知状态名，不能把包含 error 等词的一般场景 ID 当成状态。
    if (/^(dialog-open|modal-open|hover|hovered|focus|focused|focus-visible|disabled|loading|error|print|printing|print-mode|dark|dark-mode|打印|暗色|暗色模式|悬停|聚焦|禁用|加载中|错误|弹窗打开)$/.test(state)) {
      return '缺陷要求非默认状态 ' + value + '，本轮未复现该状态，未从默认态冒充取图';
    }
  }
  for (const source of [defect, defect.element || {}]) {
    if (source.isMainFrame === false || source.frameUrl || source.framePath
        || (source.frame != null && !['', 'main', 'mainFrame', 'top'].includes(source.frame))) {
      return '缺陷来自非主 frame 或无法确认的 frame，本轮仅截取主页面当前可复现状态';
    }
    for (const key of ['url', 'pageUrl', 'targetUrl', 'page']) {
      const value = source[key];
      if (typeof value === 'string' && (/^https?:\/\//i.test(value) || /^[\/#?]/.test(value))) {
        if (canonicalUrl(value) !== canonicalUrl(currentUrl)) return '缺陷目标 URL 与当前页面不同，未从当前页面冒充取图: ' + value;
      }
    }
  }
  return '';
}

async function rejectLogin(page) {
  const url = page.url();
  if (/(?:^|[\/_.?&=#-])(?:log[-_]?in|sign[-_]?in|passport)(?:[\/_.?&=#-]|$)/i.test(url)
      || await page.locator('input[type="password"]').count() > 0) {
    throw new Error('页面疑似停留在登录页（登录态缺失或已过期），拒绝截图: ' + url);
  }
}

async function main() {
  if (!REPORT || !URL_) {
    console.error('[x] 缺少 --report 或 --url');
    process.exitCode = 1;
    return;
  }
  const reportPath = path.resolve(REPORT);
  const report = JSON.parse(fs.readFileSync(reportPath, 'utf8'));
  const defects = Array.isArray(report.defects) ? report.defects : [];
  for (const defect of defects) {
    for (const key of ['screenshots', 'shotBox', 'shotError', 'shotNote', 'designNote']) delete defect[key];
  }
  report.defects = defects;
  const summary = { total: defects.length, captured: 0, elementMissing: 0, pageLevel: 0, failed: 0,
    settleNote: '', authOk: false, sidebar: null, safetyPolicy: SAFETY };
  report.defectShots = summary;
  const persist = () => fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));
  // 在启动浏览器前落盘；即使本轮启动失败，也不得留下上一轮证据。
  persist();
  if (!defects.length) {
    console.log('[i] 报告里没有缺陷，无需附图');
    return;
  }
  let handle;
  let shotDir;
  try {
    if (!['all', 'safe', 'mockWrites', 'staticOnly'].includes(SAFETY)) throw new Error('不支持的 safety-policy: ' + SAFETY);
    const root = path.resolve(OUT, 'shots', 'defects');
    fs.mkdirSync(root, { recursive: true });
    shotDir = fs.mkdtempSync(path.join(root, 'run-'));
    try { handle = await launch({ profile: PROFILE, chrome: CHROME, headed: HEADED, viewport: vp() }); }
    catch (e) { throw new Error('浏览器启动失败: ' + String(e.message || e)); }
    if (handle.error) {
      process.exitCode = 2;
      throw new Error('浏览器启动失败: ' + handle.error);
    }
    // 写操作防护：goto 成功前的非只读请求是页面初始化，放行并记录；之后的
    // 业务写按 mockWrites 中断。避免此前「goto 前全拦」掐断认证请求、
    // 页面停在错误态，再让逐缺陷定位在错误态上误报「元素未找到」。
    const writeGuard = createWriteGuard(handle.context, SAFETY);
    await writeGuard.beforeNavigation();
    const authInfo = await restoreAuth(handle.context);
    summary.authOk = !!(authInfo && authInfo.ok);
    const page = handle.context.pages()[0] || await handle.context.newPage();
    try { await page.goto(URL_, { waitUntil: 'load', timeout: 60000 }); }
    catch (e) { throw new Error('页面打开失败: ' + String(e.message || e)); }
    writeGuard.markNavigationSucceeded();
    await rejectLogin(page);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(e => {
      summary.settleNote = '等待 networkidle 超时（' + String(e.message || e).slice(0, 60) + '），按当前渲染状态截图';
    });
    await page.waitForTimeout(SETTLE);
    // 页面身份校验：未达目标态（登录页/错误页/空壳）时不逐缺陷取图，
    // 每条缺陷留 notRun 性质的原因，绝不拿错误态页面冒充缺陷现场。
    const identity = await verifyPageIdentity(page, URL_);
    summary.pageIdentity = identity;
    summary.writeProtection = writeGuard.finalize(identity.reachedTarget);
    if (!identity.reachedTarget) {
      const reason = '页面未达目标态（' + (identity.isLoginPage ? '登录页' : '身份校验失败')
        + '，原因: ' + identity.reasons.join('; ') + '）：缺陷附图未取'
        + (summary.writeProtection.reason ? '；' + summary.writeProtection.reasonNote : '');
      for (const defect of defects.slice(0, MAX)) {
        defect.screenshots = [{ kind: 'none', note: reason }];
        defect.shotError = reason;
      }
      summary.identityBlocked = true;
      summary.note = reason;
      persist();
      console.error('ERROR: ' + reason);
      process.exitCode = 3;
      return;
    }
    if (ALIGN_SIDEBAR) summary.sidebar = await alignSidebar(page, ALIGN_SIDEBAR);
    await rejectLogin(page);
    const usedStems = new Set();
    const items = defects.slice(0, MAX);
    for (const defect of items) {
      const shots = [];
      defect.screenshots = shots;
      const stem = stemFor(defect, usedStems);
      const el = defect.element || {};
      const selector = String(el.selector || '').trim();
      try {
        await rejectLogin(page);
        const mismatch = originError(defect, page.url());
        if (mismatch) throw new Error(mismatch);
        if (!selector) {
          summary.pageLevel += 1;
          if (!SKIP_CONTEXT) {
            const file = path.join(shotDir, stem + '.page.png');
            await page.screenshot({ path: file });
            if (!fs.existsSync(file)) throw new Error('整页截图未生成文件');
            shots.push({ kind: 'page', path: file, note: '页面级缺陷，无元素定位' });
          }
        } else {
          const nth = el.nth == null ? 0 : Number(el.nth);
          const count = await page.locator(selector).count();
          if (!count || !Number.isInteger(nth) || nth < 0 || nth >= count) {
            summary.elementMissing += 1;
            throw new Error(!count ? '元素未找到（' + selector + '），未取到缺陷位置图；可能需登录或特定交互状态'
              : '元素 nth 无效或越界（' + el.nth + '，匹配 ' + count + ' 项），未取到缺陷位置图');
          }
          const locator = page.locator(selector).nth(nth);
          await locator.scrollIntoViewIfNeeded({ timeout: 5000 });
          const box = await locator.boundingBox();
          if (!box || box.width <= 0 || box.height <= 0) throw new Error('元素不可见，未取到缺陷位置图');
          if (box.width < 6 || box.height < 6) {
            defect.shotNote = '元素尺寸 ' + Math.round(box.width) + 'x' + Math.round(box.height) + 'px 过小，仅保留整页图';
          } else {
            const crop = path.join(shotDir, stem + '.crop.png');
            await locator.screenshot({ path: crop, timeout: 10000 });
            if (!fs.existsSync(crop)) throw new Error('元素截图未生成文件');
            shots.push({ kind: 'crop', path: crop, note: '缺陷元素局部' });
          }
          const scrollY = await page.evaluate(() => window.scrollY);
          defect.shotBox = { x: Math.round(box.x), y: Math.round(box.y), w: Math.round(box.width), h: Math.round(box.height),
            scrollY: Math.round(scrollY || 0), viewport: vp() };
          if (!SKIP_CONTEXT) {
            const full = path.join(shotDir, stem + '.page.png');
            let outlined;
            try {
              outlined = await outlineOn(locator);
              await page.screenshot({ path: full, fullPage: true });
              if (!fs.existsSync(full)) throw new Error('整页截图未生成文件');
              shots.push({ kind: 'page', path: full, note: '整页图，红框标出缺陷位置' });
            } finally { await outlineOff(outlined); }
          }
        }
      } catch (e) {
        summary.failed += 1;
        defect.shotError = String(e.message || e).slice(0, 300);
        if (!shots.length) shots.push({ kind: 'none', path: '', note: defect.shotError });
      }
      if (shots.some(shot => shot.kind === 'crop' || shot.kind === 'page')) summary.captured += 1;
    }
    for (const defect of defects.slice(items.length)) {
      defect.screenshots = [];
      defect.shotNote = '未附图：超出本轮 --max ' + MAX + ' 上限';
    }
    if (summary.captured === 0 || summary.failed > 0) process.exitCode = 3;
    console.log('[i] 附图结果: 取到 ' + summary.captured + ' / ' + summary.total + '，失败 ' + summary.failed);
  } catch (e) {
    const reason = String(e.message || e).slice(0, 300);
    summary.failed = defects.length;
    summary.reason = reason;
    for (const defect of defects) {
      defect.screenshots = [];
      defect.shotError = reason;
    }
    process.exitCode = process.exitCode || 1;
    console.error('[x] ' + reason);
  } finally {
    try {
      persist();
      if (shotDir) fs.writeFileSync(path.join(shotDir, 'manifest.json'), JSON.stringify(
        { url: URL_, viewport: vp(), summary, defects: defects.map((d, i) => ({ index: i + 1, dimension: d.dimension,
          screenshots: d.screenshots || [], shotError: d.shotError || null, shotNote: d.shotNote || null })) }, null, 2));
    } finally {
      if (handle && !handle.error) await closeAll(handle);
    }
  }
}

main().catch(e => {
  console.error('[x] 未捕获异常: ' + String(e.stack || e).slice(0, 600));
  process.exitCode = 1;
});
