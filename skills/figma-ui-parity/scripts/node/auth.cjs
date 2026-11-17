#!/usr/bin/env node
/**
 * 登录态持久化（跨浏览器重启免重复登录）。
 *
 * 为什么需要：Chromium 的**会话 cookie 默认不落盘**，`launchPersistentContext` 的 profile
 * 在浏览器关闭后也会丢掉会话 cookie；只靠持久 profile，多轮开启浏览器仍会掉登录、卡在登录页。
 * 做法：登录成功后把 `context.storageState()` + 原始 cookies（含 session cookie）导出成文件，
 * 之后每次启动在导航前注入（cookies + localStorage），即可免登录继续。
 *
 * 存放位置：按序探测，取首个「已存在 auth.storage.json」的目录；都不存在时回落到 `<cwd>/parity-test/.auth/`
 *   顺序：`PARITY_AUTH_DIR` → `<cwd>/parity-test/.auth/` → `<cwd>/parity-test/<子目录>/.auth/` → `<cwd>/.auth/`
 *   - auth.storage.json（storageState：cookies + origins localStorage）
 *   - auth.cookies.json（原始 cookies，保留 expires=-1 的会话 cookie）
 *   - auth_info.json（保存时间/条数/域名，便于排查）
 *
 * 集成用法：
 *   const { restoreAuth, saveAuth } = require('./auth.cjs');
 *   const context = await pw.chromium.launchPersistentContext(profile, {...});
 *   await restoreAuth(context);                 // 启动即恢复（须在首次导航前）
 *   ... 校验首页已登录 ...
 *   await saveAuth(context, 'default');         // 刷新保存，保持新鲜
 */
const fs = require('fs');
const path = require('path');

/**
 * 解析登录态目录。
 *
 * `PARITY_AUTH_DIR` 显式指定时**无条件生效**：指向一个空目录就是「本轮不用登录态」的
 * 明确意图，不能因为别处恰好存过登录态就回落到那里——那会让「公开页面」或「换账号」
 * 的运行静默沿用上一个身份，通过/失败全被归到错误的人身上，报告看起来完全正常。
 *
 * 未显式指定时按序探测，取首个已存在 `auth.storage.json` 的目录，
 * 避免与历史存放位置分裂（存了却找不到、又要重登）。
 */
function resolveAuthDir() {
  if (process.env.PARITY_AUTH_DIR) return process.env.PARITY_AUTH_DIR;
  const cwd = process.cwd();
  const def = path.join(cwd, 'parity-test', '.auth');
  const cands = [def];
  try {
    const pt = path.join(cwd, 'parity-test');
    if (fs.existsSync(pt)) for (const e of fs.readdirSync(pt, { withFileTypes: true })) if (e.isDirectory()) cands.push(path.join(pt, e.name, '.auth'));
  } catch (e) {
    // 探测失败退回默认目录即可，但要把原因说出来：静默吞掉会让「为什么没找到登录态」无从排查
    console.warn('[warn] 枚举 parity-test 子目录失败，登录态仅在默认目录与非默认候选间探测: '
      + String((e && e.message) || e).slice(0, 100));
  }
  cands.push(path.join(cwd, '.auth'));
  for (const c of cands) { try { if (fs.existsSync(path.join(c, 'auth.storage.json'))) return c; } catch (e) { } }
  return def;
}
const DIR = resolveAuthDir();
const STORAGE = path.join(DIR, 'auth.storage.json');
const COOKIES = path.join(DIR, 'auth.cookies.json');
const INFO = path.join(DIR, 'auth_info.json');

function ensure() { fs.mkdirSync(DIR, { recursive: true }); }

/** 保存当前上下文登录态 → 三个文件 */
async function saveAuth(context, label) {
  ensure();
  await context.storageState({ path: STORAGE });
  const cookies = await context.cookies();
  fs.writeFileSync(COOKIES, JSON.stringify(cookies, null, 2));
  // 【F32】session cookie 是凭据——默认 0644 同机其他用户可读。收紧到 0600
  // （Windows 上 chmod 仅只读位生效，属尽力而为；POSIX 上是硬约束）。
  try { fs.chmodSync(COOKIES, 0o600); } catch (e) { /* 尽力而为 */ }
  try { fs.chmodSync(STORAGE, 0o600); } catch (e) { /* 尽力而为 */ }
  const state = JSON.parse(fs.readFileSync(STORAGE, 'utf8'));
  const info = {
    savedAt: new Date().toISOString(), label: label || '',
    cookieCount: cookies.length,
    sessionCookies: cookies.filter(c => !c.expires || c.expires === -1).length,
    cookieNames: [...new Set(cookies.map(c => c.name))],
    origins: (state.origins || []).map(o => o.origin),
    localStorageKeys: (state.origins || []).map(o => ({ origin: o.origin, keys: o.localStorage.map(x => x.name) })),
  };
  fs.writeFileSync(INFO, JSON.stringify(info, null, 2));
  return info;
}

