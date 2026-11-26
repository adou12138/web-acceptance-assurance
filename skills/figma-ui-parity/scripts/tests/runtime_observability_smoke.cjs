/*
 * runtime_observability.js 的可执行冒烟：用桩全局在 Node 里真跑
 * （① install ② 触发后 collect），验证脚本能运行且能采到 7.35 异常、7.37 自动确认。
 * 浏览器里才有的 API 全部用最小桩，避免"语法过了但一跑就炸"。
 * 退出码 0 = 全过。
 */
const fs = require('fs');
const path = require('path');

const SRC = fs.readFileSync(path.join(__dirname, '..', 'runtime_observability.js'), 'utf8');

function makeEnv(fetchImpl) {
  const listeners = {};
  const fakeConsole = { error: () => {} };
  const win = {
    addEventListener: (t, fn) => { (listeners[t] = listeners[t] || []).push(fn); },
    innerWidth: 1440,
    innerHeight: 900,
    scrollX: 0,
    scrollY: 0,
  };
  if (fetchImpl) { win.fetch = function () { return fetchImpl.apply(this, arguments); }; }
  const doc = {
    querySelectorAll: () => [],
    elementFromPoint: () => null,
    documentElement: {},
    body: {},
  };
  const getComputedStyle = () => ({
    display: 'block', visibility: 'visible', opacity: '1',
    pointerEvents: 'auto', position: 'static',
  });
  const location = { href: 'https://example.test/p' };
  // 用括号包住表达式：SRC 以多行注释开头，"return 注释 (IIFE)" 会触发 ASI 变成
  // "return;"，导致返回 undefined。
  const run = new Function(
    'window', 'document', 'location', 'getComputedStyle', 'console',
    'return (' + SRC + '\n)'
  );
  return {
    listeners,
    fakeConsole,
    win,
    call: () => run(win, doc, location, getComputedStyle, fakeConsole),
  };
}

let pass = 0;
let fail = 0;
function check(name, ok, detail) {
  if (ok) { pass += 1; console.log('[PASS] ' + name); }
  else { fail += 1; console.log('[FAIL] ' + name + (detail ? ' — ' + detail : '')); }
}

// ① 7.35：install → 触发错误 → collect
const env = makeEnv();
let out = JSON.parse(env.call());
check('安装钩子返回 mode=install', out.mode === 'install', JSON.stringify(out));

env.fakeConsole.error('boom-from-console');
(env.listeners['error'] || []).forEach((fn) => fn({ message: 'boom-from-onerror', filename: 'app.js' }));

out = JSON.parse(env.call());
check('采集返回 mode=collect', out.mode === 'collect', JSON.stringify(out));
const dims = (out.findings || []).map((f) => f.dimension);
check('采到 7.35 运行期异常', dims.indexOf('7.35') >= 0, JSON.stringify(dims));
check('异常条数 >= 2（console + onerror）',
  (out.findings || []).filter((f) => f.dimension === '7.35').length >= 2, JSON.stringify(out.summary));
check('findings 契约齐全（severity/confidence/status/symptom）',
  (out.findings || []).every((f) => f.severity && f.confidence != null && f.status && f.symptom));

let crashed = false;
try { JSON.parse(env.call()); } catch (e) { crashed = true; }
check('重复采集不崩溃', !crashed);

// ①b 增量游标（M2/动作窗口）：collect 带 sinceLastRead/cursor——重复采集同一事件不重复入账
const eInc = makeEnv();
eInc.call();                                   // install
eInc.fakeConsole.error('first-batch-error');   // 动作窗口 1 的异常
const c1 = JSON.parse(eInc.call());            // 第一次 collect
check('首次 collect 报全量增量（errors=1）',
  c1.sinceLastRead && c1.sinceLastRead.errors === 1, JSON.stringify(c1.sinceLastRead));
eInc.fakeConsole.error('second-batch-error');  // 动作窗口 2 的新增事件
const c2 = JSON.parse(eInc.call());
check('第二次 collect 只报新增 1 条（cursor 累计=2）',
  c2.sinceLastRead.errors === 1 && c2.cursor.errors === 2, JSON.stringify(c2.sinceLastRead));
const c3 = JSON.parse(eInc.call());            // 第三次 collect：无新增
check('无新增时 sinceLastRead 为 0（重复采集不重复入账）',
  c3.sinceLastRead.errors === 0 && c3.sinceLastRead.requests === 0, JSON.stringify(c3.sinceLastRead));

