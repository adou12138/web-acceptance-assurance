#!/usr/bin/env node
/**
 * 7.44 交互首响应时延：点击后「多久才有可见反应」。
 *
 * 与 7.22/7.23 的分工：那两个维度判「有没有反应」，本模块判「多久才有反应」。
 * 有反应但慢到 2 秒，在 7.22 视角是 pass，对用户却是卡死。
 *
 * 口径（为什么不是 INP）
 * --------------------
 * INP 需要真实用户交互采样与按次分组，本 skill 没有 RUM 数据。这里测的是
 * 「首个可观测变化出现的时间」，比 INP 粗、但可复现。因此结论写成
 * 「首响应 xx ms」，不冒充 INP 数值。
 *
 * 怎么判「有反应」：MutationObserver，不是轮询整页签名
 * --------------------------------------------------
 * 早期实现按 100ms 轮询「节点数变化 >= 3 / 文本长度变化 >= 10」，用阈值压噪声。
 * 实测这个阈值把**真实响应**也压掉了：点一下只弹出一个 6 字 toast（+1 节点），
 * 两个按钮全被判成「无响应」——比不测更糟。DOM 变更由浏览器自己告知，
 * 不需要阈值猜测：装一个 observer，记下第一条变更的时间戳即可。
 *
 * 代价要如实写：页面若有与交互无关的周期性 DOM 更新（时钟、轮播），会让测量偏乐观。
 * 这个偏差是单向的（只会把"慢"测成"快"），不会把"没反应"测成有反应。
 */

/** 在点击前安装 MutationObserver。必须在 action 之前调用，否则漏掉首帧变更。 */
const INSTALL_JS = `(() => {
  const state = { installedAt: Date.now(), first: null, count: 0, error: '' };
  window.__vpResp = state;
  try {
    const obs = new MutationObserver((records) => {
      state.count += records.length;
      if (state.first) return;
      let added = 0;
      let textChanged = false;
      for (let i = 0; i < records.length; i += 1) {
        const r = records[i];
        if (r.type === 'childList' && r.addedNodes) added += r.addedNodes.length;
        if (r.type === 'characterData') textChanged = true;
      }
      state.first = { at: Date.now(), added: added, textChanged: textChanged };
    });
    obs.observe(document.documentElement, { childList: true, subtree: true, characterData: true });
    state.observer = obs;
  } catch (e) {
    state.error = String((e && e.message) || e).slice(0, 80);
  }
  return state.error ? 'error:' + state.error : 'installed';
})()`;

/** 读取已记录的首次变更（只返回状态对象，不扫 DOM）。 */
const READ_JS = `(() => {
  const s = window.__vpResp;
  if (!s) return null;
  return { first: s.first, count: s.count, error: s.error };
})()`;

const DEFAULTS = {
  timeoutMs: 3000,
  intervalMs: 60,
  warnAtMs: 1000,
  failAtMs: 2000,
};

/**
 * 多路信号取最早：DOM 变更 / URL 变化 / 请求发出，谁先发生算谁。
 * 全部缺失返回 null（= 观测窗内没有任何可观测反应）。
 */
function pickFirst(candidates) {
  const valid = (candidates || []).filter((v) => typeof v === 'number' && !isNaN(v) && v >= 0);
  return valid.length ? Math.min.apply(null, valid) : null;
}

/**
 * 纯判定：耗时 → 结论。返回 null 表示不构成问题（不产 finding）。
 */
function judge(elapsedMs, opts) {
  const o = Object.assign({}, DEFAULTS, opts || {});
  if (typeof elapsedMs !== 'number' || isNaN(elapsedMs) || elapsedMs < 0) return null;
  if (elapsedMs >= o.failAtMs) {
    return { status: 'confirmed', severity: 'P2', confidence: 0.7,
             reason: '首响应 ' + Math.round(elapsedMs) + 'ms，超过 ' + o.failAtMs + 'ms' };
  }
  if (elapsedMs >= o.warnAtMs) {
    return { status: 'warn', severity: 'P3', confidence: 0.6,
             reason: '首响应 ' + Math.round(elapsedMs) + 'ms，超过 ' + o.warnAtMs
               + 'ms（未达缺陷门槛 ' + o.failAtMs + 'ms）' };
  }
  return null;
}

/**
 * 在页面上测量一次交互的首响应时间。
 * @param page Playwright page
 * @param action 触发交互的 async 函数
 * @param opts  { timeoutMs, intervalMs, requestsBefore(): number }
 */
