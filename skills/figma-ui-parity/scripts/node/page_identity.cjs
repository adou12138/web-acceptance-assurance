/**
 * 页面身份校验的共享模块：在产出结论前确认「测的是目标页」。
 *
 * 为什么需要它：state_scenarios 曾在旧认证目录下落到登录页，却按登录页的
 * 样式产出「深色模式 18 个元素近白底」等假缺陷并进入正式报告。根因是
 * 各驱动只看「导航成功」，不看「页面身份」。
 *
 * 保守判定（命中任一即非目标页）：
 *   - URL 含登录路径片段：/login /signin /passport /sso /auth（词边界，
 *     不误伤 /checkout 等普通路径）；
 *   - 页面存在可见的密码输入框（type=password 且非隐藏）。
 * 满足以下任一即认为已达目标态：
 *   - 当前 URL 与目标 URL 同源且非登录特征；
 *   - 页面正文非空（>=200 字符可见文本）且非登录特征。
 * 判定不了的极端情况（evaluate 失败）按未达目标处理并留原因，不猜。
 *
 * 返回 { reachedTarget, isLoginPage, actualUrl, reasons[] }，调用方原样
 * 写进产物；reachedTarget=false 时驱动应停止产出 findings。
 */
const LOGIN_PATH_RE = /\/(?:login|signin|sign-in|passport|sso)(?:[/?#]|$)/i;

function loginHintsFromUrl(url) {
  const reasons = [];
  let u;
  try { u = new URL(url); } catch (e) { return ['url-unparseable']; }
  if (LOGIN_PATH_RE.test(u.pathname)) reasons.push('login-url:' + u.pathname);
  return reasons;
}

async function verifyPageIdentity(page, targetUrl) {
  const result = { reachedTarget: false, isLoginPage: false, actualUrl: '', reasons: [] };
  try {
    result.actualUrl = page.url();
  } catch (e) {
    result.reasons.push('url-read-failed');
    return result;
  }
  const urlHints = loginHintsFromUrl(result.actualUrl);
  let bodyChars = -1;
  let hasPassword = false;
  try {
    bodyChars = await page.evaluate(() => (document.body ? (document.body.innerText || '').trim().length : -1));
    hasPassword = await page.evaluate(() => {
      const els = document.querySelectorAll('input[type=password]');
      for (const el of els) {
        const r = el.getBoundingClientRect();
        const st = getComputedStyle(el);
        if (r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none') return true;
      }
      return false;
    });
  } catch (e) {
    result.reasons.push('page-evaluate-failed');
    return result;
  }
  if (hasPassword) {
    result.isLoginPage = true;
    result.reasons.push('visible-password-input');
  }
  for (const h of urlHints) result.reasons.push(h);
  result.isLoginPage = result.isLoginPage || urlHints.length > 0;
  const sameOrigin = (() => {
    try { return new URL(result.actualUrl).origin === new URL(targetUrl).origin; }
    catch (e) { return false; }
  })();
  // SPA 渲染缓冲：goto 返回时正文可能尚未挂载（实测同页 0ms 时 <200、800ms 后 411）。
  // 非登录特征且同源、但正文过短时，做短周期重试——登录/密码框特征命中则不重试
  //（那类判定不依赖渲染完成度，重试只会浪费时间）。
  if (!result.isLoginPage && sameOrigin && bodyChars < 200) {
    const waits = [800, 800, 800, 800]; // 最多再等 ~3.2s
    for (const w of waits) {
      await new Promise(r => setTimeout(r, w)).catch(() => {});
      try {
        bodyChars = await page.evaluate(() => (document.body ? (document.body.innerText || '').trim().length : -1));
      } catch (e) { break; }
      if (bodyChars >= 200) break;
    }
  }
  result.reachedTarget = !result.isLoginPage && sameOrigin && bodyChars >= 200;
  if (!result.reachedTarget && result.reasons.length === 0) {
    result.reasons.push(bodyChars < 200 ? 'page-body-too-thin' : 'origin-mismatch');
  }
  return result;
}

module.exports = { verifyPageIdentity, loginHintsFromUrl };
