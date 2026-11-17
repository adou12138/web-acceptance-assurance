'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const observerPath = path.join(__dirname, '../scripts/browser_observer.js');
let observer;
try {
  observer = require(observerPath);
} catch (error) {
  if (error.code !== 'MODULE_NOT_FOUND' || fs.existsSync(observerPath)) throw error;
  observer = {};
}

function facts(overrides = {}) {
  return {
    url: 'https://example.test/screen?token=secret#private',
    title: '验收页面',
    viewport: { width: 800, height: 600 },
    renderedNodeCount: 4,
    hasBody: true,
    hasBodyContent: true,
    documentWidth: 800,
    extents: { left: 0, right: 800 },
    elements: [],
    ...overrides
  };
}

function codes(result) {
  return result.findings.map(item => item.code);
}

test('提供纯逻辑与只读收集入口', () => {
  assert.equal(typeof observer.evaluateFacts, 'function');
  assert.equal(typeof observer.collect, 'function');
});

test('拒绝无效输入和真假值字符串，不作隐式布尔转换', () => {
  assert.equal(typeof observer.evaluateFacts, 'function');
  for (const value of [null, [], 'facts']) {
    assert.throws(() => observer.evaluateFacts(value), TypeError);
  }
  assert.throws(() => observer.evaluateFacts(facts({ hasBody: 'false' })), TypeError);
  assert.throws(() => observer.evaluateFacts(facts({ viewport: { width: -1, height: 600 } })), TypeError);
  assert.throws(() => observer.evaluateFacts(facts({ elements: [{}] })), TypeError);
  assert.throws(() => observer.evaluateFacts(facts(), { collectPerformance: 'false' }), TypeError);
  assert.throws(() => observer.evaluateFacts(facts(), { expectedOrigin: '*' }), TypeError);
  assert.throws(() => observer.evaluateFacts(facts(), { maxElements: 0 }), TypeError);
});

test('干净事实只标observed，默认去除URL凭据查询片段，不宣称已通过', () => {
  const input = facts();
  const before = JSON.stringify(input);
  const result = observer.evaluateFacts(input);
  assert.equal(result.status, 'observed');
  assert.equal(result.page.url, 'https://example.test/screen');
  assert.equal(result.page.title, '验收页面');
  assert.equal(result.page.hasBodyContent, true);
  assert.deepEqual(result.findings, []);
  assert.deepEqual(result.confirmedBugs, []);
  assert.equal(result.performance.status, 'not-requested');
  assert.equal(JSON.stringify(input), before);
  assert.ok(result.limitations.length > 0);
});

test('期望origin不符时阻止目标检查，不能输出干净通过', () => {
  const result = observer.evaluateFacts(facts(), { expectedOrigin: 'https://other.test' });
  assert.equal(result.status, 'blocked');
  assert.equal(result.target.matches, false);
  assert.ok(codes(result).includes('wrong-target'));
  assert.equal(result.findings[0].classification, 'blocked');
  assert.throws(() => observer.evaluateFacts(facts(), { expectedOrigin: 'https://example.test/path' }), TypeError);
});

test('精确origin匹配且false保持false，空白页与无正文区分', () => {
  const blank = observer.evaluateFacts(facts({ hasBodyContent: false, renderedNodeCount: 1 }), {
    expectedOrigin: 'https://example.test'
  });
  assert.equal(blank.target.matches, true);
  assert.equal(blank.page.hasBodyContent, false);
  assert.ok(codes(blank).includes('blank-content'));
  assert.ok(codes(observer.evaluateFacts(facts({ hasBody: false, hasBodyContent: false }))).includes('missing-body'));
});

test('左右横向溢出分别记录，保持候选而不是确认bug', () => {
  const result = observer.evaluateFacts(facts({ documentWidth: 900, extents: { left: -20, right: 900 } }));
  const finding = result.findings.find(item => item.code === 'horizontal-overflow');
  assert.equal(finding.evidence.leftOverflow, 20);
  assert.equal(finding.evidence.rightOverflow, 100);
  assert.equal(finding.classification, 'candidate');
  assert.equal(finding.needsReview, true);
  assert.deepEqual(result.confirmedBugs, []);
});

