const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const CHANGE_LABEL_KEYS = [
    'change_logo_light_aria',
    'change_logo_dark_aria',
    'change_icon_aria',
];

function createElementStub() {
    const attributes = new Map();
    return {
        setAttribute(name, value) {
            attributes.set(name, String(value));
        },
        getAttribute(name) {
            return attributes.get(name) ?? null;
        },
    };
}

function createPreviewStub() {
    const uploadArea = createElementStub();
    const preview = {
        children: [],
        textContent: 'Upload',
        classList: {
            values: new Set(),
            add(value) {
                this.values.add(value);
            },
        },
        appendChild(child) {
            this.children.push(child);
        },
        closest(selector) {
            return selector === '.upload-area' ? uploadArea : null;
        },
    };
    return { preview, uploadArea };
}

function loadUploadModule(previews) {
    const german = JSON.parse(fs.readFileSync(
        path.join(__dirname, '../../i18n/de/server_setup.json'),
        'utf8'
    ));
    const state = { serverData: {} };
    const window = {
        location: { origin: 'https://setup.example' },
        getTranslation(key, fallback) {
            return german[key] ?? fallback;
        },
    };
    const document = {
        createElement(tagName) {
            assert.equal(tagName, 'img');
            return createElementStub();
        },
        getElementById(id) {
            return previews[id]?.preview ?? null;
        },
    };
    class ImmediateFileReader {
        readAsDataURL() {
            this.onload({ target: { result: 'data:image/png;base64,dGVzdA==' } });
        }
    }

    const context = {
        URL,
        console: { error() {} },
        document,
        FileReader: ImmediateFileReader,
        state,
        window,
    };
    vm.createContext(context);
    vm.runInContext(
        fs.readFileSync(path.join(__dirname, 'upload.js'), 'utf8'),
        context
    );
    return { context, german, state, window };
}

function assertLocalizedPreview(previewStub, expectedLabel, expectedKey, expectedAltKey) {
    assert.equal(previewStub.uploadArea.getAttribute('aria-label'), expectedLabel);
    assert.equal(
        previewStub.uploadArea.getAttribute('data-i18n-attr'),
        `aria-label:${expectedKey}`
    );
    assert.equal(previewStub.preview.children.length, 1);
    assert.equal(
        previewStub.preview.children[0].getAttribute('data-i18n-attr'),
        `alt:${expectedAltKey}`
    );
}

test('selected branding previews get localized, purpose-specific change labels', () => {
    const previews = {
        logoLightPreview: createPreviewStub(),
        logoDarkPreview: createPreviewStub(),
        iconPreview: createPreviewStub(),
    };
    const { context, german } = loadUploadModule(previews);
    const file = { name: 'branding.png', type: 'image/png', size: 1024 };

    context.handleLogoSelection(
        { target: { files: [file] } },
        'light',
        previews.logoLightPreview.preview
    );
    context.handleLogoSelection(
        { target: { files: [file] } },
        'dark',
        previews.logoDarkPreview.preview
    );
    context.handleIconSelection(
        { target: { files: [file] } },
        previews.iconPreview.preview
    );

    assertLocalizedPreview(
        previews.logoLightPreview,
        'Logo für helles Theme ändern',
        'change_logo_light_aria',
        'logo_preview_alt'
    );
    assertLocalizedPreview(
        previews.logoDarkPreview,
        'Logo für dunkles Theme ändern',
        'change_logo_dark_aria',
        'logo_preview_alt'
    );
    assertLocalizedPreview(
        previews.iconPreview,
        'App-Symbol ändern',
        'change_icon_aria',
        'icon_preview_alt'
    );
    assert.equal(previews.logoLightPreview.preview.children[0].getAttribute('alt'), german.logo_preview_alt);
    assert.equal(previews.iconPreview.preview.children[0].getAttribute('alt'), german.icon_preview_alt);
});

test('preloaded branding previews use the same localized change labels', async () => {
    const previews = {
        logoLightPreview: createPreviewStub(),
        logoDarkPreview: createPreviewStub(),
        iconPreview: createPreviewStub(),
    };
    const { window } = loadUploadModule(previews);
    window.authedFetch = async () => ({
        ok: true,
        async json() {
            return {
                logos: {
                    light: { url: '/light.svg' },
                    dark: { url: '/dark.svg' },
                },
                icon: { url: '/icon.svg' },
            };
        },
    });

    await window.loadSavedBrandingAssets();

    assertLocalizedPreview(
        previews.logoLightPreview,
        'Logo für helles Theme ändern',
        'change_logo_light_aria',
        'logo_preview_alt'
    );
    assertLocalizedPreview(
        previews.logoDarkPreview,
        'Logo für dunkles Theme ändern',
        'change_logo_dark_aria',
        'logo_preview_alt'
    );
    assertLocalizedPreview(
        previews.iconPreview,
        'App-Symbol ändern',
        'change_icon_aria',
        'icon_preview_alt'
    );
});

test('branding change labels are translated in every server setup locale', () => {
    const i18nRoot = path.join(__dirname, '../../i18n');
    const locales = fs.readdirSync(i18nRoot, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name);

    for (const locale of locales) {
        const translations = JSON.parse(fs.readFileSync(
            path.join(i18nRoot, locale, 'server_setup.json'),
            'utf8'
        ));
        for (const key of CHANGE_LABEL_KEYS) {
            assert.equal(
                typeof translations[key],
                'string',
                `${locale} is missing ${key}`
            );
            assert.ok(translations[key].trim(), `${locale} has an empty ${key}`);
        }
    }
});
