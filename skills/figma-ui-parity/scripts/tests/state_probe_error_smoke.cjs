/*
 * F51 锁定：state_probes 的 F40 error 契约必须被驱动消费——读不到 mode 时
 * 探测**没有执行**，旧版把 error JSON 的空 findings 当成"跑完没发现问题"记
 * pass（假通过，且外观完全合理）。现在必须记 notRun + detail。
 * 用假 page 无浏览器验证 RUNNERS 的三个探测入口。
 * 退出码 0 = 全过。
 */
const driver = require('../node/run_state_scenarios.cjs');

let pass = 0;
let fail = 0;
function check(name, ok, detail) {
  if (ok) { pass += 1; console.log('[PASS] ' + name); }
  else { fail += 1; console.log('[FAIL] ' + name + (detail ? ' — ' + detail : '')); }
}

/** 假 page：第一次 evaluate（函数）记 mode，第二次（PROBE_SRC 字符串）按 scenario 返回 */
function fakePage(scenario) {
  return {
    async evaluate(fn) {
      if (typeof fn === 'function') return undefined;
      if (scenario === 'mode-missing') {
        return JSON.stringify({ error: 'probe-mode-missing', detail: 'window.__vpProbe 未设置或 mode 非法', findings: [] });
      }
      return JSON.stringify({ mode: 'dark', url: 'https://e.test/p', findings: [], total: 0 });
    },
    async emulateMedia() {},
    async waitForTimeout() {},
    async screenshot() {},
    async route() {},
    async unroute() {},
    async reload() {},
  };
}

(async () => {
  /* ---- mode 丢失 → notRun + detail（三个探测入口逐个锁） ---- */
  for (const dim of ['7.25', '7.26', '7.33']) {
    const r = await driver.RUNNERS[dim](fakePage('mode-missing'));
    check(dim + ' 探测未执行 → notRun 且 reason 带 detail',
      r.status === 'notRun' && r.findings.length === 0 && /探测未执行/.test(r.reason || ''),
      JSON.stringify(r).slice(0, 160));
  }

  /* ---- 正常路径不回退：空 findings 仍是 pass（没发现问题 ≠ 没跑） ---- */
  const ok = await driver.RUNNERS['7.25'](fakePage('normal'));
  check('7.25 正常探测且无发现 → pass（不得被 error 分支误伤）',
    ok.status === 'pass' && ok.dimension === '7.25', JSON.stringify(ok).slice(0, 160));

  /* ---- probe() 必须透出整包（含 error），不再只返回 findings 数组 ---- */
  const pr = await driver.probe(fakePage('mode-missing'), 'dark');
  check('probe() 透出 error 字段（旧版只返回 findings，error 被吞）',
    pr && pr.error === 'probe-mode-missing' && Array.isArray(pr.findings));

  console.log('=== F51 state_probe error 冒烟：' + pass + ' 通过 / ' + fail + ' 失败 ===');
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error('FATAL', e); process.exit(1); });
