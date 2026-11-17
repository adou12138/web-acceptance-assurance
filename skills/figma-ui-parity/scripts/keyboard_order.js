(function () {
  /*
   * 7.43 键盘可达性与焦点顺序
   *
   * 只做「Tab 能不能走到、走的顺序对不对」这两件事。判定不看截图、不看视觉，
   * 全部基于 document.activeElement 与 DOM 顺序的机械比对。
   *
   * 三段式协议（驱动负责按键，页面脚本只负责枚举与记录）：
   *   plan()   —— 枚举期望的 Tab 序列，给每个元素打上 data-vp-kb 序号
   *   record() —— 读当前 activeElement 的序号（每次 Tab 后调用一次）
   *   finish() —— 拿观测序列做比对，产出 findings
   *
   * 为什么顺序判定要单独做：正 tabindex 会把焦点顺序从 DOM 序改成数值序，
   * 视觉上完全看不出来，只有逐次按 Tab 才能发现。
   */
  var ATTR = 'data-vp-kb';
  var CANDIDATE_SEL = [
    'a[href]', 'button', 'input', 'select', 'textarea', 'summary',
    'audio[controls]', 'video[controls]',
    '[tabindex]', '[contenteditable=true]',
    '[role=button]', '[role=link]', '[role=tab]', '[role=checkbox]',
    '[role=radio]', '[role=switch]', '[role=menuitem]', '[role=option]'
  ].join(',');

  var DIALOG_SEL = '[role=dialog], [role=alertdialog], dialog[open]';
  var SKIP_INPUT_TYPES = ['hidden', 'file'];

  // 空白折叠用字符码手写，不写正则转义：本文件会被当字符串注入页面，
  // 多一层转义就会静默改变语义（全仓页内脚本的通用约束）。
  function collapse(value) {
    var raw = String(value == null ? '' : value);
    var out = '';
    var prevSpace = false;
    for (var i = 0; i < raw.length; i += 1) {
      var code = raw.charCodeAt(i);
      var isSpace = code === 32 || code === 9 || code === 10 || code === 13
        || code === 12 || code === 11 || code === 0x00A0 || code === 0x3000;
      if (isSpace) {
        if (!prevSpace) out += ' ';
        prevSpace = true;
      } else {
        out += raw.charAt(i);
        prevSpace = false;
      }
    }
    return out;
  }

  function text(el) {
    var value = (el.innerText || el.textContent || '') || '';
    return collapse(value).trim().slice(0, 30);
  }

  // 类名转义：Tailwind 任意值类名拼进选择器即非法 CSS，下游定位会失败。
  // 契约见 tests/test_selector_escape_contract.py。
  function escCls(name) {
    if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(name);
    var raw = String(name);
    for (var i = 0; i < raw.length; i += 1) {
      var c = raw.charCodeAt(i);
      var ok = (c >= 48 && c <= 57) || (c >= 65 && c <= 90) || (c >= 97 && c <= 122) || raw[i] === '_' || raw[i] === '-';
      if (!ok) return '';
    }
    return raw;
  }

  function classes(el) {
    var raw = collapse(el.getAttribute('class')).trim();
    if (!raw) return '';
    return raw.split(' ').filter(Boolean).slice(0, 2).map(escCls).filter(Boolean).join('.');
  }

  function selectorOf(el) {
    var tag = String(el.tagName || '').toLowerCase();
    var id = el.getAttribute('id');
    var cand;
    if (id) cand = tag + '#' + escCls(id);
    else {
      var cls = classes(el);
      cand = cls ? tag + '.' + cls : tag;
    }
    try {
      document.querySelectorAll(cand);
      return cand;
    } catch (e) {
      return tag;
    }
  }

  function cs(el) {
    // 不包 try：getComputedStyle 对 Element 正常不会抛，
    // 真抛了说明元素状态异常，应当让整个探测显式失败（驱动记 notRun），
    // 而不是把该元素当成"不可见"悄悄从期望序列里删掉——那会漏报不可达。
    return window.getComputedStyle(el);
  }

  function visible(el) {
    if (!el || !el.getBoundingClientRect) return false;
    var rect = el.getBoundingClientRect();
    if (rect.width <= 1 || rect.height <= 1) return false;
    var style = cs(el);
    if (!style) return false;
    if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse') return false;
    if (parseFloat(style.opacity || '1') <= 0.1) return false;
    if (el.getAttribute('aria-hidden') === 'true') return false;
    if (el.hasAttribute('inert')) return false;
    // 祖先 inert / aria-hidden 由浏览器自己排除聚焦，这里只需排除元素本身
    return true;
  }

  function disabledLike(el) {
    if (el.disabled === true) return true;
    if (String(el.getAttribute('aria-disabled') || '') === 'true') return true;
    if (el.tagName && el.tagName.toUpperCase() === 'INPUT') {
      var type = String(el.getAttribute('type') || 'text').toLowerCase();
      if (SKIP_INPUT_TYPES.indexOf(type) >= 0) return true;
    }
    return false;
  }

  function tabIndexOf(el) {
    var raw = el.getAttribute('tabindex');
    if (raw === null) return el.tabIndex;
    var value = parseInt(raw, 10);
    return isNaN(value) ? el.tabIndex : value;
  }

  function inDialog(el) {
    var node = el;
    while (node && node !== document.body) {
      if (node.matches && node.matches(DIALOG_SEL)) return true;
      node = node.parentElement;
    }
    return false;
  }

  var state = { planned: [], plannedAt: new Date().toISOString(), dialogOpen: false };

  function plan() {
    state.planned = [];
    var all = Array.prototype.slice.call(document.querySelectorAll(CANDIDATE_SEL));
    var index = 0;
    all.forEach(function (el) {
      if (!visible(el) || disabledLike(el)) return;
      var ti = tabIndexOf(el);
      // tabindex < 0：只能被脚本聚焦，Tab 走不到——不是缺陷，不计入期望序列
      if (ti < 0) return;
      el.setAttribute(ATTR, String(index));
      state.planned.push({
        index: index,
        tag: String(el.tagName || '').toLowerCase(),
        selector: selectorOf(el),
        text: text(el),
        role: el.getAttribute('role') || '',
        tabindex: ti,
        positiveTabindex: ti > 0,
        inDialog: inDialog(el)
      });
      index += 1;
    });
    state.dialogOpen = document.querySelectorAll(DIALOG_SEL).length > 0;
    return {
      total: state.planned.length,
      positiveTabindex: state.planned.filter(function (p) { return p.positiveTabindex; }),
      dialogOpen: state.dialogOpen,
      planned: state.planned
    };
  }

  function record() {
    var el = document.activeElement;
    var out = { at: Date.now() };
    if (!el || el === document.body || el === document.documentElement) {
      out.wrapped = true;
      return out;
    }
    var raw = el.getAttribute ? el.getAttribute(ATTR) : null;
    out.index = raw === null ? null : parseInt(raw, 10);
    out.tag = String(el.tagName || '').toLowerCase();
    out.selector = selectorOf(el);
    out.text = text(el);
    out.inDialog = inDialog(el);
    out.unplanned = raw === null;
    return out;
  }

  function isWrap(from, to, total) {
    // 从最后一个候选回到第一个，且中间没经过 body：视为 Tab 循环，不算顺序错误
    return from === total - 1 && to === 0;
  }

  function finish(observed) {
    var list = Array.isArray(observed) ? observed : [];
    var total = state.planned.length;
    var findings = [];
    var reached = {};
    var orderBreaks = [];
    var unplanned = [];
    var maxSeen = -1;
    var lastIndex = null;
    var stuck = [];
    var dialogEscapes = [];
    var sawDialog = false;

    list.forEach(function (item) {
      if (!item || item.wrapped) { maxSeen = -1; lastIndex = null; return; }
      if (item.unplanned) {
        unplanned.push({ selector: item.selector, tag: item.tag, text: item.text });
        return;
      }
      var index = item.index;
      if (typeof index !== 'number' || isNaN(index)) return;
      sawDialog = sawDialog || !!item.inDialog || !!state.dialogOpen;
      reached[index] = true;

      if (lastIndex === index && index >= 0) stuck.push(state.planned[index]);
      if (maxSeen >= 0 && index < maxSeen && !isWrap(maxSeen, index, total)) {
        orderBreaks.push({ from: maxSeen, to: index });
      }
      if (index > maxSeen) maxSeen = index;
      lastIndex = index;

      // 焦点陷阱：弹层开着，焦点却落到了弹层外的元素
      if (item.inDialog) {
        dialogEscapes.push({ index: index, escaped: false });
      } else if (dialogEscapes.length && dialogEscapes[dialogEscapes.length - 1].escaped === false) {
        dialogEscapes[dialogEscapes.length - 1].escaped = true;
        dialogEscapes[dialogEscapes.length - 1].to = item.selector;
      }
    });

    var skipped = state.planned.filter(function (p) { return !reached[p.index]; });

    function push(property, status, severity, confidence, symptom, expected, actual, extra, element) {
      findings.push({
        dimension: '7.43',
        status: status,
        property: property,
        severity: severity,
        severityHint: severity,
        confidence: confidence,
        symptom: symptom,
        expected: expected,
        actual: actual,
        element: element || { selector: '', nth: 0, bbox: { x: 0, y: 0, w: 0, h: 0 }, anchor: property },
        evidence: [{ type: 'keyboard-tab-sequence', planned: total, observed: list.length }],
        raw: extra || {},
        sources: ['keyboard-order']
      });
    }

    if (skipped.length) {
      skipped.slice(0, 8).forEach(function (p) {
        push('kb-unreachable', 'confirmed', 'P2', 0.8,
          '可见且 tabindex>=0 的元素按 Tab 走不到：' + (p.text || p.selector),
          '所有可见且未被禁用的可聚焦元素都应能按 Tab 抵达（WCAG 2.1 2.1.1）',
          '枚举 ' + total + ' 个期望元素，Tab 循环后仍有 ' + skipped.length + ' 个从未获得焦点',
          { selector: p.selector, tag: p.tag, text: p.text, role: p.role, tabindex: p.tabindex },
          { selector: p.selector, nth: 0, bbox: { x: 0, y: 0, w: 0, h: 0 }, anchor: p.text || p.selector });
      });
    }

    var positives = state.planned.filter(function (p) { return p.positiveTabindex; });
    if (positives.length) {
      push('kb-positive-tabindex', 'confirmed', 'P3', 0.85,
        '存在 tabindex 大于 0 的元素，焦点顺序会被改写成数值序，与视觉/DOM 顺序不一致',
        '使用 tabindex=0 或 -1，不要用正数改变焦点顺序（WCAG 2.1 2.4.3）',
        '命中 ' + positives.length + ' 个：' + positives.slice(0, 5).map(function (p) {
          return (p.text || p.selector) + '(tabindex=' + p.tabindex + ')';
        }).join('、'),
        { count: positives.length, elements: positives.slice(0, 8) });
    }

    if (orderBreaks.length) {
      push('kb-order-mismatch', 'confirmed', 'P2', 0.75,
        'Tab 焦点顺序与 DOM 顺序不一致（中间没有循环回落）',
        '焦点顺序应与 DOM/视觉阅读顺序一致（WCAG 2.1 2.4.3）',
        '检出 ' + orderBreaks.length + ' 处回跳，例：第 ' + orderBreaks[0].from + ' 项之后跳到第 ' + orderBreaks[0].to + ' 项',
        { breaks: orderBreaks.slice(0, 10) });
    }

    if (stuck.length) {
      push('kb-focus-stuck', 'pending', 'P3', 0.5,
        '连续两次 Tab 焦点停在同一元素上，可能被焦点陷阱或错误 tabindex 拦住',
        '每次 Tab 都应移动到下一个可聚焦元素；只有在弹层内循环才是预期',
        '同一元素连续获得焦点 ' + stuck.length + ' 次：' + (stuck[0].text || stuck[0].selector),
        { elements: stuck.slice(0, 5).map(function (p) { return p.selector; }) });
    }

    var escapes = dialogEscapes.filter(function (d) { return d.escaped; });
    if (escapes.length) {
      push('kb-dialog-escape', 'pending', 'P3', 0.55,
        '弹层打开时焦点离开了弹层，落到了背后的页面元素上',
        '模态弹层应把焦点限制在弹层内，关闭后归还到触发元素（WCAG 2.1 2.4.3 / ARIA APG）',
        '检出 ' + escapes.length + ' 次逃逸，例：弹层内第 ' + escapes[0].index + ' 项之后落到 ' + (escapes[0].to || '未知元素'),
        { escapes: escapes.slice(0, 5) });
    }

    if (unplanned.length) {
      push('kb-unplanned-focusable', 'pending', 'P3', 0.4,
        'Tab 落在了脚本未能枚举的元素上（可能位于 iframe / shadow DOM 内）',
        '可聚焦元素应可被枚举，否则可达性结论只是部分覆盖',
        '观测到 ' + unplanned.length + ' 次未计划焦点，例：' + (unplanned[0].selector || unplanned[0].tag),
        { samples: unplanned.slice(0, 5) });
    }

    // 全部通过 → 显式 pass 证据。没有这一条，assemble 只会看到「0 条 findings」，
    // 把「跑过且干净」记成 notRun——测过被判成没测。
    if (findings.length === 0) {
      push('kb-no-violation', 'pass', 'P3', 0.8,
        '键盘 Tab 遍历未发现问题',
        '所有可见且 tabindex>=0 的元素均可 Tab 抵达，顺序与 DOM 一致',
        '枚举 ' + total + ' 个可聚焦元素，' + list.length + ' 次按键全部命中且顺序正确',
        { planned: total, observed: list.length, dialogOpen: !!state.dialogOpen });
    }

    return JSON.stringify({
      planned: total,
      observed: list.length,
      reached: Object.keys(reached).length,
      skipped: skipped.map(function (p) { return { selector: p.selector, text: p.text, role: p.role }; }).slice(0, 20),
      positiveTabindex: positives.map(function (p) { return { selector: p.selector, tabindex: p.tabindex }; }).slice(0, 20),
      orderBreaks: orderBreaks.slice(0, 10),
      dialogOpen: sawDialog,
      unplanned: unplanned.slice(0, 10),
      findings: findings,
      total: findings.length
    });
  }

  function reset() {
    Array.prototype.slice.call(document.querySelectorAll('[' + ATTR + ']')).forEach(function (el) {
      el.removeAttribute(ATTR);
    });
    state.planned = [];
  }

  window.__vpKb = { plan: plan, record: record, finish: finish, reset: reset };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { plan: plan, record: record, finish: finish, reset: reset, state: state };
  }
  return 'installed';
})()
