#!/usr/bin/env node
/**
 * a11y_audit.js 自检：在 Node 最小 DOM 桩里真跑内置规则集。
 *
 * 为什么必须真跑：对比度是纯计算，阈值或 luminance 公式写错不会有任何报错，
 * 只会输出一个"看起来合理"的比值；而"背景回溯失败时按白底算"会在深色主题上
 * 批量误报。本文件把这两类都驱动一遍：
 *   - 对比度不足必须 confirmed，足够必须不产出条目；
 *   - 背景是回溯推断（assumed）时必须 pending，不能 confirmed。
 *
 * 用法：node scripts/tests/a11y_audit_smoke.cjs
 */
const fs = require('fs');
const path = require('path');

const SRC = fs.readFileSync(path.join(__dirname, '..', 'a11y_audit.js'), 'utf8');
const RUN = new Function('return (' + SRC + ')');

let failures = 0;
function check(name, ok, detail) {
  if (!ok) failures += 1;
  console.log('[' + (ok ? 'PASS' : 'FAIL') + '] ' + name + (ok || detail === undefined ? '' : ' — ' + detail));
}

const BASE_STYLE = {
  display: 'block', visibility: 'visible', opacity: '1',
  color: 'rgb(0, 0, 0)', backgroundColor: 'rgba(0, 0, 0, 0)',
  fontSize: '16px', fontWeight: '400',
};

class El {
  constructor(tag, attrs, style, text) {
    this.tagName = String(tag).toUpperCase();
    this.attrs = attrs || {};
    this.style = Object.assign({}, BASE_STYLE, style || {});
    this.text = text || '';
    this.children = [];
    this.parentElement = null;
    this.nodeType = 1;
  }
  get id() { return this.attrs.id || ''; }
  get className() { return this.attrs.class || ''; }
  get type() { return this.attrs.type || ''; }
  get innerText() { return this.text || ''; }
  get childNodes() {
    const out = this.text ? [{ nodeType: 3, nodeValue: this.text }] : [];
    this.children.forEach((c) => out.push(c));
    return out;
  }
  getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name) ? String(this.attrs[name]) : null; }
  hasAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name); }
  getBoundingClientRect() { return { left: 0, top: 0, width: 100, height: 20 }; }
  append(child) { child.parentElement = this; this.children.push(child); return child; }
  querySelectorAll(selector) { return queryAll(this, selector); }
  querySelector(selector) { return queryAll(this, selector)[0] || null; }
  matches(selector) { return matches(this, selector); }
}

function matches(el, selector) {
  let sel = String(selector).trim();
  let neg = null;
  const notIdx = sel.indexOf(':not(');
  if (notIdx >= 0) {
    const close = sel.indexOf(')', notIdx);
    neg = sel.slice(notIdx + 5, close).trim();
    sel = (sel.slice(0, notIdx) + sel.slice(close + 1)).trim();
  }
  let tagPart = sel;
  let attr = null;
  let val = null;
  const br = sel.indexOf('[');
  if (br >= 0) {
    tagPart = sel.slice(0, br);
    const inner = sel.slice(br + 1, sel.lastIndexOf(']'));
    const eq = inner.indexOf('=');
    if (eq >= 0) {
      attr = inner.slice(0, eq).trim();
      val = inner.slice(eq + 1).trim();
      if (val.length > 1 && val[0] === '"' && val[val.length - 1] === '"') val = val.slice(1, -1);
      if (val.length > 1 && val[0] === "'" && val[val.length - 1] === "'") val = val.slice(1, -1);
    } else {
      attr = inner.trim();
    }
  }
  if (tagPart && el.tagName.toLowerCase() !== tagPart.toLowerCase()) return false;
  if (attr && !Object.prototype.hasOwnProperty.call(el.attrs, attr)) return false;
  if (attr && val !== null && String(el.attrs[attr]) !== val) return false;
  if (neg) {
    const n = neg.slice(1, -1);
    if (Object.prototype.hasOwnProperty.call(el.attrs, n)) return false;
  }
  return true;
}

function queryAll(root, selector) {
  const parts = String(selector).split(',').map((s) => s.trim()).filter(Boolean);
  const out = [];
  const walk = (node) => {
    node.children.forEach((child) => {
      if (parts.some((p) => matches(child, p))) out.push(child);
      walk(child);
    });
  };
  walk(root);
  return out;
}

/** 建页面：root=html，含 head/body */
function makePage(htmlAttrs) {
  const html = new El('html', htmlAttrs || {});
  const body = html.append(new El('body', {}, { backgroundColor: 'rgb(255, 255, 255)' }));
  return { html, body };
}