test('可见断图与无名控件成为候选，隐藏与未加载图片不会误判断图', () => {
  const result = observer.evaluateFacts(facts({ elements: [
    { tag: 'img', visible: true, complete: true, naturalWidth: 0, hasImageSource: true },
    { tag: 'img', visible: false, complete: true, naturalWidth: 0, hasImageSource: true },
    { tag: 'img', visible: true, complete: false, naturalWidth: 0, hasImageSource: true },
    { tag: 'button', visible: true, interactive: true, hasAccessibleName: false },
    { tag: 'button', visible: true, interactive: true, hasAccessibleName: true }
  ] }));
  assert.equal(codes(result).filter(code => code === 'broken-image').length, 1);
  assert.equal(codes(result).filter(code => code === 'unnamed-control').length, 1);
});

test('合法ellipsis仍仅为待复核裁切候选，故意隐藏节点忽略', () => {
  const result = observer.evaluateFacts(facts({ elements: [
    { tag: 'p', visible: true, textClipped: true, ellipsis: true },
    { tag: 'p', visible: false, textClipped: true, intentionallyHidden: true },
    { tag: 'p', visible: true, textClipped: false }
  ] }));
  const findings = result.findings.filter(item => item.code === 'text-clipping');
  assert.equal(findings.length, 1);
  assert.equal(findings[0].classification, 'candidate');
  assert.equal(findings[0].evidence.ellipsis, true);
  assert.equal(findings[0].needsReview, true);
  assert.ok(findings[0].needsReviewReason.includes('省略'));
});

test('伪链接按候选记录，正常页内链接不报告假链接', () => {
  const result = observer.evaluateFacts(facts({ elements: [
    { tag: 'a', visible: true, hrefKind: 'placeholder' },
    { tag: 'a', visible: true, hrefKind: 'javascript' },
    { tag: 'a', visible: true, hrefKind: 'fragment' },
    { tag: 'a', visible: true, hrefKind: 'navigation' }
  ] }));
  assert.equal(codes(result).filter(code => code === 'placeholder-link').length, 2);
});

test('不透传输入值、正文、名称与href等未允许敏感字段', () => {
  const result = observer.evaluateFacts(facts({ elements: [
    { tag: 'input', visible: true, interactive: true, hasAccessibleName: true,
      value: 'private-value', text: 'private-text', name: 'private-name', href: 'private-href' }
  ], secret: 'private-top' }));
  const serialized = JSON.stringify(result);
  for (const secret of ['private-value', 'private-text', 'private-name', 'private-href', 'private-top']) {
    assert.equal(serialized.includes(secret), false);
  }
});

test('性能未采样明确missing，不以0伪造INP或达标', () => {
  const result = observer.evaluateFacts(facts(), { collectPerformance: true });
  assert.equal(result.performance.status, 'unavailable');
  assert.equal(result.performance.navigation, null);
  assert.equal(result.performance.inp.value, null);
  assert.equal(result.performance.inp.status, 'not-collected');
  assert.ok(result.performance.missing.includes('navigation'));
  assert.ok(result.performance.missing.includes('resources'));
});

test('性能只聚合已有navigation/resource样本，部分数据保持partial', () => {
  const result = observer.evaluateFacts(facts({ performance: {
    source: 'Performance API',
    navigation: { duration: 120, domContentLoadedEventEnd: 80, loadEventEnd: 100, responseStart: 20 },
    resources: { count: 2, durationTotal: 30, transferSizeTotal: 0, transferSizeKnownCount: 0 }
  } }), { collectPerformance: true });
  assert.equal(result.performance.navigation.duration, 120);
  assert.equal(result.performance.resources.count, 2);
  assert.equal(result.performance.status, 'partial');
  assert.equal(result.performance.coverage.navigation, 'available');
  assert.equal(result.performance.coverage.interaction, 'not-collected');
  assert.equal(result.performance.inp.value, null);
  assert.equal(result.performance.source, 'Performance API');
  const partial = observer.evaluateFacts(facts({ performance: { source: 'Performance API', resources: { count: 0 } } }),
    { collectPerformance: true });
  assert.equal(partial.performance.status, 'partial');
  assert.equal(partial.performance.navigation, null);
  assert.equal(partial.performance.resources.count, 0);
});

