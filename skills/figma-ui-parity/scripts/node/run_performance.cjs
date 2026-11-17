#!/usr/bin/env node
/**
 * 性能指标驱动：7.40 TTFB / FCP / LCP / CLS / TBT / 资源体积
 *
 * 为什么需要独立驱动：CLS 与 longtask 没有 buffered 入口，采集时才装观察器永远
 * 拿不到；只有在页面加载前注入观察器才能测到真实的布局偏移。所以本驱动用
 * addInitScript 先装 install 阶段，再用同一份脚本的 collect 阶段取数。
 *
 * 判定纪律（与 references/performance.md 一致）：
 *   - 有网络节流（--throttle fast3g）时，耗时超「差阈值」可确认为缺陷；
 *   - 未节流时绝对耗时与机器性能强相关，只记 warn 作参考，不自动确认；
 *   - 资源体积、未压缩大图与网络档无关，可直接确认。
 *
 * 用法：
 *   node run_performance.cjs --url "https://目标页" [--out "./parity-test"]
 *     [--throttle none|fast3g|fast4g] [--wait 1500] [--profile 资料目录] [--headed] [--chrome 路径]
 *
 * 产物：<out>/performance.json —— { metrics, resources, throttling, findings[] }
 */
const fs = require('fs');
const path = require('path');
const { launch, closeAll, restoreAuth } = require('./browser.cjs');
const { verifyPageIdentity } = require('./page_identity.cjs');

function arg(name, def) {
  const i = process.argv.indexOf('--' + name);
  return i >= 0 && process.argv[i + 1] && !process.argv[i + 1].startsWith('--') ? process.argv[i + 1] : def;
}
const URL_ = arg('url');
const OUT = arg('out', path.join(process.cwd(), 'parity-test'));
const PROFILE = arg('profile', null);
const CHROME = arg('chrome', null);
const HEADED = process.argv.includes('--headed');
const THROTTLE = String(arg('throttle', 'none') || 'none');
const WAIT = parseInt(arg('wait', '1500'), 10);

// Lighthouse 移动端默认档（Fast 3G）：1.6Mbps 下行 / 750kbps 上行 / 150ms RTT
const THROTTLE_PRESETS = {
  none: null,
  fast3g: { latency: 150, download: 1.6 * 1024 * 1024 / 8, upload: 750 * 1024 / 8 },
  fast4g: { latency: 40, download: 9 * 1024 * 1024 / 8, upload: 3 * 1024 * 1024 / 8 },
};

if (!URL_ && require.main === module) { console.error('ERROR: --url is required'); process.exit(1); }
if (!Object.prototype.hasOwnProperty.call(THROTTLE_PRESETS, THROTTLE)) {
  console.error('[x] --throttle 非法值: ' + THROTTLE + '（合法: none / fast3g / fast4g）');
  process.exit(1);
}

const PROBE_SRC = fs.readFileSync(path.join(__dirname, '..', 'performance_probe.js'), 'utf8');

function writeNotRun(reason) {
  fs.mkdirSync(OUT, { recursive: true });
  const payload = {
    url: URL_, engine: 'performance-api', error: 'probe-not-executed',
    throttling: { mode: THROTTLE, applied: false },
    findings: [{
      dimension: '7.40',
      symptom: '性能指标未能采集：' + reason,
      expected: '能取到 navigation 与 paint 条目并完成阈值判定',
      actual: reason,
      severityHint: 'P3', severity: 'P3', confidence: 1, status: 'notRun',
      property: 'probe-not-executed',
      element: { selector: '', nth: 0, bbox: { x: 0, y: 0, w: 0, h: 0 }, anchor: 'page-metrics' },
      raw: { reason: reason }, evidence: [{ type: 'driver-error' }], sources: ['performance-driver'],
    }],
    summary: { notRun: 1 },
  };
  const outFile = path.join(OUT, 'performance.json');
  fs.writeFileSync(outFile, JSON.stringify(payload, null, 2), 'utf8');
  console.error('[x] ' + reason);
  console.log(JSON.stringify({ out: outFile, status: 'notRun', reason: reason }, null, 2));
}

