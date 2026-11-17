#!/usr/bin/env node
/**
 * performance_probe.js 自检：在 Node 最小桩里真跑 install → collect 两阶段。
 *
 * 为什么必须真跑：性能判定最容易"看起来对"——阈值表写错、没拿到 CLS 却当 0 判通过、
 * 没节流也把耗时判成缺陷，这些都不会报错，只会产出看似合理的结论。
 * 本文件用桩把每条判据都驱动一遍，重点锁两个防假缺陷口径：
 *   1. 无节流时耗时超阈值只能是 warn，不能 confirmed；
 *   2. 没装观察器时 CLS 必须是 null（进 skipped），不能当 0 参与判定。
 *
 * 用法：node scripts/tests/performance_probe_smoke.cjs
 */
const fs = require('fs');
const path = require('path');

const SRC = fs.readFileSync(path.join(__dirname, '..', 'performance_probe.js'), 'utf8');
const RUN = new Function('return (' + SRC + ')');

let failures = 0;
function check(name, ok, detail) {
  if (!ok) failures += 1;
  console.log('[' + (ok ? 'PASS' : 'FAIL') + '] ' + name + (ok || detail === undefined ? '' : ' — ' + detail));
}

/** 建一个最小浏览器桩；返回 { setMode, fire, collect } */
function makeEnv(opts) {
  const o = opts || {};
  const observers = [];
  const store = { cls: 0, lcp: 0, longTasks: [] };

  global.window = {};
  global.location = { href: 'https://example.test/perf' };

  global.PerformanceObserver = class {
    constructor(cb) { this.cb = cb; }
    observe(config) { observers.push({ cb: this.cb, type: (config && config.type) || '' }); }
  };

  const navEntries = o.nav === null ? [] : [{
    responseStart: o.ttfb === undefined ? 300 : o.ttfb,
    domContentLoadedEventEnd: 900,
    loadEventEnd: 1400,
  }];
  const paintEntries = o.fcp === null ? [] : [{ name: 'first-contentful-paint', startTime: o.fcp === undefined ? 900 : o.fcp }];
  const resourceEntries = o.resources || [];
  const lcpEntries = o.lcpEntries || [];

  global.performance = {
    now: () => 0,
    getEntriesByType: (type) => {
      if (type === 'navigation') return navEntries;
      if (type === 'paint') return paintEntries;
      if (type === 'resource') return resourceEntries;
      if (type === 'largest-contentful-paint') return lcpEntries;
      return [];
    },
  };

  const fire = (type, entries) => {
    observers.filter((x) => x.type === type).forEach((x) => x.cb({ getEntries: () => entries }));
  };

  return {
    // install 阶段：先设 mode，再跑脚本
    install(throttle) {
      global.window.__vpPerfMode = 'install';
      global.window.__vpPerfThrottle = throttle || { mode: 'none', applied: false };
      const raw = RUN();
      global.window.__vpPerfMode = 'collect';
      return JSON.parse(raw);
    },
    fire,
    collect() {
      const raw = RUN();
      return JSON.parse(raw);
    },
    observerCount: () => observers.length,
  };
}

// ---- 1) 有节流 + LCP 超差 → confirmed ----
{
  const env = makeEnv({ ttfb: 300, fcp: 900 });
  const inst = env.install({ mode: 'fast3g', applied: true });
  check('install 阶段返回 mode=installed', inst.mode === 'installed', JSON.stringify(inst));
  check('install 阶段装了 3 个观察器（layout-shift / lcp / longtask）', env.observerCount() === 3, 'n=' + env.observerCount());

  env.fire('layout-shift', [{ hadRecentInput: false, value: 0.05 }]);
  env.fire('largest-contentful-paint', [{ startTime: 5200 }]);
  env.fire('longtask', [{ duration: 120 }, { duration: 80 }]);

  const out = env.collect();
  const lcpFinding = out.findings.filter((f) => f.property === 'vitals-lcp')[0];
  check('有节流时 LCP 超差 → confirmed',
    !!lcpFinding && lcpFinding.status === 'confirmed', JSON.stringify(lcpFinding && lcpFinding.status));
  check('有节流时 P1 等级', !!lcpFinding && lcpFinding.severity === 'P1', lcpFinding && lcpFinding.severity);
  check('CLS 由观察器累加得出（0.05）', out.metrics.cls === 0.05, String(out.metrics.cls));
  check('TBT 只累加超过 50ms 的部分（70+30=100）', out.metrics.tbt === 100, String(out.metrics.tbt));
  check('clsSource 标为 layout-shift-observer', out.clsSource === 'layout-shift-observer', out.clsSource);
}