function installDom(html) {
  global.document = {
    documentElement: html,
    body: html.children[0],
    querySelectorAll: (sel) => queryAll(html, sel),
    querySelector: (sel) => queryAll(html, sel)[0] || null,
    getElementById: (id) => queryAll(html, '[id]').filter((el) => el.attrs.id === id)[0] || null,
  };
  global.window = { scrollX: 0, scrollY: 0 };
  global.location = { href: 'https://example.test/a11y' };
  global.getComputedStyle = (el) => (el && el.style ? el.style : BASE_STYLE);
}

function run() {
  return JSON.parse(RUN());
}

// ---- 1) 空白页面：html 无 lang、无 main → 必须报出对应规则 ----
{
  const { html } = makePage({});
  installDom(html);
  const out = run();
  check('html 缺 lang → html-lang 命中', out.findings.some((f) => f.rule === 'html-lang'),
    JSON.stringify(out.findings.map((f) => f.rule)));
  check('缺 main 地标 → landmark-main 命中（pending）',
    out.findings.some((f) => f.rule === 'landmark-main' && f.status === 'pending'));
  check('规则集自报覆盖数量', out.summary.rulesCoveredCount >= 15, String(out.summary.rulesCoveredCount));
  check('产物必须带 coverageNote（不得声称全量合规）',
    String(out.coverageNote).indexOf('不是全量合规结论') >= 0, out.coverageNote);
}

// ---- 2) 完全合规页面 → 必须产出 pass 证据，不能空数组 ----
{
  const { html, body } = makePage({ lang: 'zh-CN' });
  body.append(new El('main', {}, {}, '正文内容'));
  installDom(html);
  const out = run();
  check('合规页面产出 status=pass 的证据', out.findings.some((f) => f.status === 'pass'),
    JSON.stringify(out.findings.map((f) => f.rule + ':' + f.status)));
}

// ---- 3) img 缺 alt → confirmed；alt="" 合法不报 ----
{
  const { html, body } = makePage({ lang: 'zh-CN' });
  body.append(new El('main', {}, {}, 'x'));
  const noAlt = body.append(new El('img', { src: '/a.png' }));
  const decorative = body.append(new El('img', { src: '/b.png', alt: '' }));
  installDom(html);
  const out = run();
  const hits = out.findings.filter((f) => f.rule === 'img-alt');
  check('缺 alt 的 img 被报出', hits.length === 1, 'hits=' + hits.length);
  check('alt="" 的装饰图不算违规', hits.every((h) => h.element.selector.indexOf('#') !== 0) && hits.length === 1,
    JSON.stringify(hits.map((h) => h.element.selector)));
  check('img-alt 等级 P2', hits[0] && hits[0].severity === 'P2', hits[0] && hits[0].severity);
  check('finding 带 WCAG 编号', hits[0] && hits[0].wcag === 'WCAG 2.1 1.1.1 (A)', hits[0] && hits[0].wcag);
}
void 0; // 保持上方块级作用域独立

// ---- 4) 对比度：不足必须 confirmed；足够不产出 ----
{
  const { html, body } = makePage({ lang: 'zh-CN' });
  body.append(new El('main', {}, {}, 'x'));
  const low = body.append(new El('p', { id: 'low' }, { color: 'rgb(153, 153, 153)' }, '灰色小字'));
  const ok = body.append(new El('p', { id: 'ok' }, { color: 'rgb(0, 0, 0)' }, '黑色正文'));
  installDom(html);
  const out = run();
  const lowHit = out.findings.filter((f) => f.rule === 'color-contrast' && f.element.selector === '#low')[0];
  const okHit = out.findings.filter((f) => f.rule === 'color-contrast' && f.element.selector === '#ok')[0];
  check('#999 灰字在白底 → 对比度不足被报出', !!lowHit, 'ratio=' + (lowHit && lowHit.raw && lowHit.raw.ratio));
  check('对比度不足为 confirmed（背景是实测不透明色）', !!lowHit && lowHit.status === 'confirmed',
    lowHit && lowHit.status);
  check('对比度比值算在 2.8~2.9（#999 vs 白）', !!lowHit && lowHit.raw.ratio >= 2.8 && lowHit.raw.ratio <= 2.9,
    lowHit && String(lowHit.raw.ratio));
  check('黑字白底不产出对比度问题', !okHit, JSON.stringify(okHit && okHit.status));
}

