const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function extractFunction(source, functionName) {
    const start = source.indexOf(`function ${functionName}(`);
    assert.notEqual(start, -1, `expected ${functionName} in chatBox.js`);

    const bodyStart = source.indexOf('{', start);
    assert.notEqual(bodyStart, -1, `expected ${functionName} body`);

    let depth = 0;
    for (let index = bodyStart; index < source.length; index += 1) {
        const char = source[index];
        if (char === '{') depth += 1;
        if (char === '}') depth -= 1;
        if (depth === 0) return source.slice(start, index + 1);
    }

    throw new Error(`Could not extract ${functionName}`);
}

/**
 * Extract one complete CSS block with balanced braces. This prevents a greedy
 * regular expression from accidentally matching a rule in a later media query.
 */
function extractCssBlock(source, blockHeader, fromIndex = 0) {
    const start = source.indexOf(blockHeader, fromIndex);
    assert.notEqual(start, -1, `expected ${blockHeader} in stylesheet`);

    const bodyStart = source.indexOf('{', start);
    assert.notEqual(bodyStart, -1, `expected ${blockHeader} body`);

    let depth = 0;
    for (let index = bodyStart; index < source.length; index += 1) {
        const char = source[index];
        if (char === '{') depth += 1;
        if (char === '}') depth -= 1;
        if (depth === 0) return source.slice(bodyStart + 1, index);
    }

    throw new Error(`Could not extract ${blockHeader}`);
}

test('meeting capture only exposes discard when media can be lost', () => {
    const source = readFrontendSource(path.join(__dirname, 'chatBox.js'), 'utf8');
    const context = {};
    vm.runInNewContext(
        `${extractFunction(source, 'shouldShowMeetingCaptureDiscardButton')}\nthis.shouldShow = shouldShowMeetingCaptureDiscardButton;`,
        context,
        { filename: 'chatBox.js' },
    );

    assert.equal(context.shouldShow(false, false), false);
    assert.equal(context.shouldShow(true, false), true);
    assert.equal(context.shouldShow(false, true), true);
});

test('meeting source tabs control the visible result panel after a file is selected', () => {
    const source = readFrontendSource(path.join(__dirname, 'chatBox.js'), 'utf8');
    const context = {};
    vm.runInNewContext(
        `${extractFunction(source, 'getMeetingSourcePanelId')}\nthis.getPanelId = getMeetingSourcePanelId;`,
        context,
        { filename: 'chatBox.js' },
    );

    assert.equal(context.getPanelId('upload', false), 'chatBoxMeetingUploadPanel');
    assert.equal(context.getPanelId('microphone', false), 'chatBoxMeetingCapturePanel');
    assert.equal(context.getPanelId('screen', false), 'chatBoxMeetingCapturePanel');
    assert.equal(context.getPanelId('screen', true, 'screen'), 'chatBoxMeetingResultPanel');
    assert.equal(context.getPanelId('upload', true, 'screen'), 'chatBoxMeetingUploadPanel');
});

test('meeting capture moves focus from Stop to the completed selection before hiding the recorder', () => {
    const source = readFrontendSource(path.join(__dirname, 'chatBox.js'), 'utf8');
    const stopButton = {};
    let focusCount = 0;
    const context = {
        chatBoxMeetingClearSelectionButton: {
            focus() {
                focusCount += 1;
            },
        },
        chatBoxMeetingCapturePanel: {
            contains(element) {
                return element === stopButton;
            },
        },
        document: { activeElement: stopButton },
    };
    vm.runInNewContext(
        `${extractFunction(source, 'moveMeetingFocusToCompletedSelection')}\nthis.moveFocus = moveMeetingFocusToCompletedSelection;`,
        context,
        { filename: 'chatBox.js' },
    );

    context.moveFocus({ name: 'meeting.webm' });
    assert.equal(focusCount, 1);
});

test('shared button styling preserves hidden controls', () => {
    const stylesheet = readFrontendSource(path.join(__dirname, '..', '..', 'css', 'common', 'elementsNew.css'), 'utf8');

    assert.match(
        stylesheet,
        /\.om-button\[hidden\]\s*\{\s*display:\s*none\s*!important;/,
    );
});

test('meeting modal redesign keeps accessible source, recorder, and translated header primitives', () => {
    const modalSource = readFrontendSource(path.join(__dirname, 'deleteWarningModals.js'), 'utf8');
    const chatSource = readFrontendSource(path.join(__dirname, 'chatBox.js'), 'utf8');
    const customSelectSource = readFrontendSource(path.join(__dirname, '..', 'common', 'customSelect.js'), 'utf8');
    const stylesheet = readFrontendSource(path.join(__dirname, '..', '..', 'css', 'chat', 'chatBox', 'chatBoxMeetingModal.css'), 'utf8');

    assert.match(modalSource, /id="chatBoxMeetingSourceTabs" role="tablist"/);
    assert.match(modalSource, /class="chat-meeting-tab-indicator" aria-hidden="true"/);
    assert.match(modalSource, /role="tabpanel" aria-labelledby="chatBoxMeetingUploadOption"/);
    assert.match(modalSource, /id="chatBoxMeetingResultPanel"[^>]*role="tabpanel"/);
    assert.match(modalSource, /class="chat-meeting-record-button" id="chatBoxMeetingCaptureToggleButton"/);
    assert.match(modalSource, /actionsLeadHtml: `<p class="chat-meeting-modal__note">/);
    assert.match(modalSource, /aria-labelledby="chatBoxMeetingLegalBasisLabel" aria-required="true"/);
    assert.doesNotMatch(modalSource, /id="chatBoxMeetingCapture(?:ModeLabel|Status|Details|Hint)" data-i18n=/);
    assert.match(customSelectSource, /'aria-describedby', 'aria-required'/);
    assert.match(customSelectSource, /nativeSelect\.required\s*=/);
    assert.match(chatSource, /function trapMeetingModalFocus\(event\)/);
    assert.match(chatSource, /button\.tabIndex = active \? 0 : -1;/);
    assert.match(chatSource, /chatBoxMeetingCaptureTimer\.setAttribute\('aria-live', 'off'\)/);
    assert.match(chatSource, /document\.addEventListener\('i18n:updated', updateMeetingCaptureUi\)/);
    assert.match(modalSource, /cardClass: 'chat-meeting-modal shared-modal--wide'/);
    assert.match(modalSource, /chat-meeting-modal__header shared-modal-header shared-modal-header--main/);
    assert.match(modalSource, /chat-meeting-modal__body shared-modal-body/);
    assert.doesNotMatch(stylesheet, /#chatBoxMeetingModal > \.warning-navigation/);
    const responsiveSection = stylesheet.indexOf('/* ---------- Responsive bottom sheet ---------- */');
    assert.notEqual(responsiveSection, -1, 'expected the responsive meeting section');
    const mobileStyles = extractCssBlock(stylesheet, '@media (max-width: 640px)', responsiveSection);
    assert.match(mobileStyles, /\.chat-meeting-modal__body\s*\{[^}]*gap: 14px;/);

    const localesRoot = path.join(__dirname, '..', '..', 'i18n');
    for (const locale of fs.readdirSync(localesRoot)) {
        const dictionaryPath = path.join(localesRoot, locale, 'index.json');
        if (!fs.existsSync(dictionaryPath)) continue;
        const dictionary = JSON.parse(readFrontendSource(dictionaryPath, 'utf8'));
        assert.ok(dictionary.chat_meeting_eyebrow, `${locale} translates the meeting modal eyebrow`);
    }
});
