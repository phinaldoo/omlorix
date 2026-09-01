const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const {
    SEND_MESSAGE_SCRIPT_URLS,
    readSendMessageSource,
} = require('./sending/source.cjs');

const SEND_MESSAGE_SOURCE = readSendMessageSource();
const I18N_ROOT = path.join(__dirname, '..', '..', 'i18n');
const CODE_BLOCK_KEYS = [
    'code_block_view_mode_label',
    'code_block_tab_code',
    'code_block_tab_preview',
    'code_block_reload_preview',
    'code_block_reloading_preview',
    'code_block_copy_code',
    'code_block_copy_code_success',
    'code_block_download_code',
    'code_block_downloaded_code',
    'code_block_collapse',
    'code_block_expand',
    'code_block_run',
    'code_block_running',
    'code_block_run_python',
    'code_block_run_python_stream_locked',
    'code_block_running_python',
    'visualization_preview_label',
    'visualization_preview_title',
    'visualization_preview_aria',
    'visualization_preview_empty',
    'visualization_preview_static_frame_title',
    'visualization_preview_interactive_frame_title',
    'code_block_open_large_preview',
    'canvas_html_preview_settings',
    'canvas_html_interactions',
    'canvas_html_external_content',
    'code_block_vega_external_origin_not_approved',
    'code_block_vega_external_fetch_blocked',
    'code_block_vega_external_fetch_failed',
    'code_block_vega_external_fetch_timeout',
    'code_block_vega_external_resource_too_large',
    'code_block_vega_external_resources_allow',
    'code_block_vega_external_resources_block',
    'code_block_vega_external_resources_cancelled',
    'code_block_vega_external_resources_desc',
    'code_block_vega_external_resources_remember',
    'code_block_vega_external_resources_review',
    'code_block_vega_external_resources_revoke',
    'code_block_vega_external_resources_title',
    'code_block_vega_external_scheme_unsupported',
    'visualization_followup_unavailable',
    'visualization_followup_invalid',
    'visualization_followup_title',
    'visualization_followup_desc',
    'visualization_followup_send',
    'visualization_external_data_unavailable',
    'visualization_external_data_invalid_url',
    'visualization_external_data_title',
    'visualization_external_data_desc',
    'visualization_external_data_allow',
    'visualization_external_data_block',
    'visualization_external_data_cancelled',
    'visualization_external_data_failed',
    'visualization_download_unavailable',
    'visualization_download_title',
    'visualization_download_desc',
    'visualization_download_allow',
    'visualization_download_too_large',
    'visualization_action_unsupported',
    'visualization_runtime_unavailable',
];

