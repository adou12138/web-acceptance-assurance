/*
 * dense-form.html 旅程逻辑冒烟（M6/§7.1 验收影响问题 #3）。
 * 用最小桩执行夹具内联脚本，验证「校验失败 → 修正 → 提交 → 重开读回」可完成：
 *   1) 空值校验失败、不写入存储；
 *   2) 超长值校验失败；
 *   3) 合法值保存成功、重开（新桩读同一存储）读回一致；
 *   4) 重复保存覆盖同一键（不追加、不重复记录）。
 * 说明：这是夹具**能力**的机制级证据；真实浏览器取证与状态读回仍待实跑（试点登记未执行）。
 * 退出码 0 = 全过。
 */
const fs = require('fs');
const path = require('path');

const HTML = fs.readFileSync(
  path.join(__dirname, '..', '..', '..', '..', 'tests', 'fixtures', 'ui', 'dense-form.html'), 'utf8');
const match = HTML.match(/<script>([\s\S]*?)<\/script>/);
if (!match) { console.error('FAIL: dense-form.html 缺少内联旅程脚本'); process.exit(1); }
const SRC = match[1];

function makeStorage(seed) {
  const map = seed ? new Map(seed) : new Map();
  return {
    map,
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => { map.set(k, String(v)); },
    removeItem: (k) => { map.delete(k); },
  };
}

function loadApi(storage) {
  const fakeModule = { exports: {} };
  const run = new Function('window', 'document', 'module', 'localStorage',
    `"use strict";var window={localStorage:localStorage};var document={getElementById:function(){return null;}};` +
    `${SRC}\n;return (typeof window.__denseForm!=="undefined")?window.__denseForm:module.exports;`);
  return run(undefined, undefined, fakeModule, storage);
}

let pass = 0;
let fail = 0;
function check(name, ok, detail) {
  if (ok) { pass += 1; console.log('[PASS] ' + name); }
  else { fail += 1; console.log('[FAIL] ' + name + (detail ? ' — ' + detail : '')); }
}

const storage = makeStorage();
const api = loadApi(storage);
check('内联脚本导出 __denseForm API（validate/save/load）',
  !!(api && api.validate && api.save && api.load), JSON.stringify(Object.keys(api || {})));

// 1) 空值校验失败，不写入
const bad = api.save('   ', storage);
check('空昵称校验失败（保留错误提示语义）', bad.ok === false && bad.message.indexOf('不能为空') >= 0,
  JSON.stringify(bad));
check('校验失败不写入存储', storage.map.size === 0, JSON.stringify([...storage.map.keys()]));

// 2) 超长值校验失败
const tooLong = api.save('x'.repeat(21), storage);
check('超过 20 字校验失败', tooLong.ok === false && tooLong.message.indexOf('20') >= 0,
  JSON.stringify(tooLong));

// 3) 修正为合法值 → 提交成功 → 重开读回
const saved = api.save('  新昵称  ', storage);
check('合法值保存成功（trim 后入库）', saved.ok === true && saved.saved === '新昵称', JSON.stringify(saved));
const reopened = loadApi(makeStorage(storage.map));  // 模拟重开：从同一存储重建
check('重开读回与保存值一致', reopened.load(makeStorage(storage.map)) === '新昵称',
  reopened.load(makeStorage(storage.map)));

// 4) 重复保存覆盖同一键（不追加）
api.save('第二版昵称', storage);
check('重复保存覆盖同一键、不追加记录',
  storage.map.size === 1 && storage.getItem(api.storeKey) === '第二版昵称',
  JSON.stringify([...storage.map.entries()]));

console.log('\n==== dense-form 旅程冒烟：' + pass + ' 通过 / ' + fail + ' 失败 ====');
process.exit(fail ? 1 : 0);