/** 恢复登录态：注入 cookies（含 session）+ localStorage（addInitScript）。须在首次导航前调用。

    【F57】旧版对 addCookies / addInitScript 一律 `.catch(() => {})`，无论注入成功与否都返回
    `ok: true`。调用方据此认为"登录态已就位"，于是在**未登录的登录页**上跑完整套检查——
    页面上的每个按钮/文案/暗色对比度都判成 mismatch，报告里是一片「缺陷」，却没有任何一行
    提示"这其实是登录页"。反过来说，真缺陷清单被这些噪声淹没，等于整轮白跑。
    现在区分三种结果，让调用方能分辨：
      · 没有登录态文件      → ok:false + noAuthFile（**正常情况**，公开页面本就无需登录）
      · 文件存在但解析不了  → ok:false + badStorage（需重跑 save_auth.cjs）
      · 注入本身失败        → ok:false + failed[]（必须留痕并提醒，不能静默继续）
    只有真正注入成功才 ok:true。
*/
/** 读取登录态新鲜度：返回 { ageHours, savedAt, dir }；无文件时 ageHours=null。 */
function authFreshness() {
  try {
    if (!fs.existsSync(INFO)) return { ageHours: null, savedAt: null, dir: DIR };
    const info = JSON.parse(fs.readFileSync(INFO, 'utf8'));
    const ageHours = info.savedAt ? (Date.now() - new Date(info.savedAt).getTime()) / 3600000 : null;
    return { ageHours: ageHours == null ? null : Math.round(ageHours * 10) / 10, savedAt: info.savedAt || null, dir: DIR };
  } catch (e) {
    return { ageHours: null, savedAt: null, dir: DIR, reason: String((e && e.message) || e).slice(0, 120) };
  }
}

/**
 * 新鲜度告警：超过 ageHoursThreshold（默认 24h）时 stderr 出声。
 * 只告警不阻断——是否可用由页面身份校验兜底，这里只负责「过期还能看出来」。
 */
function warnIfStale(thresholdHours) {
  const f = authFreshness();
  if (f.ageHours != null && f.ageHours > (thresholdHours || 24)) {
    console.warn('[warn] 登录态已 ' + f.ageHours + ' 小时未刷新（保存于 ' + f.savedAt
      + '，目录 ' + f.dir + '）：可能已过期；若页面落到登录页，请先跑 save_auth.cjs 重新保存');
  }
  return f;
}

async function restoreAuth(context) {
  warnIfStale();
  if (!fs.existsSync(STORAGE)) return { ok: false, injected: false, noAuthFile: true, reason: 'no auth file: ' + STORAGE };
  let state;
  try { state = JSON.parse(fs.readFileSync(STORAGE, 'utf8')); } catch (e) { return { ok: false, injected: false, badStorage: true, reason: 'bad storage file: ' + STORAGE }; }
  let cookies = state.cookies || [];
  if (fs.existsSync(COOKIES)) { try { cookies = cookies.concat(JSON.parse(fs.readFileSync(COOKIES, 'utf8'))); } catch (e) { } }
  const seen = new Set(); const uniq = [];
  for (const c of cookies) { const k = c.name + '|' + c.domain + '|' + c.path; if (seen.has(k)) continue; seen.add(k); uniq.push(c); }
  const failed = [];
  if (uniq.length) {
    try { await context.addCookies(uniq); }
    catch (e) { failed.push('addCookies 失败: ' + String(e).slice(0, 160)); }
  }
  const origins = state.origins || [];
  if (origins.length) {
    try {
      await context.addInitScript((list) => {
        try {
          const hit = list.find(x => location.origin === x.origin);
          if (hit) for (const it of hit.localStorage) { try { localStorage.setItem(it.name, it.value); } catch (e) { } }
        } catch (e) { }
      }, origins);
    } catch (e) { failed.push('addInitScript 失败: ' + String(e).slice(0, 160)); }
  }
  if (failed.length) {
    return { ok: false, injected: false, cookies: uniq.length, origins: origins.map(o => o.origin),
             failed, reason: '登录态注入失败（本轮将在未登录状态下检查）: ' + failed.join(' | ') };
  }
  return { ok: true, injected: true, cookies: uniq.length, origins: origins.map(o => o.origin) };
}

/** 多信号登录页判定（密码框只是弱信号，不可单独用） */
async function isLoginPage(page) {
  return await page.evaluate(() => {
    const body = document.body ? document.body.innerText : '';
    const url = location.href || '';
    return /login|signin/i.test(url) || /未登录|您还未登录|请先登录|请登录/.test(body) || /立即注册|忘记用户名|验证码登录|账号登录/.test(body) || /系统提示/.test(document.title || '');
  }).catch(() => true);
}

/**
 * 自动刷新登录态（按钮审计 / 官方提取每轮跑完调用）。
 *
 * 与 saveAuth 的区别：**上下文里一个 cookie 都没有时不写盘**。
 * 原因是覆盖方向：跑公开页面或登录已过期时，把空态写进 auth.storage.json
 * 会把上次辛苦存好的登录态直接抹掉——下次想免登录时反而要重登，
 * 而本轮报告完全看不出这件事发生过。跳过时返回 skipped:true 并带原因，
 * 调用方负责把原因打到输出里（不允许静默）。
 *
 * 显式的「我就是想清空重存」请删掉 .auth 目录后用 save_auth.cjs，不在这个路径上。
 */
async function saveAuthIfPresent(context, label) {
  let cookies;
  try {
    cookies = await context.cookies();
  } catch (e) {
    return { ok: false, skipped: false, reason: '读取上下文 cookies 失败（未写盘）: ' + String((e && e.message) || e).slice(0, 120) };
  }
  if (!cookies || !cookies.length) {
    return { ok: false, skipped: true, reason: '本轮上下文无任何 cookie（公开页面或登录已过期），未写盘以免覆盖既有登录态' };
  }
  const info = await saveAuth(context, label);
  return { ok: true, skipped: false, cookieCount: info.cookieCount, savedAt: info.savedAt };
}

module.exports = { DIR, STORAGE, COOKIES, INFO, resolveAuthDir, saveAuth, saveAuthIfPresent, restoreAuth, isLoginPage, authFreshness, warnIfStale };
