/*
 * 可访问性规则审计（页面内执行，输出 findings 列表）—— 维度 7.41
 *
 * 与 7.19 的分工：
 *   7.19 = 可访问性树的**跨轮结构 diff**（回答「相对上次变没变」，首次无基线只能 dataMissing）
 *   7.41 = 单次运行的**规则合规判定**（回答「这一版合不合规」，首次运行即可出结论）
 *
 * 两条通道：
 *   builtin —— 本文件内置的 WCAG 2.1 A/AA 子集（15 条规则，纯计算，无外部依赖）
 *   axe     —— driver 检测到 axe-core 时额外注入跑一遍，结果合并后 engine=axe+builtin
 * 产物里必须写清 rulesCovered 与 coverageNote：内置规则是子集，**不构成全量合规结论**。
 *
 * 用法：driver 里 page.evaluate(AUDIT_SRC)，返回 JSON 字符串。
 * 约束：全文不出现反斜杠转义。
 */

(() => {
  const findings = [];
  const ruleHits = {};
  const MAX_PER_RULE = 8;
  const RULES = [];

  const push = (rule, wcag, symptom, expected, actual, el, opts) => {
    const o = opts || {};
    ruleHits[rule] = (ruleHits[rule] || 0) + 1;
    if (ruleHits[rule] > MAX_PER_RULE) return;
    findings.push({
      dimension: '7.41',
      symptom: symptom,
      expected: expected,
      actual: actual,
      severityHint: o.severity || 'P3',
      severity: o.severity || 'P3',
      confidence: typeof o.confidence === 'number' ? o.confidence : 0.8,
      status: o.status || 'confirmed',
      property: 'a11y-' + rule,
      wcag: wcag,
      element: {
        selector: selectorOf(el),
        nth: 0,
        bbox: el && el.getBoundingClientRect ? rectOf(el) : { x: 0, y: 0, w: 0, h: 0 },
        anchor: o.anchor || '',
      },
      evidenceType: 'a11y-rule',
      rule: rule,
      raw: o.raw || {},
      evidence: [{ type: 'a11y-rule', rule: rule, wcag: wcag }],
      sources: ['a11y-audit'],
    });
  };

  // 类名直接拼进选择器，在 Tailwind 项目里必然非法：h-[34px] / w-1/2 / mb-1.5 / md:flex
  // 都不是合法 CSS 标识符。后果不是"少一条证据"，而是缺陷**指不到元素**——
  // 实测本轮 5 条对比度缺陷全因 SyntaxError 拿不到位置图，报告里只能写"未取到"。
  // 与 collect_page_inventory.js 同口径：先转义、再自检、仍非法退结构选择器。
  // 这里刻意不用正则（注入环境对反斜杠极敏感），改用字符码判定。
  const isNameChar = (ch) => {
    const c = ch.charCodeAt(0);
    return (c >= 48 && c <= 57) || (c >= 65 && c <= 90) || (c >= 97 && c <= 122) || ch === '_' || ch === '-';
  };
  const plainName = (name) => {
    if (!name || !name.length) return false;
    for (let i = 0; i < name.length; i += 1) if (!isNameChar(name[i])) return false;
    return true;
  };
  const escCls = (name) => {
    if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(name);
    // 没有 CSS.escape 时宁可丢掉这个类名，也不生成非法选择器
    return plainName(name) ? name : '';
  };
  const structuralOf = (el, prefix) => {
    const base = prefix || el.tagName.toLowerCase();
    const parent = el.parentElement;
    if (!parent) return base;
    const same = Array.prototype.filter.call(parent.children, (c) => c.tagName === el.tagName);
    if (same.length <= 1) return base;
    return base + ':nth-of-type(' + (same.indexOf(el) + 1) + ')';
  };
  const selectorOf = (el) => {
    if (!el || !el.tagName) return '';
    const candidate = (() => {
      if (el.id) return '#' + escCls(el.id);
      const cls = (el.className && typeof el.className === 'string')
        ? el.className.split(String.fromCharCode(32)).filter(Boolean).slice(0, 2).map(escCls).filter(Boolean).join('.')
        : '';
      return structuralOf(el, el.tagName.toLowerCase() + (cls ? '.' + cls : ''));
    })();
    try {
      document.querySelectorAll(candidate);
      return candidate;
    } catch (e) {
      return structuralOf(el);
    }
  };

  const rectOf = (el) => {
    const r = el.getBoundingClientRect();
    return { x: Math.round(r.left + window.scrollX), y: Math.round(r.top + window.scrollY),
             w: Math.round(r.width), h: Math.round(r.height) };
  };

  const visible = (el) => {
    if (!el || !el.getBoundingClientRect) return false;
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return false;
    const s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && parseFloat(s.opacity) !== 0;
  };

  const textOf = (el) => String((el && el.innerText) || '').trim();

  const accessibleName = (el) => {
    if (!el) return '';
    const aria = el.getAttribute ? (el.getAttribute('aria-label') || '') : '';
    if (aria.trim()) return aria.trim();
    const labelledby = el.getAttribute ? (el.getAttribute('aria-labelledby') || '') : '';
    if (labelledby.trim()) {
      const parts = labelledby.trim().split(String.fromCharCode(32));
      const text = parts.map((id) => {
        const target = document.getElementById(id);
        return target ? textOf(target) : '';
      }).join(' ').trim();
      if (text) return text;
    }
    const title = el.getAttribute ? (el.getAttribute('title') || '') : '';
    if (title.trim()) return title.trim();
    const text = textOf(el);
    if (text) return text;
    if (el.tagName === 'INPUT') {
      const type = String(el.type || '').toLowerCase();
      if (type === 'submit' || type === 'button' || type === 'reset') return String(el.value || '');
      if (type === 'image') return String(el.alt || '');
    }
    if (el.tagName === 'IMG') return String(el.alt || '');
    return '';
  };

  // ---------- 颜色与对比度 ----------
  const parseColor = (value) => {
    if (!value) return null;
    const text = String(value).trim();
    if (text.indexOf('rgb') !== 0) return null;
    const open = text.indexOf('(');
    const close = text.lastIndexOf(')');
    if (open < 0 || close < 0) return null;
    const parts = text.slice(open + 1, close).split(',').map((p) => parseFloat(p));
    if (parts.length < 3) return null;
    const alpha = parts.length > 3 ? parts[3] : 1;
    return { r: parts[0], g: parts[1], b: parts[2], a: isNaN(alpha) ? 1 : alpha };
  };

  const blend = (fg, bg) => ({
    r: Math.round(fg.r * fg.a + bg.r * (1 - fg.a)),
    g: Math.round(fg.g * fg.a + bg.g * (1 - fg.a)),
    b: Math.round(fg.b * fg.a + bg.b * (1 - fg.a)),
    a: 1,
  });

  const luminance = (c) => {
    const channel = (v) => {
      const x = v / 255;
      return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4);
    };
    return 0.2126 * channel(c.r) + 0.7152 * channel(c.g) + 0.0722 * channel(c.b);
  };

  const contrastRatio = (a, b) => {
    const la = luminance(a);
    const lb = luminance(b);
    const hi = Math.max(la, lb);
    const lo = Math.min(la, lb);
    return (hi + 0.05) / (lo + 0.05);
  };

  // 背景回溯：向上找第一个不透明背景；一路到 html 都没有则视为默认白底，
  // 但要标记 assumed，避免在深色主题上把「未设置背景」误判成白底而滥报对比度。
  const backgroundOf = (el) => {
    let node = el;
    let assumed = false;
    while (node && node !== document.documentElement) {
      const bg = parseColor(getComputedStyle(node).backgroundColor);
      if (bg && bg.a > 0.95) return { color: bg, assumed: false };
      if (bg && bg.a > 0) return { color: blend(bg, { r: 255, g: 255, b: 255, a: 1 }), assumed: true };
      node = node.parentElement;
    }
    assumed = true;
    return { color: { r: 255, g: 255, b: 255, a: 1 }, assumed: assumed };
  };

  const hasDirectText = (el) => {
    const nodes = el.childNodes || [];
    for (let i = 0; i < nodes.length; i += 1) {
      if (nodes[i].nodeType === 3 && String(nodes[i].nodeValue || '').trim()) return true;
    }
    return false;
  };

  // ---------- 规则 1：图片缺少 alt 属性 ----------
  {
    const imgs = Array.prototype.slice.call(document.querySelectorAll('img')).filter(visible);
    imgs.forEach((img) => {
      if (img.hasAttribute('alt')) return;   // alt="" 是合法的装饰图写法
      push('img-alt', 'WCAG 2.1 1.1.1 (A)',
        '图片缺少 alt 属性：' + selectorOf(img),
        '所有 img 都应有 alt；装饰图写 alt=""，内容图写等价文本',
        'src=' + String(img.getAttribute('src') || '').slice(-60) + ' 且无 alt 属性',
        img, { severity: 'P2', anchor: String(img.getAttribute('src') || '').slice(-40) });
    });
    RULES.push({ id: 'img-alt', wcag: '1.1.1 (A)', checked: imgs.length });
  }

  // ---------- 规则 2/3：按钮与链接的可访问名称 ----------
  {
    const buttons = Array.prototype.slice
      .call(document.querySelectorAll('button, [role=button], input[type=submit], input[type=button]'))
      .filter(visible);
    buttons.forEach((el) => {
      if (accessibleName(el)) return;
      push('button-name', 'WCAG 2.1 4.1.2 (A)',
        '按钮没有可访问名称：' + selectorOf(el),
        '按钮应有可见文本，或 aria-label / aria-labelledby / title',
        '可见文本、aria-label、title 均为空', el, { severity: 'P2' });
    });
    RULES.push({ id: 'button-name', wcag: '4.1.2 (A)', checked: buttons.length });

    const links = Array.prototype.slice.call(document.querySelectorAll('a[href]')).filter(visible);
    links.forEach((el) => {
      if (accessibleName(el)) return;
      push('link-name', 'WCAG 2.1 2.4.4 (A)',
        '链接没有可访问名称：' + selectorOf(el),
        '链接应有可见文本或 aria-label；纯图标链接必须补 aria-label',
        'href=' + String(el.getAttribute('href') || '').slice(-50) + ' 且无名称', el, { severity: 'P2' });
    });
    RULES.push({ id: 'link-name', wcag: '2.4.4 (A)', checked: links.length });
  }

  // ---------- 规则 4：表单控件缺少标签 ----------
  {
    const controls = Array.prototype.slice
      .call(document.querySelectorAll('input, select, textarea'))
      .filter((el) => visible(el) && String(el.type || '').toLowerCase() !== 'hidden');
    controls.forEach((el) => {
      const id = el.getAttribute('id');
      let labelled = false;
      if (id) {
        try { labelled = !!document.querySelector('label[for="' + id + '"]'); } catch (e) { labelled = false; }
      }
      if (!labelled) {
        let parent = el.parentElement;
        while (parent && parent !== document.body) {
          if (parent.tagName === 'LABEL') { labelled = true; break; }
          parent = parent.parentElement;
        }
      }
      if (labelled) return;
      const aria = (el.getAttribute('aria-label') || '').trim()
        || (el.getAttribute('aria-labelledby') || '').trim()
        || (el.getAttribute('title') || '').trim();
      if (aria) return;
      const placeholder = (el.getAttribute('placeholder') || '').trim();
      // placeholder 不能替代标签（失焦即消失），只作为降级证据记 pending
      push('form-label', 'WCAG 2.1 1.3.1 / 3.3.2 (A)',
        '表单控件没有关联标签：' + selectorOf(el),
        '每个可输入控件都应有 label[for] / 包裹 label / aria-label',
        placeholder ? '仅依赖 placeholder「' + placeholder + '」，无 label' : '无 label、无 aria-label、无 placeholder',
        el, { severity: 'P2', status: placeholder ? 'pending' : 'confirmed',
              confidence: placeholder ? 0.6 : 0.85 });
    });
    RULES.push({ id: 'form-label', wcag: '1.3.1 / 3.3.2 (A)', checked: controls.length });
  }

  // ---------- 规则 5：标题层级跳级 ----------
  {
    const headings = Array.prototype.slice
      .call(document.querySelectorAll('h1, h2, h3, h4, h5, h6, [role=heading]'))
      .filter(visible);
    const levels = headings.map((el) => {
      const ariaLevel = el.getAttribute ? el.getAttribute('aria-level') : null;
      if (el.tagName === 'H1' || el.tagName === 'H2' || el.tagName === 'H3'
          || el.tagName === 'H4' || el.tagName === 'H5' || el.tagName === 'H6') {
        return parseInt(el.tagName.slice(1), 10);
      }
      return ariaLevel ? parseInt(ariaLevel, 10) : 2;
    });
    for (let i = 1; i < levels.length; i += 1) {
      if (levels[i] - levels[i - 1] > 1 && levels[i - 1] > 0) {
        push('heading-order', 'WCAG 2.1 1.3.1 (A)',
          '标题层级跳级：h' + levels[i - 1] + ' 之后直接出现 h' + levels[i],
          '标题层级应逐级递增（h2 之后用 h3，不跳级）',
          '顺序中第 ' + (i + 1) + ' 个标题从 h' + levels[i - 1] + ' 跳到 h' + levels[i],
          headings[i], { severity: 'P3', status: 'pending', confidence: 0.55,
                         raw: { sequence: levels.slice(0, 20) } });
        break;
      }
    }
    RULES.push({ id: 'heading-order', wcag: '1.3.1 (A)', checked: headings.length });
  }

  // ---------- 规则 6：html lang ----------
  {
    const html = document.documentElement;
    const lang = (html.getAttribute('lang') || '').trim();
    if (!lang) {
      push('html-lang', 'WCAG 2.1 3.1.1 (A)',
        'html 元素缺少 lang 属性', 'html 应声明 lang（中文页写 zh-CN）',
        'html 无 lang 属性', html, { severity: 'P2', confidence: 0.95 });
    }
    RULES.push({ id: 'html-lang', wcag: '3.1.1 (A)', checked: 1 });
  }

  // ---------- 规则 7：main 地标 ----------
  {
    const mains = Array.prototype.slice
      .call(document.querySelectorAll('main, [role=main]')).filter(visible);
    if (mains.length === 0) {
      push('landmark-main', 'WCAG 2.1 1.3.1 (A)',
        '页面缺少 main 地标', '主内容区应包在 <main> 或 role=main 容器内，便于跳过导航',
        '未找到 main / [role=main]', null, { severity: 'P3', status: 'pending', confidence: 0.6 });
    }
    RULES.push({ id: 'landmark-main', wcag: '1.3.1 (A)', checked: 1 });
  }

  // ---------- 规则 8：role 值合法性 ----------
  {
    const VALID_ROLES = ('alert alertdialog application article banner button cell checkbox '
      + 'columnheader combobox complementary contentinfo definition dialog directory document '
      + 'feed figure form grid gridcell group heading img link list listbox listitem log main '
      + 'marquee math menu menubar menuitem menuitemcheckbox menuitemradio navigation none note '
      + 'option presentation progressbar radio radiogroup region row rowgroup rowheader scrollbar '
      + 'search searchbox separator slider spinbutton status switch tab table tablist tabpanel '
      + 'term textbox timer toolbar tooltip tree treegrid treeitem').split(String.fromCharCode(32));
    const withRole = Array.prototype.slice.call(document.querySelectorAll('[role]'));
    withRole.forEach((el) => {
      const role = String(el.getAttribute('role') || '').trim().toLowerCase();
      if (!role) return;
      if (VALID_ROLES.indexOf(role) >= 0) return;
      push('role-valid', 'WCAG 2.1 4.1.2 (A)',
        'role 取值非法：role=' + role,
        'role 必须是合法的 WAI-ARIA 角色名',
        'role="' + role + '" 不在合法角色表内', el, { severity: 'P3', confidence: 0.9 });
    });
    RULES.push({ id: 'role-valid', wcag: '4.1.2 (A)', checked: withRole.length });
  }

  // ---------- 规则 9：aria-hidden 容器内存在可聚焦元素 ----------
  {
    const hidden = Array.prototype.slice
      .call(document.querySelectorAll('[aria-hidden=true]')).filter(visible);
    hidden.forEach((box) => {
      const focusables = Array.prototype.slice
        .call(box.querySelectorAll('a[href], button, input, select, textarea, [tabindex]'))
        .filter((el) => el.getAttribute('tabindex') !== '-1' && visible(el));
      if (!focusables.length) return;
      push('aria-hidden-focus', 'WCAG 2.1 4.1.2 (A)',
        'aria-hidden 容器内存在可聚焦元素：' + selectorOf(box),
        '对 AT 隐藏的区域不该保留可聚焦控件（键盘用户会聚焦到"不可见"目标）',
        focusables.length + ' 个可聚焦元素（首个 ' + selectorOf(focusables[0]) + '）',
        box, { severity: 'P1', confidence: 0.85 });
    });
    RULES.push({ id: 'aria-hidden-focus', wcag: '4.1.2 (A)', checked: hidden.length });
  }

  // ---------- 规则 10：tabindex > 0 ----------
  {
    const positives = Array.prototype.slice
      .call(document.querySelectorAll('[tabindex]'))
      .filter((el) => parseInt(el.getAttribute('tabindex') || '0', 10) > 0);
    positives.slice(0, MAX_PER_RULE).forEach((el) => {
      push('tabindex-positive', 'WCAG 2.1 2.4.3 (A)',
        '存在正数 tabindex：' + selectorOf(el),
        'tabindex 只应使用 0（自然顺序）或 -1（程序聚焦），正数会打乱 Tab 顺序',
        'tabindex=' + el.getAttribute('tabindex'), el, { severity: 'P2', confidence: 0.9 });
    });
    RULES.push({ id: 'tabindex-positive', wcag: '2.4.3 (A)', checked: positives.length });
  }

  // ---------- 规则 11：文本对比度 ----------
  {
    const candidates = Array.prototype.slice
      .call(document.querySelectorAll('p, span, a, label, li, td, th, div, h1, h2, h3, h4, h5, h6, button, strong, em'))
      .filter((el) => visible(el) && hasDirectText(el));
    // 背景回溯一路到 html 都没有不透明背景时，只能假设默认白底。这个假设在浅色
    // 页面基本成立（可确认），在暗色页面会把深底当白底、批量误报（必须 pending）。
    const darkMode = (typeof window.matchMedia === 'function')
      && window.matchMedia('(prefers-color-scheme: dark)').matches;
    let checked = 0;
    candidates.forEach((el) => {
      const style = getComputedStyle(el);
      const fgRaw = parseColor(style.color);
      if (!fgRaw) return;
      const bg = backgroundOf(el);
      const fg = fgRaw.a < 1 ? blend(fgRaw, bg.color) : fgRaw;
      const ratio = contrastRatio(fg, bg.color);
      const fontSize = parseFloat(style.fontSize) || 16;
      const weight = parseInt(style.fontWeight, 10) || 400;
      const large = fontSize >= 24 || (fontSize >= 18.66 && weight >= 700);
      const required = large ? 3.0 : 4.5;
      checked += 1;
      if (ratio >= required) return;
      const trusted = !bg.assumed || !darkMode;
      const bgNote = !bg.assumed ? ''
        : (darkMode ? '（背景为回溯推断，暗色模式下不可靠，需人工确认）'
                    : '（背景按浏览器默认白底推断）');
      push('color-contrast', 'WCAG 2.1 1.4.3 (AA)',
        '文本对比度不足：' + ratio.toFixed(2) + ':1（要求 ' + required + ':1）',
        '正文对比度 >= 4.5:1，大字（>=24px 或 >=18.66px 粗体）>= 3:1',
        '前景 ' + style.color + ' / 背景 rgb(' + bg.color.r + ',' + bg.color.g + ',' + bg.color.b + ')'
          + bgNote,
        el, { severity: 'P2', status: trusted ? 'confirmed' : 'pending',
              confidence: bg.assumed ? (darkMode ? 0.45 : 0.65) : 0.8,
              raw: { ratio: Math.round(ratio * 100) / 100, required: required,
                     fontSize: fontSize, fontWeight: weight, bgAssumed: bg.assumed,
                     darkMode: darkMode } });
    });
    RULES.push({ id: 'color-contrast', wcag: '1.4.3 (AA)', checked: checked });
  }

  // ---------- 规则 12：重复 id ----------
  {
    const seen = {};
    const dups = [];
    Array.prototype.slice.call(document.querySelectorAll('[id]')).forEach((el) => {
      const id = String(el.getAttribute('id') || '');
      if (!id) return;
      if (seen[id]) { dups.push({ id: id, count: (seen[id].count || 1) + 1 }); seen[id].count = (seen[id].count || 1) + 1; return; }
      seen[id] = { count: 1, el: el };
    });
    dups.slice(0, MAX_PER_RULE).forEach((d) => {
      const target = seen[d.id] ? seen[d.id].el : null;
      push('duplicate-id', 'WCAG 2.1 4.1.1 (A)',
        'id 重复：#' + d.id + '（' + d.count + ' 次）',
        '同一文档内 id 必须唯一（label[for] / aria-labelledby 依赖它定位）',
        '重复 ' + d.count + ' 次', target, { severity: 'P3', confidence: 0.9 });
    });
    RULES.push({ id: 'duplicate-id', wcag: '4.1.1 (A)', checked: Object.keys(seen).length });
  }

  // ---------- 规则 13：aria-label 空串 ----------
  {
    const empties = Array.prototype.slice
      .call(document.querySelectorAll('[aria-label]'))
      .filter((el) => String(el.getAttribute('aria-label') || '').trim() === '');
    empties.slice(0, MAX_PER_RULE).forEach((el) => {
      push('aria-label-empty', 'WCAG 2.1 4.1.2 (A)',
        'aria-label 为空字符串：' + selectorOf(el),
        '空的 aria-label 会覆盖元素自身的可访问名称，应删除该属性或补上文本',
        'aria-label=""', el, { severity: 'P2', confidence: 0.85 });
    });
    RULES.push({ id: 'aria-label-empty', wcag: '4.1.2 (A)', checked: empties.length });
  }

  // ---------- 规则 14：label for 指向不存在的 id ----------
  {
    const labels = Array.prototype.slice.call(document.querySelectorAll('label[for]'));
    let dangling = 0;
    labels.forEach((label) => {
      const id = String(label.getAttribute('for') || '');
      if (!id) return;
      let target = null;
      try { target = document.getElementById(id); } catch (e) { target = null; }
      if (target) return;
      dangling += 1;
      push('label-for-dangling', 'WCAG 2.1 1.3.1 (A)',
        'label 指向不存在的控件：for=' + id,
        'label[for] 的值必须对应文档中真实存在的控件 id',
        '未找到 id=' + id + ' 的元素', label, { severity: 'P2', confidence: 0.9 });
    });
    RULES.push({ id: 'label-for-dangling', wcag: '1.3.1 (A)', checked: labels.length });
  }

  // ---------- 规则 15：input 缺少 type 或 type 非法 ----------
  {
    const inputs = Array.prototype.slice
      .call(document.querySelectorAll('input:not([type])')).filter(visible);
    inputs.slice(0, MAX_PER_RULE).forEach((el) => {
      push('input-type-missing', 'WCAG 2.1 1.3.1 (A)',
        'input 未声明 type：' + selectorOf(el),
        '显式声明 type（text / email / tel / number 等），移动端才能唤起正确键盘',
        '无 type 属性，浏览器按 text 处理', el, { severity: 'P3', status: 'pending', confidence: 0.5 });
    });
    RULES.push({ id: 'input-type-missing', wcag: '1.3.1 (A)', checked: inputs.length });
  }

  const confirmed = findings.filter((f) => f.status === 'confirmed');
  const pending = findings.filter((f) => f.status === 'pending');

  // 全部合规 → 显式 pass 证据（否则维度退化成 notRun，"测过"被判成"没测"）
  if (findings.length === 0) {
    push('no-violation', 'WCAG 2.1 A/AA 子集',
      '内置规则集未发现违规',
      '已检查的 ' + RULES.length + ' 条规则全部通过',
      RULES.map((r) => r.id + '(' + r.checked + ')').join('，'),
      null, { severity: 'P3', status: 'pass', confidence: 0.8 });
  }

  const truncated = Object.keys(ruleHits)
    .filter((rule) => ruleHits[rule] > MAX_PER_RULE)
    .map((rule) => ({ rule: rule, total: ruleHits[rule], listed: MAX_PER_RULE }));

  return JSON.stringify({
    url: location.href,
    engine: 'builtin',
    findings: findings,
    rules: RULES,
    summary: {
      total: findings.length,
      confirmed: confirmed.length,
      pending: pending.length,
      byRule: ruleHits,
      truncated: truncated,
      rulesCovered: RULES.map((r) => r.id),
      rulesCoveredCount: RULES.length,
    },
    coverageNote: '内置引擎覆盖 WCAG 2.1 A/AA 的 ' + RULES.length + ' 条子集规则（图片替代文本、'
      + '控件命名、标签关联、标题层级、语言、地标、ARIA 合法性、tabindex、对比度、重复 id）。'
      + '这不是全量合规结论：键盘可达性、焦点顺序、动态内容播报、屏幕阅读器实测、缩放与重排等'
      + '未包含在内，需要 axe-core 或人工复核补齐。',
    crossRef: '7.19 回答"相对上次结构变没变"，本维度回答"这一版合不合规"，两者不互相替代。',
  });
})()
