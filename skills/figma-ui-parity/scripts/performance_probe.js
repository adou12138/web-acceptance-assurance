/*
 * 性能与核心网页指标（页面内执行，输出 findings 列表）—— 维度 7.40
 *
 * 覆盖：TTFB / FCP / LCP / CLS / 长任务与总阻塞时间 / 资源体积 / 未压缩大图。
 *
 * 两阶段（与 runtime_observability.js 同款）：
 *   install —— 在页面加载前注入，安装 LayoutShift / LCP / longtask 观察器。
 *              CLS 没有 buffered 入口，采集时才装观察器等于永远拿不到，必须提前装。
 *   collect —— 页面稳定后采集，产出 findings。
 *
 * driver 路径（能拿到 CLS）：
 *   page.addInitScript({ content: "window.__vpPerfMode='install';" + PROBE_SRC })
 *   page.evaluate(PROBE_SRC)                 // MODE 缺省为 collect
 * MCP browser_evaluate 路径：直接 eval 本文件，没有 install 阶段，CLS 标 unavailable。
 *
 * 约束：全文不出现反斜杠转义，避免 MCP 传参吃掉一层反斜杠导致静默改语义。
 */

(() => {
  const MODE = (typeof window.__vpPerfMode !== 'undefined') ? window.__vpPerfMode : 'collect';

  // Web Vitals 官方阈值：[good 上限, poor 下限]。单位与指标一致。
  const THRESHOLD = {
    ttfb: [800, 1800],
    fcp: [1800, 3000],
    lcp: [2500, 4000],
    cls: [0.1, 0.25],
    tbt: [200, 600],
    transferMB: [2, 5],
  };

  const gradeOf = (value, pair) => {
    if (typeof value !== 'number' || isNaN(value)) return 'unknown';
    if (value <= pair[0]) return 'good';
    if (value <= pair[1]) return 'needsImprovement';
    return 'poor';
  };

  const round1 = (n) => Math.round(n * 10) / 10;

  // ---------- install 阶段 ----------
  if (MODE === 'install') {
    const store = { cls: 0, shiftCount: 0, lcp: null, longTasks: [], degraded: [] };
    try {
      new PerformanceObserver((list) => {
        list.getEntries().forEach((entry) => {
          if (entry.hadRecentInput) return;
          store.cls += entry.value;
          store.shiftCount += 1;
        });
      }).observe({ type: 'layout-shift', buffered: true });
    } catch (e) {
      store.degraded.push('layout-shift:' + String((e && e.message) || e));
    }
    try {
      new PerformanceObserver((list) => {
        const items = list.getEntries();
        const last = items[items.length - 1];
        if (last) store.lcp = Math.round(last.startTime);
      }).observe({ type: 'largest-contentful-paint', buffered: true });
    } catch (e) {
      store.degraded.push('lcp:' + String((e && e.message) || e));
    }
    try {
      new PerformanceObserver((list) => {
        list.getEntries().forEach((entry) => store.longTasks.push(Math.round(entry.duration)));
      }).observe({ type: 'longtask', buffered: true });
    } catch (e) {
      store.degraded.push('longtask:' + String((e && e.message) || e));
    }
    window.__vpPerf = store;
    // install 完成后立刻切回 collect：driver 的 initScript 与下次 evaluate 用的是
    // 同一份源码，若 __vpPerfMode 停在 install，后续采集会再装一遍观察器并返回
    // {mode:"installed"}——没有 findings，调用方只能判"探针返回形状异常"。
    window.__vpPerfMode = 'collect';
    return JSON.stringify({ mode: 'installed', degraded: store.degraded });
  }

  // ---------- collect 阶段 ----------
  const findings = [];
  const push = (symptom, expected, actual, opts) => {
    const o = opts || {};
    findings.push({
      dimension: '7.40',
      symptom: symptom,
      expected: expected,
      actual: actual,
      severityHint: o.severity || 'P2',
      severity: o.severity || 'P2',
      confidence: typeof o.confidence === 'number' ? o.confidence : 0.7,
      status: o.status || 'pending',
      property: o.property || 'performance',
      element: { selector: '', nth: 0, bbox: { x: 0, y: 0, w: 0, h: 0 }, anchor: 'page-metrics' },
      evidenceType: 'performance-api',
      raw: o.raw || {},
      evidence: [{ type: 'performance-entry', metric: o.property || 'performance' }],
      sources: ['performance-probe'],
    });
  };

  const store = (typeof window.__vpPerf !== 'undefined') ? window.__vpPerf : null;

  let nav = null;
  try {
    const list = performance.getEntriesByType('navigation');
    nav = (list && list.length) ? list[0] : null;
  } catch (e) {
    nav = null;
  }

  const metrics = {
    ttfb: nav ? Math.round(nav.responseStart) : null,
    domContentLoaded: nav ? Math.round(nav.domContentLoadedEventEnd) : null,
    load: nav ? Math.round(nav.loadEventEnd) : null,
    fcp: null,
    lcp: null,
    cls: null,
    tbt: null,
    longTaskCount: null,
  };

  try {
    const paints = performance.getEntriesByType('paint') || [];
    for (let i = 0; i < paints.length; i += 1) {
      if (paints[i].name === 'first-contentful-paint') metrics.fcp = Math.round(paints[i].startTime);
    }
  } catch (e) {
    // 拿不到 paint 条目要在产物里留痕：只写注释等于"失败被折成没有"，
    // 调用方无从区分"没有 FCP"和"读不到 FCP"
    metrics.paintError = String((e && e.message) || e).slice(0, 80);
  }

  if (store && typeof store.lcp === 'number') {
    metrics.lcp = store.lcp;
  } else {
    try {
      const lcps = performance.getEntriesByType('largest-contentful-paint') || [];
      if (lcps.length) metrics.lcp = Math.round(lcps[lcps.length - 1].startTime);
    } catch (e) { /* 保持 null */ }
  }

  const degraded = (store && Array.isArray(store.degraded)) ? store.degraded : [];
  const clsReady = !!store && degraded.filter((d) => d.indexOf('layout-shift') === 0).length === 0;
  const taskReady = !!store && degraded.filter((d) => d.indexOf('longtask') === 0).length === 0;

  if (clsReady) metrics.cls = Math.round(store.cls * 1000) / 1000;
  if (taskReady && store && Array.isArray(store.longTasks)) {
    metrics.longTaskCount = store.longTasks.length;
    // TBT 近似：只累加超过 50ms 的部分，与 Lighthouse 的 TBT 口径一致
    metrics.tbt = Math.round(store.longTasks.reduce((sum, d) => sum + Math.max(0, d - 50), 0));
  }

  // ---------- 资源体积 ----------
  const resources = { count: 0, transferBytes: 0, byType: {}, heavyUncompressedImages: [], transferMB: 0 };
  try {
    const entries = performance.getEntriesByType('resource') || [];
    entries.forEach((entry) => {
      const bytes = typeof entry.transferSize === 'number' ? entry.transferSize : 0;
      resources.count += 1;
      resources.transferBytes += bytes;
      const type = entry.initiatorType || 'other';
      const bucket = resources.byType[type] || { count: 0, transferBytes: 0 };
      bucket.count += 1;
      bucket.transferBytes += bytes;
      resources.byType[type] = bucket;
      // 未压缩大图：解码后体积远超传输体积且总量可观，说明服务器没开压缩
      const decoded = typeof entry.decodedBodySize === 'number' ? entry.decodedBodySize : 0;
      const encoded = typeof entry.encodedBodySize === 'number' ? entry.encodedBodySize : 0;
      const isImage = type === 'img' || type === 'image';
      if (isImage && decoded > 200 * 1024 && encoded > 0 && encoded >= decoded * 0.95) {
        if (resources.heavyUncompressedImages.length < 10) {
          resources.heavyUncompressedImages.push({
            url: String(entry.name || '').split('?')[0].slice(-120),
            decodedKB: Math.round(decoded / 1024),
            encodedKB: Math.round(encoded / 1024),
          });
        }
      }
    });
  } catch (e) {
    resources.error = String((e && e.message) || e).slice(0, 80);
  }
  resources.transferMB = round1(resources.transferBytes / 1024 / 1024);

  // ---------- 节流状态：没有节流时绝对耗时不可作缺陷证据 ----------
  const throttle = (typeof window.__vpPerfThrottle !== 'undefined')
    ? window.__vpPerfThrottle : { mode: 'none', applied: false };

  // ---------- 判定 ----------
  const items = [];
  const classify = (key, value, unit, label) => {
    const pair = THRESHOLD[key];
    if (!pair) return;
    const grade = gradeOf(value, pair);
    if (grade === 'unknown') return;
    items.push({ key: key, value: value, unit: unit, label: label, grade: grade, pair: pair });
  };

  classify('ttfb', metrics.ttfb, 'ms', 'TTFB 首字节');
  classify('fcp', metrics.fcp, 'ms', 'FCP 首次内容绘制');
  classify('lcp', metrics.lcp, 'ms', 'LCP 最大内容绘制');
  classify('cls', metrics.cls, '', 'CLS 布局偏移');
  classify('tbt', metrics.tbt, 'ms', 'TBT 总阻塞时间');

  const poor = items.filter((i) => i.grade === 'poor');
  const mid = items.filter((i) => i.grade === 'needsImprovement');
  const good = items.filter((i) => i.grade === 'good');

  // 未压缩大图是确定性证据（与网络档无关），直接确认
  resources.heavyUncompressedImages.forEach((img) => {
    push('图片未启用压缩：' + img.url + ' 传输 ' + img.encodedKB + 'KB / 解码 ' + img.decodedKB + 'KB',
      '静态图片应启用 gzip/br 或改用 webp/avif，单图建议 < 200KB',
      '传输体积与解码体积几乎相同（压缩率 < 5%）',
      { severity: 'P2', status: 'confirmed', confidence: 0.9, property: 'uncompressed-image',
        raw: { url: img.url, encodedKB: img.encodedKB, decodedKB: img.decodedKB } });
  });

  const transferGrade = gradeOf(resources.transferMB, THRESHOLD.transferMB);
  if (transferGrade === 'poor') {
    push('页面资源传输总量 ' + resources.transferMB + 'MB，超过 5MB 上限',
      '首屏资源传输总量建议 < 2MB（上限 5MB）',
      '实测 ' + resources.transferMB + 'MB / ' + resources.count + ' 个请求',
      { severity: 'P2', status: 'confirmed', confidence: 0.85, property: 'transfer-size',
        raw: { transferMB: resources.transferMB, count: resources.count } });
  } else if (transferGrade === 'needsImprovement') {
    push('页面资源传输总量 ' + resources.transferMB + 'MB，超过建议值 2MB',
      '首屏资源传输总量建议 < 2MB',
      '实测 ' + resources.transferMB + 'MB / ' + resources.count + ' 个请求',
      { severity: 'P3', status: 'pending', confidence: 0.6, property: 'transfer-size',
        raw: { transferMB: resources.transferMB } });
  }

  // 耗时类：有节流才敢确认；无节流只报参考值（warn），不自动确认成缺陷
  const emitVitals = (list, status, severity, verdict) => {
    list.forEach((i) => {
      push(i.label + ' ' + i.value + i.unit + '，' + verdict,
        i.label + ' 应 <= ' + i.pair[0] + i.unit + '（差阈值 ' + i.pair[1] + i.unit + '）',
        '实测 ' + i.value + i.unit
          + (throttle.applied ? '（网络档 ' + throttle.mode + '）'
                              : '（未施加网络节流，绝对耗时仅供参考）'),
        { severity: severity, status: status, confidence: throttle.applied ? 0.85 : 0.5,
          property: 'vitals-' + i.key,
          raw: { metric: i.key, value: i.value, threshold: i.pair, throttling: throttle } });
    });
  };

  if (throttle.applied) {
    emitVitals(poor, 'confirmed', 'P1', '超出差阈值');
    emitVitals(mid, 'warn', 'P3', '超出良好阈值、未达差阈值');
  } else {
    emitVitals(poor.concat(mid), 'warn', 'P3', '超阈值（无节流，需在固定网络档复测确认）');
  }

  // 一条可判定结论都没有 → dataMissing，不能当 pass
  const noMetric = items.length === 0;
  if (noMetric && findings.length === 0) {
    push('未采集到任何可判定的性能指标',
      '至少能取到 TTFB / FCP / LCP 之一，或资源条目',
      'navigation 条目与 paint 条目均为空（可能是 about:blank、跨进程首帧未就绪或 API 被禁用）',
      { severity: 'P3', status: 'dataMissing', confidence: 1, property: 'metrics-unavailable',
        raw: { hasNav: !!nav, resourceCount: resources.count } });
  } else if (findings.length === 0) {
    // 全部达标 → 显式 pass 证据，否则该维度会退化成 notRun（"测过"被判成"没测"）
    push('性能指标全部在良好阈值内：' + good.map((i) => i.key + '=' + i.value + i.unit).join('，'),
      '各指标 <= 良好阈值',
      items.length + ' 项指标均达标' + (throttle.applied ? '' : '（无节流，绝对耗时仅供参考）'),
      { severity: 'P3', status: 'pass', confidence: throttle.applied ? 0.85 : 0.6,
        property: 'vitals-pass', raw: { items: items, throttling: throttle } });
  }

  return JSON.stringify({
    mode: 'collect',
    url: location.href,
    engine: 'performance-api',
    throttling: throttle,
    metrics: metrics,
    resources: resources,
    observerReady: clsReady,
    clsSource: clsReady ? 'layout-shift-observer' : 'unavailable',
    lcpFinalized: false,
    note: 'LCP/CLS 在采集时刻可能尚未 finalize（真实 finalize 发生在首次用户交互或页面隐藏），'
      + '阈值判定因此偏乐观；无节流时耗时类指标只作参考，不自动确认为缺陷。',
    summary: {
      items: items,
      good: good.map((i) => i.key),
      needsImprovement: mid.map((i) => i.key),
      poor: poor.map((i) => i.key),
      skipped: [
        metrics.cls === null ? 'cls:no-observer' : '',
        metrics.tbt === null ? 'tbt:no-longtask-observer' : '',
      ].filter(Boolean),
    },
    findings: findings,
  });
})()
