const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function extractFunction(source, functionName) {
    const start = source.indexOf(`function ${functionName}(`);
    assert.notEqual(start, -1, `expected ${functionName} in chatBox.js`);
    // Function parameters in this file can contain default object literals or
    // destructuring. The body begins at the first brace after the closing
    // parameter sequence, not necessarily the first brace after the name.
    const parameterEnd = source.indexOf(') {', start);
    assert.notEqual(parameterEnd, -1, `expected ${functionName} parameter end`);
    const bodyStart = parameterEnd + 2;
    let depth = 0;
    for (let index = bodyStart; index < source.length; index += 1) {
        if (source[index] === '{') depth += 1;
        if (source[index] === '}') depth -= 1;
        if (depth === 0) return source.slice(start, index + 1);
    }
    throw new Error(`Could not extract ${functionName}`);
}

function loadImageHelpers() {
    const source = readFrontendSource(path.join(__dirname, 'chatBox.js'), 'utf8');
    const mimeTypes = source.match(/const CHAT_COMPOSER_IMAGE_MIME_TYPES = new Set\(\[[\s\S]*?\]\);/)?.[0];
    const extensionTypes = source.match(/const CHAT_COMPOSER_IMAGE_EXTENSION_MIME_TYPES = Object\.freeze\(\{[\s\S]*?\}\);/)?.[0];
    assert.ok(mimeTypes);
    assert.ok(extensionTypes);

    const context = {};
    vm.runInNewContext([
        mimeTypes,
        extensionTypes,
        extractFunction(source, 'resolveChatComposerImageMimeType'),
        extractFunction(source, 'isChatComposerImageAttachment'),
        extractFunction(source, 'createChatComposerImagePreviewFile'),
        'this.helpers = { resolveChatComposerImageMimeType, isChatComposerImageAttachment, createChatComposerImagePreviewFile };',
    ].join('\n\n'), context, { filename: 'chatBox.js' });
    return context.helpers;
}

function loadAttachmentA11yHelpers(translations = {}) {
    const source = readFrontendSource(path.join(__dirname, 'chatBox.js'), 'utf8');
    const interpolate = (template, vars = {}) => String(template).replace(/\{(\w+)\}/g, (_, token) => {
        return vars[token] === undefined ? '' : String(vars[token]);
    });
    const context = {
        window: {
            getTranslation(key, fallback) {
                return translations[key] || fallback;
            },
            formatTranslation(key, fallback, vars) {
                return interpolate(translations[key] || fallback, vars);
            },
        },
    };
    vm.runInNewContext([
        extractFunction(source, 'getChatI18nString'),
        extractFunction(source, 'formatChatI18nString'),
        extractFunction(source, 'setChatComposerAttachmentRemoveLabel'),
        'this.helpers = { setChatComposerAttachmentRemoveLabel };',
    ].join('\n\n'), context, { filename: 'chatBox.js' });
    return context.helpers;
}

test('composer image preview accepts only browser-previewable image formats', () => {
    const { isChatComposerImageAttachment } = loadImageHelpers();

    assert.equal(isChatComposerImageAttachment({ mimeType: 'image/png; charset=binary' }), true);
    assert.equal(isChatComposerImageAttachment({ name: 'photo.WEBP', fileType: 'application/octet-stream' }), true);
    assert.equal(isChatComposerImageAttachment({ extension: '.svg' }), true);
    assert.equal(isChatComposerImageAttachment({ name: 'report.pdf', mimeType: 'application/pdf' }), false);
    assert.equal(isChatComposerImageAttachment({ name: 'disguised.png', mimeType: 'application/pdf' }), false);
    assert.equal(isChatComposerImageAttachment({ name: 'clip.mp4', mimeType: 'video/mp4' }), false);
    assert.equal(isChatComposerImageAttachment({ name: 'photo.avif', mimeType: 'image/avif' }), false);
});

test('composer image preview descriptor preserves authenticated file metadata', () => {
    const { createChatComposerImagePreviewFile } = loadImageHelpers();
    const descriptor = createChatComposerImagePreviewFile({
        name: 'diagram.png',
        mimeType: 'image/png',
        fileSize: 2048,
    }, 'file-123');

    assert.equal(descriptor.file_id, 'file-123');
    assert.equal(descriptor.file_type, 'image/png');
    assert.equal(descriptor.file_size, 2048);
    assert.equal(descriptor.meta.original_filename, 'diagram.png');
});

test('composer attachment wiring opens only the normalized image descriptor', () => {
    const source = readFrontendSource(path.join(__dirname, 'chatBox.js'), 'utf8');
    const createSource = extractFunction(source, 'createChatAttachmentElement');
    const openSource = extractFunction(source, 'openChatComposerImagePreview');

    assert.match(createSource, /if \(!element\.__imagePreviewFile\) return;/);
    assert.match(createSource, /event\.key !== 'Enter'.*event\.key !== ' '/s);
    assert.match(openSource, /await preview\.open\(previewFile\)/);
    assert.match(openSource, /preview\.activeFileId/);
});

test('composer attachment remove control has a translated filename-specific accessible name', () => {
    const { setChatComposerAttachmentRemoveLabel } = loadAttachmentA11yHelpers({
        chat_sr_remove_attachment: 'Anhang {name} entfernen',
    });
    const attributes = new Map();
    const removeTarget = {
        setAttribute(name, value) {
            attributes.set(name, value);
        },
        title: '',
    };

    setChatComposerAttachmentRemoveLabel(removeTarget, 'screen-capture-20260822-135817.png');

    const expected = 'Anhang screen-capture-20260822-135817.png entfernen';
    assert.equal(attributes.get('aria-label'), expected);
    assert.equal(removeTarget.title, expected);
});

test('composer attachment remove label remains synchronized with attachment metadata', () => {
    const source = readFrontendSource(path.join(__dirname, 'chatBox.js'), 'utf8');
    const createSource = extractFunction(source, 'createChatAttachmentElement');
    const updateSource = extractFunction(source, 'applyAttachmentContent');

    assert.match(createSource, /setChatComposerAttachmentRemoveLabel\(deleteEl, nameEl\.textContent\)/);
    assert.match(updateSource, /setChatComposerAttachmentRemoveLabel\([\s\S]*element\.__deleteTarget/);
});

test('attachment remove accessible name is translated in every supported locale', () => {
    const localeRoot = path.join(__dirname, '..', '..', 'i18n');
    const locales = fs.readdirSync(localeRoot).filter((locale) => {
        return fs.existsSync(path.join(localeRoot, locale, 'index.json'));
    });

    for (const locale of locales) {
        const dictionary = JSON.parse(fs.readFileSync(path.join(localeRoot, locale, 'index.json'), 'utf8'));
        assert.equal(
            typeof dictionary.chat_sr_remove_attachment,
            'string',
            `${locale} must translate chat_sr_remove_attachment`
        );
        assert.match(
            dictionary.chat_sr_remove_attachment,
            /\{name\}/,
            `${locale} remove label must include the attachment name`
        );
    }
});
