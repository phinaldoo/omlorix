const fs = require('node:fs');
const path = require('node:path');
const { app, BrowserWindow } = require('electron');

app.whenReady().then(async () => {
    const browser = new BrowserWindow({ show: false, webPreferences: { contextIsolation: true, nodeIntegration: false } });
    await browser.loadURL('data:text/html,<html><body></body></html>');
    const root = path.resolve(__dirname, '../../..');
    for (const sourcePath of [
        'common/dependencyUtils.js', 'admin/helper/fieldLayout.js', 'admin/helper/selectControls.js',
        'admin/mediaGenerationCommon.js', 'common/publicUsers.js', 'common/textPreview.js',
    ]) {
        await browser.webContents.executeJavaScript(fs.readFileSync(path.join(root, 'frontend/js', sourcePath), 'utf8') + '\n;void 0;');
    }
    const result = await browser.webContents.executeJavaScript(`(${async function () {
        function check(condition, message) { if (!condition) throw new Error(message); }
        const same = (actual, expected, message) => check(JSON.stringify(actual) === JSON.stringify(expected), message);
        const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
        window.getTranslation = (key, fallback) => ({ common_load_more: 'Mehr laden', chat_load_retry: 'Erneut versuchen' })[key] || fallback;
        window.helperT = window.getTranslation;

        // Shared schema constraints distinguish string lengths from numeric bounds.
        const textInput = document.createElement('input');
        applyFieldAttributesToControl(textInput, { attributes: { min: 2, max: 12 } });
        check(textInput.minLength === 2 && textInput.maxLength === 12 && !textInput.min, 'text constraints');
        const numberInput = document.createElement('input');
        numberInput.type = 'number';
        applyFieldAttributesToControl(numberInput, { attributes: { min: 0, max: 2, step: 0.25 } });
        check(numberInput.min === '0' && numberInput.max === '2' && numberInput.step === '0.25', 'number constraints');
        const section = createSchemaSection({ title: '<Model>', description: 'Help' }, ['provider-settings-section']);
        check(section.sectionEl.querySelector('h3').textContent === '<Model>' && !section.sectionEl.querySelector('model'), 'safe section text');
        check(section.bodyEl.parentElement === section.sectionEl && section.sectionEl.classList.contains('provider-settings-section'), 'section shell');
        check(SchemaDependencyUtils.matchesDependencyValue(['a'], ['a', 'b']), 'array dependencies');
        check(!SchemaDependencyUtils.matchesDependencyValue('false', false), 'boolean dependency types');

        // Provider suggestions retain input events, manual values, and accessible select upgrades.
        let syncs = 0;
        window.upgradeAdminSingleSelect = (select) => {
            check(select.getAttribute('aria-label') === 'Suggested provider URL', 'suggestion accessible name');
            return { syncFromSelect: () => { syncs += 1; } };
        };
        const input = document.createElement('input');
        input.value = 'https://provider.test/v1/';
        const events = [];
        input.addEventListener('input', () => events.push('input'));
        input.addEventListener('change', () => events.push('change'));
        const wrapper = createProviderUrlSuggestionSelect({ metadata: { provider_url_suggestions: [
            { name: 'Default', url: 'https://provider.test/v1' }, { name: 'Region', url: 'https://region.test/v1' }, { name: '', url: '' },
        ] } }, input);
        const select = wrapper.querySelector('select');
        check(select.options.length === 3 && select.value === 'https://provider.test/v1', 'URL normalization');
        select.value = 'https://region.test/v1';
        select.dispatchEvent(new Event('change'));
        same(events, ['input', 'change'], 'provider field events');
        input.value = 'https://custom.test';
        input.dispatchEvent(new Event('input'));
        check(select.value === '__custom__' && syncs > 1, 'manual URL synchronization');

        // Media controls preserve arrays, false strings, null numbers, and request cancellation.
        const UI = window.MediaGenerationUI;
        const field = { key: 'formats', type: 'select', multiple: true, options: [{ value: 'png', label: 'PNG' }, { value: 'webp', label: 'WebP' }] };
        const { valueControl } = UI.buildFieldControl(field);
        const values = {};
        let changed = 0;
        UI.bindFieldValue(field, valueControl, ['webp'], values, () => { changed += 1; });
        same(values.formats, ['webp'], 'initial multi-select');
        valueControl.options[0].selected = true;
        valueControl.dispatchEvent(new Event('change'));
        same(values.formats, ['png', 'webp'], 'changed multi-select');
        check(changed === 1, 'one change notification');
        const toggle = UI.buildFieldControl({ type: 'boolean' }).valueControl;
        UI.applyFieldValue({ type: 'boolean' }, toggle, 'false');
        check(!UI.readFieldValue({ type: 'boolean' }, toggle), 'false string coercion');
        UI.buildSettingsRow({ title: 'Enable audio', control: toggle });
        check(toggle.getAttribute('aria-label') === 'Enable audio', 'media field accessible name');
        check(UI.readFieldValue({ type: 'number' }, { value: '' }) === null, 'empty numeric value');
        let signal = new AbortController().signal;
        let seenRequest;
        window.authedFetch = async (url, init) => { seenRequest = { url, init }; return new Response('{}'); };
        const api = UI.createApiClient(() => signal);
        await api('/settings/music_generation', { method: 'POST', body: { enabled: false } });
        check(seenRequest.init.signal === signal && seenRequest.init.headers['Content-Type'] === 'application/json', 'media signal and JSON header');
        same(JSON.parse(seenRequest.init.body), { enabled: false }, 'media body');
        signal = new AbortController().signal;
        await api('/settings/image_generation');
        check(seenRequest.init.signal === signal, 'signal is resolved for every request');

        // Streamed previews decode split UTF-8, respect range headers, and cancel oversized bodies.
        const encoded = new TextEncoder().encode('A€B');
        const stream = new ReadableStream({ start(controller) {
            controller.enqueue(encoded.slice(0, 2)); controller.enqueue(encoded.slice(2)); controller.close();
        } });
        same(await TextPreview.readTextPreviewContent(new Response(stream), 20), { text: 'A€B', truncated: false }, 'split UTF-8');
        let cancelled = false;
        const large = new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode('abcdef')); }, cancel() { cancelled = true; } });
        same(await TextPreview.readTextPreviewContent(new Response(large), 3), { text: 'abc', truncated: true }, 'bounded preview');
        check(cancelled && !large.locked, 'stream cancelled and unlocked');
        const partial = new Response('abc', { status: 206, headers: { 'Content-Range': 'bytes 0-2/999' } });
        check((await TextPreview.readTextPreviewContent(partial, 20)).truncated, 'range truncation');
        same(await TextPreview.readTextPreviewContent({ blob: async () => new Blob(['abcdef']) }, 3), { text: 'abc', truncated: true }, 'blob fallback');

        // Directory requests are bounded, searchable, deduplicated, and advance by raw page length.
        const page = await PublicUsers.fetchPage({ q: ' Ada ', limit: 10000, offset: 5, request: async (url) => {
            const query = new URL(url, 'https://example.test').searchParams;
            check(query.get('q') === 'Ada' && query.get('limit') === '100' && query.get('offset') === '5', 'bounded query');
            return new Response(JSON.stringify([{ id: 'a' }, { id: 'a' }, { id: '' }]), { headers: { 'X-Has-More': 'true' } });
        } });
        same(page, { users: [{ id: 'a' }], nextOffset: 8, hasMore: true }, 'directory page');

        // A picker fetches on demand, retains selected chips, and ignores obsolete responses.
        const list = document.createElement('div');
        document.body.appendChild(list);
        const pending = [];
        let known = [{ id: 'selected' }];
        const picker = PublicUsers.createPicker({
            list,
            fetchPage: (options) => new Promise((resolve, reject) => pending.push({ options, resolve, reject })),
            render: (users) => { list.replaceChildren(...users.map((user) => {
                const button = document.createElement('button'); button.dataset.userId = user.id; return button;
            })); },
            onUsers: (users) => { known = users; },
            selectedUsers: () => known.filter((user) => user.id === 'selected'),
            loadingMessage: 'Loading', errorMessage: 'Failed',
        });
        const initial = picker.search('', { immediate: true });
        pending[0].resolve({ users: [{ id: 'a' }], nextOffset: 1, hasMore: true });
        await initial;
        check(pending.length === 1, 'no eager pagination');
        check(list.lastElementChild.textContent === 'Mehr laden', 'translated load more');
        list.lastElementChild.click();
        check(pending[1].options.offset === 1, 'next page offset');
        pending[1].resolve({ users: [{ id: 'a' }, { id: 'b' }], nextOffset: 3, hasMore: false });
        await tick();
        same(known.map((user) => user.id), ['selected', 'a', 'b'], 'page deduplication and selected retention');
        check(document.activeElement.dataset.userId === 'b', 'load more restores keyboard focus');
        const oldSearch = picker.search('old', { immediate: true });
        const newSearch = picker.search('new', { immediate: true });
        check(pending[2].options.signal.aborted, 'obsolete search aborted');
        pending[3].resolve({ users: [{ id: 'new' }], nextOffset: 1, hasMore: false });
        await newSearch;
        pending[2].resolve({ users: [{ id: 'old' }], nextOffset: 1, hasMore: false });
        await oldSearch;
        same(known.map((user) => user.id), ['selected', 'new'], 'stale response ignored');
        const failedSearch = picker.search('failure', { immediate: true });
        pending[4].reject(new Error('Unavailable'));
        await failedSearch;
        check(list.querySelector('[role="alert"]').textContent === 'Unavailable' && list.lastElementChild.textContent === 'Erneut versuchen', 'accessible retry');
        picker.search('debounced');
        picker.dispose();
        await new Promise((resolve) => setTimeout(resolve, 280));
        check(pending.length === 5 && !list.hasAttribute('aria-busy'), 'dispose cancels pending debounce');
        return { status: 'passed' };
    }} )()`);
    process.stdout.write(JSON.stringify(result));
    browser.destroy();
    app.quit();
}).catch((error) => {
    process.stderr.write(error.stack || String(error));
    app.exit(1);
});