test('无样本、非法或尚未完成的导航耗时不作为有效零值', () => {
  const result = observer.evaluateFacts(facts({ performance: {
    source: 'Performance API', navigation: { duration: 0, loadEventEnd: 0, responseStart: -1 }, resources: { count: 0 }
  } }), { collectPerformance: true });
  assert.equal(result.performance.navigation.duration, null);
  assert.equal(result.performance.navigation.loadEventEnd, null);
  assert.equal(result.performance.navigation.responseStart, null);
});

test('扫描不完整明确incomplete且候选数不代表全页面结果', () => {
  const result = observer.evaluateFacts(facts({ scan: { totalNodes: 100, scannedNodes: 20, truncated: true } }));
  assert.equal(result.status, 'incomplete');
  assert.equal(result.coverage.truncated, true);
  assert.equal(result.coverage.scannedNodes, 20);
});

test('扫描覆盖元信息也采用白名单，拒绝嵌套敏感内容透传', () => {
  const result = observer.evaluateFacts(facts({
    scan: { totalNodes: 20, scannedNodes: 20, truncated: false, secret: 'private-scan' },
    focusedInput: { tag: 'input', visible: true, value: 'private-focus', required: false, invalid: true }
  }));
  assert.equal(JSON.stringify(result).includes('private-scan'), false);
  assert.equal(JSON.stringify(result).includes('private-focus'), false);
  assert.equal(result.focusedInput.required, false);
  assert.equal(result.focusedInput.invalid, true);
});

test('同一源码是独立evaluate表达式且不在浏览器环境写全局或日志', () => {
  assert.equal(fs.existsSync(observerPath), true);
  const source = fs.readFileSync(observerPath, 'utf8');
  const context = vm.createContext({ URL });
  const api = vm.runInContext(source, context);
  assert.equal(typeof api.collect, 'function');
  assert.equal(typeof api.evaluateFacts, 'function');
  assert.deepEqual(Object.keys(context), ['URL']);
  assert.equal(api.evaluateFacts(facts()).status, 'observed');
  assert.throws(() => api.collect(), /document|浏览器/);
});

function fakeElement(overrides = {}) {
  const attributes = overrides.attributes || {};
  const element = {
    localName: overrides.localName || 'button',
    type: overrides.type !== undefined ? overrides.type : (overrides.localName === 'button' ? 'submit' : 'text'),
    hidden: false,
    tabIndex: overrides.tabIndex !== undefined ? overrides.tabIndex : -1,
    isContentEditable: false,
    disabled: false,
    readOnly: false,
    required: false,
    textContent: overrides.textContent || '',
    labels: overrides.labels || [],
    childNodes: [],
    getAttribute: name => (name in attributes ? attributes[name] : null),
    hasAttribute: name => name in attributes,
    getBoundingClientRect: () => ({ left: 10, top: 10, right: 110, bottom: 50, width: 100, height: 40 }),
    querySelectorAll: () => []
  };
  if (overrides.properties) Object.assign(element, overrides.properties);
  return element;
}

function fakeDocument(elements) {
  const style = { display: 'block', visibility: 'visible', opacity: '1', overflowX: 'visible', overflowY: 'visible', textOverflow: '', webkitLineClamp: undefined };
  const win = {
    innerWidth: 800, innerHeight: 600, scrollX: 0,
    location: { href: 'https://example.test/page' },
    getComputedStyle: () => style,
    performance: null
  };
  const doc = {
    title: '页面',
    body: { contains: () => true, scrollWidth: 800 },
    documentElement: { clientWidth: 800, scrollWidth: 800 },
    activeElement: null,
    getElementsByTagName: () => elements,
    getElementById: () => null
  };
  return { doc, win };
}

