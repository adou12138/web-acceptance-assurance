/*
 * F57 锁定：auth.cjs restoreAuth 不得在注入失败时谎报成功。
 * 旧版对 addCookies / addInitScript 一律 `.catch(() => {})` 后仍返回 ok:true，
 * 调用方据此认为"登录态已就位"，实际在未登录的登录页上跑完检查——报告里一片假缺陷，
 * 却没有任何一行提示本轮没有登录态。
 * 用假 context（不需要浏览器）验证三种结果必须可分辨。
 * 退出码 0 = 全过。
 */
const fs = require('fs');
const os = require('os');
const path = require('path');

let pass = 0;
let fail = 0;
function check(name, ok, detail) {
  if (ok) { pass += 1; console.log('[PASS] ' + name); }
  else { fail += 1; console.log('[FAIL] ' + name + (detail ? ' — ' + detail : '')); }
}

/** 每个用例都要重新 require（DIR 在模块加载时算），所以先清缓存 */
function freshAuth(authDir) {
  delete require.cache[require.resolve('../node/auth.cjs')];
  process.env.PARITY_AUTH_DIR = authDir;
  return require('../node/auth.cjs');
}

function writeStorage(dir, origins) {
  fs.writeFileSync(path.join(dir, 'auth.storage.json'), JSON.stringify({
    cookies: [{ name: 'sid', value: 'v', domain: 'e.test', path: '/' }],
    origins: origins || [],
  }));
}

/** 可配置的假 context：让 addCookies / addInitScript 按用例报错 */
function fakeCtx(opts) {
  return {
    async addCookies(list) {
      if (opts.cookieThrow) throw new Error('Protocol error: cookie rejected');
      opts.cookies = list.length;
    },
    async addInitScript(fn, arg) {
      if (opts.scriptThrow) throw new Error('Protocol error: init script rejected');
      opts.origins = (arg || []).length;
    },
  };
}

