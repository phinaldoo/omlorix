const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');

const EDITOR_PATH = path.resolve(__dirname, 'slide-presentation-editor.js');

function editorSource() {
    return fs.readFileSync(EDITOR_PATH, 'utf8');
}

function sourceSection(source, startMarker, endMarker) {
    const start = source.indexOf(startMarker);
    const end = source.indexOf(endMarker, start);
    assert.notEqual(start, -1, `Missing source marker: ${startMarker}`);
    assert.notEqual(end, -1, `Missing source marker: ${endMarker}`);
    return source.slice(start, end);
}

test('slide inspector keeps deck metadata out of parent-document HTML parsing', () => {
    const source = editorSource();
    const helperSource = source.match(/function escapeHtmlAttribute\(value\) \{[\s\S]*?\n\}/)?.[0];
    assert.ok(helperSource, 'attribute escaping helper remains available');

    // Exercise the production helper with the encoded-quote payload from the
    // finding and with ordinary editor values that must retain their meaning.
    const escapeHtmlAttribute = vm.runInNewContext(`(${helperSource})`);
    assert.equal(escapeHtmlAttribute('12px solid #000'), '12px solid #000');
    assert.equal(
        escapeHtmlAttribute('slide x" onmouseover="alert(1) <tag> & value'),
        'slide x&quot; onmouseover=&quot;alert(1) &lt;tag&gt; &amp; value',
    );

    const slideSection = sourceSection(source, 'function renderSlideTab(body)', 'function parseRootVars(css)');
    assert.match(
        slideSection,
        /id="sClass" value="\$\{escapeHtmlAttribute\(\[\.\.\.s\.classList\]/,
        'slide classes must be escaped before entering the inspector template',
    );

    const elementSection = sourceSection(source, 'function renderElementTab(body)', 'function renderSlideTab(body)');
    assert.doesNotMatch(elementSection, /\$\{describe\(/);
    assert.match(elementSection, /button\.textContent = describe\(element\)/);
    assert.match(elementSection, /button\.dataset\.ci = String\(index\)/);

    const themeSection = sourceSection(source, 'function renderThemeTab(body)', 'let codeScope =');
    assert.doesNotMatch(themeSection, /row\.innerHTML/);
    assert.match(themeSection, /name\.textContent = v\.name/);
    assert.match(themeSection, /textInp\.value = v\.value/);
});

test('resetting the native editor restores interaction after dismissing nested modals', () => {
    const source = editorSource();
    const resetSection = sourceSection(source, 'function resetNativeEditorState()', 'function openNativeEditor(');

    assert.match(resetSection, /\$\$\('\.modal-back'\)\.forEach/);
    assert.match(resetSection, /\$\('#app'\)\.inert = false;/);
});

test('slide inspector alignment controls expose localized names and pressed state', () => {
    const source = editorSource();
    const elementSection = sourceSection(source, 'function renderElementTab(body)', 'function renderSlideTab(body)');

    assert.ok(
        elementSection.includes('<div class="seg" id="iAlign" role="group" aria-labelledby="iAlignLabel">'),
        'the visible alignment label must name the button group',
    );

    const alignments = [
        ['left', 'slide_presentation_editor_align_left', 'Align left'],
        ['center', 'slide_presentation_editor_align_center', 'Align center'],
        ['right', 'slide_presentation_editor_align_right', 'Align right'],
    ];
    for (const [alignment, key, fallback] of alignments) {
        assert.ok(
            elementSection.includes(
                `data-v="${alignment}" aria-label="\${escapeHtmlAttribute(tr('${key}', '${fallback}'))}" `
                + `aria-pressed="\${textAlignment === '${alignment}'}"`,
            ),
            `${alignment} alignment must have a translated name and initial pressed state`,
        );
    }
    assert.equal(
        (elementSection.match(/Icons\.withSvgAttributes\("markdownEditorIcons\.align(?:Left|Center|Right)"[^\n]+"aria-hidden": "true", "focusable": "false"/g) || []).length,
        3,
        'decorative alignment icons must stay out of the accessibility tree',
    );

    const stateHelperSource = source.match(
        /function updateAlignmentButtonStates\(buttons, alignment\) \{[\s\S]*?\n\}/,
    )?.[0];
    assert.ok(stateHelperSource, 'alignment state helper remains available');
    const updateAlignmentButtonStates = vm.runInNewContext(`(${stateHelperSource})`);
    const buttons = ['left', 'center', 'right'].map(value => ({
        dataset: { v: value },
        on: null,
        pressed: null,
        classList: {
            toggle(name, selected) {
                assert.equal(name, 'on');
                this.owner.on = selected;
            },
            owner: null,
        },
        setAttribute(name, value) {
            assert.equal(name, 'aria-pressed');
            this.pressed = value;
        },
    }));
    buttons.forEach(button => { button.classList.owner = button; });

    updateAlignmentButtonStates(buttons, 'center');
    assert.deepEqual(
        buttons.map(button => [button.on, button.pressed]),
        [[false, 'false'], [true, 'true'], [false, 'false']],
    );

    const directionHelperSource = source.match(
        /function resolvedTextAlignment\(computedStyle\) \{[\s\S]*?\n\}/,
    )?.[0];
    assert.ok(directionHelperSource, 'logical alignment resolver remains available');
    const resolvedTextAlignment = vm.runInNewContext(`(${directionHelperSource})`);
    assert.equal(resolvedTextAlignment({ textAlign: 'start', direction: 'ltr' }), 'left');
    assert.equal(resolvedTextAlignment({ textAlign: 'start', direction: 'rtl' }), 'right');
    assert.equal(resolvedTextAlignment({ textAlign: 'end', direction: 'rtl' }), 'left');

    const locales = ['ar', 'de', 'en', 'es', 'fr', 'hi', 'it', 'ja', 'pt', 'ru', 'zh'];
    for (const locale of locales) {
        const translations = JSON.parse(
            fs.readFileSync(path.resolve(__dirname, `../../i18n/${locale}/index.json`), 'utf8'),
        );
        for (const [, key] of alignments) {
            assert.equal(typeof translations[key], 'string', `${locale} must translate ${key}`);
            assert.ok(translations[key].trim(), `${locale} must not leave ${key} empty`);
        }
    }
});