test('无内容button默认type=submit仍有可辨名称判定为无名称，input提交按钮保留默认名', () => {
  const source = fs.readFileSync(observerPath, 'utf8');
  const api = vm.runInContext(source, vm.createContext({ URL }));
  const emptyButton = fakeElement({ localName: 'button', type: 'submit' });
  const textButton = fakeElement({ localName: 'button', type: 'button', textContent: '保存' });
  const inputSubmit = fakeElement({ localName: 'input', type: 'submit' });
  const { doc, win } = fakeDocument([emptyButton, textButton, inputSubmit]);
  const context = vm.createContext({ URL, document: doc, window: win });
  const result = vm.runInContext(source, context).collect({});
  const byTag = {};
  for (const item of result.candidates) byTag[item.tag + ':' + (item.type || '')] = item;
  assert.equal(byTag['button:submit'].hasAccessibleName, false, '无内容button不应因默认type=submit被视为有名称');
  assert.equal(byTag['button:button'].hasAccessibleName, true);
  assert.equal(byTag['input:submit'].hasAccessibleName, true, 'input提交按钮有浏览器默认名称');
  const findingEvidence = result.findings.filter(f => f.code === 'unnamed-control');
  assert.equal(findingEvidence.length, 1);
  assert.equal(findingEvidence[0].evidence.index, 0);
});

const BASE_STYLE = { display: 'block', visibility: 'visible', opacity: '1', overflowX: 'visible', overflowY: 'visible',
  textOverflow: '', webkitLineClamp: undefined, objectFit: 'fill' };

function elementOf(spec = {}) {
  const attributes = spec.attributes || {};
  return {
    localName: spec.localName || 'div',
    type: spec.type !== undefined ? spec.type : (spec.localName === 'button' ? 'submit' : 'text'),
    hidden: false,
    tabIndex: -1,
    isContentEditable: false,
    disabled: false,
    readOnly: false,
    required: false,
    complete: spec.complete === true,
    naturalWidth: spec.naturalWidth || 0,
    naturalHeight: spec.naturalHeight || 0,
    clientWidth: spec.clientWidth || 0,
    clientHeight: spec.clientHeight || 0,
    scrollWidth: spec.scrollWidth || 0,
    scrollHeight: spec.scrollHeight || 0,
    clientLeft: 0,
    clientTop: 0,
    scrollLeft: 0,
    scrollTop: 0,
    textContent: spec.textContent || '',
    labels: [],
    childNodes: [],
    contains: other => (spec.descendants || []).includes(other),
    querySelectorAll: () => spec.descendants || [],
    getAttribute: name => (name in attributes ? attributes[name] : null),
    hasAttribute: name => name in attributes,
    getBoundingClientRect: () => spec.rect || { left: 10, top: 10, right: 110, bottom: 50, width: 100, height: 40 }
  };
}

function pageOf({ elements, styles = new Map(), elementFromPoint, title }) {
  const fallback = Object.assign({}, BASE_STYLE);
  const win = {
    innerWidth: 800, innerHeight: 600, scrollX: 0, scrollY: 0,
    location: { href: 'https://example.test/page' },
    getComputedStyle: element => styles.get(element) || fallback,
    performance: { getEntriesByType: () => [] }
  };
  const doc = {
    title: title || '页面',
    body: { contains: () => true, scrollWidth: 800 },
    documentElement: { clientWidth: 800, scrollWidth: 800 },
    activeElement: null,
    getElementsByTagName: () => elements,
    getElementById: () => null,
    elementFromPoint
  };
  return { doc, win };
}

function collectWith(page, options = {}) {
  const source = fs.readFileSync(observerPath, 'utf8');
  return vm.runInContext(source, vm.createContext({ URL, document: page.doc, window: page.win })).collect(options);
}