test('message sending scripts load completely and in dependency order', () => {
    for (const pageName of ['index.html', 'chat_share.html']) {
        const markup = fs.readFileSync(path.join(__dirname, '..', '..', pageName), 'utf8');
        const uncommentedMarkup = markup.replace(/<!--[\s\S]*?-->/g, '');
        const scriptSources = Array.from(
            uncommentedMarkup.matchAll(/<script\b[^>]*\bsrc\s*=\s*(["'])([^"']+)\1[^>]*>/gi),
            (match) => match[2],
        );
        const scriptIndexes = SEND_MESSAGE_SCRIPT_URLS.map((scriptUrl) => {
            assert.equal(
                scriptSources.filter((source) => source === scriptUrl).length,
                1,
                `${scriptUrl} must occur once in ${pageName}`,
            );
            return scriptSources.indexOf(scriptUrl);
        });

        assert.deepEqual(scriptIndexes, [...scriptIndexes].sort((left, right) => left - right), pageName);
    }
});

function extractFunction(source, functionName) {
    const start = source.indexOf(`function ${functionName}`);
    assert.notEqual(start, -1, `${functionName} not found`);

    const bodyStart = source.indexOf('{', start);
    assert.notEqual(bodyStart, -1, `${functionName} body start not found`);

    let depth = 0;
    for (let index = bodyStart; index < source.length; index += 1) {
        const char = source[index];
        if (char === '{') depth += 1;
        if (char === '}') {
            depth -= 1;
            if (depth === 0) {
                return source.slice(start, index + 1);
            }
        }
    }

    throw new Error(`${functionName} body was not closed`);
}

function loadCodePreviewKind() {
    const source = SEND_MESSAGE_SOURCE;
    const context = { result: null };

    vm.createContext(context);
    vm.runInContext(`
        function getVegaPreviewKind() { return ''; }
        function isMermaidLanguage() { return false; }
        function isHtmlPreviewLanguage() { return false; }
        function isMarkdownPreviewLanguage() { return false; }
        function isStructuredDataPreviewLanguage() { return false; }
        function isDelimitedPreviewLanguage() { return false; }
        ${extractFunction(source, 'getCodePreviewKind')}
        result = getCodePreviewKind;
    `, context);
    return context.result;
}

function loadMarkdownCodeProtectionHelpers() {
    const source = SEND_MESSAGE_SOURCE;
    const helpers = [
        'createMarkdownCodePlaceholder',
        'getMarkdownIndentColumns',
        'getPreviousMarkdownLine',
        'getPreviousNonBlankMarkdownLine',
        'getMarkdownListCodeIndent',
        'isMarkdownBlockBoundaryBeforeIndentedCode',
        'canStartIndentedMarkdownCodeBlock',
        'protectIndentedMarkdownCodeBlocks',
    ].map((name) => extractFunction(source, name)).join('\n');
    const context = { result: null };

    vm.createContext(context);
    vm.runInContext(`
        const MARKDOWN_CODE_PLACEHOLDER_PREFIX = 'MARKDOWNCODEPLACEHOLDER';
        const MARKDOWN_CODE_PLACEHOLDER_SUFFIX = 'ENDMARKDOWNCODE';
        ${helpers}
        result = { protectIndentedMarkdownCodeBlocks };
    `, context);
    return context.result;
}

function loadHtmlPreviewHelpers() {
    const source = SEND_MESSAGE_SOURCE;
    const helpers = [
        'isHtmlPreviewExternalReference',
        'analyzeHtmlPreviewCapabilities',
    ].map((name) => extractFunction(source, name)).join('\n');
    const context = { result: null };

    vm.createContext(context);
    vm.runInContext(`
        ${helpers}
        result = {
            analyzeHtmlPreviewCapabilities,
        };
    `, context);
    return context.result;
}

function loadVegaExternalResourceHelpers() {
    const source = SEND_MESSAGE_SOURCE;
    const helpers = [
        'normalizeVegaResourceReference',
        'collectVegaExternalResources',
        'getVegaExternalResourceSignature',
    ].map((name) => extractFunction(source, name)).join('\n');
    const context = {
        result: null,
        URL,
        document: { baseURI: 'https://omlorix.example/chats/1' },
        window: { location: { href: 'https://omlorix.example/chats/1' } },
    };

    vm.createContext(context);
    vm.runInContext(`
        const VEGA_RESOURCE_KEYS = new Set(['url', 'href', 'src']);
        ${helpers}
        result = {
            normalizeVegaResourceReference,
            collectVegaExternalResources,
            getVegaExternalResourceSignature,
        };
    `, context);
    return context.result;
}

function loadVegaPreviewLoader(authedFetch) {
    const source = SEND_MESSAGE_SOURCE;
    const helpers = [
        'normalizeVegaResourceReference',
        'VegaExternalResourcePermissionError',
        'createVegaPreviewLoader',
    ].map((name) => {
        if (name === 'VegaExternalResourcePermissionError') {
            const start = source.indexOf(`class ${name}`);
            const end = source.indexOf('\n\nfunction getVegaThemeConfig', start);
            assert.notEqual(start, -1, `${name} not found`);
            assert.notEqual(end, -1, `${name} end not found`);
            return source.slice(start, end);
        }
        return extractFunction(source, name);
    }).join('\n');
    const context = {
        AbortController,
        Blob,
        Response,
        TextDecoder,
        URL,
        authedFetch,
        clearTimeout,
        document: { baseURI: 'https://omlorix.example/chats/1' },
        result: null,
        setTimeout,
    };

    context.window = {
        authedFetch,
        location: {
            href: 'https://omlorix.example/chats/1',
            protocol: 'https:',
        },
    };
    vm.createContext(context);
    vm.runInContext(`
        const VEGA_EXTERNAL_RESOURCE_ENDPOINT = '/api/v1/chats/code-preview/vega/resource';
        const VEGA_EXTERNAL_REQUEST_TIMEOUT_MS = 15_000;
        const VEGA_EXTERNAL_RESPONSE_MAX_LENGTH = 10_000_000;
        function getChatPreviewTranslation(_key, fallback) { return fallback; }
        function formatChatPreviewTranslation(_key, fallback, values = {}) {
            return Object.entries(values).reduce(
                (message, [key, value]) => message.replaceAll('{' + key + '}', String(value)),
                fallback,
            );
        }
        ${helpers}
        result = { createVegaPreviewLoader };
    `, context);
    return context.result.createVegaPreviewLoader;
}

test('code block action source includes translated accessible button semantics', () => {
    const source = SEND_MESSAGE_SOURCE;

    assert.match(source, /function getCodeBlockActionA11yAttrs\(key, fallback\)/);
    assert.match(source, /function setCodeBlockCollapseButtonState\(button, isCollapsed\)/);
    assert.match(source, /<div class="code-block-mode-toggle" role="tablist" aria-label="\$\{escapeHtml\(codeBlockViewLabel\)\}" data-i18n-attr="aria-label:code_block_view_mode_label">/);
    assert.match(source, /<span data-i18n="code_block_tab_code">\$\{escapeHtml\(codeTabLabel\)\}<\/span>/);
    assert.match(source, /<span data-i18n="code_block_tab_preview">\$\{escapeHtml\(previewTabLabel\)\}<\/span>/);
    assert.match(source, /<button type="button" class="code-action-btn copy-code-btn"/);
    assert.match(source, /<button type="button" class="code-action-btn download-code-btn"/);
    assert.match(source, /<button type="button" class="code-action-btn collapse-code-btn"/);
    assert.match(source, /setCodeBlockActionButtonLabel\(button, 'code_block_reloading_preview', 'Reloading preview'\)/);
    assert.match(source, /setCodeBlockActionButtonLabel\(button, 'code_block_copy_code_success', 'Copied code'\)/);
    assert.match(source, /button\.setAttribute\('data-i18n-attr', `aria-label:\$\{key\};title:\$\{key\}`\)/);

    CODE_BLOCK_KEYS.forEach((key) => {
        assert.match(source, new RegExp(key));
    });
});

test('code block action translations exist in every supported locale', () => {
    const locales = fs.readdirSync(I18N_ROOT, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name);

    locales.forEach((locale) => {
        const file = path.join(I18N_ROOT, locale, 'index.json');
        const dictionary = JSON.parse(fs.readFileSync(file, 'utf8'));

        CODE_BLOCK_KEYS.forEach((key) => {
            assert.ok(
                Object.prototype.hasOwnProperty.call(dictionary, key),
                `${locale} is missing ${key}`,
            );
        });
    });
});

test('inline visualization previews default to static rendering until interactive mode is requested', () => {
    const source = SEND_MESSAGE_SOURCE;
    const cspMatch = source.match(/function buildVisualizerPreviewContentSecurityPolicy\(allowScripts = false\) \{([\s\S]*?)\n\}/);

    assert.ok(cspMatch, 'buildVisualizerPreviewContentSecurityPolicy not found');
    assert.match(cspMatch[1], /connect-src 'none';/);
    assert.doesNotMatch(cspMatch[1], /http:/);
    assert.match(source, /const ALLOWED_PREVIEW_ACTIONS = Object\.freeze\(\['expand', 'run-interactive'\]\);/);
    assert.match(source, /data-preview-action="run-interactive"/);
    assert.doesNotMatch(source, /view-source|openVisualizerSourceModal|visualizer-preview-source-btn/);
    assert.doesNotMatch(source, /function isVisualizerLanguage|openVisualizerPreviewModal|previewKind === 'visualizer'/);
    assert.doesNotMatch(source, /\b(?:visualizer|visualiser|visualize|visualise|llmviz|modelviz): 'html'/);
    assert.match(source, /function stripVisualizerAuthoredScripts\(source\)/);
    assert.match(source, /window\.OmlorixCanvasHtmlPreview/);
    assert.match(source, /proxyRuntime\.render\(iframe, previewDocument/);
    assert.match(source, /allowScripts: true,/);
    assert.match(source, /allowEval: false,/);
    assert.match(source, /allowExternalContent: allowScripts,/);
    assert.match(source, /trustedLocalScripts: !allowScripts,/);
    assert.match(source, /relayVisualizationMessages: true,/);
    assert.doesNotMatch(source, /iframe\.srcdoc = buildVisualizerPreviewDocument/);
    assert.match(source, /const sourceText = allowScripts \? rawSourceText : stripVisualizerAuthoredScripts\(rawSourceText\)/);
    assert.doesNotMatch(source, /shouldReduceVisualizerAnimations/);
    assert.match(source, /allowScripts: false,\s*\n\s*isModal: true,/);
    assert.match(
        source,
        /window\.OmlorixVisualizer = Object\.freeze\(\{\s*\n\s*mount: mountVisualizerPreview,/
    );
});

test('legacy visualization fences render as ordinary code blocks', () => {
    const getCodePreviewKind = loadCodePreviewKind();

    ['visualizer', 'visualiser', 'visualize', 'visualise', 'llmviz', 'modelviz'].forEach((language) => {
        assert.equal(getCodePreviewKind(language, '<div>Legacy visualization</div>'), '');
    });
});

test('visualization runtime asset URLs invalidate cached build failures', () => {
    const source = SEND_MESSAGE_SOURCE;

    assert.match(source, /const VISUALIZATION_RUNTIME_ASSET_VERSION = '[^']+';/);
    assert.match(source, /visualization-runtime\.css\?v=\$\{VISUALIZATION_RUNTIME_ASSET_VERSION\}/);
    assert.match(source, /d3\.min\.js\?v=\$\{VISUALIZATION_RUNTIME_ASSET_VERSION\}/);
    assert.match(source, /topojson-client\.min\.js\?v=\$\{VISUALIZATION_RUNTIME_ASSET_VERSION\}/);
    assert.match(source, /lucide\.min\.js\?v=\$\{VISUALIZATION_RUNTIME_ASSET_VERSION\}/);
});

test('visualization host bridge is capability-gated and keeps network access outside the iframe', () => {
    const source = SEND_MESSAGE_SOURCE;
    const cspBody = extractFunction(source, 'buildVisualizerPreviewContentSecurityPolicy');
    const hostBody = extractFunction(source, 'handleVisualizationHostRequest');
    const bridgeStart = source.indexOf('function buildVisualizerBridgeScript');
    const bridgeEnd = source.indexOf('\nfunction buildVisualizerPreviewDocument', bridgeStart);
    const bridgeBody = source.slice(bridgeStart, bridgeEnd);

    assert.match(cspBody, /connect-src 'none'/);
    assert.match(hostBody, /capabilities\.external_data !== true/);
    assert.match(hostBody, /capabilities\.chat_followup !== true/);
    assert.match(hostBody, /VEGA_EXTERNAL_RESOURCE_ENDPOINT/);
    assert.match(hostBody, /window\.showWarningConfirm/);
    assert.match(source, /function normalizeVisualizationCapabilitiesForSurface\(capabilities\)/);
    assert.match(source, /dataset\?\.page === 'chat-share'/);
    assert.match(bridgeBody, /window\.omlorix = Object\.freeze/);
    assert.match(bridgeBody, /sendFollowUpMessage/);
    assert.match(bridgeBody, /requestExternalData/);
    assert.doesNotMatch(bridgeBody, /window\.fetch/);
});

test('shared-chat visualizations remove authenticated host capabilities', () => {
    const source = SEND_MESSAGE_SOURCE;
    const helperSource = extractFunction(source, 'normalizeVisualizationCapabilitiesForSurface');
    const shared = vm.runInNewContext(
        `${helperSource}\nnormalizeVisualizationCapabilitiesForSurface({ scripts: true, external_data: true, chat_followup: true, download: true });`,
        { document: { body: { dataset: { page: 'chat-share' } } } },
    );

    assert.deepEqual(
        JSON.parse(JSON.stringify(shared)),
        { scripts: true, external_data: false, chat_followup: false, download: true },
    );
});

test('Vega previews use the AST interpreter required by the production CSP', () => {
    const source = SEND_MESSAGE_SOURCE;
    const functionStart = source.indexOf('async function renderVegaPreview');
    const functionEnd = source.indexOf('\nfunction hashCodeBlockSource', functionStart);

    assert.notEqual(functionStart, -1, 'renderVegaPreview not found');
    assert.notEqual(functionEnd, -1, 'renderVegaPreview end not found');
    const renderVegaPreview = source.slice(functionStart, functionEnd);

    // Vega's default expression compiler calls Function(), which is blocked by
    // the production script-src policy. The AST option makes Vega-Embed select
    // its bundled expression interpreter without weakening that policy.
    assert.match(renderVegaPreview, /\bast:\s*true,/);
});

test('Vega expand action lives in the code-block header without a duplicate preview badge', () => {
    const source = SEND_MESSAGE_SOURCE;

    assert.match(source, /class="code-action-btn vega-preview-expand-btn"/);
    assert.match(source, /openVegaPreviewModal\(wrapper\)/);
    assert.doesNotMatch(source, /vega-preview-badge/);
    assert.doesNotMatch(source, /vega-preview-toolbar/);
});

test('Vega external resources are grouped by exact origin while inline URLs stay local', () => {
    const {
        normalizeVegaResourceReference,
        collectVegaExternalResources,
        getVegaExternalResourceSignature,
    } = loadVegaExternalResourceHelpers();
    const collected = collectVegaExternalResources({
        data: [
            { name: 'remote', url: 'https://data.example/a.csv' },
            { name: 'remote-two', url: 'https://data.example/b.csv' },
            { name: 'relative', url: '/api/chart.csv' },
            { name: 'inline', url: 'data:text/csv,x%0A1' },
        ],
        href: 'https://links.example/item',
    });
    const sources = Array.from(collected.sources.values());

    assert.deepEqual(
        sources.map((item) => item.origin),
        ['https://data.example', 'https://links.example'],
    );
    assert.equal(sources[0].paths.length, 2, 'two URLs on one origin require only one grant');
    assert.equal(collected.unsupported.length, 0);
    assert.equal(normalizeVegaResourceReference('blob:preview').kind, 'inline');
    assert.equal(normalizeVegaResourceReference('file:///tmp/data.csv').kind, 'unsupported');
    assert.equal(
        getVegaExternalResourceSignature(sources),
        'https://data.example|https://links.example',
    );
});

test('Vega external consent is reversible and approved data uses the same-origin proxy', () => {
    const source = SEND_MESSAGE_SOURCE;
    const loaderBody = extractFunction(source, 'createVegaPreviewLoader');

    assert.match(source, /omlorix:vega-preview:external-resource-consent:v1/);
    assert.match(source, /data-action="toggle-vega-external-resources"/);
    assert.doesNotMatch(source, /vega-preview-external-resources-btn"[^>]*aria-pressed/);
    assert.match(source, /forgetSavedVegaExternalConsent\(signature\)/);
    assert.match(loaderBody, /window\.authedFetch/);
    assert.match(loaderBody, /VEGA_EXTERNAL_RESOURCE_ENDPOINT/);
    assert.match(loaderBody, /method:\s*'POST'/);
    assert.match(loaderBody, /credentials:\s*'same-origin'/);
    assert.doesNotMatch(loaderBody, /window\.fetch\(uri/);
    assert.match(loaderBody, /approvedOrigins\.has\(resource\.origin\)/);
    assert.match(loaderBody, /VegaExternalResourcePermissionError/);
    assert.match(source, /rememberCheckbox\.checked/);
    assert.match(source, /panel\.setAttribute\('aria-labelledby', titleId\)/);
    assert.match(source, /blockButton\.focus\?\./);
    assert.match(source, /retriedAfterDiscovery:\s*true/);
});

test('approved Vega data is loaded through the authenticated proxy at runtime', async () => {
    const requests = [];
    const createVegaPreviewLoader = loadVegaPreviewLoader(async (url, options) => {
        requests.push({ url, options });
        return new Response(JSON.stringify([{ Horsepower: 130 }]), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
        });
    });
    const vegaApi = {
        loader() {
            return {
                async sanitize(uri) { return { href: uri }; },
                async http(uri) { return uri; },
                async load() { return null; },
            };
        },
    };
    const loader = createVegaPreviewLoader(
        vegaApi,
        new Set(['https://vega.github.io']),
    );

    const data = await loader.http(
        'https://vega.github.io/vega-datasets/data/cars.json',
        { response: 'json' },
    );

    // The loader runs in a VM context, so compare the serialized payload
    // instead of requiring cross-realm Array/Object prototypes to match.
    assert.equal(JSON.stringify(data), JSON.stringify([{ Horsepower: 130 }]));
    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, '/api/v1/chats/code-preview/vega/resource');
    assert.equal(requests[0].options.method, 'POST');
    assert.equal(requests[0].options.credentials, 'same-origin');
    assert.deepEqual(
        JSON.parse(requests[0].options.body),
        { url: 'https://vega.github.io/vega-datasets/data/cars.json' },
    );
});

test('markdown click handling does not call a generation-local finalizer', () => {
    const source = SEND_MESSAGE_SOURCE;
    const clickHandler = extractFunction(source, 'handleMarkdownClick');

    assert.doesNotMatch(clickHandler, /finalizeGenerationState/);
});

test('code blocks reset async preview state before cross-surface HTML transfer', () => {
    const source = SEND_MESSAGE_SOURCE;
    const transferBody = extractFunction(source, 'prepareMarkdownCodeBlocksForTransfer');

    assert.match(transferBody, /cleanupMarkdownCodeBlockPreviews\(root\)/);
    assert.match(transferBody, /setCodeBlockView\(wrapper, 'code', \{ skipStatePersist: true \}\)/);
    assert.match(transferBody, /previewPane\.dataset\.previewState = 'idle'/);
    assert.match(source, /window\.prepareMarkdownCodeBlocksForTransfer = prepareMarkdownCodeBlocksForTransfer/);
});

test('HTML preview settings detect which switches apply to the source', () => {
    const { analyzeHtmlPreviewCapabilities } = loadHtmlPreviewHelpers();

    const staticTemplate = analyzeHtmlPreviewCapabilities(`
        <main><h1>Confirmation code</h1><a href="https://example.com/help">Help</a></main>
    `);
    assert.equal(staticTemplate.scripts, false);
    assert.equal(staticTemplate.externalContent, false, 'ordinary links do not load external content automatically');

    const scriptedTemplate = analyzeHtmlPreviewCapabilities(`
        <button onclick="showCode()">Show code</button><script>function showCode() {}</script>
    `);
    assert.equal(scriptedTemplate.scripts, true);
    assert.equal(scriptedTemplate.externalContent, true, 'arbitrary scripts can navigate their own frame');

    const hiddenNavigation = analyzeHtmlPreviewCapabilities(`
        <script>location.href = atob('aHR0cHM6Ly9hdHRhY2tlci5leGFtcGxl')</script>
    `);
    assert.equal(hiddenNavigation.scripts, true);
    assert.equal(hiddenNavigation.externalContent, true, 'security must not depend on finding a literal URL');

    const remoteTemplate = analyzeHtmlPreviewCapabilities(`
        <link rel="stylesheet" href="https://cdn.example.com/mail.css">
        <img src="https://cdn.example.com/logo.png" alt="Logo">
    `);
    assert.equal(remoteTemplate.scripts, false);
    assert.equal(remoteTemplate.externalContent, true);

    const remoteScript = analyzeHtmlPreviewCapabilities('<script src="https://cdn.example.com/app.js"></script>');
    assert.equal(remoteScript.scripts, true);
    assert.equal(remoteScript.externalContent, true);

    const embeddedAssets = analyzeHtmlPreviewCapabilities('<img src="data:image/png;base64,AA=="><style>.x{background:url(blob:preview)}</style>');
    assert.equal(embeddedAssets.externalContent, false);

    const inertExamples = analyzeHtmlPreviewCapabilities(`
        <!-- <script src="https://example.com/example.js"></script> -->
        <script type="application/json">{"onclick": "example"}</script>
        <pre>onclick="example()" url(https://example.com)</pre>
    `);
    assert.equal(inertExamples.scripts, false);
    assert.equal(inertExamples.externalContent, false);
});

test('HTML previews use the isolated proxy runtime and accessible settings switches', () => {
    const source = SEND_MESSAGE_SOURCE;
    assert.match(source, /class="code-action-btn code-block-html-settings-trigger"/);
    assert.match(source, /class="code-block-html-settings-menu"[^>]*role="dialog"/);
    assert.match(source, /class="toggle-input html-preview-capability-toggle html-preview-scripts-toggle"[^>]*role="switch"/);
    assert.match(source, /class="toggle-input html-preview-capability-toggle html-preview-external-content-toggle"[^>]*role="switch"/);
    assert.match(source, /document\.addEventListener\('change', handleMarkdownChange/);
    assert.match(source, /setHtmlPreviewSettingsMenuOpen\(wrapper, false, \{ restoreFocus: true \}\)/);
    assert.doesNotMatch(source, /html-preview-capability-btn/);
    assert.match(source, /wrapper\.dataset\.htmlPreviewScripts = 'false'/);
    assert.match(source, /event\.source !== frame\.contentWindow/);

    // This function has an object-literal default parameter (`options = {}`),
    // which looks like the body opener to the intentionally simple generic
    // extractor used elsewhere in this file. Slice to the next declaration so
    // this assertion covers the complete integration instead of the signature.
    const mountStart = source.indexOf('function mountHtmlCodePreview');
    const mountEnd = source.indexOf('\nfunction buildVisualizerPreviewContentSecurityPolicy', mountStart);
    assert.notEqual(mountStart, -1, 'mountHtmlCodePreview not found');
    assert.notEqual(mountEnd, -1, 'mountHtmlCodePreview boundary not found');
    const mountFunction = source.slice(mountStart, mountEnd);
    assert.match(mountFunction, /window\.OmlorixCanvasHtmlPreview/);
    assert.match(mountFunction, /runtime\.render\(iframe, source/);
    assert.match(mountFunction, /allowScripts,/);
    assert.match(mountFunction, /allowExternalContent,/);
    assert.match(mountFunction, /options\.allowScripts === true[\s\S]*&& allowExternalContent/);
    assert.match(mountFunction, /hydrateAuthenticatedFiles: false/);
    assert.doesNotMatch(mountFunction, /buildHtmlPreviewDocument/);
    assert.doesNotMatch(mountFunction, /iframe\.setAttribute\('sandbox', allowScripts/);
    assert.doesNotMatch(mountFunction, /code-block-html-preview-toolbar/);
    assert.doesNotMatch(mountFunction, /run-html-preview/);
});

test('every page using the shared Markdown renderer loads the HTML proxy runtime first', () => {
    const pages = ['index.html', 'chat_share.html'];

    pages.forEach((pageName) => {
        const html = fs.readFileSync(path.join(__dirname, '..', '..', pageName), 'utf8');
        const runtimeIndex = html.indexOf('/js/common/canvasHtmlPreview.js');
        const rendererIndex = html.indexOf('/js/chat/composer/state-and-transport.js');

        assert.notEqual(runtimeIndex, -1, `${pageName} is missing the HTML preview runtime`);
        assert.ok(runtimeIndex < rendererIndex, `${pageName} must load the HTML preview runtime before the message renderer scripts`);
    });
});

test('LaTeX protection skips literal markdown code examples', () => {
    const source = SEND_MESSAGE_SOURCE;
    const renderMatch = source.match(/md\.render = function \(src, env\) \{([\s\S]*?)\n    \};/);

    assert.ok(renderMatch, 'markdown render override not found');
    assert.match(source, /function protectFencedMarkdownCodeBlocks\(text, segments\)/);
    assert.match(source, /function protectIndentedMarkdownCodeBlocks\(text, segments\)/);
    assert.match(source, /function protectInlineMarkdownCodeSpans\(text, segments\)/);

    const renderBody = renderMatch[1];
    const protectCodeIndex = renderBody.indexOf('protectMarkdownCodeSegments(preprocessedSrc)');
    const protectLatexIndex = renderBody.indexOf('protectLatexBlocks(codeProtectedSrc)');
    const restoreCodeIndex = renderBody.indexOf('restoreMarkdownCodeSegments(protectedSrc, codeSegments)');
    const baseRenderIndex = renderBody.indexOf('baseRender(markdownSource, env)');

    assert.ok(protectCodeIndex !== -1, 'markdown code segments must be shielded first');
    assert.ok(protectLatexIndex > protectCodeIndex, 'LaTeX protection must run after code shielding');
    assert.ok(restoreCodeIndex > protectLatexIndex, 'markdown code must be restored after LaTeX protection');
    assert.ok(baseRenderIndex > restoreCodeIndex, 'markdown-it must receive restored code segments');
});

test('indented markdown shielding leaves nested list and blockquote math visible to LaTeX protection', () => {
    const { protectIndentedMarkdownCodeBlocks } = loadMarkdownCodeProtectionHelpers();
    const segments = new Map();
    const source = [
        '1. Nested list item',
        '    $x + y$',
        '> Quoted math',
        '    $a + b$',
        '',
    ].join('\n');

    const protectedSource = protectIndentedMarkdownCodeBlocks(source, segments);

    assert.equal(protectedSource, source);
    assert.equal(segments.size, 0);
});

test('indented markdown shielding still protects standalone indented code blocks', () => {
    const { protectIndentedMarkdownCodeBlocks } = loadMarkdownCodeProtectionHelpers();
    const segments = new Map();
    const source = [
        'Before',
        '',
        '    const formula = "$x + y$";',
        '',
    ].join('\n');

    const protectedSource = protectIndentedMarkdownCodeBlocks(source, segments);

    assert.match(protectedSource, /MARKDOWNCODEPLACEHOLDER0ENDMARKDOWNCODE/);
    assert.equal(segments.get('MARKDOWNCODEPLACEHOLDER0ENDMARKDOWNCODE'), '    const formula = "$x + y$";\n');
});
