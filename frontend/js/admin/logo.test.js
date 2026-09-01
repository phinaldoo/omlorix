const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const scriptPath = path.join(__dirname, 'logo.js');
const generalStylePath = path.join(__dirname, '..', '..', 'css', 'admin', 'general.css');

function readAdminLogoScript() {
    return fs.readFileSync(scriptPath, 'utf8');
}

function readAdminGeneralStyle() {
    return fs.readFileSync(generalStylePath, 'utf8');
}

test('admin dark mode gives uploaded light logos a white preview canvas', () => {
    const style = readAdminGeneralStyle();

    assert.match(
        style,
        /\[data-mode="dark"\]\s+#logoPreviewLight\[data-has-preview="true"\]\s*\{[^}]*background:\s*#fff;/,
    );
});

test('admin branding assets can render fetched SVG previews inline for theme-aware styling', () => {
    const source = readAdminLogoScript();

    assert.match(source, /const renderInlineSvg =/);
    assert.match(source, /new DOMParser\(\)/);
    assert.match(source, /document\.importNode\s*\(/);
    assert.match(source, /asset-upload-inline-svg/);
    assert.match(source, /inlineSvg:\s*true/);
    assert.match(source, /URL\.createObjectURL\s*\(\s*blob\s*\)/);
    assert.match(source, /document\.createElement\s*\(\s*['"]img['"]\s*\)/);
});

test('admin branding upload optimistic previews do not inline unsanitized local SVG files', () => {
    const source = readAdminLogoScript();

    assert.match(source, /previewSelection:\s*\(file\)\s*=>\s*storeLogo\(theme,\s*file,\s*file\.type\)/);
    assert.match(source, /previewSelection:\s*\(file\)\s*=>\s*storeIcon\(file,\s*file\.type\)/);
    assert.doesNotMatch(source, /previewSelection:\s*\(file\)\s*=>\s*storeLogo\(theme,\s*file,\s*file\.type,\s*\{\s*inlineSvg:\s*true/);
    assert.doesNotMatch(source, /previewSelection:\s*\(file\)\s*=>\s*storeIcon\(file,\s*file\.type,\s*\{\s*inlineSvg:\s*true/);
});

test('admin branding uploads use the persistent file picker helper instead of one-off detached inputs', () => {
    const source = readAdminLogoScript();

    assert.match(source, /createPersistentFilePicker/);
    assert.doesNotMatch(source, /document\.createElement\s*\(\s*['"]input['"]\s*\)/);
});

test('admin icon uploads render an immediate local preview before waiting for backend reload', () => {
    const source = readAdminLogoScript();

    assert.match(source, /previewSelection:\s*\(file\)\s*=>\s*storeIcon\(file,\s*file\.type\)/);
    assert.match(source, /onFailure:\s*\(\)\s*=>\s*fetchIconAsset\(\)/);
});

test('admin logo uploads render an immediate local preview and cache-bust the follow-up fetch', () => {
    const source = readAdminLogoScript();

    assert.match(source, /const buildLogoAssetUrl =/);
    assert.match(source, /previewSelection:\s*\(file\)\s*=>\s*storeLogo\(theme,\s*file,\s*file\.type\)/);
    assert.match(source, /onFailure:\s*\(\)\s*=>\s*fetchLogoVariant\(theme\)/);
    assert.match(source, /onSuccess:\s*\(\)\s*=>\s*fetchLogoVariant\(theme,\s*\{\s*version:\s*Date\.now\(\)\s*\}\)/);
});

test('admin asset upload cards use a dedicated loading state that preserves preview markup', () => {
    const source = readAdminLogoScript();

    assert.match(source, /const setAssetUploadCardLoadingState =/);
    assert.doesNotMatch(source, /setButtonLoadingState\?\.\(triggerButton,\s*true/);
    assert.doesNotMatch(source, /setButtonLoadingState\?\.\(triggerButton,\s*false/);
});

test('admin branding exposes its loader without auto-fetching on script load', () => {
    const source = readAdminLogoScript();

    assert.match(source, /state\.loadPromise/);
    assert.match(source, /window\.loadAllLogos\s*=\s*loadAllLogos/);
    assert.doesNotMatch(source, /loadAllLogos\(\);\s*window\.loadAllLogos\s*=/);
});