async function main() {
  if (!URL_) { console.error('ERROR: --url is required'); process.exit(1); }
  const preset = THROTTLE_PRESETS[THROTTLE];
  const throttleInfo = { mode: THROTTLE, applied: false, preset: preset || null };

  const handle = await launch({ profile: PROFILE, headed: HEADED, chrome: CHROME });
  if (handle.error) {
    writeNotRun(handle.error);
    process.exit(2);
  }

  try {
    await restoreAuth(handle.context);
    // install 阶段必须在任何页面脚本之前注入
    const initSrc = 'window.__vpPerfMode="install";'
      + 'window.__vpPerfThrottle=' + JSON.stringify(throttleInfo) + ';'
      + PROBE_SRC;
    await handle.context.addInitScript({ content: initSrc });

    const page = handle.context.pages()[0] || await handle.context.newPage();

    // CDP 网络节流要在 goto 之前设，否则首个请求不受限
    if (preset) {
      try {
        const cdp = await handle.context.newCDPSession(page);
        await cdp.send('Network.enable');
        await cdp.send('Network.emulateNetworkConditions', {
          offline: false,
          latency: preset.latency,
          downloadThroughput: Math.round(preset.download),
          uploadThroughput: Math.round(preset.upload),
        });
        throttleInfo.applied = true;
      } catch (e) {
        // 节流失效必须如实降级：声称节流了却没生效，耗时结论会变成假缺陷
        throttleInfo.applied = false;
        throttleInfo.error = String(e).slice(0, 160);
        console.warn('[warn] 网络节流设置失败，耗时指标降级为参考值: ' + throttleInfo.error);
      }
    }

    // 不用 process.exit：它不会执行 finally，浏览器的 closeAll 会被跳过、
    // chrome 子进程留给操作系统回收。改为设置 exitCode 后 return，让 finally 收尾。
    let gotoError = null;
    try {
      await page.goto(URL_, { waitUntil: 'load', timeout: 60000 });
    } catch (e) {
      gotoError = String(e).slice(0, 140);
    }
    if (gotoError) {
      writeNotRun('起始页不可达：' + gotoError);
      process.exitCode = 2;
      return;
    }
    // 页面身份校验：登录页的 TTFB/LCP 不是目标页的性能，全 notRun 留因。
    const identity = await verifyPageIdentity(page, URL_);
    if (!identity.reachedTarget) {
      writeNotRun('页面未达目标态（' + (identity.isLoginPage ? '登录页' : '身份校验失败')
        + '，原因: ' + identity.reasons.join('; ') + '）：' + identity.actualUrl);
      process.exitCode = 5;
      return;
    }

    // LCP 在首次交互/隐藏前会持续更新；等一小段让尾部资源与布局稳定，
    // 仍标注 lcpFinalized=false（真实 finalize 事件不可得）。
    await page.waitForTimeout(isNaN(WAIT) ? 1500 : WAIT);

    await page.evaluate((info) => { window.__vpPerfThrottle = info; }, throttleInfo);

    const raw = await page.evaluate(PROBE_SRC);
    const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw;
    if (!parsed || typeof parsed !== 'object' || !Array.isArray(parsed.findings)) {
      writeNotRun('探针返回形状异常（既不是字符串也不是含 findings 的对象）');
      process.exitCode = 2;
      return;
    }

    parsed.throttling = throttleInfo;
    parsed.startedAt = new Date().toISOString();
    parsed.viewport = { w: 1440, h: 900 };
    parsed.summary = Object.assign({}, parsed.summary, {
      total: parsed.findings.length,
      confirmed: parsed.findings.filter((f) => f.status === 'confirmed').length,
      pending: parsed.findings.filter((f) => f.status === 'pending').length,
      warn: parsed.findings.filter((f) => f.status === 'warn').length,
    });

    fs.mkdirSync(OUT, { recursive: true });
    const outFile = path.join(OUT, 'performance.json');
    fs.writeFileSync(outFile, JSON.stringify(parsed, null, 2), 'utf8');
    console.log(JSON.stringify({
      out: outFile,
      throttling: throttleInfo,
      metrics: parsed.metrics,
      transferMB: parsed.resources ? parsed.resources.transferMB : null,
      findings: parsed.findings.length,
      byStatus: {
        confirmed: parsed.summary.confirmed,
        pending: parsed.summary.pending,
        warn: parsed.summary.warn,
        pass: parsed.findings.filter((f) => f.status === 'pass').length,
      },
    }, null, 2));
  } finally {
    await closeAll(handle);
  }
}

if (require.main === module) {
  main().catch((e) => { console.error('FATAL', String(e)); process.exit(1); });
}

module.exports = { THROTTLE_PRESETS };