(async () => {
  /* ---- 1) 没有登录态文件：正常情况，必须可与"注入失败"区分 ----
   * 这里必须造一个「cwd 下别处恰好存着登录态」的环境：
   * 只用空目录测不出真正的问题——探测逻辑会在别处找到旧 cookie 并沿用，
   * 于是「我在跑公开页面」变成「我在用上一个人的身份跑」，
   * 而报告里一切正常，没人会怀疑。
   */
  const cwd0 = process.cwd();
  const fakeCwd = fs.mkdtempSync(path.join(os.tmpdir(), 'authcwd-'));
  const strayDir = path.join(fakeCwd, 'parity-test', '.auth');
  fs.mkdirSync(strayDir, { recursive: true });
  writeStorage(strayDir, [{ origin: 'https://stray.test', localStorage: [{ name: 'k', value: 'v' }] }]);
  const emptyDir = fs.mkdtempSync(path.join(os.tmpdir(), 'authempty-'));
  let r1;
  try {
    process.chdir(fakeCwd);
    const a1 = freshAuth(emptyDir);
    r1 = await a1.restoreAuth(fakeCtx({}));
  } finally {
    process.chdir(cwd0);
  }
  check('显式指定空目录 → ok:false + noAuthFile（不得回落到别处的登录态）',
    r1.ok === false && r1.noAuthFile === true, JSON.stringify(r1).slice(0, 160));

  /* ---- 2) 注入 cookies 失败：必须报失败，不得静默返回 ok:true ---- */
  const d2 = fs.mkdtempSync(path.join(os.tmpdir(), 'authfail-'));
  writeStorage(d2, [{ origin: 'https://e.test', localStorage: [{ name: 'k', value: 'v' }] }]);
  const a2 = freshAuth(d2);
  const o2 = { cookieThrow: true };
  const r2 = await a2.restoreAuth(fakeCtx(o2));
  check('addCookies 注入失败 → ok:false + failed[]（旧版此处 ok:true）',
    r2.ok === false && Array.isArray(r2.failed) && r2.failed.length > 0,
    JSON.stringify(r2).slice(0, 200));

  /* ---- 3) localStorage 注入失败同样算失败 ---- */
  const d3 = fs.mkdtempSync(path.join(os.tmpdir(), 'authfail2-'));
  writeStorage(d3, [{ origin: 'https://e.test', localStorage: [{ name: 'k', value: 'v' }] }]);
  const a3 = freshAuth(d3);
  const r3 = await a3.restoreAuth(fakeCtx({ scriptThrow: true }));
  check('addInitScript 注入失败 → ok:false + failed[]',
    r3.ok === false && Array.isArray(r3.failed) && r3.failed.length > 0,
    JSON.stringify(r3).slice(0, 200));

  /* ---- 4) 正常路径不回退：注入成功必须仍是 ok:true 且计数正确 ---- */
  const d4 = fs.mkdtempSync(path.join(os.tmpdir(), 'authok-'));
  writeStorage(d4, [{ origin: 'https://e.test', localStorage: [{ name: 'k', value: 'v' }] }]);
  const a4 = freshAuth(d4);
  const o4 = {};
  const r4 = await a4.restoreAuth(fakeCtx(o4));
  check('注入成功 → ok:true + cookies/origins 计数（禁止把此判成失败）',
    r4.ok === true && r4.injected === true && r4.cookies === 1 && (o4.origins || 0) === 1,
    JSON.stringify(r4).slice(0, 200));

  /* ---- 5) 自动刷新：零 cookie 不写盘（防止空态覆盖既有登录态） ---- */
  const d5 = fs.mkdtempSync(path.join(os.tmpdir(), 'authskip-'));
  const a5 = freshAuth(d5);
  const emptyCtx = { async cookies() { return []; } };
  const s5 = await a5.saveAuthIfPresent(emptyCtx, 'default');
  check('零 cookie → skipped:true 且不写盘（防覆盖旧登录态）',
    s5.ok === false && s5.skipped === true && String(s5.reason).indexOf('未写盘') >= 0,
    JSON.stringify(s5).slice(0, 200));
  check('零 cookie 时 .auth 目录保持为空（连 mkdir 都不做）',
    !fs.existsSync(path.join(d5, 'auth.storage.json')) && !fs.existsSync(path.join(d5, 'auth.cookies.json')),
    fs.existsSync(d5) ? '目录存在: ' + fs.readdirSync(d5).join(',') : '目录不存在');

  /* ---- 6) 自动刷新：有 cookie 正常写盘，返回计数 ---- */
  const d6 = fs.mkdtempSync(path.join(os.tmpdir(), 'authrefresh-'));
  const a6 = freshAuth(d6);
  const fullCtx = {
    async cookies() { return [{ name: 'sid', value: 'v', domain: 'e.test', path: '/', expires: -1 }]; },
    async storageState(opts) {
      fs.writeFileSync(opts.path, JSON.stringify({ cookies: await this.cookies(), origins: [] }));
    },
  };
  const s6 = await a6.saveAuthIfPresent(fullCtx, 'default');
  check('有 cookie → ok:true + 计数，三个文件齐',
    s6.ok === true && s6.cookieCount === 1
    && fs.existsSync(path.join(d6, 'auth.storage.json'))
    && fs.existsSync(path.join(d6, 'auth.cookies.json'))
    && fs.existsSync(path.join(d6, 'auth_info.json')),
    JSON.stringify(s6).slice(0, 200));

  /* ---- 7) cookies() 本身抛错：不许吞，也不写盘 ---- */
  const d7 = fs.mkdtempSync(path.join(os.tmpdir(), 'autherr-'));
  const a7 = freshAuth(d7);
  const errCtx = { async cookies() { throw new Error('Protocol error: context closed'); } };
  const s7 = await a7.saveAuthIfPresent(errCtx, 'default');
  check('cookies() 抛错 → ok:false 且 reason 带原始错误（不静默不写盘）',
    s7.ok === false && s7.skipped === false && String(s7.reason).indexOf('context closed') >= 0,
    JSON.stringify(s7).slice(0, 200));

  console.log('=== F57 auth restore 冒烟：' + pass + ' 通过 / ' + fail + ' 失败 ===');
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error('FATAL', e); process.exit(1); });
