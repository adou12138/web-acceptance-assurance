/**
 * 页面身份校验接入各驱动的契约测试。
 * 每个用例生成一个「驱动源码 + 解包 main」的临时脚本：把 require.main===module
 * 守卫剥掉、直接调用 main()，再用子进程执行并拦截 browser.cjs。
 * 验证「登录页上全 notRun 且非零退出」。不访问网络。
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');

const SCRIPTS = path.join(__dirname, '..', 'node');

const PRELUDE = `
const pageMock = {
  url: () => 'https://x/login',
  goto: async () => {},
  waitForTimeout: async () => {},
  waitForLoadState: async () => {},
  evaluate: async (fn) => String(fn).includes('password') ? false : 900,
  $: async () => null,
  keyboard: { press: async () => {} },
};
const contextMock = {
  pages: () => [pageMock],
  newPage: async () => pageMock,
  addCookies: async () => {},
  addInitScript: async () => {},
  route: async () => {},
};
const browserMod = {
  launch: async () => ({ context: contextMock }),
  closeAll: async () => {},
  restoreAuth: async () => ({ ok: true }),
};
const Module = require('module');
const orig = Module.prototype.require;
Module.prototype.require = function (request) {
  if (String(request).endsWith('browser.cjs')) return browserMod;
  return orig.call(this, request);
};
`;

function writeUnwrapped(tmp, scriptFile, extraArgs) {
  const src = fs.readFileSync(path.join(SCRIPTS, scriptFile), 'utf8');
  // 守卫改恒真；相对 require 与 __dirname 以 runner 位置解析会错，
  // 统一改写为驱动目录绝对路径。
  const unwrapped = src
    .replace(/^#![^\n]*\n/, '')                       // shebang 必须在首行
    .replace(/if \(require\.main === module\)/g, 'if (true)')
    .replace(/require\('\.\/(page_identity|write_guard|auth|browser)\.cjs'\)/g,
             (m, n) => 'require(' + JSON.stringify(path.join(SCRIPTS, n + '.cjs')) + ')')
    .replace(/require\('\.\.\/(stabilize)\.js'\)/g,
             (m, n) => 'require(' + JSON.stringify(path.join(SCRIPTS, '..', n + '.js')) + ')')
    .replace(/__dirname/g, JSON.stringify(SCRIPTS));
  assert.ok(!/if \(require\.main === module\)/.test(unwrapped), '未能剥开入口守卫: ' + scriptFile);
  const argvLine = 'process.argv = ' + JSON.stringify(
    ['node', 'u', '--url', 'https://x/', '--out', tmp, '--safety-policy', 'all',
     '--wait', '10', ...extraArgs]) + ';\n';
  return PRELUDE + argvLine + unwrapped;
}

const CASES = [
  { script: 'run_a11y_audit.cjs', artifact: 'a11y_audit.json', args: [] },
  { script: 'run_keyboard_order.cjs', artifact: 'keyboard_order.json', args: [] },
  { script: 'run_performance.cjs', artifact: 'performance.json', args: [] },
];

for (const { script, artifact, args } of CASES) {
  test(script + ' on login page produces notRun artifact with exit 5', () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'idgate-'));
    const runner = path.join(tmp, 'unwrapped.cjs');
    fs.writeFileSync(runner, writeUnwrapped(tmp, script, args), 'utf8');
    let code = 0;
    try {
      execFileSync('node', [runner], { encoding: 'utf8', timeout: 60000, stdio: 'pipe' });
    } catch (e) {
      code = e.status == null ? -1 : e.status;
    }
    const outFile = path.join(tmp, artifact);
    assert.ok(fs.existsSync(outFile), 'notRun 产物必须落盘: ' + artifact);
    const parsed = JSON.parse(fs.readFileSync(outFile, 'utf8'));
    // 各驱动 notRun 形状不同：keyboard 顶层 status；performance 的 reason 在
    // findings[0]（writeNotRun 统一形状）；断言都指向「notRun + 登录页归因」。
    const notRun = parsed.status != null ? parsed.status === 'notRun'
      : (parsed.findings || []).some(f => f.status === 'notRun');
    assert.ok(notRun, '产物必须标记 notRun: ' + JSON.stringify(parsed).slice(0, 200));
    const reasonText = (parsed.reason || '') + ' ' + JSON.stringify(parsed.findings || []);
    assert.ok(/登录页|未达目标态/.test(reasonText), '原因要点名登录页: ' + reasonText.slice(0, 200));
    assert.equal(code, 5, '身份门禁必须非零退出（got ' + code + '）');
  });
}
