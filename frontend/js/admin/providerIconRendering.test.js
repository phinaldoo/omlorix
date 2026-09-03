const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const iconPickerSource = fs.readFileSync(path.join(__dirname, 'iconPicker.js'), 'utf8');
const modelsSource = fs.readFileSync(path.join(__dirname, 'models.js'), 'utf8');
const modelsCreateSource = fs.readFileSync(path.join(__dirname, 'modelsCreate.js'), 'utf8');
const providersCreateSource = fs.readFileSync(path.join(__dirname, 'providersCreate.js'), 'utf8');
const providerGroupsSource = fs.readFileSync(path.join(__dirname, 'providerGroups.js'), 'utf8');
const modelRenderingSources = [
    ['admin models', modelsSource],
    ['agent model selectors', fs.readFileSync(path.join(__dirname, '..', 'chat', 'agents.js'), 'utf8')],
    ['automation model selectors', fs.readFileSync(path.join(__dirname, '..', 'chat', 'automations.js'), 'utf8')],
    ['BYOK models', fs.readFileSync(path.join(__dirname, '..', 'chat', 'byok.js'), 'utf8')],
    ['model mentions', fs.readFileSync(path.join(__dirname, '..', 'chat', 'chatBox', 'mentions.js'), 'utf8')],
    ['main model select', fs.readFileSync(path.join(__dirname, '..', 'chat', 'modelSelect.js'), 'utf8')],
];

test('icon rendering gives every SVG instance unique IDs and matching references', () => {
    const icons = {
        google_aistudio: [
            '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">',
            '<defs><clipPath id="_clip1"><path d="M0 0h1v1H0z"/></clipPath>',
            '<image id="_Image2" href="data:image/png;base64,AA=="/></defs>',
            '<g clip-path="url(#_clip1)"><use xlink:href="#_Image2"/></g>',
            '</svg>',
        ].join(''),
    };
    const context = {
        Icons: icons,
        window: { Icons: icons },
    };

    vm.runInNewContext(iconPickerSource, context);

    const first = context.window.IconPicker.renderIconMarkup('google_aistudio');
    const second = context.window.IconPicker.renderIconMarkup('google_aistudio');
    const firstClipId = first.match(/id="(_clip1-[^"]+)"/)[1];
    const secondClipId = second.match(/id="(_clip1-[^"]+)"/)[1];
    const firstImageId = first.match(/id="(_Image2-[^"]+)"/)[1];
    const secondImageId = second.match(/id="(_Image2-[^"]+)"/)[1];

    assert.notEqual(firstClipId, secondClipId);
    assert.notEqual(firstImageId, secondImageId);
    assert.match(first, new RegExp(`clip-path="url\\(#${firstClipId}\\)"`));
    assert.match(first, new RegExp(`xlink:href="#${firstImageId}"`));
    assert.match(second, new RegExp(`clip-path="url\\(#${secondClipId}\\)"`));
    assert.match(second, new RegExp(`xlink:href="#${secondImageId}"`));

    const firstFallback = context.window.IconPicker.renderIconMarkup('missing', {
        fallback: icons.google_aistudio,
    });
    const secondFallback = context.window.IconPicker.renderIconMarkup('missing', {
        fallback: icons.google_aistudio,
    });
    assert.notEqual(
        firstFallback.match(/id="(_clip1-[^"]+)"/)[1],
        secondFallback.match(/id="(_clip1-[^"]+)"/)[1]
    );

    // Stored values from older releases must use the configured preset
    // fallback instead of putting an emoji back into any LLM icon surface.
    const legacyEmojiFallback = context.window.IconPicker.renderIconMarkup('✅', {
        fallback: icons.google_aistudio,
    });
    assert.match(legacyEmojiFallback, /<svg/);
    assert.doesNotMatch(legacyEmojiFallback, /✅/);
});

test('image icons render as square circular cover crops', () => {
    const context = {
        Icons: {},
        window: { Icons: {} },
    };

    vm.runInNewContext(iconPickerSource, context);

    const markup = context.window.IconPicker.renderIconMarkup('data:image/png;base64,AA==', {
        imageAlt: 'Provider icon',
    });
    assert.match(markup, /class="icon-picker-image"/);
    assert.match(markup, /aspect-ratio:1/);
    assert.match(markup, /object-fit:cover/);
    assert.match(markup, /border-radius:50%/);
    assert.doesNotMatch(markup, /object-fit:contain/);
});

test('model presets render as Omlorix while provider presets remain Connections icons', () => {
    const connections = '<svg data-glyph="connections"></svg>';
    const omlorixModel = '<svg data-glyph="omlorix-model"></svg>';
    const icons = {
        anthropic: connections,
        connections,
        omlorix: '<svg data-glyph="omlorix-brand"></svg>',
        omlorixModel,
        openai: connections,
    };
    const context = {
        Icons: icons,
        window: {
            DEFAULT_PROVIDER_ICON_KEYS: ['openai', 'anthropic'],
            Icons: icons,
        },
    };

    vm.runInNewContext(iconPickerSource, context);

    assert.equal(context.window.IconPicker.renderIconMarkup('openai'), connections);
    assert.equal(context.window.IconPicker.renderModelIconMarkup('openai'), omlorixModel);
    assert.equal(context.window.IconPicker.renderModelIconMarkup('connections'), omlorixModel);
    assert.ok(
        context.window.IconPicker.getAvailableIcons('model').every(({ svg }) => svg === omlorixModel),
    );
    assert.ok(
        context.window.IconPicker.getAvailableIcons('provider').every(({ svg }) => svg === connections),
    );
});

test('every LLM model rendering surface uses the model-specific icon renderer', () => {
    for (const [surface, source] of modelRenderingSources) {
        assert.match(source, /IconPicker\?\.renderModelIconMarkup/, `${surface} bypasses the model icon renderer`);
    }
});

test('uploaded icon crop is centered and square for every source shape', () => {
    const context = {
        Icons: {},
        window: { Icons: {} },
    };

    vm.runInNewContext(iconPickerSource, context);
    const crop = context.window.IconPicker.calculateSquareCrop;

    assert.deepEqual({ ...crop(400, 200) }, { x: 100, y: 0, size: 200 });
    assert.deepEqual({ ...crop(120, 360) }, { x: 0, y: 120, size: 120 });
    assert.deepEqual({ ...crop(256, 256) }, { x: 0, y: 0, size: 256 });
});

test('all LLM provider selection and grouping surfaces use collision-safe icon rendering', () => {
    assert.match(
        modelsCreateSource,
        /window\.IconPicker\.renderIconMarkup\(iconValue \|\| fallbackKey,/
    );
    assert.match(
        modelsCreateSource,
        /window\.IconPicker\.renderIconMarkup\(groupIconValue,/
    );
    assert.match(
        providersCreateSource,
        /window\.IconPicker\.renderIconMarkup\(key,/
    );
    assert.match(
        providerGroupsSource,
        /return renderCollisionSafeIcon\(iconsMap\[key\] \? key : fallbackKey, fallback\);/
    );
    assert.match(modelsCreateSource, /isImageIconValue\?\.\(configuredGroupIcon\)/);
    assert.match(providerGroupsSource, /isImageIconValue\?\.\(configuredIcon\)/);
});

test('LLM icon picker exposes no emoji mode or emoji value handling', () => {
    assert.doesNotMatch(iconPickerSource, /emoji/i);
});
