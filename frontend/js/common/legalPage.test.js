const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(path.join(__dirname, 'legalPage.js'), 'utf8');

function resolveReducedMotion(operatingSystemPrefersReducedMotion) {
  const document = {};
  const window = {
    matchMedia(query) {
      assert.equal(query, '(prefers-reduced-motion: reduce)');
      return { matches: operatingSystemPrefersReducedMotion };
    },
  };

  vm.runInNewContext(SOURCE, { document, window }, { filename: 'legalPage.js' });
  return window.legalPageUtils.shouldReduceMotion();
}

test('the operating-system preference is used directly', () => {
  assert.equal(resolveReducedMotion(true), true);
  assert.equal(resolveReducedMotion(false), false);
});

test('table-of-contents links scroll to numeric and encoded fragment IDs', () => {
  const clickListeners = new Map();
  const targets = new Map([
    ['1-einleitung', { scrollCalls: [] }],
    ['uberblick-über-rechte', { scrollCalls: [] }],
  ]);
  const anchors = ['#1-einleitung', '#uberblick-%C3%BCber-rechte'].map((href) => ({
    getAttribute(name) {
      assert.equal(name, 'href');
      return href;
    },
    addEventListener(type, listener) {
      assert.equal(type, 'click');
      clickListeners.set(href, listener);
    },
    removeEventListener() {},
    classList: {
      toggle() {},
    },
  }));
  targets.forEach((target) => {
    target.scrollIntoView = (options) => target.scrollCalls.push(options);
  });

  const historyCalls = [];
  const document = {
    querySelectorAll(selector) {
      if (selector === '.toc-list a') return anchors;
      if (selector === '.section') return [];
      assert.fail(`Unexpected selector: ${selector}`);
    },
    getElementById(id) {
      if (id === 'scrollToTop') return null;
      return targets.get(id) || null;
    },
  };
  const window = {
    legalPageUtils: null,
    pageYOffset: 0,
    addEventListener() {},
    matchMedia() {
      return { matches: true };
    },
    removeEventListener() {},
  };
  const history = {
    pushState(_state, _unused, href) {
      historyCalls.push(href);
    },
  };

  vm.runInNewContext(SOURCE, {
    decodeURIComponent,
    document,
    history,
    window,
  }, { filename: 'legalPage.js' });

  window.legalPageUtils.initializeLegalInteractions();
  anchors.forEach((anchor) => {
    let defaultPrevented = false;
    clickListeners.get(anchor.getAttribute('href')).call(anchor, {
      preventDefault() {
        defaultPrevented = true;
      },
    });
    assert.equal(defaultPrevented, true);
  });

  for (const target of targets.values()) {
    assert.equal(target.scrollCalls.length, 1);
    assert.equal(target.scrollCalls[0].behavior, 'auto');
    assert.equal(target.scrollCalls[0].block, 'start');
  }
  assert.deepEqual(historyCalls, ['#1-einleitung', '#uberblick-%C3%BCber-rechte']);
});
