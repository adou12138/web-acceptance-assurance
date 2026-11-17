#!/usr/bin/env node
/**
 * keyboard_order.js 行为冒烟（DOM 桩，不启浏览器）。
 *
 * 每条用例都问一句：把对应实现撤掉，这条会红吗？
 * 反向用例（全部走到时不得报不可达）专门用来防「判定恒真」。
 */
const fs = require('fs');
const path = require('path');

const SRC = fs.readFileSync(path.join(__dirname, '..', 'keyboard_order.js'), 'utf8');

let pass = 0;
let fail = 0;
function check(name, ok, detail) {
  if (ok) { pass += 1; console.log('[PASS] ' + name); }
  else { fail += 1; console.log('[FAIL] ' + name + (detail ? ' :: ' + String(detail).slice(0, 260) : '')); }
}

const BASE_STYLE = { display: 'block', visibility: 'visible', opacity: '1' };

class El {
  constructor(tag, attrs, style) {
    this._tag = String(tag);
    this.attrs = Object.assign({}, attrs || {});
    this.style = Object.assign({}, BASE_STYLE, style || {});
    this.parentElement = null;
    this.children = [];
    this.rect = { width: 100, height: 20 };
  }
  get tagName() { return this._tag.toUpperCase(); }
  get innerText() { return this.attrs['data-text'] || ''; }
  get textContent() { return this.attrs['data-text'] || ''; }
  get id() { return this.attrs.id || ''; }
  get disabled() { return this.attrs.disabled === 'true'; }
  get tabIndex() {
    if (this.attrs.tabindex !== undefined) return parseInt(this.attrs.tabindex, 10);
    const tag = this._tag.toLowerCase();
    if (tag === 'a') return this.attrs.href !== undefined ? 0 : -1;
    if (['button', 'input', 'select', 'textarea', 'summary'].indexOf(tag) >= 0) return 0;
    return -1;
  }
  getAttribute(name) { return this.attrs[name] === undefined ? null : String(this.attrs[name]); }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  hasAttribute(name) { return this.attrs[name] !== undefined; }
  removeAttribute(name) { delete this.attrs[name]; }
  append(child) { child.parentElement = this; this.children.push(child); return child; }
  getBoundingClientRect() { return Object.assign({ top: 0, left: 0 }, this.rect); }
  matches(selector) {
    // 只覆盖本脚本会用到的选择器：弹层判定与属性存在性
    const parts = String(selector).split(',').map((s) => s.trim());
    return parts.some((part) => {
      if (part === '[role=dialog]') return this.attrs.role === 'dialog';
      if (part === '[role=alertdialog]') return this.attrs.role === 'alertdialog';
      if (part === 'dialog[open]') return this._tag.toLowerCase() === 'dialog' && this.attrs.open !== undefined;
      if (/^\[[\w-]+\]$/.test(part)) return this.attrs[part.slice(1, -1)] !== undefined;
      return false;
    });
  }
  describes(selector) {
    // 供 querySelectorAll 使用：只还原到「本桩需要识别」的粒度
    const tag = this._tag.toLowerCase();
    if (selector.indexOf('data-vp-kb') >= 0) return this.attrs['data-vp-kb'] !== undefined;
    if (selector.indexOf('role=dialog') >= 0) return this.matches(selector);
    const parts = selector.split(',');
    for (const raw of parts) {
      const part = raw.trim();
      if (part === tag) return true;
      if (part === 'a[href]' && tag === 'a' && this.attrs.href !== undefined) return true;
      if (part === 'audio[controls]' && tag === 'audio' && this.attrs.controls !== undefined) return true;
      if (part === 'video[controls]' && tag === 'video' && this.attrs.controls !== undefined) return true;
      if (part === '[tabindex]' && this.attrs.tabindex !== undefined) return true;
      if (part === '[contenteditable=true]' && this.attrs.contenteditable === 'true') return true;
      if (/^\[role=\w+\]$/.test(part) && this.attrs.role === part.slice(6, -1)) return true;
    }
    return false;
  }
}

