#!/usr/bin/env node
/**
 * B4（借鉴 Argos ARIA 树 diff）：可访问性树快照 + 结构 diff。
 * 让 a11y 回归变成确定性、可解释的结论——像素看不出的 role / name / 焦点序问题可被抓出，
 * 对应清单 7.19（可访问性与键盘可用性）。
 *
 * 用法：
 *  node capture_aria.js --url <页面URL> --out aria.json [--profile <持久profile>] [--headed] [--wait-login]
 *  node capture_aria.js --diff a.json b.json        # 结构 diff（role + name 树）
 */
const fs = require('fs');
const path = require('path');

function resolvePlaywright() {
  const candidates = ['playwright-core', path.join(process.cwd(), 'node_modules', 'playwright-core')];
  if (process.env.PLAYWRIGHT_CORE_PATH) candidates.push(process.env.PLAYWRIGHT_CORE_PATH);
  for (const c of candidates) { try { return require(c); } catch (e) { /* next */ } }
  return null;
}
// 【F42b】复用 scripts/stabilize.js 的跨平台 resolveChrome，去掉 Windows-only 解析。
const { resolveChrome } = require('./stabilize.js');

async function isLoginPage(page) {
  return page.evaluate(() => {
    const body = document.body ? document.body.innerText : '';
    const url = location.href || '';
    return /login|signin/i.test(url) || /未登录|您还未登录|请先登录|请登录/.test(body) || /立即注册|忘记用户名|验证码登录|账号登录/.test(body);
  }).catch(() => true);
}
async function waitLogin(page, maxMin = 15) {
  const deadline = Date.now() + maxMin * 60000;
  while (Date.now() < deadline) {
    await page.waitForTimeout(5000);
    if (!(await isLoginPage(page))) return true;
  }
  return false;
}

/** 抓取可访问性快照（role + name 树）
 *
 * 【API 失配修复】原来直接调 `page.accessibility.snapshot()`，该 API 在
 * Playwright 1.48 起标记废弃、后续版本已移除。本机跑的是 1.63，调用必然抛
 * `Cannot read properties of undefined (reading 'snapshot')`——7.19 因此不是
 * 「没跑」，是**跑不了**，而报告只写「无产物覆盖」，把能力失效伪装成了漏跑。
 *
 * 改为在页面内自建等价的 role+name 树：
 *   - role 取显式 role 属性，缺省用标签推断的隐式 role；
 *   - name 按 ARIA 的可访问名称优先级：aria-labelledby → aria-label → alt →
 *     title → placeholder → 内部文本；
 *   - 只保留有语义的节点（有 role 或有 name），纯布局容器不产生节点。
 * 输出形状与旧快照一致（{role, name, children}），flatten / diffAria 无需改动。
 */
async function captureAria(page) {
  return await page.evaluate(() => {
    const IMPLICIT = {
      A: (el) => (el.getAttribute('href') ? 'link' : null),
      BUTTON: () => 'button',
      INPUT: (el) => {
        const t = (el.getAttribute('type') || 'text').toLowerCase();
        if (t === 'checkbox') return 'checkbox';
        if (t === 'radio') return 'radio';
        if (t === 'submit' || t === 'button' || t === 'reset') return 'button';
        if (t === 'range') return 'slider';
        return 'textbox';
      },
      TEXTAREA: () => 'textbox',
      SELECT: () => 'combobox',
      IMG: () => 'img',
      H1: () => 'heading', H2: () => 'heading', H3: () => 'heading',
      H4: () => 'heading', H5: () => 'heading', H6: () => 'heading',
      UL: () => 'list', OL: () => 'list', LI: () => 'listitem',
      TABLE: () => 'table', TR: () => 'row', TD: () => 'cell', TH: () => 'columnheader',
      NAV: () => 'navigation', MAIN: () => 'main', ASIDE: () => 'complementary',
      HEADER: () => 'banner', FOOTER: () => 'contentinfo', FORM: () => 'form',
      DIALOG: () => 'dialog', LABEL: () => 'label',
    };

    const visible = (el) => {
      const cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0) return false;
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    };

    const textOf = (el) => (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();

    const nameOf = (el) => {
      const labelledBy = el.getAttribute('aria-labelledby');
      if (labelledBy) {
        const target = document.getElementById(labelledBy.split(/\s+/)[0]);
        if (target) {
          const t = textOf(target);
          if (t) return t.slice(0, 80);
        }
      }
      const ariaLabel = (el.getAttribute('aria-label') || '').trim();
      if (ariaLabel) return ariaLabel.slice(0, 80);
      const alt = (el.getAttribute('alt') || '').trim();
      if (alt) return alt.slice(0, 80);
      const title = (el.getAttribute('title') || '').trim();
      if (title) return title.slice(0, 80);
      const ph = (el.getAttribute('placeholder') || '').trim();
      if (ph) return ph.slice(0, 80);
      const tag = el.tagName;
      // 容器类元素不取内部文本：否则整页会缩成一两个巨型节点
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return '';
      const t = textOf(el);
      return t.length <= 80 ? t : '';
    };

    const roleOf = (el) => {
      const explicit = (el.getAttribute('role') || '').trim();
      if (explicit) return explicit.split(/\s+/)[0];
      const fn = IMPLICIT[el.tagName];
      return fn ? fn(el) : null;
    };

    const walk = (el, depth) => {
      if (depth > 40) return null;
      const role = roleOf(el);
      const name = nameOf(el);
      // 有语义才成节点；纯布局 div 自身不产生节点，但子节点照常上浮。
      const self = role ? { role, name } : null;
      const children = [];
      for (const child of el.children) {
        if (!visible(child)) continue;
        const node = walk(child, depth + 1);
        if (node) children.push(node);
      }
      if (!self) {
        if (children.length === 0) return null;
        // 无 role 的容器：单子节点直接上浮，多子节点用一个 generic 包裹保结构
        return children.length === 1 ? children[0] : { role: 'generic', name: '', children };
      }
      if (children.length) self.children = children;
      return self;
    };

    const root = walk(document.body, 0) || { role: 'generic', name: '' };
    return root;
  });
}

