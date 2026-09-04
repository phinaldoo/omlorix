const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const repositoryRoot = path.resolve(__dirname, '..', '..', '..');
const iconsPath = path.join(__dirname, 'icons.js');
const providerBrandManifestPath = path.join(
    repositoryRoot,
    'third_party_assets_manifest/provider-brand-assets.manifest.json',
);
const htmlFiles = [
    'electron/renderer/launcher.html',
    'frontend/admin.html',
    'frontend/error.html',
    'frontend/index.html',
    'frontend/leaderboard.html',
    'frontend/login.html',
    'frontend/server_setup.html',
];

const normalizedSemanticIconNames = [
    'appearance',
    'callDuration',
    'checkCircle',
    'chevronsLeft',
    'chip',
    'currencyDollar',
    'email',
    'externalLink',
    'eyeOff',
    'filePlus',
    'generalPreferences',
    'key',
    'mapPin',
    'palette',
    'panelLayout',
    'passkey',
    'proxy',
    'save',
    'search',
    'shieldAlert',
    'shieldOff',
    'shieldPlus',
    'sidebarNavigation',
    'smile',
    'storageDrive',
    'temporaryChat',
    'textSpacing',
    'verticalAlign',
    'verticalAlignBottom',
    'verticalAlignMiddle',
    'verticalAlignTop',
];
const normalizedOutlineSemanticIconNames = new Set(['palette', 'temporaryChat']);

function loadIconRegistry() {
    const context = { console: { error() {}, log() {}, warn() {} } };
    context.globalThis = context;
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(iconsPath, 'utf8'), context, { filename: iconsPath });
    return context.Icons;
}

test('every declarative HTML icon resolves through icons.js', () => {
    const icons = loadIconRegistry();
    let placeholderCount = 0;

    for (const relativeFile of htmlFiles) {
        const source = fs.readFileSync(path.join(repositoryRoot, relativeFile), 'utf8');
        assert.doesNotMatch(source, /<svg\b/i, `${relativeFile} still contains inline SVG markup`);
        for (const match of source.matchAll(/data-omlorix-icon="([^"]+)"/g)) {
            placeholderCount += 1;
            assert.ok(icons.resolveIcon(match[1]), `${relativeFile} references missing icon ${match[1]}`);
        }
    }

    assert.equal(placeholderCount, 366);
});

test('new semantic icons and dynamic SVG helpers are registered', () => {
    const icons = loadIconRegistry();
    for (const iconName of normalizedSemanticIconNames) {
        assert.match(icons.resolveIcon(iconName), /^<svg\b/i, `${iconName} is not registered`);
    }
    assert.match(icons.resolveIcon('markdownEditorIcons.paint'), /^<svg\b/i);
    assert.equal(typeof icons.createSlidePlaceholder, 'function');
    assert.match(icons.createSlidePlaceholder('<Slide & 1>'), /&lt;Slide &amp; 1&gt;/);
});

test('new semantic UI icons share the 20px currentColor system', () => {
    const icons = loadIconRegistry();

    for (const iconName of normalizedSemanticIconNames) {
        const markup = icons.resolveIcon(iconName);
        const rootTag = markup.match(/^<svg\b[^>]*>/i)?.[0] || '';

        assert.match(rootTag, /\bxmlns="http:\/\/www\.w3\.org\/2000\/svg"/i, `${iconName} is missing the SVG namespace`);
        assert.match(rootTag, /\bwidth="20"/i, `${iconName} does not have a 20px width`);
        assert.match(rootTag, /\bheight="20"/i, `${iconName} does not have a 20px height`);
        assert.match(rootTag, /\bviewBox="0 0 20 20"/i, `${iconName} does not use the 20px viewBox`);
        assert.match(rootTag, /\bstroke-width="1\.33"/i, `${iconName} does not declare the regular optical weight`);
        assert.match(rootTag, /\bstroke-linecap="round"/i, `${iconName} does not declare rounded line caps`);
        assert.match(rootTag, /\bstroke-linejoin="round"/i, `${iconName} does not declare rounded line joins`);
        assert.match(rootTag, /\bfocusable="false"/i, `${iconName} is not hidden from legacy SVG focus handling`);
        assert.match(markup, /<path\b/i, `${iconName} has no path geometry`);

        if (normalizedOutlineSemanticIconNames.has(iconName)) {
            assert.match(rootTag, /\bfill="none"/i, `${iconName} is not an outline icon`);
            assert.match(rootTag, /\bstroke="currentColor"/i, `${iconName} does not inherit the stroke color`);
            assert.doesNotMatch(markup, /<path\b[^>]*\bfill=/i, `${iconName} contains a filled path`);
        } else {
            assert.match(rootTag, /\bfill="currentColor"/i, `${iconName} does not inherit the foreground color`);
            assert.doesNotMatch(markup, /\bstroke\s*=/i, `${iconName} still depends on stroke rendering`);
            assert.doesNotMatch(markup, /\bfill="(?!currentColor)[^"]+"/i, `${iconName} contains a non-currentColor fill`);
        }
        if (iconName === 'temporaryChat') {
            assert.match(markup, /\bstroke-dasharray="5\.5 3\.3"/i, `${iconName} is missing its temporary-state dash pattern`);
        }
        assert.doesNotMatch(markup, /<(?!\/?(?:svg|path)\b)[a-z][^>]*>/i, `${iconName} contains non-path SVG geometry`);
    }
});