// ---- 5) 背景回溯失败：浅色页面可确认，暗色模式必须降级 pending ----
{
  const { html, body } = makePage({ lang: 'zh-CN' });
  body.style.backgroundColor = 'rgba(0, 0, 0, 0)';   // 去掉 body 的背景，逼背景回溯落空
  body.append(new El('main', {}, {}, 'x'));
  body.append(new El('p', { id: 'dim' }, { color: 'rgb(120, 120, 120)' }, '浅灰字'));
  installDom(html);                                   // 无 matchMedia → 视为浅色
  const out = run();
  const dimHit = out.findings.filter((f) => f.rule === 'color-contrast' && f.element.selector === '#dim')[0];
  check('浅色页面 + 背景推断 → confirmed（白底假设成立）',
    !!dimHit && dimHit.status === 'confirmed', dimHit && dimHit.status);
  check('浅色推断时理由写明白底推断', !!dimHit && String(dimHit.actual).indexOf('默认白底推断') >= 0,
    dimHit && dimHit.actual);
  check('浅色推断的置信度低于实测背景（0.65 < 0.8）', !!dimHit && dimHit.confidence === 0.65,
    dimHit && String(dimHit.confidence));
}
{
  const { html, body } = makePage({ lang: 'zh-CN' });
  body.style.backgroundColor = 'rgba(0, 0, 0, 0)';
  body.append(new El('main', {}, {}, 'x'));
  body.append(new El('p', { id: 'dim2' }, { color: 'rgb(120, 120, 120)' }, '深色主题下的浅灰字'));
  installDom(html);
  global.window.matchMedia = () => ({ matches: true });   // 暗色模式
  const out = run();
  const hit = out.findings.filter((f) => f.rule === 'color-contrast' && f.element.selector === '#dim2')[0];
  check('暗色模式 + 背景推断 → pending（白底假设不可靠）', !!hit && hit.status === 'pending',
    hit && hit.status);
  check('暗色推断时理由写明暗色模式不可靠', !!hit && String(hit.actual).indexOf('暗色模式') >= 0,
    hit && hit.actual);
}

// ---- 6) aria-hidden 内含可聚焦元素 → P1 ----
{
  const { html, body } = makePage({ lang: 'zh-CN' });
  body.append(new El('main', {}, {}, 'x'));
  const box = body.append(new El('div', { 'aria-hidden': 'true' }));
  box.append(new El('button', { id: 'ghost' }, {}, '看不见但能聚焦'));
  installDom(html);
  const out = run();
  const hit = out.findings.filter((f) => f.rule === 'aria-hidden-focus')[0];
  check('aria-hidden 内有可聚焦元素 → 命中且 P1', !!hit && hit.severity === 'P1',
    JSON.stringify(hit && { rule: hit.rule, sev: hit.severity }));
  check('命中的按钮同时被 button-name 放过（它有文本）',
    !out.findings.some((f) => f.rule === 'button-name'), 'button-name 不应命中');
}

// ---- 7) 重复 id / label 悬空 / tabindex 正数 ----
{
  const { html, body } = makePage({ lang: 'zh-CN' });
  body.append(new El('main', {}, {}, 'x'));
  body.append(new El('input', { id: 'dup', 'aria-label': '账号' }));
  body.append(new El('input', { id: 'dup', 'aria-label': '密码' }));
  body.append(new El('label', { for: 'nope' }, {}, '悬空标签'));
  body.append(new El('button', { tabindex: '3' }, {}, '顺序被打乱'));
  installDom(html);
  const out = run();
  check('重复 id 被报出', out.findings.some((f) => f.rule === 'duplicate-id'),
    JSON.stringify(out.findings.filter((f) => f.rule === 'duplicate-id').length));
  check('label[for] 悬空被报出', out.findings.some((f) => f.rule === 'label-for-dangling'));
  check('正数 tabindex 被报出', out.findings.some((f) => f.rule === 'tabindex-positive'));
}

// ---- 8) 规则命中要有上限且截断留痕（防止一页几千条冲垮报告）----
{
  const { html, body } = makePage({ lang: 'zh-CN' });
  body.append(new El('main', {}, {}, 'x'));
  for (let i = 0; i < 30; i += 1) {
    body.append(new El('img', { src: '/x' + i + '.png' }));
  }
  installDom(html);
  const out = run();
  const imgHits = out.findings.filter((f) => f.rule === 'img-alt').length;
  check('单规则产出有上限（8 条）', imgHits === 8, 'hits=' + imgHits);
  const trunc = out.summary.truncated.filter((t) => t.rule === 'img-alt')[0];
  check('截断留痕写明真实总数 30', !!trunc && trunc.total === 30, JSON.stringify(out.summary.truncated));
}

console.log('\n==== a11y_audit 自检：' + (failures === 0 ? '全部通过' : failures + ' 项失败') + ' ====');
process.exit(failures === 0 ? 0 : 1);