/** 把树拍平成 {role, name, depth, path} 列表，便于 diff */
function flatten(node, depth, out, parentPath) {
  if (!node) return;
  const path = (parentPath ? parentPath + ' > ' : '') + (node.role || '?') + (node.name ? ':' + node.name : '');
  out.push({ role: node.role, name: node.name || '', depth, path });
  if (Array.isArray(node.children)) for (const c of node.children) flatten(c, depth + 1, out, path);
}

/** 结构 diff：比较两份快照的 role+name 树 */
function diffAria(a, b) {
  const fa = [], fb = [];
  flatten(a, 0, fa, '');
  flatten(b, 0, fb, '');
  const setB = new Set(fb.map(x => x.path));
  const setA = new Set(fa.map(x => x.path));
  const added = fb.filter(x => !setA.has(x.path)).map(x => ({ role: x.role, name: x.name, path: x.path }));
  const removed = fa.filter(x => !setB.has(x.path)).map(x => ({ role: x.role, name: x.name, path: x.path }));
  return {
    ok: true,
    aCount: fa.length,
    bCount: fb.length,
    addedCount: added.length,
    removedCount: removed.length,
    added, removed,
    summary: added.length === 0 && removed.length === 0 ? '无结构变化' : (added.length + removed.length) + ' 处可访问性结构变化',
  };
}

// ---- CLI ----
function cliArg(n, def) {
  const i = process.argv.indexOf('--' + n);
  return i >= 0 && process.argv[i + 1] && !process.argv[i + 1].startsWith('--') ? process.argv[i + 1] : def;
}
if (require.main === module) {
  if (process.argv.includes('--diff')) {
    const ai = process.argv.indexOf('--diff') + 1;
    const bi = process.argv.indexOf('--diff') + 2;
    const a = JSON.parse(fs.readFileSync(process.argv[ai], 'utf8'));
    const b = JSON.parse(fs.readFileSync(process.argv[bi], 'utf8'));
    console.log(JSON.stringify(diffAria(a, b), null, 2));
    process.exit(0);
  }
  (async () => {
    const url = cliArg('url');
    const out = cliArg('out', 'aria.json');
    if (!url) { console.error('ERROR: --url required (or use --diff a.json b.json)'); process.exit(1); }
    const pw = resolvePlaywright();
    if (!pw) { console.error('ERROR: playwright-core not found'); process.exit(1); }
    const chrome = resolveChrome();
    const headed = process.argv.includes('--headed');
    // 【审查修复·P1】此前布尔旗标也叫 waitLogin，遮蔽了顶层的同名异步函数——
    // line 105 的 waitLogin(page) 实际在调用布尔值，必抛 TypeError，
    // 「等待人工登录后抓取」功能整体不可用。旗标改名让开。
    const wantLoginWait = process.argv.includes('--wait-login');
    const profileIdx = process.argv.indexOf('--profile');
    const profile = profileIdx >= 0 ? process.argv[profileIdx + 1] : null;
    let context;
    if (profile) context = await pw.chromium.launchPersistentContext(profile, { headless: !headed, executablePath: chrome, args: ['--no-sandbox'], viewport: { width: 1440, height: 900 } });
    else { const b = await pw.chromium.launch({ headless: !headed, executablePath: chrome, args: ['--no-sandbox'] }); context = await b.newContext({ viewport: { width: 1440, height: 900 } }); }
    // 登录态：与其余驱动同一条通道（.auth 里的 storage state），且必须在 goto 之前注入。
    // 此前本脚本只认 --profile，非 profile 模式下必然判「login required」直接退出——
    // 7.19 因此恒 notRun，而真实原因被写成"没提供驱动产物"。
    try {
      const auth = require('./node/auth.cjs');
      const restored = await auth.restoreAuth(context).catch(() => null);
      if (restored && restored.injected) console.log('[i] 登录态已注入 cookies=' + (restored.cookies || 0));
    } catch (e) { /* auth 模块不可用属正常（公开页面无需登录） */ }
    const page = context.pages()[0] || await context.newPage();
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 }).catch(e => console.warn('[warn]', e));
    await page.waitForTimeout(4000);
    if (await isLoginPage(page)) {
      if (!headed && !wantLoginWait) { console.error('[x] login required (use --headed/--wait-login)'); await context.close(); process.exit(2); }
      if (!(await waitLogin(page))) { await context.close(); process.exit(2); }
    }
    const snap = await captureAria(page);
    fs.writeFileSync(out, JSON.stringify(snap, null, 2));
    console.log('[i] aria snapshot ->', out, '(nodes:', JSON.stringify(snap).length, 'bytes)');
    await context.close();
    process.exit(0);
  })().catch(e => { console.error('FATAL', String(e)); process.exit(1); });
}

module.exports = { captureAria, diffAria, flatten };