async function measure(page, action, opts) {
  const o = Object.assign({}, DEFAULTS, opts || {});
  const requestsOf = typeof o.requestsBefore === 'function' ? o.requestsBefore : () => 0;
  const installed = await page.evaluate(INSTALL_JS);
  if (installed !== 'installed') {
    // 观察器装不上就不能用「没有变更」下结论——那是把「测不了」当成「没反应」
    return { first: null, installError: String(installed).slice(0, 120),
             waitedMs: 0, timeoutMs: o.timeoutMs, samples: [], judged: null };
  }
  const requestsBefore = requestsOf();
  const urlBefore = page.url();
  const startedAt = Date.now();
  await action();
  const clickedAt = Date.now();

  let first = null;
  const samples = [];
  while (Date.now() - clickedAt < o.timeoutMs) {
    await page.waitForTimeout(o.intervalMs);
    const elapsed = Date.now() - clickedAt;
    const snap = await page.evaluate(READ_JS);
    const signals = [];
    let domAt = null;
    if (snap && snap.first) {
      domAt = Math.max(0, snap.first.at - clickedAt);
      signals.push('dom');
    }
    const urlChangedAt = page.url() !== urlBefore ? elapsed : null;
    if (urlChangedAt !== null) signals.push('url');
    const requestAt = requestsOf() > requestsBefore ? elapsed : null;
    if (requestAt !== null) signals.push('request');
    samples.push({ at: elapsed, dom: domAt, url: urlChangedAt, request: requestAt });
    const elapsedFirst = pickFirst([domAt, urlChangedAt, requestAt]);
    if (elapsedFirst !== null) {
      first = {
        elapsedMs: elapsedFirst,
        signals: signals,
        domAt: domAt,
        // 采样点：首变更发生在它之前的某一刻，报告据此解释精度
        observedAt: elapsed,
      };
      break;
    }
  }
  return {
    first,
    clickCostMs: clickedAt - startedAt,
    waitedMs: Date.now() - clickedAt,
    timeoutMs: o.timeoutMs,
    samples: samples.slice(-6),
    judged: first ? judge(first.elapsedMs, o) : null,
  };
}

/** 汇总一组耗时，给报告顶部一个概览。 */
function summarize(records) {
  const values = (records || [])
    .map((r) => (r && r.first ? r.first.elapsedMs : null))
    .filter((v) => typeof v === 'number' && !isNaN(v))
    .sort((a, b) => a - b);
  // 最近秩法取分位：n=2 时 p50 应落在较小值上，floor 会错取到最大值
  const pick = (p) => (values.length ? values[Math.max(0, Math.ceil(p * values.length) - 1)] : null);
  // 退化检测：页面若一直在改 DOM（流式输出、光标闪烁、轮播、骨架动画），
  // MutationObserver 会把「与本次点击无关的下一帧变更」当成响应，测出来全是 0ms。
  // 这不是「响应很快」，而是**没测到**——照抄进报告就是一条假的性能结论。
  // 判据取「全部样本都小于 MIN_PLAUSIBLE_MS」：真实交互不可能在 5ms 内完成重渲染。
  const MIN_PLAUSIBLE_MS = 5;
  const max = values.length ? values[values.length - 1] : null;
  const degenerate = values.length >= 3 && max !== null && max < MIN_PLAUSIBLE_MS;
  return {
    measured: values.length,
    reliable: !degenerate,
    unreliableReason: degenerate
      ? '首个 DOM 变更全部 < ' + MIN_PLAUSIBLE_MS + 'ms（n=' + values.length + '），'
        + '页面存在持续 DOM 变动，测到的是背景噪声而非交互响应；本次时延不可作为结论'
      : null,
    noResponse: (records || []).filter((r) => r && r.clicked && r.first === null).length,
    p50: pick(0.5),
    p90: pick(0.9),
    max,
    overWarn: values.filter((v) => v >= DEFAULTS.warnAtMs).length,
    overFail: values.filter((v) => v >= DEFAULTS.failAtMs).length,
    thresholds: { warnMs: DEFAULTS.warnAtMs, failMs: DEFAULTS.failAtMs, timeoutMs: DEFAULTS.timeoutMs },
    note: '首响应时延为 MutationObserver 记录的「首个 DOM 变更」，并与 URL / 请求信号取最早；'
      + '不是 INP 数值。无网络节流时测得的是理想网络下的下限；'
      + '页面若有不相关的周期性 DOM 更新，测量会偏乐观（单向偏差）。',
  };
}

module.exports = { INSTALL_JS, READ_JS, DEFAULTS, pickFirst, judge, measure, summarize };