// ② 7.37 自动确认（fetch 钩子在 .then 里记录，故须 await 后再 collect）
(async () => {
  const URL = 'https://example.test/api/create';
  const OPTS = { method: 'POST', body: 'title=x' };

  // 全部成功 → confirmed
  const okImpl = () => Promise.resolve({ status: 200, ok: true });
  const e1 = makeEnv(okImpl);
  e1.call();
  await e1.win.fetch(URL, OPTS);
  await e1.win.fetch(URL, OPTS);
  const o1 = JSON.parse(e1.call());
  const f1 = (o1.findings || []).filter((f) => f.dimension === '7.37');
  check('7.37 全部成功→confirmed', f1.length === 1 && f1[0].status === 'confirmed', JSON.stringify(f1));
  check('7.37 confirmed 置信度>=0.8', f1.length === 1 && f1[0].confidence >= 0.8,
    JSON.stringify((f1[0] || {}).confidence));

  // 有失败 → pending（服务端拦了重复，交人工）
  let n = 0;
  const mixedImpl = () => { n += 1; const ok = n === 1; return Promise.resolve({ status: ok ? 200 : 500, ok: ok }); };
  const e2 = makeEnv(mixedImpl);
  e2.call();
  await e2.win.fetch(URL, OPTS);
  await e2.win.fetch(URL, OPTS);
  const o2 = JSON.parse(e2.call());
  const f2 = (o2.findings || []).filter((f) => f.dimension === '7.37');
  check('7.37 有失败→pending', f2.length === 1 && f2[0].status === 'pending', JSON.stringify(f2));

  // 单次请求不报
  const e3 = makeEnv(okImpl);
  e3.call();
  await e3.win.fetch(URL, OPTS);
  const o3 = JSON.parse(e3.call());
  check('7.37 单次请求不报',
    (o3.findings || []).filter((f) => f.dimension === '7.37').length === 0, JSON.stringify(o3.summary));

  // ③ 7.36 业务码：HTTP 200 也可能业务失败（只看 status 永远看不到这类缺陷）
  const jsonResp = (body) => ({
    status: 200, ok: true,
    headers: { get: (k) => (String(k).toLowerCase() === 'content-type' ? 'application/json' : null) },
    clone: () => ({ text: () => Promise.resolve(body) }),
  });
  const settle = () => new Promise((r) => setImmediate(r));

  // success:false → pending（业务码语义不可知，交人工）
  const e4 = makeEnv(() => Promise.resolve(jsonResp('{"success":false,"msg":"余额不足"}')));
  e4.call();
  await e4.win.fetch('https://example.test/api/pay', OPTS);
  await settle();
  const o4 = JSON.parse(e4.call());
  const f4 = (o4.findings || []).filter((f) => f.property === 'business-error-as-200');
  check('HTTP 200 + success:false → 报 7.36 且 pending',
    f4.length === 1 && f4[0].dimension === '7.36' && f4[0].status === 'pending', JSON.stringify(f4));

  // 错误页伪装 200 → confirmed（确定性证据）
  const e5 = makeEnv(() => Promise.resolve(jsonResp(
    '<html><head><title>Whitelabel Error Page</title></head><body>500</body></html>')));
  e5.call();
  await e5.win.fetch('https://example.test/api/report', OPTS);
  await settle();
  const o5 = JSON.parse(e5.call());
  const f5 = (o5.findings || []).filter((f) => f.property === 'error-page-as-200');
  check('错误页伪装 HTTP 200 → confirmed P1',
    f5.length === 1 && f5[0].status === 'confirmed' && f5[0].severity === 'P1', JSON.stringify(f5));

  // 正常业务码（code:200 / errcode:0）不得误报 —— 反向用例
  const e6 = makeEnv(() => Promise.resolve(jsonResp('{"code":200,"errcode":0,"data":[1,2]}')));
  e6.call();
  await e6.win.fetch('https://example.test/api/list', OPTS);
  await settle();
  const o6 = JSON.parse(e6.call());
  check('正常业务码（code:200 / errcode:0）不误报',
    (o6.findings || []).filter((f) => f.dimension === '7.36').length === 0, JSON.stringify(o6.summary));

  // 非文本响应（无 clone / 无 headers）不得崩
  const e7 = makeEnv(() => Promise.resolve({ status: 200, ok: true }));
  e7.call();
  await e7.win.fetch('https://example.test/api/blob', OPTS);
  await settle();
  let crashed7 = false;
  try { JSON.parse(e7.call()); } catch (e) { crashed7 = true; }
  check('响应体不可读时不崩溃', !crashed7);

  // ④ 7.45 请求载荷完整性
  const plain = () => Promise.resolve({ status: 200, ok: true });

  // 占位符泄漏 → confirmed（确定性证据，不依赖业务语义）
  const e8 = makeEnv(plain);
  e8.call();
  await e8.win.fetch('https://example.test/api/save', { method: 'POST', body: '{"name":"undefined","id":undefined}' });
  const o8 = JSON.parse(e8.call());
  const f8 = (o8.findings || []).filter((f) => f.dimension === '7.45');
  check('参数含 undefined 占位符 → confirmed', f8.length === 1 && f8[0].status === 'confirmed',
    JSON.stringify(f8));
  check('占位符泄漏 property 正确', f8[0] && f8[0].property === 'payload-placeholder',
    f8[0] && f8[0].property);

  // [object Object] 泄漏 → confirmed
  const e9 = makeEnv(plain);
  e9.call();
  await e9.win.fetch('https://example.test/api/save', { method: 'POST', body: 'filter=[object Object]' });
  const o9 = JSON.parse(e9.call());
  check('[object Object] 泄漏 → confirmed',
    (o9.findings || []).filter((f) => f.property === 'payload-placeholder').length === 1,
    JSON.stringify(o9.findings || []));

  // FormData 的字符串化产物是采集侧产生的，不得当成占位符 —— 反向用例
  const e10 = makeEnv(plain);
  e10.call();
  const fd = new FormData();
  fd.append('a', '1');
  await e10.win.fetch('https://example.test/api/upload', { method: 'POST', body: fd });
  const o10 = JSON.parse(e10.call());
  check('FormData body 不误报占位符',
    (o10.findings || []).filter((f) => f.property === 'payload-placeholder').length === 0,
    JSON.stringify(o10.findings || []));
  // 不判但要计数：否则"载荷没检查"和"载荷查了没问题"在产物里长得一模一样
  check('FormData 请求计入 payloadNotInspectable',
    (o10.summary || {}).payloadNotInspectable === 1,
    JSON.stringify(o10.summary || {}));

  // 重复 query 键 → pending（服务端取值取决于实现）
  const e11 = makeEnv(plain);
  e11.call();
  await e11.win.fetch('https://example.test/api/list?a=1&a=2&b=3', { method: 'GET' });
  const o11 = JSON.parse(e11.call());
  const f11 = (o11.findings || []).filter((f) => f.property === 'payload-duplicate-key');
  check('重复 query 键 → pending', f11.length === 1 && f11[0].status === 'pending', JSON.stringify(f11));

  // 写请求无任何参数 → pending（可能是合法无参接口）
  const e12 = makeEnv(plain);
  e12.call();
  await e12.win.fetch('https://example.test/api/logout', { method: 'POST' });
  const o12 = JSON.parse(e12.call());
  const f12 = (o12.findings || []).filter((f) => f.property === 'payload-empty-write');
  check('写请求无参数 → pending', f12.length === 1 && f12[0].status === 'pending', JSON.stringify(f12));

  // 带参数的写请求不得报空载荷 —— 反向用例
  const e13 = makeEnv(plain);
  e13.call();
  await e13.win.fetch('https://example.test/api/save', { method: 'POST', body: '{"id":12}' });
  const o13 = JSON.parse(e13.call());
  check('带参数的写请求不报空载荷',
    (o13.findings || []).filter((f) => f.property === 'payload-empty-write').length === 0,
    JSON.stringify(o13.findings || []));

  check('可判读的请求计入 payloadChecked',
    (o13.summary || {}).payloadChecked === 1 && (o13.summary || {}).payloadNotInspectable === 0,
    JSON.stringify(o13.summary || {}));

  // 干净请求一条都不报 —— 反向用例（防判定恒真）
  check('正常请求不产生任何 7.45 缺陷',
    (o13.findings || []).filter((f) => f.dimension === '7.45').length === 0,
    JSON.stringify(o13.findings || []));

  console.log('\n==== runtime_observability 冒烟：' + pass + ' 通过 / ' + fail + ' 失败 ====');
  process.exit(fail ? 1 : 0);
})();
