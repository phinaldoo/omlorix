const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const vm = require('node:vm');

const rendererRoot = path.join(__dirname, '..', 'renderer');

/** Exercise the production environment-to-form mapping with external storage. */
function hydrateExternalStorage(source) {
  const start = source.indexOf('  function hydrateForm(data) {');
  const end = source.indexOf('\n\n  function appendVersionOption(', start);
  assert(start >= 0 && end > start, 'hydrateForm must exist');
  const hydrateSource = source.slice(start, end);
  const elements = new Map();
  const els = new Proxy({}, {
    get(_target, property) {
      if (!elements.has(property)) {
        elements.set(property, { value: '', checked: false });
      }
      return elements.get(property);
    },
  });
  const context = {
    els,
    state: { availableVersionsChannel: 'stable' },
    loadAvailableVersions() {},
    renderVersionOptions() {},
    setChecked(element, value, defaultValue = false) {
      const normalized = String(value ?? '').trim().toLowerCase();
      element.checked = normalized ? normalized !== 'false' : Boolean(defaultValue);
    },
    setToggles() {},
    syncCustomSelect() {},
  };

  vm.runInNewContext(`${hydrateSource}\nglobalThis.hydrateForm = hydrateForm;`, context);
  context.hydrateForm({
    serverSettings: { updateChannel: 'stable' },
    env: {
      OMLORIX_USE_BUNDLED_STORAGE: 'false',
      FILE_STORAGE_PROVIDER: 'gcs',
      FILE_STORAGE_GCS_BUCKET: 'external-bucket',
      FILE_STORAGE_GCS_PROJECT: 'external-project',
    },
  });

  return {
    mode: els.fileStorageModeInput.value,
    provider: els.fileStorageProviderSelect.value,
    bucket: els.fileStorageGcsBucketInput.value,
    project: els.fileStorageGcsProjectInput.value,
  };
}

test('file storage exposes distinct local, bundled MinIO, and external modes', async () => {
  const [html, source] = await Promise.all([
    fs.readFile(path.join(rendererRoot, 'launcher.html'), 'utf8'),
    fs.readFile(path.join(rendererRoot, 'launcher.js'), 'utf8'),
  ]);

  assert.match(html, /name="fileStorageMode" value="local" data-storage-mode="local"/);
  assert.match(html, /name="fileStorageMode" value="bundled" data-storage-mode="bundled"/);
  assert.match(html, /name="fileStorageMode" value="external" data-storage-mode="external"/);
  const controls = html.match(/<input\b[^>]*\bname="fileStorageMode"[^>]*>/g) || [];
  assert.equal(controls.length, 3);
  controls.forEach((control) => assert.match(control, /\btype="radio"/));
  assert.doesNotMatch(html, /<option value="local">Local disk<\/option>/);
  assert.match(html, /<span class="field-label">External provider<\/span>/);
  assert.match(html, /Docker volume[\s\S]*<code>app_data<\/code>/);

  assert.match(source, /markSettingsChanged\('useBundledStorage', 'fileStorageProvider'\)/);
  assert.match(source, /fileStorageMode === 'bundled'[\s\S]*\? 's3'/);
  assert.match(source, /fileStorageMode === 'local'[\s\S]*\? 'local'/);
  assert.deepEqual(hydrateExternalStorage(source), {
    mode: 'external',
    provider: 'gcs',
    bucket: 'external-bucket',
    project: 'external-project',
  });
});
