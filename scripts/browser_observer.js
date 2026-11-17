(function () {
  'use strict';

  const isRecord = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const nonnegative = value => finite(value) && value >= 0;
  const booleanFields = ['visible', 'interactive', 'hasAccessibleName', 'complete', 'hasImageSource',
    'textClipped', 'textClippedViaDescendant', 'ellipsis', 'intentionallyHidden', 'disabled', 'readOnly',
    'required', 'invalid', 'inViewport', 'occluded'];
  const FIXTURE_STRONG = ['受控', '夹具', '预埋', '非生产', '非用户生产', 'sandbox', 'fixture', 'not production', 'non-production'];
  const FIXTURE_WEAK = ['样例', '示例', '演示', '测试站', '自测', 'demo', 'sample', 'test page'];

  function validateOptions(options) {
    if (!isRecord(options)) throw new TypeError('options必须是对象');
    if (options.collectPerformance !== undefined && typeof options.collectPerformance !== 'boolean') {
      throw new TypeError('collectPerformance必须是布尔值');
    }
    if (options.maxElements !== undefined && (!Number.isInteger(options.maxElements) || options.maxElements < 1 || options.maxElements > 20000)) {
      throw new TypeError('maxElements必须为1到20000之间的整数');
    }
    if (options.expectedOrigin !== undefined) {
      if (typeof options.expectedOrigin !== 'string') throw new TypeError('expectedOrigin必须是完整origin');
      let parsed;
      try { parsed = new URL(options.expectedOrigin); } catch (_) { throw new TypeError('expectedOrigin必须是完整origin'); }
      if (!['http:', 'https:'].includes(parsed.protocol) || parsed.origin !== options.expectedOrigin) {
        throw new TypeError('expectedOrigin必须是无路径、查询、凭据的HTTP(S) origin');
      }
    }
    return options;
  }

  function parsePageUrl(value) {
    if (typeof value !== 'string') throw new TypeError('url必须是绝对URL字符串');
    let url;
    try { url = new URL(value); } catch (_) { throw new TypeError('url必须是绝对URL字符串'); }
    const origin = url.origin;
    url.username = '';
    url.password = '';
    url.search = '';
    url.hash = '';
    return { url: ['http:', 'https:', 'file:', 'about:'].includes(url.protocol) ? url.href : url.protocol, origin };
  }

  function keywordHits(text, keywords, hits) {
    if (typeof text !== 'string' || !text) return;
    const lower = text.toLowerCase();
    for (const keyword of keywords) {
      const key = keyword.toLowerCase();
      if (lower.includes(key) && !hits.includes(keyword)) hits.push(keyword);
    }
  }

  function fixtureVisibleText(doc, limit) {
    if (!doc || !doc.body) return '';
    try {
      if (typeof doc.createTreeWalker === 'function') {
        const walker = doc.createTreeWalker(doc.body, 4, null);
        let text = '';
        let visited = 0;
        let node = walker.nextNode();
        while (node !== null && text.length < limit && visited < 2000) {
          visited += 1;
          const parent = node.parentNode;
          const tag = parent && typeof parent.localName === 'string' ? parent.localName : '';
          if (!['script', 'style', 'noscript', 'template'].includes(tag)) text += node.nodeValue || '';
          node = walker.nextNode();
        }
        return text.slice(0, limit);
      }
    } catch (_) {
      // 遍历受限时退化到整体文本，仍只截取前缀且不输出原文
    }
    return String(doc.body.textContent || '').slice(0, limit);
  }

  function fixtureDeclaration(doc, title) {
    const declaredBy = [];
    const keywords = [];
    try {
      const root = doc.documentElement;
      if (root && typeof root.getAttribute === 'function' &&
          String(root.getAttribute('data-acceptance-fixture') || '').trim().toLowerCase() === 'true') declaredBy.push('attribute');
      const metas = typeof doc.getElementsByTagName === 'function' ? doc.getElementsByTagName('meta') : [];
      for (let index = 0; index < (metas ? metas.length : 0); index += 1) {
        const node = metas[index];
        if (!node || node.localName !== 'meta') continue;
        if (String(node.getAttribute('name') || '').trim().toLowerCase() !== 'acceptance-fixture') continue;
        if (String(node.getAttribute('content') || '').trim().toLowerCase() !== 'true') continue;
        if (!declaredBy.includes('meta')) declaredBy.push('meta');
        break;
      }
    } catch (_) {
      // 属性或meta读取受阻时按未声明处理，不额外访问网络或跨源内容
    }
    const bodyText = fixtureVisibleText(doc, 3000);
    const titleStrong = [];
    const bodyStrong = [];
    keywordHits(typeof title === 'string' ? title : '', FIXTURE_STRONG, titleStrong);
    keywordHits(bodyText, FIXTURE_STRONG, bodyStrong);
    if (titleStrong.length && !declaredBy.includes('title')) declaredBy.push('title');
    if (bodyStrong.length && !declaredBy.includes('body')) declaredBy.push('body');
    for (const keyword of titleStrong.concat(bodyStrong)) if (!keywords.includes(keyword)) keywords.push(keyword);
    const weak = [];
    keywordHits(typeof title === 'string' ? title : '', FIXTURE_WEAK, weak);
    keywordHits(bodyText, FIXTURE_WEAK, weak);
    for (const keyword of weak) if (!keywords.includes(keyword)) keywords.push(keyword);
    return { declared: declaredBy.length > 0, declaredBy, keywords };
  }

  function elementMetadata(element, index) {
    if (!isRecord(element) || typeof element.tag !== 'string' || !/^[a-z][a-z0-9-]*$/.test(element.tag)) {
      throw new TypeError('elements中的节点必须提供小写tag');
    }
    const result = { index, tag: element.tag };
    for (const key of booleanFields) {
      if (element[key] !== undefined) {
        if (typeof element[key] !== 'boolean') throw new TypeError(key + '必须是布尔值');
        result[key] = element[key];
      }
    }
    if (element.naturalWidth !== undefined) {
      if (!nonnegative(element.naturalWidth)) throw new TypeError('naturalWidth必须是非负数');
      result.naturalWidth = element.naturalWidth;
    }
    if (element.hrefKind !== undefined) {
      if (!['missing', 'placeholder', 'javascript', 'fragment', 'navigation', 'other'].includes(element.hrefKind)) {
        throw new TypeError('hrefKind无效');
      }
      result.hrefKind = element.hrefKind;
    }
    if (element.occluderTag !== undefined) {
      if (typeof element.occluderTag !== 'string' || !/^[a-z][a-z0-9-]*$/.test(element.occluderTag)) {
        throw new TypeError('occluderTag必须为小写tag');
      }
      result.occluderTag = element.occluderTag;
    }
    if (element.aspectDeviation !== undefined) {
      if (!nonnegative(element.aspectDeviation)) throw new TypeError('aspectDeviation必须是非负数');
      result.aspectDeviation = element.aspectDeviation;
    }
    if (typeof element.type === 'string' && /^[a-z-]{1,30}$/.test(element.type)) result.type = element.type;
    if (isRecord(element.rect)) {
      const rect = {};
      for (const key of ['left', 'top', 'right', 'bottom', 'width', 'height']) {
        if (finite(element.rect[key])) rect[key] = element.rect[key];
      }
      result.rect = rect;
    }
    return result;
  }

  function performanceSummary(raw, requested) {
    const result = {
      status: requested ? 'unavailable' : 'not-requested',
      source: requested && isRecord(raw) && raw.source === 'Performance API' ? 'Performance API' : null,
      navigation: null,
      resources: null,
      paint: null,
      contentful: null,
      coverage: {
        navigation: requested ? 'missing' : 'not-requested',
        resources: requested ? 'missing' : 'not-requested',
        loading: requested ? 'missing' : 'not-requested',
        contentful: requested ? 'missing' : 'not-requested',
        interaction: 'not-collected',
        layout: 'not-collected'
      },
      notCollected: ['cls', 'inp', 'longtask'],
      inp: { status: 'not-collected', value: null, reason: '未采集真实交互及Event Timing样本，不能判定INP' },
      missing: [],
      limitations: ['仅当前文档已有的Performance API缓冲记录；不是完整性能验收',
        '交互(INP)、布局(CLS)与长任务不在只读采集范围，未采即为未知而不是0或达标',
        '资源缓存、跨源Timing-Allow-Origin与缓冲区限制会使耗时或transferSize缺失；0字节不能推断免费或快速',
        'LCP/FCP取自采集时已有的缓冲快照，后续内容变化可能改变结论']
    };
    if (!requested) return result;
    if (isRecord(raw) && isRecord(raw.navigation)) {
      result.navigation = {};
      for (const key of ['duration', 'domContentLoadedEventEnd', 'loadEventEnd', 'responseStart']) {
        const value = raw.navigation[key];
        result.navigation[key] = finite(value) && value > 0 ? value : null;
        if (result.navigation[key] === null) result.missing.push('navigation.' + key);
      }
      result.coverage.navigation = result.missing.some(key => key.startsWith('navigation.')) ? 'partial' : 'available';
    } else result.missing.push('navigation');
    if (isRecord(raw) && isRecord(raw.resources) && Number.isInteger(raw.resources.count) && raw.resources.count >= 0) {
      result.resources = { count: raw.resources.count };
      for (const key of ['durationTotal', 'transferSizeTotal', 'transferSizeKnownCount']) {
        const value = raw.resources[key];
        result.resources[key] = nonnegative(value) ? value : null;
        if (result.resources[key] === null) result.missing.push('resources.' + key);
      }
      result.coverage.resources = result.missing.some(key => key.startsWith('resources.')) ? 'partial' : 'available';
    } else result.missing.push('resources');
    const fcp = isRecord(raw) && isRecord(raw.paint) ? raw.paint.fcp : undefined;
    if (finite(fcp) && fcp > 0) {
      result.paint = { fcp };
      result.coverage.loading = 'available';
    } else result.missing.push('paint');
    const lcp = isRecord(raw) && isRecord(raw.contentful) ? raw.contentful.lcp : undefined;
    if (finite(lcp) && lcp > 0) {
      result.contentful = { lcp };
      if (nonnegative(raw.contentful.size)) result.contentful.size = raw.contentful.size;
      result.coverage.contentful = 'available';
    } else result.missing.push('contentful');
    const families = Object.values(result.coverage);
    const measured = families.some(value => value === 'available' || value === 'partial');
    result.status = measured ? 'partial' : 'unavailable';
    return result;
  }

  function evaluateFacts(facts, options = {}) {
    validateOptions(options);
    if (!isRecord(facts)) throw new TypeError('facts必须是对象');
    const parsed = parsePageUrl(facts.url);
    if (typeof facts.title !== 'string' || typeof facts.hasBody !== 'boolean' || typeof facts.hasBodyContent !== 'boolean') {
      throw new TypeError('title必须是字符串，hasBody和hasBodyContent必须是布尔值');
    }
    if (!isRecord(facts.viewport) || !nonnegative(facts.viewport.width) || !nonnegative(facts.viewport.height) ||
        !Number.isInteger(facts.renderedNodeCount) || facts.renderedNodeCount < 0 || !Array.isArray(facts.elements)) {
      throw new TypeError('viewport、renderedNodeCount或elements无效');
    }
    if (facts.documentWidth !== undefined && !nonnegative(facts.documentWidth)) throw new TypeError('documentWidth必须为非负数');
    if (facts.viewportMeta !== undefined && (!isRecord(facts.viewportMeta) ||
        typeof facts.viewportMeta.present !== 'boolean' || typeof facts.viewportMeta.deviceWidth !== 'boolean')) {
      throw new TypeError('viewportMeta必须包含布尔present与deviceWidth');
    }
    if (facts.extents !== undefined && (!isRecord(facts.extents) || !finite(facts.extents.left) || !finite(facts.extents.right))) {
      throw new TypeError('extents必须包含有限的left和right');
    }
    if (facts.fixture !== undefined) {
      if (!isRecord(facts.fixture) || typeof facts.fixture.declared !== 'boolean' ||
          !Array.isArray(facts.fixture.declaredBy) || !Array.isArray(facts.fixture.keywords) ||
          !facts.fixture.declaredBy.every(item => typeof item === 'string') ||
          !facts.fixture.keywords.every(item => typeof item === 'string')) {
        throw new TypeError('fixture必须包含布尔declared与字符串数组declaredBy、keywords');
      }
    }
    const elements = facts.elements.map(elementMetadata);
    const scan = facts.scan;
    if (scan !== undefined && (!isRecord(scan) || !Number.isInteger(scan.totalNodes) || scan.totalNodes < 0 ||
        !Number.isInteger(scan.scannedNodes) || scan.scannedNodes < 0 || scan.scannedNodes > scan.totalNodes || typeof scan.truncated !== 'boolean')) {
      throw new TypeError('scan必须提供有效的节点计数与布尔truncated');
    }
    const result = {
      schemaVersion: '1.0',
      status: scan && scan.truncated ? 'incomplete' : 'observed',
      target: { expectedOrigin: options.expectedOrigin || null, observedOrigin: parsed.origin,
        matches: options.expectedOrigin === undefined ? null : parsed.origin === options.expectedOrigin },
      page: { url: parsed.url, title: facts.title, viewport: { width: facts.viewport.width, height: facts.viewport.height },
        hasBody: facts.hasBody, hasBodyContent: facts.hasBodyContent, renderedNodeCount: facts.renderedNodeCount,
        viewportMeta: facts.viewportMeta ? { present: facts.viewportMeta.present, deviceWidth: facts.viewportMeta.deviceWidth } : null,
        fixture: facts.fixture ? { declared: facts.fixture.declared, declaredBy: [...facts.fixture.declaredBy],
          keywords: [...facts.fixture.keywords] } : { declared: false, declaredBy: [], keywords: [] } },
      coverage: scan ? { totalNodes: scan.totalNodes, scannedNodes: scan.scannedNodes, truncated: scan.truncated } :
        { totalNodes: null, scannedNodes: null, truncated: false },
      candidates: elements.filter(element => element.interactive === true || ['a', 'button', 'form', 'input', 'select', 'textarea'].includes(element.tag)),
      focusedInput: facts.focusedInput ? elementMetadata(facts.focusedInput, -1) : null,
      findings: [],
      confirmedBugs: [],
      performance: performanceSummary(facts.performance, options.collectPerformance === true),
      limitations: ['DOM启发式候选均需人工复核，不能据此宣称完整UX或WCAG合规或验收通过',
        '故意隐藏、正常文本省略、布局与滚动容器可能产生误报；遮挡只抽样控件中心点，图片比例只覆盖object-fit为fill的可见图片',
        '未读取完整无障碍树、跨源iframe、Shadow DOM，也不测量布局位移、动画过程与富媒体遮挡',
        'viewport元信息只按文档声明判断，不代表真机缩放后的实际观感；桌面视口下影响需移动模拟复核',
        '不读取表单value；正文、名称与href仅在页面内判断布尔或分类，不输出原文；页面title与去除凭据/query/hash后的URL路径仍可能含敏感信息',
        'renderedNodeCount为本次扫描内具有可见布局框的元素数；可见不保证未被其他元素遮挡']
    };
    if (result.page.fixture.declared) {
      result.limitations.push('页面自述为受控样例、夹具或非生产环境（命中位置：' + result.page.fixture.declaredBy.join('、') +
        '），页面内异常可能是预埋项，不得直接作为产品缺陷上报或计入缺陷率，需先确认该页用途');
    }
    if (result.target.matches === false) {
      result.status = 'blocked';
      result.findings.push({ code: 'wrong-target', classification: 'blocked', needsReview: true,
        evidence: { expectedOrigin: options.expectedOrigin, observedOrigin: parsed.origin } });
      result.candidates = [];
      result.focusedInput = null;
      result.performance = performanceSummary(null, false);
      return result;
    }
    const candidate = (code, evidence, reason) => {
      const finding = { code, classification: 'candidate', needsReview: true, evidence };
      if (reason) finding.needsReviewReason = reason;
      result.findings.push(finding);
    };
    if (!facts.hasBody) candidate('missing-body', { hasBody: false });
    else if (!facts.hasBodyContent) candidate('blank-content', { hasBodyContent: false });
    if (facts.viewportMeta && facts.viewportMeta.present !== true) {
      candidate('missing-viewport-meta', { present: false },
        '移动端会按默认布局视口渲染再整体缩放，需在真实移动设备或设备模拟下确认字号与点击区域');
    } else if (facts.viewportMeta && facts.viewportMeta.deviceWidth !== true) {
      candidate('missing-viewport-meta', { present: true, deviceWidth: false },
        'viewport声明未包含width=device-width，移动端可能按固定宽度布局后缩放');
    }
    const leftOverflow = Math.max(0, -(facts.extents ? facts.extents.left : 0));
    const rightOverflow = Math.max(0, Math.max(facts.documentWidth || 0, facts.extents ? facts.extents.right : 0) - facts.viewport.width);
    if (leftOverflow > 1 || rightOverflow > 1) candidate('horizontal-overflow', { leftOverflow, rightOverflow });
    for (const element of elements) {
      if (element.visible !== true || element.intentionallyHidden === true) continue;
      const evidence = { index: element.index, tag: element.tag };
      if (element.tag === 'img' && element.complete === true && element.naturalWidth === 0 && element.hasImageSource === true) {
        candidate('broken-image', { ...evidence, complete: true, naturalWidth: 0 });
      }
      if (element.interactive === true && element.hasAccessibleName === false) candidate('unnamed-control', evidence);
      if (element.textClipped === true) {
        candidate('text-clipping', { ...evidence, ellipsis: element.ellipsis === true,
          viaDescendant: element.textClippedViaDescendant === true },
        element.ellipsis === true ? '可能为合法省略（ellipsis），需确认是否影响信息获取' : null);
      }
      if (element.occluded === true) {
        candidate('occluded-control', { ...evidence, occluderTag: element.occluderTag || null },
          '仅抽样控件中心点，动画或透明浮层可能产生误报');
      }
      if (element.tag === 'img' && element.aspectDeviation !== undefined && element.aspectDeviation > 0.05) {
        candidate('image-aspect-distortion', { ...evidence, deviation: element.aspectDeviation },
          'object-fit为fill时按自然比例对照；裁切或占位图可能属预期');
      }
      if (element.tag === 'a' && ['placeholder', 'javascript'].includes(element.hrefKind)) {
        candidate('placeholder-link', { ...evidence, hrefKind: element.hrefKind });
      }
    }
    return result;
  }

  function collect(options = {}) {
    validateOptions(options);
    if (typeof document === 'undefined' || typeof window === 'undefined') throw new Error('collect需要浏览器document和window');
    const doc = document;
    const win = window;
    const root = doc.documentElement;
    const viewport = { width: root ? root.clientWidth : win.innerWidth, height: win.innerHeight };
    const facts = { url: win.location.href, title: doc.title, viewport,
      hasBody: Boolean(doc.body), hasBodyContent: false, renderedNodeCount: 0, documentWidth: 0,
      extents: { left: 0, right: viewport.width }, elements: [] };
    facts.fixture = fixtureDeclaration(doc, doc.title);
    const viewportContents = [];
    const metaNodes = typeof doc.getElementsByTagName === 'function' ? doc.getElementsByTagName('meta') : [];
    for (let index = 0; index < (metaNodes ? metaNodes.length : 0); index += 1) {
      const node = metaNodes[index];
      if (!node || node.localName !== 'meta') continue;
      if (String(node.getAttribute('name') || '').trim().toLowerCase() !== 'viewport') continue;
      viewportContents.push(String(node.getAttribute('content') || '').slice(0, 120));
    }
    facts.viewportMeta = {
      present: viewportContents.length > 0,
      deviceWidth: viewportContents.some(content => /width\s*=\s*device-width/i.test(content))
    };
    if (options.expectedOrigin !== undefined && new URL(facts.url).origin !== options.expectedOrigin) {
      return evaluateFacts(facts, options);
    }
    const max = options.maxElements === undefined ? 5000 : options.maxElements;
    const nodes = doc.getElementsByTagName('*');
    const totalNodes = nodes.length;
    const scannedNodes = Math.min(totalNodes, max);
    facts.scan = { totalNodes, scannedNodes, truncated: totalNodes > scannedNodes };
    facts.documentWidth = Math.max(root ? root.scrollWidth : 0, doc.body ? doc.body.scrollWidth : 0);
    const hiddenCache = new WeakMap();
    function hidden(element) {
      if (!element) return false;
      if (hiddenCache.has(element)) return hiddenCache.get(element);
      const style = win.getComputedStyle(element);
      const value = element.hidden === true || style.display === 'none' || style.visibility === 'hidden' ||
        style.visibility === 'collapse' || Number(style.opacity) === 0 || hidden(element.parentElement);
      hiddenCache.set(element, value);
      return value;
    }
    function hasName(element) {
      const label = element.getAttribute('aria-label');
      if (label && label.trim()) return true;
      const labelledBy = element.getAttribute('aria-labelledby');
      if (labelledBy && labelledBy.trim().split(/\s+/).some(id => {
        const ref = doc.getElementById(id);
        return ref && Boolean(ref.textContent.trim());
      })) return true;
      if (element.labels && Array.from(element.labels).some(item => item.textContent.trim())) return true;
      if (element.getAttribute('title') && element.getAttribute('title').trim()) return true;
      if (element.localName === 'img' || element.type === 'image') return Boolean((element.getAttribute('alt') || '').trim());
      if (element.localName === 'input' && ['submit', 'reset'].includes(element.type)) return true;
      if (['input', 'select', 'textarea'].includes(element.localName)) return false;
      if (element.textContent.trim()) return true;
      return Array.from(element.querySelectorAll('img[alt]')).some(image => image.getAttribute('alt').trim());
    }
    function metadata(element, index) {
      const tag = element.localName;
      const rect = element.getBoundingClientRect();
      const visible = !hidden(element) && rect.width > 0 && rect.height > 0;
      const role = element.getAttribute('role');
      const interactive = ['button', 'input', 'select', 'textarea', 'summary'].includes(tag) ||
        (tag === 'a' && element.hasAttribute('href')) || ['button', 'link', 'checkbox', 'radio', 'textbox', 'combobox', 'switch', 'tab', 'menuitem', 'slider'].includes(role) ||
        (element.hasAttribute('tabindex') && element.tabIndex >= 0) || element.isContentEditable;
      const item = { tag, visible, inViewport: visible && rect.bottom > 0 && rect.right > 0 && rect.top < viewport.height && rect.left < viewport.width,
        interactive: Boolean(interactive), rect: { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom, width: rect.width, height: rect.height } };
      if (interactive) item.hasAccessibleName = hasName(element);
      if (['input', 'select', 'textarea', 'button'].includes(tag)) {
        item.type = element.type;
        item.disabled = Boolean(element.disabled);
        item.readOnly = Boolean(element.readOnly);
        item.required = Boolean(element.required);
        item.invalid = element.getAttribute('aria-invalid') === 'true' || Boolean(element.validity && !element.validity.valid);
      }
      if (tag === 'a') {
        const href = element.getAttribute('href');
        item.hrefKind = href === null ? 'missing' : !href.trim() || href.trim() === '#' ? 'placeholder' :
          /^\s*javascript:/i.test(href) ? 'javascript' : href.trim().startsWith('#') ? 'fragment' : 'navigation';
      }
      if (tag === 'img') {
        item.complete = element.complete;
        item.naturalWidth = element.naturalWidth;
        item.hasImageSource = Boolean(element.currentSrc || element.getAttribute('src') || element.getAttribute('srcset'));
      }
      if (visible) {
        const style = win.getComputedStyle(element);
        const directText = Array.from(element.childNodes).some(node => node.nodeType === 3 && node.textContent.trim());
        const subtreeText = directText || Boolean(element.textContent && element.textContent.trim());
        const clipX = ['hidden', 'clip'].includes(style.overflowX);
        const clipY = ['hidden', 'clip'].includes(style.overflowY);
        item.textClipped = subtreeText && ((clipX && element.scrollWidth > element.clientWidth + 1) ||
          (clipY && element.scrollHeight > element.clientHeight + 1));
        if (item.textClipped && !directText) item.textClippedViaDescendant = true;
        item.ellipsis = style.textOverflow === 'ellipsis' || (style.webkitLineClamp && style.webkitLineClamp !== 'none');
        item.ellipsis = Boolean(item.ellipsis);
        if (tag === 'img' && element.complete === true && element.naturalWidth > 0 && element.naturalHeight > 0 &&
            rect.width > 1 && rect.height > 1 && (style.objectFit || 'fill') === 'fill') {
          const naturalRatio = element.naturalWidth / element.naturalHeight;
          const renderedRatio = rect.width / rect.height;
          const deviation = Math.abs(renderedRatio - naturalRatio) / naturalRatio;
          if (deviation > 0.05) item.aspectDeviation = Math.round(deviation * 1000) / 1000;
        }
        if (interactive && typeof doc.elementFromPoint === 'function') {
          const pointX = rect.left + rect.width / 2;
          const pointY = rect.top + rect.height / 2;
          if (pointX >= 0 && pointY >= 0 && pointX < viewport.width && pointY < viewport.height) {
            let hit = null;
            try { hit = doc.elementFromPoint(pointX, pointY); } catch (_) { hit = null; }
            if (hit && hit !== element && !element.contains(hit) && hit !== doc.documentElement && hit !== doc.body) {
              item.occluded = true;
              if (typeof hit.localName === 'string') item.occluderTag = hit.localName;
            }
          }
        }
        if (doc.body && doc.body.contains(element) && !['script', 'style', 'noscript', 'template'].includes(tag) &&
            (directText || interactive || ['img', 'canvas', 'video', 'svg', 'iframe', 'audio'].includes(tag))) facts.hasBodyContent = true;
        facts.renderedNodeCount += 1;
        facts.extents.left = Math.min(facts.extents.left, rect.left + win.scrollX);
        facts.extents.right = Math.max(facts.extents.right, rect.right + win.scrollX);
      }
      if (interactive || ['a', 'form', 'img'].includes(tag) || item.textClipped) facts.elements.push(item);
      if (element === doc.activeElement && ['input', 'select', 'textarea'].includes(tag) && visible) facts.focusedInput = item;
    }
    for (let index = 0; index < scannedNodes; index += 1) metadata(nodes[index], index);
    const clippedElements = facts.elements.filter(item => item.textClipped === true);
    if (clippedElements.length > 1) {
      const dropped = new Set();
      for (const outer of clippedElements) {
        for (const inner of clippedElements) {
          if (outer === inner) continue;
          const outerNode = nodes[outer.index];
          const innerNode = nodes[inner.index];
          if (outerNode && innerNode && outerNode !== innerNode && typeof outerNode.contains === 'function' && outerNode.contains(innerNode)) {
            dropped.add(outer);
            break;
          }
        }
      }
      if (dropped.size) facts.elements = facts.elements.filter(item => !dropped.has(item));
    }
    if (options.collectPerformance === true && win.performance && typeof win.performance.getEntriesByType === 'function') {
      const perf = { source: 'Performance API' };
      try {
        const navigation = win.performance.getEntriesByType('navigation')[0];
        if (navigation) perf.navigation = { duration: navigation.duration, domContentLoadedEventEnd: navigation.domContentLoadedEventEnd,
          loadEventEnd: navigation.loadEventEnd, responseStart: navigation.responseStart };
        const resources = win.performance.getEntriesByType('resource');
        perf.resources = { count: resources.length, durationTotal: 0, transferSizeTotal: 0, transferSizeKnownCount: 0 };
        for (const resource of resources) {
          if (nonnegative(resource.duration)) perf.resources.durationTotal += resource.duration;
          if (finite(resource.transferSize) && resource.transferSize > 0) {
            perf.resources.transferSizeTotal += resource.transferSize;
            perf.resources.transferSizeKnownCount += 1;
          }
        }
        const paints = win.performance.getEntriesByType('paint');
        const fcp = Array.isArray(paints) ? paints.find(entry => entry && entry.name === 'first-contentful-paint') : null;
        if (fcp && finite(fcp.startTime) && fcp.startTime > 0) perf.paint = { fcp: fcp.startTime };
        const contentful = win.performance.getEntriesByType('largest-contentful-paint');
        if (Array.isArray(contentful) && contentful.length) {
          const lcp = contentful[contentful.length - 1];
          if (lcp && finite(lcp.startTime) && lcp.startTime > 0) {
            perf.contentful = { lcp: lcp.startTime };
            if (nonnegative(lcp.size)) perf.contentful.size = lcp.size;
          }
        }
      } catch (_) {
        // 平台不提供记录时保留缺失标记，不触发网络、观察器或额外采样。
      }
      facts.performance = perf;
    }
    return evaluateFacts(facts, options);
  }

  const api = { collect, evaluateFacts };
  if (typeof module === 'object' && module !== null && module.exports) module.exports = api;
  return api;
})()
