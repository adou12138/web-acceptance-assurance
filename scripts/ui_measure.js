/**
 * ui_measure.js —— 页面侧取数引擎，产出可直接喂给 scripts/ui_metrics.py 的测量记录。
 *
 * 为什么单独存在：ui_metrics.py 只负责判定，它必须知道"背景色是不是真的"。
 * 一旦取数层把未知背景兜底成 #fff，白字压照片会被算成 1.0:1 —— 实测真实登录页
 * 16 条失败里有 11 条是这么来的。所以背景来源必须由页面侧判定并显式声明。
 *
 * 产出三类记录：
 * - 对比度：text_normal / text_large / ui_component / focus_indicator（带 background_source）
 * - 目标尺寸：target（带 inline_in_text / spacing_clear / ua_controlled / essential）
 * - 间距覆盖：text_spacing_override（带 before_clipped / after_clipped，差分口径）
 * - 重排：reflow（带 viewport_width_px / scroll_width_px）
 *
 * 用法（Playwright）：
 *     const raw = await measure(page, { page: 'https://...' });
 *     fs.writeFileSync('measurements.json', raw);
 */

const SPACING_OVERRIDE_CSS = `
  body, body * {
    line-height: 1.5 !important;
    letter-spacing: 0.12em !important;
    word-spacing: 0.16em !important;
  }
  body p { margin-bottom: 2em !important; }
`;

const INTERACTIVE = 'a[href],button,input,select,textarea,summary,[role="button"],' +
  '[role="tab"],[role="link"],[role="menuitem"],[onclick],[tabindex]:not([tabindex="-1"])';

/**
 * 页面内取数的纯函数：只依赖当前 document，不依赖 Playwright。
 *
 * 为什么要拆出来（DEV-1119-03）：measure() 强依赖 page.evaluate，而实测环境里
 * Playwright 实例与已登录浏览器不是同一个、profile 登录态也已失效，官方取数脚本
 * 根本跑不动——只能临时把 page.evaluate 内部逻辑抄一份改宿主，抄出的副本会与主逻辑
 * 漂移。拆成纯函数后，Playwright 与 DevTools 等任意宿主共用同一份判定输入。
 *
 * 用法（DevTools / 任何能在页面上下文求值的地方）：
 *     const fn = collectInDocument.toString();          // 连同函数体一起取出
 *     const raw = await page.evaluate(fn, { pageName: location.href });
 * 返回数组（未序列化）；measure() 会再 JSON.stringify。
 */
