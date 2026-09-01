const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const launcherInitSource = fs.readFileSync(
  path.join(__dirname, '..', 'renderer', 'launcher-init.js'),
  'utf8',
);
const launcherStyles = fs.readFileSync(
  path.join(__dirname, '..', 'renderer', 'launcher.css'),
  'utf8',
);
const launcherMainSource = fs.readFileSync(
  path.join(__dirname, '..', 'main.js'),
  'utf8',
);

/** Create the small DOM surface needed to exercise launcher navigation. */
function createNavigationHarness() {
  let readyHandler;
  let focusedElement = null;
  const clickHandlers = new Map();
  const activeClasses = new Map();

  function createElement({
    id = '',
    section = '',
    parentSection = '',
    openSection = '',
    sectionFocus = '',
  } = {}) {
    const attributes = new Map();
    const dataset = {};
    if (section) dataset.section = section;
    if (parentSection) dataset.parentSection = parentSection;
    if (openSection) dataset.openSection = openSection;
    if (sectionFocus) dataset.sectionFocus = sectionFocus;
    const element = {
      id,
      dataset,
      scrollTop: 0,
      classList: {
        toggle(name, enabled) {
          activeClasses.set(element, { name, enabled });
        },
      },
      setAttribute(name, value) {
        attributes.set(name, value);
      },
      removeAttribute(name) {
        attributes.delete(name);
      },
      addEventListener(name, handler) {
        if (name === 'click') clickHandlers.set(element, handler);
      },
      focus() {
        focusedElement = element;
      },
    };
    return element;
  }

  const statusLink = createElement({ section: 'status' });
  const settingsLink = createElement({ section: 'settings' });
  const statusSection = createElement({ id: 'status' });
  const settingsSection = createElement({ id: 'settings' });
  const storageMigrationSection = createElement({
    id: 'storage-migration',
    parentSection: 'settings',
  });
  const storageMigrationLabel = createElement({ id: 'storageMigrationLabel' });
  const openStorageMigrationButton = createElement({
    id: 'openStorageMigrationButton',
    openSection: 'storage-migration',
    sectionFocus: 'storageMigrationLabel',
  });
  const storageMigrationBackButton = createElement({
    id: 'storageMigrationBackButton',
    openSection: 'settings',
    sectionFocus: 'openStorageMigrationButton',
  });
  const contentPanel = createElement();
  const documentScroller = createElement();
  const documentElement = createElement();
  const sessionValues = new Map();
  const elementsById = new Map([
    [storageMigrationLabel.id, storageMigrationLabel],
    [openStorageMigrationButton.id, openStorageMigrationButton],
    [storageMigrationBackButton.id, storageMigrationBackButton],
  ]);

  const document = {
    documentElement,
    scrollingElement: documentScroller,
    addEventListener(name, handler) {
      if (name === 'DOMContentLoaded') readyHandler = handler;
    },
    querySelector(selector) {
      return selector === '.app-content' ? contentPanel : null;
    },
    querySelectorAll(selector) {
      if (selector === '.sidebar-nav .nav-link') return [statusLink, settingsLink];
      if (selector === '.content-section') {
        return [statusSection, settingsSection, storageMigrationSection];
      }
      if (selector === '[data-open-section]') {
        return [openStorageMigrationButton, storageMigrationBackButton];
      }
      return [];
    },
    getElementById(id) {
      return elementsById.get(id) || null;
    },
  };

  const context = {
    document,
    localStorage: { getItem: () => null },
    sessionStorage: {
      getItem(key) {
        return sessionValues.get(key) || null;
      },
      setItem(key, value) {
        sessionValues.set(key, value);
      },
    },
    window: {
      addEventListener() {},
      matchMedia: () => ({ matches: false, addEventListener() {} }),
    },
  };

  vm.runInNewContext(launcherInitSource, context);
  readyHandler();

  return {
    clickSettings() {
      clickHandlers.get(settingsLink)({ preventDefault() {} });
    },
    clickOpenStorageMigration() {
      clickHandlers.get(openStorageMigrationButton)({ preventDefault() {} });
    },
    clickStorageMigrationBack() {
      clickHandlers.get(storageMigrationBackButton)({ preventDefault() {} });
    },
    isActive(element) {
      return activeClasses.get(element)?.enabled === true;
    },
    get focusedElement() {
      return focusedElement;
    },
    openStorageMigrationButton,
    settingsLink,
    settingsSection,
    storageMigrationLabel,
    storageMigrationSection,
    contentPanel,
    documentScroller,
  };
}

test('switching launcher sections resets panel and document scroll positions', () => {
  const harness = createNavigationHarness();
  harness.contentPanel.scrollTop = 640;
  harness.documentScroller.scrollTop = 320;

  harness.clickSettings();

  assert.equal(harness.contentPanel.scrollTop, 0);
  assert.equal(harness.documentScroller.scrollTop, 0);
});

test('launcher window can reach its compact responsive layout', () => {
  assert.match(launcherMainSource, /minWidth:\s*520,/);
  assert.match(launcherStyles, /@media \(max-width:\s*560px\)/);
});

test('storage migration opens as a Settings detail page and restores focus on return', () => {
  const harness = createNavigationHarness();

  harness.clickOpenStorageMigration();

  assert.equal(harness.isActive(harness.storageMigrationSection), true);
  assert.equal(harness.isActive(harness.settingsSection), false);
  assert.equal(harness.isActive(harness.settingsLink), true);
  assert.equal(harness.focusedElement, harness.storageMigrationLabel);

  harness.clickStorageMigrationBack();

  assert.equal(harness.isActive(harness.storageMigrationSection), false);
  assert.equal(harness.isActive(harness.settingsSection), true);
  assert.equal(harness.focusedElement, harness.openStorageMigrationButton);
});

test('compact launcher header stays consistent below 900 pixels', () => {
  const compactStyles = launcherStyles.slice(
    launcherStyles.lastIndexOf('@media (max-width: 900px)'),
  );

  assert.match(compactStyles, /\.sidebar-header\s*{\s*display:\s*none;/);
  assert.match(compactStyles, /\.sidebar-footer\s*{[^}]*align-items:\s*stretch;[^}]*flex-wrap:\s*nowrap;/s);
  assert.match(compactStyles, /\.sidebar-actions\s*>\s*\.btn-primary\s*{\s*width:\s*100%;/);
});

test('launcher sections open and switch without a page animation', () => {
  assert.doesNotMatch(launcherStyles, /launcher-fade-in/);
});
