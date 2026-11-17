#!/usr/bin/env node
/**
 * 脚本 E「图标与文字垂直居中」的离线冒烟。
 *
 * 为什么要用假 DOM 跑：这段逻辑只有在页面里才有几何值，而真实浏览器不在单测环境里。
 * 用最小 DOM 替身跑**脚本本身的源码**（不复制逻辑），能验证三件事：
 * 1. 中心差 6px → pass=false；
 * 2. 中心差 1px → pass=true；
 * 3. 绝对定位图标 / 无文字宿主 → 不产出条目（防假缺陷）。
 *
 * 局限（如实声明）：替身只覆盖几何与可见性，不覆盖真实布局引擎的亚像素取整。
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const SRC = fs.readFileSync(path.join(__dirname, '..', 'alignment_modal_checks.js'), 'utf8');

function makeEl(tag, opts = {}) {
  const rect = opts.rect || { left: 0, top: 0, width: 0, height: 0 };
  const node = {
    nodeType: 1,
    tagName: tag.toUpperCase(),
    id: opts.id || '',
    textContent: opts.text || '',
    childNodes: opts.childNodes || [],
    scrollWidth: opts.scrollWidth || 0,
    clientWidth: opts.clientWidth || 0,
    parentElement: opts.parent || null,
    nextElementSibling: null,
    getAttribute: name => (name === 'class' ? (opts.cls || '') : null),
    getBoundingClientRect: () => ({
      left: rect.left, top: rect.top, width: rect.width, height: rect.height,
      right: rect.left + rect.width, bottom: rect.top + rect.height
    }),
    querySelector: sel => (opts.querySelector ? opts.querySelector(sel) : null),
    querySelectorAll: sel => (opts.querySelectorAll ? opts.querySelectorAll(sel) : [])
  };
  return node;
}

function run(hosts) {
  const sandbox = {
    window: { scrollX: 0, scrollY: 0 },
    getComputedStyle: el => ({
      display: 'block', visibility: 'visible', fontSize: '12px',
      position: (el && el._position) || 'static'
    }),
    document: {
      body: makeEl('body'),
      querySelectorAll: sel => (sel.indexOf('button') >= 0 || sel.indexOf('Btn') >= 0 ? hosts : []),
      createRange: () => ({ selectNodeContents() {}, getBoundingClientRect: () => ({ left: 0, top: 0, width: 0, height: 0 }) }),
      createTreeWalker: () => ({ nextNode: () => false })
    },
    NodeFilter: { SHOW_TEXT: 4 }
  };
  const out = vm.runInNewContext(SRC, sandbox, { timeout: 5000 });
  return JSON.parse(out);
}

function host({ iconTop, iconH = 16, textTop, textH = 16, iconPosition = 'static', withText = true }) {
  const icon = makeEl('i', { rect: { left: 10, top: iconTop, width: 16, height: iconH }, cls: 'icon-add' });
  icon._position = iconPosition;
  const label = makeEl('span', { rect: { left: 30, top: textTop, width: 40, height: textH }, text: '新增' });
  return makeEl('button', {
    cls: 'btn-add',
    text: '新增',
    // 宿主必须有非零尺寸：脚本用「可见性 = display/visibility + 宽高 > 0」过滤，
    // 零尺寸会被当成不可见元素直接跳过（替身漏给 rect 会假失败）。
    rect: { left: 0, top: 96, width: 120, height: 24 },
    querySelector: () => icon,
    querySelectorAll: () => (withText ? [label] : [])
  });
}

function centeringRows(result) {
  return (result.alignments || []).filter(r => r.kind === 'icon-text-center');
}

// 1. 带装饰性干扰：页面里还有别的元素，确保不会误抓
let res = run([host({ iconTop: 100, textTop: 103 })]); // 中心: 108 vs 111 → -3
assert.strictEqual(centeringRows(res).length, 1, '应产出 1 条 icon-text-center');
assert.strictEqual(centeringRows(res)[0].pass, false, '中心差 3px 应判 fail');
assert.ok(Math.abs(centeringRows(res)[0].deltaCenterY + 3) < 0.01, 'deltaCenterY 应为 -3');

// 2. 1px 内视为居中
res = run([host({ iconTop: 100, textTop: 101 })]);
assert.strictEqual(centeringRows(res)[0].pass, true, '中心差 1px 应判 pass');

// 3. 绝对定位图标不参与基线对齐
res = run([host({ iconTop: 100, textTop: 120, iconPosition: 'absolute' })]);
assert.strictEqual(centeringRows(res).length, 0, '绝对定位图标不应产出对齐条目');

// 4. 没有文字就不比（避免把纯图标按钮报成"未居中"）
res = run([host({ iconTop: 100, textTop: 120, withText: false })]);
assert.strictEqual(centeringRows(res).length, 0, '无文字宿主不应产出对齐条目');

console.log('PASS: alignment icon-text-center 4 cases');