// ---- 2) 无节流 + LCP 超差 → 只能 warn（防假缺陷核心断言）----
{
  const env = makeEnv({});
  env.install({ mode: 'none', applied: false });
  env.fire('largest-contentful-paint', [{ startTime: 5200 }]);
  const out = env.collect();
  const lcpFinding = out.findings.filter((f) => f.property === 'vitals-lcp')[0];
  check('无节流时 LCP 超差 → warn（不自动确认）',
    !!lcpFinding && lcpFinding.status === 'warn', JSON.stringify(lcpFinding && lcpFinding.status));
  check('无节流时置信度降为 0.5', !!lcpFinding && lcpFinding.confidence === 0.5, lcpFinding && lcpFinding.confidence);
  check('无节流结论里写明"未施加网络节流"',
    !!lcpFinding && String(lcpFinding.actual).indexOf('未施加网络节流') >= 0, lcpFinding && lcpFinding.actual);
}

// ---- 3) 未装观察器 → CLS 必须 null 并进 skipped，不能当 0 判 pass ----
{
  const env = makeEnv({});
  const out = env.collect();   // 直接 collect，没有 install
  check('缺 install 阶段时 CLS 为 null（不是 0）', out.metrics.cls === null, String(out.metrics.cls));
  check('未采集的指标进 skipped', out.summary.skipped.indexOf('cls:no-observer') >= 0, JSON.stringify(out.summary.skipped));
  const clsFinding = out.findings.filter((f) => f.property === 'vitals-cls')[0];
  check('CLS 为 null 时不产出判断（不得当作 0 达标）', !clsFinding, JSON.stringify(clsFinding && clsFinding.status));
}

// ---- 4) 全达标 → 必须有 pass 证据（否则维度退化成 notRun）----
{
  const env = makeEnv({ ttfb: 200, fcp: 1000 });
  env.install({ mode: 'fast3g', applied: true });
  env.fire('largest-contentful-paint', [{ startTime: 1500 }]);
  env.fire('layout-shift', [{ hadRecentInput: false, value: 0.02 }]);
  env.fire('longtask', [{ duration: 60 }]);
  const out = env.collect();
  const passFinding = out.findings.filter((f) => f.status === 'pass')[0];
  check('全部达标时产出 status=pass 的一条证据', !!passFinding, JSON.stringify(out.findings.map((f) => f.status)));
  check('达标证据里列出各指标取值', !!passFinding && String(passFinding.symptom).indexOf('lcp=1500') >= 0,
    passFinding && passFinding.symptom);
}

// ---- 5) 拿不到任何指标 → dataMissing，不能 pass ----
{
  const env = makeEnv({ nav: null, fcp: null, resources: [] });
  const out = env.collect();
  check('无 navigation/paint/资源条目时判 dataMissing',
    out.findings.some((f) => f.status === 'dataMissing'), JSON.stringify(out.findings.map((f) => f.status)));
}

// ---- 6) 未压缩大图 → 与网络档无关，直接 confirmed ----
{
  const big = 400 * 1024;
  const env = makeEnv({
    resources: [{ initiatorType: 'img', transferSize: big, encodedBodySize: big, decodedBodySize: big, name: 'https://x/a.png' }],
  });
  const out = env.collect();
  const imgFinding = out.findings.filter((f) => f.property === 'uncompressed-image')[0];
  check('未压缩大图直接 confirmed（体积证据与节流无关）',
    !!imgFinding && imgFinding.status === 'confirmed', JSON.stringify(imgFinding && imgFinding.status));
  check('资源体积按 MB 汇总', out.resources.transferMB === 0.4, String(out.resources.transferMB));
}

// ---- 7) 资源总量超 5MB → confirmed ----
{
  const env = makeEnv({
    resources: [{ initiatorType: 'script', transferSize: 6 * 1024 * 1024, encodedBodySize: 6 * 1024 * 1024, decodedBodySize: 6 * 1024 * 1024, name: 'https://x/a.js' }],
  });
  const out = env.collect();
  const sizeFinding = out.findings.filter((f) => f.property === 'transfer-size')[0];
  check('资源总量 6MB → confirmed', !!sizeFinding && sizeFinding.status === 'confirmed',
    JSON.stringify(sizeFinding && sizeFinding.status));
}

console.log('\n==== performance_probe 自检：' + (failures === 0 ? '全部通过' : failures + ' 项失败') + ' ====');
process.exit(failures === 0 ? 0 : 1);
