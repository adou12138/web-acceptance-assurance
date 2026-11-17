#!/usr/bin/env node
/**
 * 浏览器生命周期与 Playwright 解析的唯一来源。
 *
 * 为什么抽出来：三个驱动（run_state_scenarios / run_performance / run_a11y_audit）
 * 各自抄了一份 resolvePlaywright + launch + close。抄出来的问题不只是重复——
 * 每份都带若干空 catch 兜底，静默失败的命中数会随驱动数量线性增长，
 * 而"只关 context 不关 browser"这种缺陷会在某一份里被漏掉（实测新驱动就漏了，
 * 表现为进程不退出、管线等到超时，日志里只有一句"无输出"）。
 *
 * 这里只做三件事：解析 Playwright、启动上下文、关闭全部句柄。所有兜底都留痕。
 */
const fs = require('fs');
const path = require('path');
const { resolveChrome } = require('../stabilize.js');

const DEFAULT_VIEWPORT = { width: 1440, height: 900 };

/**
 * 解析 playwright-core / playwright。
 * 返回模块本身，或 { error }（把"每个候选为什么失败"带回去，而不是静默返回 null）。
 */
function resolvePlaywright() {
  const candidates = [
    'playwright-core',
    'playwright',
    path.join(process.cwd(), 'node_modules', 'playwright-core'),
    path.join(process.cwd(), 'node_modules', 'playwright'),
  ];
  if (process.env.PLAYWRIGHT_CORE_PATH) candidates.push(process.env.PLAYWRIGHT_CORE_PATH);
  const failures = [];
  for (const candidate of candidates) {
    try {
      return require(candidate);
    } catch (e) {
      failures.push(candidate + ' -> ' + String((e && e.message) || e).slice(0, 80));
    }
  }
  return {
    error: 'playwright-core 不可用（已尝试 ' + candidates.length + ' 个入口）。'
      + '请 npm i playwright-core。首个失败原因：' + (failures[0] || '未知'),
  };
}

/**
 * 启动浏览器上下文。
 * @returns {{context, browser, chrome}|{error}}
 */
async function launch(opts) {
  const o = opts || {};
  const pw = resolvePlaywright();
  if (!pw.chromium) return { error: pw.error || 'playwright 模块缺少 chromium' };
  const chrome = o.chrome && fs.existsSync(o.chrome) ? o.chrome : resolveChrome();
  if (!chrome) return { error: '未找到 Chrome 可执行文件（ms-playwright 目录下无 chrome.exe）' };
  const viewport = o.viewport || DEFAULT_VIEWPORT;
  const args = ['--no-sandbox'];

  if (o.profile) {
    const context = await pw.chromium.launchPersistentContext(o.profile,
      { headless: !o.headed, executablePath: chrome, args, viewport });
    return { context, browser: null, chrome };
  }
  const browser = await pw.chromium.launch({ headless: !o.headed, executablePath: chrome, args });
  const context = await browser.newContext({ viewport });
  return { context, browser, chrome };
}

/**
 * 关闭上下文与浏览器。只关 context 会留下 chrome 子进程，node 不退出，
 * 调用方（管线 subprocess）只能等超时——所以浏览器的关闭不能省。
 */
async function closeAll(handle) {
  if (!handle) return;
  if (handle.context) {
    try {
      await handle.context.close();
    } catch (e) {
      console.warn('[warn] context 关闭失败: ' + String(e).slice(0, 120));
    }
  }
  if (handle.browser) {
    try {
      await handle.browser.close();
    } catch (e) {
      console.warn('[warn] browser 关闭失败: ' + String(e).slice(0, 120));
    }
  }
}

/** 恢复登录态（可选）。auth 模块缺失属于正常情况，不当作失败。 */
async function restoreAuth(context) {
  let auth = null;
  try {
    auth = require('./auth.cjs');
  } catch (e) {
    console.warn('[i] auth.cjs 不可用，跳过登录态恢复: ' + String((e && e.message) || e).slice(0, 100));
  }
  if (auth && auth.restoreAuth) {
    return auth.restoreAuth(context).catch(
      (e) => ({ ok: false, injected: false, reason: 'restoreAuth 异常: ' + String(e).slice(0, 160) }));
  }
  return null;
}

module.exports = { resolvePlaywright, launch, closeAll, restoreAuth, DEFAULT_VIEWPORT };