function makeEnv(nodes, active) {
  const body = new El('body', {});
  nodes.forEach((n) => body.append(n));
  const document = {
    body,
    documentElement: new El('html', {}),
    activeElement: active || body,
    querySelectorAll: (selector) => {
      const out = [];
      const walk = (el) => {
        el.children.forEach((child) => { if (child.describes(selector)) out.push(child); walk(child); });
      };
      walk(body);
      return out;
    },
  };
  const window = { getComputedStyle: (el) => (el && el.style ? el.style : BASE_STYLE) };
  global.document = document;
  global.window = window;
  // 页面脚本是「(function(){ ... window.__vpKb = ... })()」形态，执行后从 window 取 API
  new Function('document', 'window', '(' + SRC + ')')(document, window);
  if (!window.__vpKb) throw new Error('keyboard_order.js 未挂载 window.__vpKb');
  return { scope: window.__vpKb, body };
}

function run(nodes, active, observed, opts) {
  const env = makeEnv(nodes, active);
  const plan = env.scope.plan();
  if (opts && opts.jumpTo !== undefined) {
    // 直接跳到某个 activeElement 采样（供单步校验）
    global.document.activeElement = nodes[opts.jumpTo];
  }
  const raw = env.scope.finish(observed || []);
  return { plan, result: JSON.parse(raw), env };
}

function btn(text, attrs) {
  return new El('button', Object.assign({ 'data-text': text }, attrs || {}));
}

// ---- 1) 三个按钮全部按顺序走到 → pass，无缺陷 ----
{
  const nodes = [btn('登录'), btn('注册'), btn('帮助')];
  const { plan, result } = run(nodes, null, [
    { index: 0 }, { index: 1 }, { index: 2 }, { wrapped: true },
  ]);
  check('枚举出 3 个可聚焦元素', plan.total === 3, plan.total);
  // 干净时必须产出一条显式 pass 证据（否则 assemble 会把"跑过且干净"记成 notRun）
  check('顺序完整时不产缺陷（只留一条 pass 证据）',
    result.findings.filter((f) => f.status !== 'pass').length === 0 &&
    result.findings.filter((f) => f.status === 'pass').length === 1,
    JSON.stringify(result.findings.map((f) => f.status)));
  check('顺序完整时 reached=3', result.reached === 3, result.reached);
}

// ---- 2) 一个可见按钮 Tab 走不到 → confirmed 不可达 ----
{
  const nodes = [btn('登录'), btn('提交'), btn('注册')];
  const { result } = run(nodes, null, [
    { index: 0 }, { index: 1 }, { wrapped: true },
  ]);
  const hit = result.findings.filter((f) => f.property === 'kb-unreachable');
  check('走不到的按钮被报出', hit.length === 1, JSON.stringify(result.findings.map((f) => f.property)));
  check('不可达判定为 confirmed', hit[0] && hit[0].status === 'confirmed', hit[0] && hit[0].status);
  check('不可达指向具体元素', hit[0] && hit[0].element.selector === 'button', hit[0] && hit[0].element.selector);
}

// ---- 3) 反向：全部走到时不得报不可达 ----
{
  const nodes = [btn('A'), btn('B')];
  const { result } = run(nodes, null, [{ index: 0 }, { index: 1 }]);
  check('全部走到时没有 kb-unreachable', result.findings.filter((f) => f.property === 'kb-unreachable').length === 0,
    JSON.stringify(result.findings.map((f) => f.property)));
}

// ---- 4) 正 tabindex → 提示顺序被改写 ----
{
  const nodes = [btn('第二个', { tabindex: '2' }), btn('第一个', { tabindex: '1' })];
  const { plan, result } = run(nodes, null, [{ index: 0 }, { index: 1 }]);
  check('计划里标出正 tabindex', plan.positiveTabindex.length === 2, plan.positiveTabindex.length);
  const hit = result.findings.filter((f) => f.property === 'kb-positive-tabindex');
  check('正 tabindex 被报出', hit.length === 1, hit.length);
  check('正 tabindex 严重度为 P3', hit[0] && hit[0].severity === 'P3', hit[0] && hit[0].severity);
}

