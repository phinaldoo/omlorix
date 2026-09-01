const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

test('all server setup branding previews share the same inset and height', () => {
    const styles = fs.readFileSync(
        path.join(__dirname, '../../css/serverSetup/style.css'),
        'utf8'
    );
    const previewRule = styles.match(/\.upload-preview\s*\{([\s\S]*?)\}/)?.[1] || '';

    assert.match(previewRule, /height:\s*120px/);
    assert.match(previewRule, /padding:\s*16px/);
    assert.doesNotMatch(styles, /\.upload-preview\.has-image\s*\{[\s\S]*?padding:\s*0/);
    assert.doesNotMatch(styles, /\.icon-preview img\s*\{[\s\S]*?max-height:/);
});

test('logo previews use their target mode background tokens', () => {
    const styles = fs.readFileSync(
        path.join(__dirname, '../../css/serverSetup/style.css'),
        'utf8'
    );
    const markup = fs.readFileSync(
        path.join(__dirname, '../../server_setup.html'),
        'utf8'
    );
    const themePreviewRule = styles.match(/\.theme-logo-preview\s*\{([\s\S]*?)\}/)?.[1] || '';

    assert.match(markup, /id="logoLightPreview" data-mode="light"/);
    assert.match(markup, /id="logoDarkPreview" data-mode="dark"/);
    assert.match(themePreviewRule, /background:\s*var\(--background\)/);
    assert.doesNotMatch(styles, /#logoDarkPreview\s*\{[\s\S]*?background:\s*#000/);
});
