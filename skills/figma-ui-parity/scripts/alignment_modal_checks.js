// 脚本 E：复合对齐 / 弹框发现 / 控件完整性 / 多余文案 检查（步骤 7.13-7.15 用）
// 在 mcp__playwright 的 browser_evaluate 中执行，返回 JSON 字符串。
// 反斜杠无关设计：所有正则字符类用 String.fromCharCode 构造（同脚本 C 约定），
// 可直接内联到 browser_evaluate 的 function 参数（结尾裸 })()，不要写分号）。
(() => {
  const CH = c => String.fromCharCode(c);
  const WS = new RegExp(CH(0x20) + CH(0x09) + CH(0x0A) + CH(0x0D) + CH(0x0C) + CH(0x0B) + CH(0x00A0) + '+', 'g');
  const ICON_RANGE = CH(0xE000) + '-' + CH(0xF8FF) + CH(0x200B) + '-' + CH(0x200F) + CH(0xFEFF);
  const ICON_CHARS = new RegExp('[' + ICON_RANGE + ']', 'g');
  const clean = s => (s || '').replace(ICON_CHARS, '').replace(WS, ' ').trim();

  // Tailwind 任意值类名拼进选择器即非法 CSS，下游定位会失败；先转义再自检。
  const isNameChar = (ch) => {
    const c = ch.charCodeAt(0);
    return (c >= 48 && c <= 57) || (c >= 65 && c <= 90) || (c >= 97 && c <= 122) || ch === '_' || ch === '-';
  };
  const escCls = (name) => {
    if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(name);
    for (let i = 0; i < name.length; i += 1) if (!isNameChar(name[i])) return '';
    return name;
  };

  function buildSel(el) {
    const tag = el.tagName.toLowerCase();
    const id = el.id ? '#' + escCls(el.id) : '';
    const cls = ((el.getAttribute && el.getAttribute('class')) || '').trim().split(WS)
      .filter(Boolean).slice(0, 2).map(escCls).filter(Boolean).join('.');
    const cand = tag + id + (cls ? '.' + cls : '');
    try {
      document.querySelectorAll(cand);
      return cand;
    } catch (e) {
      return tag;
    }
  }
  function pageBox(el) {
    const r = el.getBoundingClientRect();
    return { x: Math.round(r.left + window.scrollX), y: Math.round(r.top + window.scrollY),
             w: Math.round(r.width), h: Math.round(r.height) };
  }
  function vis(el) {
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 0 && r.height > 0;
  }

  const out = { modals: [], alignments: [], clippedControls: [], suspiciousText: [] };

  // ---- 1. 弹框 / 浮层发现（须逐个打开后重跑本脚本核查内部） ----
  const MODAL_SEL = '[role="dialog"], [class*="modal"], [class*="Modal"], [class*="dialog"], [class*="Dialog"], [class*="popup"], [class*="Popup"], [class*="drawer"], [class*="Drawer"]';
  Array.from(document.querySelectorAll(MODAL_SEL)).forEach(el => {
    if (!vis(el)) return;
    const b = pageBox(el);
    if (b.w < 200 || b.h < 100) return; // 过滤误命中的小组件
    out.modals.push({ sel: buildSel(el), box: b, text: clean(el.textContent).slice(0, 60) });
  });

  // ---- 2. 必填标识对齐：星号 / required 元素 与所属标签文本的垂直中心差（断言 ≤2px） ----
  const AST_RE = new RegExp('^[' + CH(0x2A) + CH(0xFF0A) + ']+$'); // * 或全角＊
  const REQ_CLS = /required|must|neces/i;
  Array.from(document.querySelectorAll('span, i, em, b, label')).forEach(el => {
    if (!vis(el)) return;
    const t = clean(el.textContent);
    const cls = (el.getAttribute && el.getAttribute('class')) || '';
    if (!(AST_RE.test(t) || REQ_CLS.test(cls))) return;
    const label = el.parentElement;
    if (!label) return;
    const ae = el.getBoundingClientRect();
    const le = label.getBoundingClientRect();
    const aC = ae.top + ae.height / 2;
    const lC = le.top + le.height / 2;
    const d = Math.round((aC - lC) * 100) / 100;
    out.alignments.push({
      kind: 'required-mark', sel: buildSel(el),
      anchor: clean(label.textContent).slice(0, 30),
      deltaCenterY: d, box: pageBox(el), pass: Math.abs(d) <= 2
    });
  });

  // ---- 3. 描述/hint 文案对齐：输入框下方小字号文本 与 输入框左边缘差（断言 ≤2px） ----
  Array.from(document.querySelectorAll('input, select, textarea')).forEach(inp => {
    if (!vis(inp)) return;
    const ib = inp.getBoundingClientRect();
    let hint = null;
    let n = inp.nextElementSibling;
    for (let i = 0; n && i < 3; i++) {
      const cs = getComputedStyle(n);
      if (parseFloat(cs.fontSize) <= 13 && clean(n.textContent)) { hint = n; break; }
      n = n.nextElementSibling;
    }
    if (!hint) return;
    const hb = hint.getBoundingClientRect();
    const d = Math.round(Math.abs(hb.left - ib.left) * 100) / 100;
    out.alignments.push({
      kind: 'hint-left-align', sel: buildSel(hint),
      anchor: clean(hint.textContent).slice(0, 30),
      deltaLeft: d, box: pageBox(hint), pass: d <= 2
    });
  });

  // ---- 3.5 按钮/链接内：图标与文字是否垂直居中（断言 ≤2px）----
  // 真实缺陷高频："+ 图标未显示 + 文案未居中"。只取**可见且非绝对定位**的图标，
  // 绝对定位图标常是装饰角标，不参与基线对齐，比了就是假缺陷。
  const HOST_SEL = 'button, a, [class*="btn"], [class*="Btn"], [class*="button"], [class*="Button"]';
  const ICON_SEL = 'svg, img, i, [class*="icon"], [class*="Icon"], [class*="ico"]';
  const TEXT_SEL = 'span, em, strong, b, label';
  Array.from(document.querySelectorAll(HOST_SEL)).forEach(host => {
    if (!vis(host)) return;
    const icon = host.querySelector(ICON_SEL);
    if (!icon || !vis(icon)) return;
    const ir = icon.getBoundingClientRect();
    if (ir.width < 4 || ir.height < 4 || ir.height > 40) return; // 过滤分隔线与大图
    if (getComputedStyle(icon).position === 'absolute') return;
    let textBox = null, label = '';
    const textEl = Array.from(host.querySelectorAll(TEXT_SEL)).find(n => vis(n) && clean(n.textContent));
    if (textEl) {
      textBox = textEl.getBoundingClientRect();
      label = clean(textEl.textContent);
    } else {
      const tn = Array.from(host.childNodes).find(n => n.nodeType === 3 && clean(n.textContent));
      if (!tn) return;
      const range = document.createRange();
      range.selectNodeContents(tn);
      textBox = range.getBoundingClientRect();
      label = clean(tn.textContent);
    }
    if (!textBox || textBox.height < 1) return;
    const d = Math.round(((ir.top + ir.height / 2) - (textBox.top + textBox.height / 2)) * 100) / 100;
    out.alignments.push({
      kind: 'icon-text-center', sel: buildSel(host),
      anchor: label.slice(0, 30),
      deltaCenterY: d, box: pageBox(host), pass: Math.abs(d) <= 2
    });
  });

  // ---- 4. 控件内容完整显示：scrollWidth 溢出 → 文案被遮挡（如下拉框） ----
  const CTRL_SEL = 'select, input, button, [class*="select"]';
  Array.from(document.querySelectorAll(CTRL_SEL)).forEach(el => {
    if (!vis(el)) return;
    if (el.scrollWidth > el.clientWidth + 1) {
      out.clippedControls.push({
        sel: buildSel(el), anchor: clean(el.textContent).slice(0, 30),
        scrollW: el.scrollWidth, clientW: el.clientWidth, box: pageBox(el)
      });
    }
  });

  // ---- 5. 多余/垃圾文案候选：形似测试数据（纯数字≥6位 / 同字符重复 / test 类词） ----
  const DIGITS = new RegExp('^-?[0-9]{6,}$');
  const REPEAT = new RegExp('^(.)' + CH(92) + '1+$'); // 反向引用 \1：正则源码需一个反斜杠字符 + '1'（CH(92)+'1'），两个反斜杠会变成"匹配字面反斜杠"
  const TESTY = /test|asdf|qwer|abc{3,}|123{3,}/i;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const seen = new Set();
  while (walker.nextNode()) {
    const t = clean(walker.currentNode.textContent);
    if (!t || t.length < 4 || seen.has(t)) continue;
    seen.add(t);
    if (DIGITS.test(t) || REPEAT.test(t) || TESTY.test(t)) {
      const p = walker.currentNode.parentElement;
      if (p && vis(p)) {
        out.suspiciousText.push({ sel: buildSel(p), text: t.slice(0, 60), box: pageBox(p) });
      }
    }
  }

  return JSON.stringify(out);
})()
