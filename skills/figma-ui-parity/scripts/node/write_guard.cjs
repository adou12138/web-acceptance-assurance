/**
 * 写操作防护的共享模块：按「用户交互前后」二分，而不是按 URL 猜白名单。
 *
 * 为什么需要它：此前各驱动自行实现 mockWrites，出现了三种不一致——
 * page_probes / capture_defect_shots 在 goto 前就拦截一切非只读请求，
 * 把页面初始化必需的认证 POST 一并掐断，页面停在错误态，错误又被当成
 * 「网站状态」产出假缺陷；button_audit 却对写请求返回假 200。同一策略
 * 三种行为，且没有任何一处把「拦截影响了页面」归因给上层。
 *
 * 语义（判据是时机，不是接口名单，换站点成立）：
 *   - 首次成功 goto 之前的非只读请求 = 页面自身初始化（登录态校验、会话建立），
 *     放行并记录清单；报告须注明「已放行 N 个初始化请求」。
 *   - goto 之后的非只读请求 = 测试触发的业务写，统一 abort 并计数。
 *     **不做假 200**：假响应会让「点击有效」的判定失真——页面对假响应的反应
 *     会被当成真实能力（详见 run_button_audit.cjs 的 WRITE_POLICY 注释）。
 *     因此 mockWrites 的 mode 只决定「是否安装防护」，不区分 mock/abort。
 *   - 兜底：装过防护的会话在 close 时若「未达目标态」（目标页校验失败），
 *     summary 里写 reason=blocked-by-write-protection，供上层判 notRun，
 *     不当网站故障、不计缺陷。
 *
 * 用法：
 *   const wp = createWriteGuard(context, 'mockWrites');
 *   await wp.beforeNavigation();          // 装预导航观察（不拦初始化）
 *   await page.goto(url);                 // 初始化请求此时放行并记录
 *   wp.markNavigationSucceeded();         // 之后业务写开始按 mode 拦截
 *   ...测试动作...
 *   wp.finalize({ reachedTarget: true }); // 汇总 summary 写入产物
 */
// 凭据类 query 键：拦截清单会进 summary → 报告，值必须在采集端打码。
const SENSITIVE_KEY = /(password|passwd|pwd|token|secret|authorization|auth|cookie|session|apikey|api[-_]?key|access[-_]?key|credential|jwt|bearer|sig|signature|ticket)/i;

function createWriteGuard(context, mode) {
  const state = {
    mode: mode || 'staticOnly',
    installed: false,
    navSucceeded: false,
    initAllowed: [],   // 交互前放行的初始化写请求
    intercepted: [],   // 交互后按 mode 处理的业务写
    blockedCount: 0,   // 其中被中断/替换的个数
  };

  function redactUrl(value) {
    return String(value == null ? '' : value).replace(/([?&;])([^=&;\s]+)=([^&;\s]*)/g,
      (m, pre, key, val) => pre + key + '=' + (SENSITIVE_KEY.test(key) ? '[REDACTED]' : val));
  }

  function note(req, list, action) {
    if (list.length < 200) {
      list.push({ method: req.method().toUpperCase(), url: redactUrl(req.url()).slice(0, 160), action });
    }
  }

  async function beforeNavigation() {
    if (state.mode !== 'mockWrites' || state.installed) return;
    state.installed = true;
    await context.route('**/*', route => {
      const req = route.request();
      const m = req.method().toUpperCase();
      if (m === 'GET' || m === 'HEAD' || m === 'OPTIONS') return route.continue();
      if (!state.navSucceeded) {
        // 页面初始化阶段：放行并留痕，避免掐断认证/会话请求。
        note(req, state.initAllowed, 'allowed-init');
        return route.continue();
      }
      note(req, state.intercepted, 'blocked');
      state.blockedCount += 1;
      return route.abort('blockedbyclient');
    });
  }

  function markNavigationSucceeded() {
    state.navSucceeded = true;
  }

  /**
   * 汇总。reachedTarget=false 时给出归因原因，供上层把本阶段标 notRun。
   * 返回 summary 对象，由调用方并入自己的产物 JSON。
   */
  function finalize(reachedTarget) {
    const s = {
      mode: state.mode,
      installed: state.installed,
      initAllowedCount: state.initAllowed.length,
      initAllowed: state.initAllowed.slice(0, 20),
      interceptedCount: state.intercepted.length,
      intercepted: state.intercepted.slice(0, 20),
      blockedCount: state.blockedCount,
      reachedTarget: !!reachedTarget,
    };
    if (state.installed && !reachedTarget) {
      s.reason = 'blocked-by-write-protection';
      s.reasonNote = '写操作防护启用且页面未达目标态：本阶段结论不可用，应标 notRun；非网站故障。';
    }
    return s;
  }

  return { beforeNavigation, markNavigationSucceeded, finalize, state };
}

module.exports = { createWriteGuard };