// ---- 5) 顺序回跳（未循环）→ kb-order-mismatch ----
{
  const nodes = [btn('A'), btn('B'), btn('C'), btn('D')];
  const { result } = run(nodes, null, [{ index: 0 }, { index: 2 }, { index: 1 }, { index: 3 }]);
  const hit = result.findings.filter((f) => f.property === 'kb-order-mismatch');
  check('回跳被报出', hit.length === 1, JSON.stringify(result.findings.map((f) => f.property)));
  check('回跳位置记录为 2→1', hit[0] && hit[0].raw.breaks[0].from === 2 && hit[0].raw.breaks[0].to === 1,
    hit[0] && JSON.stringify(hit[0].raw.breaks));
}

// ---- 6) 反向：从最后一项回到第一项不算回跳 ----
{
  const nodes = [btn('A'), btn('B'), btn('C')];
  const { result } = run(nodes, null, [{ index: 0 }, { index: 1 }, { index: 2 }, { index: 0 }]);
  check('末项回到首项不算顺序错误',
    result.findings.filter((f) => f.property === 'kb-order-mismatch').length === 0,
    JSON.stringify(result.findings.map((f) => f.property)));
}

// ---- 7) 弹层内焦点逃逸 → pending ----
{
  const dialog = new El('div', { role: 'dialog' });
  const inner1 = btn('确定');
  const inner2 = btn('取消');
  dialog.append(inner1);
  dialog.append(inner2);
  const outside = btn('背后的页面按钮');
  const nodes = [dialog, outside];
  const { result } = run(nodes, null, [
    { index: 0, inDialog: true }, { index: 1, inDialog: true }, { index: 2, inDialog: false },
  ]);
  const hit = result.findings.filter((f) => f.property === 'kb-dialog-escape');
  check('焦点逃逸被报出', hit.length === 1, JSON.stringify(result.findings.map((f) => f.property)));
  check('逃逸判定为 pending（需人看是否模态）', hit[0] && hit[0].status === 'pending', hit[0] && hit[0].status);
}

// ---- 8) 反向：焦点始终留在弹层内不得报逃逸 ----
{
  const dialog = new El('div', { role: 'dialog' });
  const inner1 = btn('确定');
  dialog.append(inner1);
  const nodes = [dialog, btn('背后按钮')];
  const { result } = run(nodes, null, [
    { index: 0, inDialog: true }, { index: 0, inDialog: true }, { wrapped: true },
  ]);
  check('弹层内循环不报逃逸',
    result.findings.filter((f) => f.property === 'kb-dialog-escape').length === 0,
    JSON.stringify(result.findings.map((f) => f.property)));
}

// ---- 9) 未计划的焦点元素 → pending，写明覆盖缺口 ----
{
  const nodes = [btn('A')];
  const { result } = run(nodes, null, [
    { index: 0 }, { index: null, unplanned: true, selector: 'iframe', tag: 'iframe' },
  ]);
  const hit = result.findings.filter((f) => f.property === 'kb-unplanned-focusable');
  check('未枚举焦点被报出', hit.length === 1, hit.length);
  check('未枚举焦点为 pending', hit[0] && hit[0].status === 'pending', hit[0] && hit[0].status);
}

// ---- 10) 禁用元素不进期望序列 ----
{
  const nodes = [btn('可用'), btn('禁用', { disabled: 'true' })];
  const { plan } = run(nodes, null, []);
  check('禁用按钮不进期望序列', plan.total === 1, plan.total);
}

// ---- 11) 隐藏元素不进期望序列 ----
{
  const hidden = btn('隐藏按钮', {});
  hidden.style.display = 'none';
  const { plan } = run([btn('可见'), hidden], null, []);
  check('display:none 不进期望序列', plan.total === 1, plan.total);
}

// ---- 12) tabindex=-1 不算不可达（只能脚本聚焦，本就 Tab 不到）----
{
  const nodes = [btn('A'), btn('脚本聚焦', { tabindex: '-1' })];
  const { plan, result } = run(nodes, null, [{ index: 0 }]);
  check('tabindex=-1 不进期望序列', plan.total === 1, plan.total);
  check('tabindex=-1 不被报不可达',
    result.findings.filter((f) => f.property === 'kb-unreachable').length === 0,
    JSON.stringify(result.findings.map((f) => f.property)));
}

console.log('\n==== keyboard_order 冒烟：' + pass + ' 通过 / ' + fail + ' 失败 ====');
process.exit(fail === 0 ? 0 : 1);