function collectInDocument({ pageName, limit, INTERACTIVE, spacingCss }) {
    const out = [];

    const style = (el) => getComputedStyle(el);
    const visible = (el) => {
      const cs = style(el);
      if (cs.display === 'none' || cs.visibility === 'hidden') return false;
      if (Number(cs.opacity) === 0) return false;
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    };
    const label = (el) => (el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') +
      (el.className && typeof el.className === 'string' && el.className.trim()
        ? '.' + el.className.trim().split(/\s+/)[0] : ''));
    const ownText = (el) => [...el.childNodes].filter((n) => n.nodeType === 3)
      .map((n) => n.textContent.trim()).join(' ').replace(/\s+/g, ' ').trim();
    const allText = (el) => (el.innerText || '').replace(/\s+/g, ' ').trim();

    // —— 背景来源：solid / image / gradient / unknown ——
    // 关键：找不到不透明背景时返回 unknown，绝不兜底成白色。
    const backgroundInfo = (el) => {
      let node = el;
      let depth = 0;
      while (node && depth < 12) {
        const cs = style(node);
        const image = cs.backgroundImage || 'none';
        const color = cs.backgroundColor || '';
        const m = color.match(/rgba?\(([^)]+)\)/);
        const parts = m ? m[1].split(',').map((v) => parseFloat(v)) : null;
        const alpha = parts && parts.length > 3 ? parts[3] : 1;
        if (image !== 'none') {
          // 渐变也是 backgroundImage，单独区分以便报告里说清是什么
          const kind = /gradient/i.test(image) ? 'gradient' : 'image';
          return { source: kind, color: null, image: image.slice(0, 120) };
        }
        if (parts && alpha >= 0.999) {
          return { source: 'solid', color, image: null };
        }
        if (parts && alpha > 0 && alpha < 0.999) {
          return { source: 'unknown', color: null, image: 'semi-transparent' };
        }
        node = node.parentElement;
        depth += 1;
      }
      const html = document.documentElement;
      const htmlImage = style(html).backgroundImage;
      if (htmlImage && htmlImage !== 'none') return { source: 'image', color: null, image: 'html' };
      return { source: 'unknown', color: null, image: null };
    };

    // —— 2.5.8 Spacing 例外：24px 圆是否与相邻目标相交 ——
    const targets = [...document.querySelectorAll(INTERACTIVE)].filter(visible);
    const boxes = targets.map((el) => ({ el, r: el.getBoundingClientRect(), cs: style(el) }));
    const circleHits = (target) => {
      const { r } = target;
      const cx = r.left + r.width / 2;
      const cy = r.top + r.height / 2;
      const rad = 24 / 2;
      return boxes.some((other) => {
        if (other.el === target.el) return false;
        const b = other.r;
        const nx = Math.max(b.left, Math.min(cx, b.right));
        const ny = Math.max(b.top, Math.min(cy, b.bottom));
        const dx = cx - nx;
        const dy = cy - ny;
        return dx * dx + dy * dy < rad * rad;
      });
    };
    const inlineInText = (el) => {
      const cs = style(el);
      if (!/^inline/.test(cs.display)) return false;
      const parent = el.parentElement;
      if (!parent) return false;
      const mine = allText(el).length;
      return allText(parent).length > mine + 6;
    };

    targets.slice(0, limit).forEach((el, index) => {
      const r = el.getBoundingClientRect();
      const cs = style(el);
      const undersized = r.width < 24 || r.height < 24;
      out.push({
        id: 'T' + String(index + 1).padStart(3, '0'),
        page: pageName,
        element: label(el) + ' «' + (allText(el) || el.value || el.type || '').slice(0, 20) + '»',
        kind: 'target',
        target_width_px: Math.round(r.width * 100) / 100,
        target_height_px: Math.round(r.height * 100) / 100,
        inline_in_text: inlineInText(el),
        spacing_clear: undersized ? !circleHits({ el, r }) : true,
        ua_controlled: false,
        essential: false,
      });
    });

    // —— 对比度：每个"自己带文本"的元素一条，按 颜色/字号/字重/背景 去重 ——
    const seen = new Set();
    let textIndex = 0;
    [...document.querySelectorAll('body *')].forEach((el) => {
      if (textIndex >= limit) return;
      const text = ownText(el);
      if (text.length < 2 || !visible(el)) return;
      const cs = style(el);
      const bg = backgroundInfo(el);
      const key = [cs.color, cs.fontSize, cs.fontWeight, bg.source, bg.color, text.slice(0, 12)]
        .join('|');
      if (seen.has(key)) return;
      seen.add(key);
      textIndex += 1;
      const size = parseFloat(cs.fontSize);
      const weight = parseInt(cs.fontWeight, 10) || 400;
      const large = size >= 24 || (size >= 18.66 && weight >= 700);
      out.push({
        id: 'X' + String(textIndex).padStart(3, '0'),
        page: pageName,
        element: label(el) + ' «' + text.slice(0, 24) + '»',
        kind: large ? 'text_large' : 'text_normal',
        foreground: cs.color,
        background: bg.color,
        background_source: bg.source,
        font_size_px: Math.round(size * 100) / 100,
        font_weight: weight,
      });
    });

    // —— 1.4.12 覆盖测试：前后差分，只认新增裁剪 ——
    const clippedCount = () => {
      let count = 0;
      const details = [];
      [...document.querySelectorAll('body *')].forEach((el) => {
        if (!visible(el)) return;
        if (ownText(el).length < 2) return;
        const cs = style(el);
        const clipY = /hidden|clip/.test(cs.overflowY) && el.scrollHeight - el.clientHeight > 1;
        const clipX = /hidden|clip/.test(cs.overflowX) && el.scrollWidth - el.clientWidth > 1;
        if (clipY || clipX) {
          count += 1;
          details.push({ element: label(el), text: ownText(el).slice(0, 24),
            scrollHeight: el.scrollHeight, clientHeight: el.clientHeight });
        }
      });
      return { count, details };
    };
    const before = clippedCount();
    const probe = document.createElement('style');
    probe.id = '__ui_measure_spacing__';
    probe.textContent = spacingCss;
    document.head.appendChild(probe);
    const after = clippedCount();
    probe.remove();
    out.push({
      id: 'S001',
      page: pageName,
      element: 'body（间距覆盖前后差分）',
      kind: 'text_spacing_override',
      before_clipped: before.count,
      after_clipped: after.count,
      before_detail: before.details.slice(0, 8),
      after_detail: after.details.slice(0, 8),
    });

    // —— 1.4.10 重排 ——
    out.push({
      id: 'R001',
      page: pageName,
      element: 'body（视口 ' + window.innerWidth + 'px）',
      kind: 'reflow',
      viewport_width_px: window.innerWidth,
      scroll_width_px: document.documentElement.scrollWidth,
    });

    return out;

}

async function measure(page, options = {}) {
  const pageName = options.page || (await page.url());
  const limit = options.limit || 400;
  const spacingCss = options.spacingCss || 'body, body * { line-height: 1.5 !important; ' +
    'letter-spacing: 0.12em !important; word-spacing: 0.16em !important; }';
  // 注意：页面上下文里拿不到外层变量，所有参数必须显式传进去（曾因漏传 options 直接抛错）
  const data = await page.evaluate(collectInDocument,
    { pageName, limit, INTERACTIVE, spacingCss });
  return JSON.stringify(data, null, 1);
}

/**
 * 宿主无关入口：调用方自己提供 evaluate 适配器（DevTools MCP / Puppeteer / 注入脚本）。
 * 返回与 measure() 相同结构的 JSON 字符串，可直接喂给 scripts/ui_metrics.py。
 *
 *   const raw = await measureInDocument({
 *     evaluate: (fn, arg) => devtoolsEvaluate(fn, arg),
 *     page: 'https://...',
 *   });
 */
async function measureInDocument(options = {}) {
  if (typeof options.evaluate !== 'function') {
    throw new Error('measureInDocument: 需要 options.evaluate(fn, arg) 适配器');
  }
  const pageName = options.page || '(unknown)';
  const limit = options.limit || 400;
  const spacingCss = options.spacingCss || 'body, body * { line-height: 1.5 !important; ' +
    'letter-spacing: 0.12em !important; word-spacing: 0.16em !important; }';
  const data = await options.evaluate(collectInDocument,
    { pageName, limit, INTERACTIVE, spacingCss });
  return JSON.stringify(data, null, 1);
}

module.exports = { measure, measureInDocument, collectInDocument, SPACING_OVERRIDE_CSS };