test('theme selector icons use the same outline rendering system', () => {
    const icons = loadIconRegistry();

    for (const iconName of ['desktop', 'sun', 'moon']) {
        const rootTag = icons.resolveIcon(iconName).match(/^<svg\b[^>]*>/i)?.[0] || '';

        assert.match(rootTag, /\bfill="none"/i, `${iconName} is not an outline icon`);
        assert.match(rootTag, /\bstroke="currentColor"/i, `${iconName} does not inherit the stroke color`);
        assert.match(rootTag, /\bstroke-width="1\.33"/i, `${iconName} has a mismatched optical weight`);
        assert.match(rootTag, /\bstroke-linecap="round"/i, `${iconName} does not use rounded line caps`);
        assert.match(rootTag, /\bstroke-linejoin="round"/i, `${iconName} does not use rounded line joins`);
    }

    for (const relativeFile of ['frontend/login.html', 'frontend/admin.html']) {
        const source = fs.readFileSync(path.join(repositoryRoot, relativeFile), 'utf8');
        const darkThemeButton = source.match(/<button\b[^>]*\bdata-theme="dark"[^>]*>[\s\S]*?<\/button>/i)?.[0] || '';

        assert.match(darkThemeButton, /data-omlorix-icon="moon"/, `${relativeFile} does not use the outline moon`);
    }
});

test('provider entries use the Connections icon and distribute no provider logo files', () => {
    const icons = loadIconRegistry();
    const manifest = JSON.parse(fs.readFileSync(providerBrandManifestPath, 'utf8'));
    const providerIconKeys = [
        'aiohttp',
        'alibaba',
        'amazon',
        'anthropic',
        'apple',
        'baidu',
        'claude',
        'cloudflare',
        'crawl4ai',
        'deepseek',
        'duckduckgo',
        'elevenlabs',
        'exa',
        'firecrawl',
        'gemini',
        'gemma',
        'github',
        'gmail',
        'google',
        'google_aistudio',
        'google_calendar',
        'google_drive',
        'grok',
        'kimi',
        'lmstudio',
        'meta',
        'microsoft',
        'minimax',
        'mistral',
        'nebius',
        'notion',
        'nvidia',
        'ollama',
        'openai',
        'openrouter',
        'perplexity',
        'qwen',
        'searxng',
        'serper',
        'slack',
        'tavily',
        'xai',
        'you',
        'youtube',
    ];
    const formerOmlorixProviderDirectories = ['exa', 'github', 'mistral', 'openai'];

    assert.match(icons.omlorixModel, /^<svg\b/i);
    assert.match(icons.omlorixModel, /omlorix-model-icon/);
    assert.match(icons.omlorixModel, /currentColor/);
    assert.notEqual(icons.omlorixModel, icons.connections);
    assert.deepEqual(manifest.official_assets, []);
    assert.equal(manifest.provider_icon.registry_key, 'connections');
    for (const iconKey of providerIconKeys) {
        assert.equal(icons[iconKey], icons.connections, `${iconKey} does not use the Connections icon`);
        assert.match(icons[iconKey], /^<svg\b/i);
        assert.match(icons[iconKey], /stroke="currentColor"/);
        assert.doesNotMatch(icons[iconKey], /M104\.45 343\.5A175 175|omlorix-provider-icon/);
        assert.doesNotMatch(icons[iconKey], /<(?:img|span)\b|assets\/brands/i);
    }

    const registrySource = fs.readFileSync(iconsPath, 'utf8');
    assert.doesNotMatch(registrySource, /OMLORIX_PROVIDER_SERVICE_ICON|omlorix-provider-icon|assets\/brands|third-party-brand-icon|github-brand-icon/i);
    for (const providerDirectory of formerOmlorixProviderDirectories) {
        const directory = path.join(repositoryRoot, 'frontend/assets/brands', providerDirectory);
        if (fs.existsSync(directory)) {
            assert.deepEqual(fs.readdirSync(directory), [], `${providerDirectory} still contains provider artwork`);
        }
    }

    assert.equal(icons.artificialAnalysis, icons.connections);
    assert.equal(icons.chatFilesGoogleDrive, icons.google_drive);
    assert.equal(icons.resolveIcon('artificialAnalysisWordmark'), '');

    const leaderboard = fs.readFileSync(path.join(repositoryRoot, 'frontend/leaderboard.html'), 'utf8');
    assert.doesNotMatch(leaderboard, /artificialAnalysisWordmark|page-header-logo/);
    assert.match(leaderboard, /https:\/\/artificialanalysis\.ai\/leaderboards\/models/);
});

test('the Electron launcher loads and packages the shared registry without provider artwork', () => {
    const launcher = fs.readFileSync(path.join(repositoryRoot, 'electron/renderer/launcher.html'), 'utf8');
    const packageConfig = JSON.parse(fs.readFileSync(path.join(repositoryRoot, 'package.json'), 'utf8'));
    const iconsIndex = launcher.indexOf('../../frontend/js/common/icons.js');
    const launcherIconsIndex = launcher.indexOf('launcher-icons.js');
    const launcherIndex = launcher.indexOf('launcher.js');

    assert.ok(iconsIndex >= 0);
    assert.ok(iconsIndex < launcherIconsIndex);
    assert.ok(iconsIndex < launcherIndex);
    assert.ok(packageConfig.build.files.includes('frontend/js/common/icons.js'));
    assert.ok(packageConfig.build.files.includes('third_party_assets_manifest/provider-brand-assets.*'));
    assert.ok(packageConfig.build.files.every((entry) => !entry.includes('frontend/assets/brands')));
});
