/**
 * write_guard / page_identity 共享模块的行为契约测试。
 * 用真实模块 + mock context/page，不访问网络。
 */
const test = require('node:test');
const assert = require('node:assert');
const path = require('path');

const { createWriteGuard } = require(path.join(__dirname, '../node/write_guard.cjs'));
const { verifyPageIdentity, loginHintsFromUrl } = require(path.join(__dirname, '../node/page_identity.cjs'));

function mockRouteRegistry() {
  const routes = [];
  return {
    routes,
    context: {
      route: async (_pattern, handler) => { routes.push(handler); },
    },
    // 模拟一次请求经过 handler；返回 route 动作的名称
    async dispatch(method, url) {
      const route = {
        request: () => ({ method: () => method, url: () => url }),
        _action: null,
        continue() { this._action = 'continue'; },
        abort() { this._action = 'abort'; },
        fulfill() { this._action = 'fulfill'; },
      };
      for (const h of routes) await h(route);
      return route._action;
    },
  };
}

test('guard staticOnly never installs route', async () => {
  const reg = mockRouteRegistry();
  const wp = createWriteGuard(reg.context, 'staticOnly');
  await wp.beforeNavigation();
  assert.equal(reg.routes.length, 0);
});

test('guard allows init writes before navigation succeeds and blocks after', async () => {
  const reg = mockRouteRegistry();
  const wp = createWriteGuard(reg.context, 'mockWrites');
  await wp.beforeNavigation();
  assert.equal(reg.routes.length, 1);
  // 初始化阶段 POST 放行并记录
  assert.equal(await reg.dispatch('POST', 'https://x/api/auth/start'), 'continue');
  wp.markNavigationSucceeded();
  // 导航成功后 POST 拦截并计数
  assert.equal(await reg.dispatch('POST', 'https://x/api/save'), 'abort');
  const s = wp.finalize(true);
  assert.equal(s.initAllowedCount, 1);
  assert.equal(s.interceptedCount, 1);
  assert.equal(s.blockedCount, 1);
  assert.equal(s.reachedTarget, true);
  assert.ok(!s.reason);
});

test('guard finalize without target produces blocked-by reason', async () => {
  const reg = mockRouteRegistry();
  const wp = createWriteGuard(reg.context, 'mockWrites');
  await wp.beforeNavigation();
  await reg.dispatch('POST', 'https://x/api/auth/start');
  const s = wp.finalize(false);
  assert.equal(s.reason, 'blocked-by-write-protection');
  assert.equal(s.reachedTarget, false);
});

test('guard without install reports installed=false and no reason', () => {
  const reg = mockRouteRegistry();
  const wp = createWriteGuard(reg.context, 'mockWrites');
  const s = wp.finalize(true);
  assert.equal(s.installed, false);
  assert.ok(!s.reason);
});

test('identity flags login url and password input', async () => {
  const page = {
    url: () => 'https://x/login',
    evaluate: async () => 900,
  };
  const r = await verifyPageIdentity(page, 'https://x/');
  // URL 命中登录路径即 isLoginPage，即使正文很长
  assert.equal(r.isLoginPage, true);
  assert.equal(r.reachedTarget, false);
  assert.ok(r.reasons.some(x => x.startsWith('login-url:')));
});

test('identity accepts rich same-origin page without password', async () => {
  let call = 0;
  const page = {
    url: () => 'https://x/',
    evaluate: async (fn) => {
      call += 1;
      // 第 1 次 evaluate 取正文长度，第 2 次查密码框
      return call === 1 ? 1500 : false;
    },
  };
  const r = await verifyPageIdentity(page, 'https://x/');
  assert.equal(r.reachedTarget, true);
  assert.equal(r.isLoginPage, false);
});

test('identity rejects thin page on same origin', async () => {
  let call = 0;
  const page = {
    url: () => 'https://x/',
    evaluate: async () => { call += 1; return call === 1 ? 30 : false; },
  };
  const r = await verifyPageIdentity(page, 'https://x/');
  assert.equal(r.reachedTarget, false);
  assert.ok(r.reasons.includes('page-body-too-thin'));
});

test('identity evaluate failure is treated as unreached, not guessed', async () => {
  const page = {
    url: () => 'https://x/',
    evaluate: async () => { throw new Error('boom'); },
  };
  const r = await verifyPageIdentity(page, 'https://x/');
  assert.equal(r.reachedTarget, false);
  assert.ok(r.reasons.includes('page-evaluate-failed'));
});

test('login path regex does not hit normal paths', () => {
  assert.deepEqual(loginHintsFromUrl('https://x/checkout'), []);
  assert.deepEqual(loginHintsFromUrl('https://x/'), []);
  assert.equal(loginHintsFromUrl('https://x/passport?next=/').length, 1);
});