test('祖先容器裁切、子元素持文本时仍产出截断候选', () => {
  const inner = elementOf({ localName: 'span', textContent: '嵌套元素里的超长关键文本不会完整显示',
    rect: { left: 10, top: 10, right: 298, bottom: 30, width: 288, height: 20 } });
  const wrapper = elementOf({ localName: 'div', textContent: '嵌套元素里的超长关键文本不会完整显示',
    clientWidth: 90, clientHeight: 20, scrollWidth: 288, scrollHeight: 20,
    rect: { left: 10, top: 10, right: 100, bottom: 30, width: 90, height: 20 }, descendants: [inner] });
  const styles = new Map([[wrapper, Object.assign({}, BASE_STYLE, { overflowX: 'hidden', overflowY: 'hidden' })]]);
  const result = collectWith(pageOf({ elements: [wrapper, inner], styles }));
  const finding = result.findings.find(item => item.code === 'text-clipping');
  assert.ok(finding, '嵌套文本裁切应产生候选而不是静默漏报');
  assert.equal(finding.evidence.viaDescendant, true);
  assert.equal(finding.classification, 'candidate');
});

test('交互控件中心点被其他元素接收时标记遮挡候选，命中自身不误报', () => {
  const hit = elementOf({ localName: 'div' });
  const covered = elementOf({ localName: 'button', textContent: '保存',
    rect: { left: 10, top: 10, right: 110, bottom: 50, width: 100, height: 40 } });
  const clear = elementOf({ localName: 'button', textContent: '取消',
    rect: { left: 200, top: 10, right: 300, bottom: 50, width: 100, height: 40 } });
  const result = collectWith(pageOf({ elements: [covered, clear], elementFromPoint: x => (x < 150 ? hit : clear) }));
  const findings = result.findings.filter(item => item.code === 'occluded-control');
  assert.equal(findings.length, 1);
  assert.equal(findings[0].evidence.occluderTag, 'div');
  assert.equal(findings[0].needsReview, true);
});

test('拉伸变形的图片成为候选，object-fit覆盖不机械判变形', () => {
  const stretch = elementOf({ localName: 'img', complete: true, naturalWidth: 400, naturalHeight: 200,
    rect: { left: 10, top: 10, right: 110, bottom: 110, width: 100, height: 100 } });
  const cover = elementOf({ localName: 'img', complete: true, naturalWidth: 400, naturalHeight: 200,
    rect: { left: 200, top: 10, right: 300, bottom: 60, width: 100, height: 50 } });
  const styles = new Map([[cover, Object.assign({}, BASE_STYLE, { objectFit: 'cover' })]]);
  const result = collectWith(pageOf({ elements: [stretch, cover], styles }));
  const findings = result.findings.filter(item => item.code === 'image-aspect-distortion');
  assert.equal(findings.length, 1);
  assert.ok(findings[0].evidence.deviation >= 0.49);
});

test('性能覆盖按族标注，导航与资源齐全也不冒充性能已测', () => {
  const result = observer.evaluateFacts(facts({ performance: {
    source: 'Performance API',
    navigation: { duration: 120, domContentLoadedEventEnd: 80, loadEventEnd: 100, responseStart: 20 },
    resources: { count: 2, durationTotal: 30, transferSizeTotal: 2048, transferSizeKnownCount: 2 },
    paint: { fcp: 45 },
    contentful: { lcp: 120 }
  } }), { collectPerformance: true });
  assert.equal(result.performance.status, 'partial');
  assert.equal(result.performance.coverage.navigation, 'available');
  assert.equal(result.performance.coverage.resources, 'available');
  assert.equal(result.performance.coverage.loading, 'available');
  assert.equal(result.performance.coverage.contentful, 'available');
  assert.equal(result.performance.coverage.interaction, 'not-collected');
  assert.equal(result.performance.coverage.layout, 'not-collected');
  assert.deepEqual(result.performance.notCollected, ['cls', 'inp', 'longtask']);
});

test('缺少viewport元信息时产出移动端缩放候选', () => {
  const result = collectWith(pageOf({ elements: [elementOf({ localName: 'div', textContent: '正文内容' })] }));
  const finding = result.findings.find(item => item.code === 'missing-viewport-meta');
  assert.ok(finding, '缺少viewport meta应产生候选而不是静默放过');
  assert.equal(finding.evidence.present, false);
  assert.equal(finding.classification, 'candidate');
  assert.equal(finding.needsReview, true);
});

