const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

/**
 * Return the declarations for a top-level CSS selector.
 *
 * The theme palette blocks intentionally contain declarations only, so a
 * small static parser is sufficient and keeps this regression test focused.
 */
function getRuleDeclarations(source, selector) {
    const escapedSelector = selector
        .replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
        .replace(/\s+/g, '\\s+');
    const match = source.match(new RegExp(`${escapedSelector}\\s*\\{([^{}]*)\\}`, 'u'));

    assert.ok(match, `Missing ${selector} rule`);
    return match[1];
}

/** Calculate WCAG contrast for two six-digit hexadecimal colors. */
function contrastRatio(foreground, background) {
    const luminance = (hex) => {
        const channels = [1, 3, 5].map((index) => Number.parseInt(hex.slice(index, index + 2), 16) / 255);
        const linear = channels.map((channel) => (
            channel <= 0.04045
                ? channel / 12.92
                : ((channel + 0.055) / 1.055) ** 2.4
        ));
        return (0.2126 * linear[0]) + (0.7152 * linear[1]) + (0.0722 * linear[2]);
    };
    const values = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
    return (values[0] + 0.05) / (values[1] + 0.05);
}

test('message color choices cannot change application UI tokens', () => {
    const source = fs.readFileSync(
        path.join(__dirname, '../../css/common/init.css'),
        'utf8'
    );
    const messageColorSource = fs.readFileSync(
        path.join(__dirname, '../../css/chat/init.css'),
        'utf8'
    );
    const lightMode = getRuleDeclarations(source, '[data-mode="light"]');
    const darkMode = getRuleDeclarations(source, '[data-mode="dark"]');
    const paletteRules = [...messageColorSource.matchAll(
        /\[data-theme="([^"]+)"\]\[data-mode="(light|dark)"\]\s*\{([^{}]*)\}/gu
    )];

    // Light and dark modes own the neutral canvas color used throughout the
    // page, including components that consume --background directly.
    assert.match(lightMode, /--background:\s*#ffffff/iu);
    assert.match(darkMode, /--background:\s*#1F1F1F/iu);

    // Seven message colors in two modes may define exactly one custom property.
    assert.equal(paletteRules.length, 14);
    for (const [, theme, mode, declarations] of paletteRules) {
        const customProperties = [...declarations.matchAll(/--([a-z0-9-]+)\s*:/giu)]
            .map((match) => match[1]);
        assert.deepEqual(
            customProperties,
            ['user-message-bg'],
            `${theme}/${mode} may only set the user-message background`,
        );
    }
});

test('all accent palettes inherit the same mode-specific input backgrounds', () => {
    const source = fs.readFileSync(
        path.join(__dirname, '../../css/common/init.css'),
        'utf8'
    );
    const lightMode = getRuleDeclarations(source, '[data-mode="light"]');
    const darkMode = getRuleDeclarations(source, '[data-mode="dark"]');
    const messageColorSource = fs.readFileSync(
        path.join(__dirname, '../../css/chat/init.css'),
        'utf8'
    );
    const paletteRules = [...messageColorSource.matchAll(
        /\[data-theme="([^"]+)"\]\[data-mode="(light|dark)"\]\s*\{([^{}]*)\}/gu
    )];

    // Input surfaces belong to the display mode rather than to the accent
    // palette, just like the application canvas and elevated surfaces do.
    assert.match(lightMode, /--input-bg:\s*#F5F5F5/iu);
    assert.match(darkMode, /--input-bg:\s*#29292C/iu);

    for (const [, theme, mode, declarations] of paletteRules) {
        assert.doesNotMatch(
            declarations,
            /--input-bg:/u,
            `${theme}/${mode} must inherit the mode-specific input background`
        );
    }
});

test('shared and Mermaid scrollbars use mode-specific high-contrast thumb colors', () => {
    const initSource = fs.readFileSync(
        path.join(__dirname, '../../css/common/init.css'),
        'utf8'
    );
    const elementsSource = fs.readFileSync(
        path.join(__dirname, '../../css/common/elements.css'),
        'utf8'
    );
    const markdownSource = fs.readFileSync(
        path.join(__dirname, '../../css/chat/markdown.css'),
        'utf8'
    );
    const lightMode = getRuleDeclarations(initSource, '[data-mode="light"]');
    const darkMode = getRuleDeclarations(initSource, '[data-mode="dark"]');
    const lightThumb = lightMode.match(/--scrollbar-thumb:\s*(#[0-9a-f]{6})/iu)?.[1];
    const darkThumb = darkMode.match(/--scrollbar-thumb:\s*(#[0-9a-f]{6})/iu)?.[1];

    assert.ok(lightThumb, 'light mode must define a scrollbar thumb color');
    assert.ok(darkThumb, 'dark mode must define a scrollbar thumb color');
    // The light thumb can appear over both the page canvas and neutral form
    // surfaces, so enforce non-text contrast against the less favorable one.
    assert.ok(contrastRatio(lightThumb, '#ffffff') >= 3);
    assert.ok(contrastRatio(lightThumb, '#f5f5f5') >= 3);
    assert.ok(contrastRatio(darkThumb, '#1f1f1f') >= 3);
    assert.match(elementsSource, /background:\s*var\(--scrollbar-thumb\)/u);
    assert.match(elementsSource, /scrollbar-color:\s*var\(--scrollbar-thumb\) transparent/u);
    assert.match(markdownSource, /\.mermaid-preview-scrollbar-thumb\s*\{[^{}]*background:\s*var\(--scrollbar-thumb\)/u);
});

test('all application controls use the fixed monochrome palette', () => {
    const initSource = fs.readFileSync(
        path.join(__dirname, '../../css/common/init.css'),
        'utf8'
    );
    const elementsNewSource = fs.readFileSync(
        path.join(__dirname, '../../css/common/elementsNew.css'),
        'utf8'
    );
    const lightMode = getRuleDeclarations(initSource, '[data-mode="light"]');
    const darkMode = getRuleDeclarations(initSource, '[data-mode="dark"]');
    const sharedSubmitHover = getRuleDeclarations(
        elementsNewSource,
        '.om-button.border.submit:hover:not(:disabled):not(.disabled):not(.is-disabled), .om-button.border.primary:hover:not(:disabled):not(.disabled):not(.is-disabled)'
    );

    assert.match(lightMode, /--primary-color:\s*#333333/iu);
    assert.match(lightMode, /--primary-light:\s*rgba\(58,\s*58,\s*60,\s*0\.1\)/iu);
    assert.match(lightMode, /--primary-hover:\s*#555555/iu);
    assert.match(lightMode, /--accent-color:\s*#111111/iu);
    assert.match(darkMode, /--primary-color:\s*#88888E/iu);
    assert.match(darkMode, /--primary-light:\s*rgba\(164,\s*164,\s*170,\s*0\.16\)/iu);
    assert.match(darkMode, /--primary-hover:\s*#A4A4AA/iu);
    assert.match(darkMode, /--accent-color:\s*#68686E/iu);
    assert.match(darkMode, /--border-color:\s*#3F3F46/iu);
    assert.match(sharedSubmitHover, /background:\s*var\(--primary-hover/u);
});

test('message color choices tint user messages in both display modes', () => {
    const messageColorSource = fs.readFileSync(
        path.join(__dirname, '../../css/chat/init.css'),
        'utf8'
    );
    const chatSource = fs.readFileSync(
        path.join(__dirname, '../../css/chat/chat.css'),
        'utf8'
    );
    const colorThemes = ['blue', 'green', 'coral', 'purple', 'teal', 'amber'];
    const userMessage = getRuleDeclarations(chatSource, '.user-message');

    assert.match(
        userMessage,
        /background-color:\s*var\(--user-message-bg,\s*var\(--input-bg\)\);/u
    );

    for (const theme of colorThemes) {
        for (const [mode, strength] of [['light', 12], ['dark', 18]]) {
            const declarations = getRuleDeclarations(
                messageColorSource,
                `[data-theme="${theme}"][data-mode="${mode}"]`
            );
            assert.match(
                declarations,
                new RegExp(`--user-message-bg:\\s*color-mix\\(in srgb,\\s*#[0-9A-F]{6} ${strength}%,\\s*var\\(--background\\)\\);`, 'iu'),
                `${theme}/${mode} should tint the user-message background`,
            );
        }
    }

    for (const mode of ['light', 'dark']) {
        const monoDeclarations = getRuleDeclarations(
            messageColorSource,
            `[data-theme="mono"][data-mode="${mode}"]`
        );
        assert.match(monoDeclarations, /--user-message-bg:\s*var\(--input-bg\)/u);
    }
});

test('profile save confirmation uses accessible success text contrast', () => {
    const initSource = fs.readFileSync(
        path.join(__dirname, '../../css/common/init.css'),
        'utf8'
    );
    const settingsSource = fs.readFileSync(
        path.join(__dirname, '../../css/userSettings/style.css'),
        'utf8'
    );
    const successRule = getRuleDeclarations(
        settingsSource,
        '.save-changes-btn--success:disabled'
    );

    assert.match(successRule, /color:\s*var\(--success-contrast-text/u);
    for (const mode of ['light', 'dark']) {
        const declarations = getRuleDeclarations(initSource, `[data-mode="${mode}"]`);
        const background = declarations.match(/--success-color:\s*(#[0-9a-f]{6})/iu)?.[1];
        const foreground = declarations.match(/--success-contrast-text:\s*(#[0-9a-f]{6})/iu)?.[1];
        assert.ok(background && foreground, `${mode} must define both success colors`);
        assert.ok(
            contrastRatio(foreground, background) >= 4.5,
            `${mode} success confirmation must meet WCAG AA text contrast`,
        );
    }
});
