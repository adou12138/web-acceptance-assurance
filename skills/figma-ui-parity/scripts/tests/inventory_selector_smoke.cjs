#!/usr/bin/env node
/*
 * 页面盘点的选择器生成自检（真实浏览器，非桩）。
 *
 * 锁定的事实：Tailwind 的任意值类名（h-[34px] / w-1/2 / md:flex / top-[calc(100%-8px)]）
 * 直接拼进 CSS 选择器就是非法语法。实测后果不是"少一条记录"，而是
 * querySelectorAll 抛 SyntaxError → 整个盘点失败 → page.json 缺位 →
 * 对应性预检与逐字段比对全部空转，而命令退出码看起来只是"数据质量闸没通过"。
 *
 * 两条路径都必须成立：
 *   1) 有 CSS.escape → 类名被转义，选择器可解析且能重新定位到同一个元素；
 *   2) 没有 CSS.escape（老内核）→ 丢掉非法类名退到结构选择器，仍然可解析、仍能定位，
 *      并在产物里如实记 selectorFallbacks，不许静默。
 */
const fs = require('fs');
const path = require('path');
const { resolveChrome } = require('../stabilize.js');

const SRC = fs.readFileSync(path.join(__dirname, '..', 'collect_page_inventory.js'), 'utf8');

const PAGE = `<!DOCTYPE html><html><body>
<div id="root">
  <button class="flex h-[34px] w-1/2 items-center" onclick="void 0">开始创作</button>
  <button class="flex h-[34px] md:flex" onclick="void 0">模板中心</button>
  <button class="top-[calc(100%-8px)] w-1/2/3" onclick="void 0">异常类名</button>
  <input class="h-[34px] w-full" placeholder="描述你的问卷" />
</div>
</body></html>`;

let pass = 0;
let fail = 0;
function check(name, ok, detail) {
  if (ok) { pass += 1; console.log('[PASS] ' + name); }
  else { fail += 1; console.log('[FAIL] ' + name + (detail ? ' — ' + detail : '')); }
}

(async () => {
  const pw = require('playwright-core');
  const chrome = resolveChrome();
  const browser = await pw.chromium.launch({ headless: true, executablePath: chrome, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    await page.setContent(PAGE, { waitUntil: 'load' });

    /* ---- 1) 有 CSS.escape：类名转义后可解析、可回定位 ---- */
    const withEscape = await page.evaluate(SRC);
    const inv = typeof withEscape === 'string' ? JSON.parse(withEscape) : withEscape;
    check('盘点不抛异常（含 Tailwind 任意值类名）', !!inv && !!inv.buttons, JSON.stringify(inv).slice(0, 120));
    check('三个按钮都入库', (inv.buttons || []).length === 3, String((inv.buttons || []).length));

    const locate = await page.evaluate((items) => items.map((it) => {
      let list;
      try { list = Array.prototype.slice.call(document.querySelectorAll(it.selector)); }
      catch (e) { return { ok: false, reason: 'selector-invalid' }; }
      const hit = list[it.nth];
      return { ok: !!hit && (hit.innerText || '').trim() === it.text, got: hit ? (hit.innerText || '').trim() : null, sel: it.selector };
    }), inv.buttons || []);
    check('每个按钮的选择器都能解析并回定位到同一元素',
      locate.every((x) => x.ok), JSON.stringify(locate).slice(0, 300));
    // 锚点必须落在**被转义的那段类名本身**（h-[34px] 里的 34px），
    // 用 'h-' 判会被 `:nth-of-type` 的子串命中，变异验证时假绿（实测踩过两次）。
    check('选择器保留可读类名（未被无谓降级）',
      (inv.buttons || []).some((b) => String(b.selector).indexOf('34px') >= 0),
      JSON.stringify((inv.buttons || []).map((b) => b.selector)));
    check('无 CSS.escape 场景未触发兜底时 selectorFallbacks=0',
      inv.counts && inv.counts.selectorFallbacks === 0, JSON.stringify(inv.counts));

    /* ---- 2) 去掉 CSS.escape：必须退到结构选择器且仍可定位 ---- */
    const page2 = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    await page2.setContent(PAGE, { waitUntil: 'load' });
    await page2.evaluate(() => { try { delete window.CSS; } catch (e) { window.CSS = undefined; } window.CSS = undefined; });
    const noEscape = await page2.evaluate(SRC);
    const inv2 = typeof noEscape === 'string' ? JSON.parse(noEscape) : noEscape;
    check('无 CSS.escape 时盘点仍不抛异常', !!inv2 && !!inv2.buttons, JSON.stringify(inv2).slice(0, 120));
    // 没有 CSS.escape 时非法类名被丢弃（不是转义），选择器仍是合法且唯一的结构定位，
    // 所以这里不该产生兜底；兜底只在「连结构选择器都不可解析」时才会发生（见下一条用例）。
    // 注意断言写成「不含方括号」而不是「不含 h-」：`:nth-of-type` 里天然含 "h-"
    // 这种子串，按子串判会把正确实现判成失败（实测踩过）。
    check('无 CSS.escape 时非法类名被丢弃而非生成非法选择器',
      (inv2.buttons || []).every((b) => String(b.selector).indexOf('[') < 0),
      JSON.stringify((inv2.buttons || []).map((b) => b.selector)));
    const locate2 = await page2.evaluate((items) => items.map((it) => {
      let list;
      try { list = Array.prototype.slice.call(document.querySelectorAll(it.selector)); }
      catch (e) { return { ok: false, reason: 'selector-invalid' }; }
      const hit = list[it.nth];
      return { ok: !!hit && (hit.innerText || '').trim() === it.text, got: hit ? (hit.innerText || '').trim() : null };
    }), inv2.buttons || []);
    check('兜底后的结构选择器仍能回定位',
      locate2.length > 0 && locate2.every((x) => x.ok), JSON.stringify(locate2).slice(0, 300));

    /* ---- 3) 连 id 都含非法字符且无 CSS.escape：必须退到结构选择器并计数 ---- */
    const page3 = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    await page3.setContent(`<!DOCTYPE html><html><body>
      <div id="wrapper"><button id="a:b" class="w-1/2" onclick="void 0">ID 含冒号</button></div>
    </body></html>`, { waitUntil: 'load' });
    await page3.evaluate(() => { window.CSS = undefined; });
    const noEscape3 = await page3.evaluate(SRC);
    const inv3 = typeof noEscape3 === 'string' ? JSON.parse(noEscape3) : noEscape3;
    check('id 含非法字符且无 CSS.escape → 退到结构选择器并计数（不静默）',
      !!inv3.counts && inv3.counts.selectorFallbacks > 0, JSON.stringify(inv3.counts));
    const locate3 = await page3.evaluate((items) => items.map((it) => {
      let list;
      try { list = Array.prototype.slice.call(document.querySelectorAll(it.selector)); }
      catch (e) { return { ok: false, reason: 'selector-invalid' }; }
      const hit = list[it.nth];
      return { ok: !!hit && (hit.innerText || '').trim() === it.text, sel: it.selector };
    }), inv3.buttons || []);
    check('结构选择器兜底后仍能回定位到同一元素',
      locate3.length > 0 && locate3.every((x) => x.ok), JSON.stringify(locate3).slice(0, 300));

  } finally {
    await browser.close();
  }
  console.log('\n==== inventory_selector 冒烟：' + pass + ' 通过 / ' + fail + ' 失败 ====');
  process.exit(fail ? 1 : 0);
})().catch((e) => { console.error('FATAL', String(e).slice(0, 400)); process.exit(1); });