test('viewport元信息未声明device-width时产出候选', () => {
  const meta = elementOf({ localName: 'meta', attributes: { name: 'viewport', content: 'width=1024' } });
  const result = collectWith(pageOf({ elements: [meta, elementOf({ localName: 'div' })] }));
  const finding = result.findings.find(item => item.code === 'missing-viewport-meta');
  assert.ok(finding);
  assert.equal(finding.evidence.present, true);
  assert.equal(finding.evidence.deviceWidth, false);
});

test('正确声明device-width时不产出viewport候选', () => {
  const meta = elementOf({ localName: 'meta', attributes: { name: 'viewport', content: 'width=device-width, initial-scale=1' } });
  const result = collectWith(pageOf({ elements: [meta] }));
  assert.equal(result.findings.filter(item => item.code === 'missing-viewport-meta').length, 0);
});

test('viewportMeta类型非法时拒绝求值', () => {
  assert.throws(() => observer.evaluateFacts(facts({ viewportMeta: 'oops' })), /viewportMeta/);
});

test('页面自述含强信号时标记为受控夹具并限制结论', () => {
  const result = collectWith(pageOf({ elements: [], title: '受控验收站：非用户生产网站' }));
  assert.equal(result.page.fixture.declared, true);
  assert.ok(result.page.fixture.declaredBy.includes('title'));
  assert.ok(result.page.fixture.keywords.includes('受控'));
  assert.ok(result.limitations.some(text => text.includes('不得直接作为产品缺陷上报')));
});

test('仅含弱信号时只记录关键词而不判定为夹具', () => {
  const result = collectWith(pageOf({ elements: [], title: '数据看板示例' }));
  assert.equal(result.page.fixture.declared, false);
  assert.equal(result.page.fixture.declaredBy.length, 0);
  assert.ok(result.page.fixture.keywords.includes('示例'));
  assert.equal(result.limitations.some(text => text.includes('不得直接作为产品缺陷上报')), false);
});

test('未自述夹具时输出空声明而不是缺字段', () => {
  const result = observer.evaluateFacts(facts());
  assert.deepEqual(result.page.fixture, { declared: false, declaredBy: [], keywords: [] });
});

test('data属性显式声明夹具时优先命中', () => {
  const page = pageOf({ elements: [] });
  page.doc.documentElement = { clientWidth: 800, scrollWidth: 800,
    getAttribute: name => (name === 'data-acceptance-fixture' ? 'true' : null) };
  const result = collectWith(page);
  assert.equal(result.page.fixture.declared, true);
  assert.ok(result.page.fixture.declaredBy.includes('attribute'));
});

test('meta声明夹具与正文强信号都能被采集到', () => {
  const meta = elementOf({ localName: 'meta', attributes: { name: 'acceptance-fixture', content: 'true' } });
  assert.ok(collectWith(pageOf({ elements: [meta] })).page.fixture.declaredBy.includes('meta'));

  const bodyPage = pageOf({ elements: [] });
  bodyPage.doc.body = { contains: () => true, scrollWidth: 800, textContent: '此面板包含故意预埋的取消行为错误' };
  const result = collectWith(bodyPage);
  assert.equal(result.page.fixture.declared, true);
  assert.ok(result.page.fixture.declaredBy.includes('body'));
});

test('fixture结构非法时拒绝求值', () => {
  assert.throws(() => observer.evaluateFacts(facts({ fixture: { declared: 'yes', declaredBy: [], keywords: [] } })), /fixture/);
  assert.throws(() => observer.evaluateFacts(facts({ fixture: { declared: true, declaredBy: 'title', keywords: [] } })), /fixture/);
  assert.throws(() => observer.evaluateFacts(facts({ fixture: { declared: true, declaredBy: [], keywords: [1] } })), /fixture/);
});
