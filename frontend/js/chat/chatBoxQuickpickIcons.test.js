const assert = require('node:assert/strict');
const fs = require('node:fs');
const { readFrontendSource } = require('../splitSource.cjs');
const path = require('node:path');
const test = require('node:test');
const { readStreamMessagesSource } = require('./messages/source.cjs');

function extractFunction(source, functionName) {
    const start = source.indexOf(`function ${functionName}(`);
    assert.notEqual(start, -1, `expected ${functionName} in chatBox.js`);

    // Locate the body after the complete parameter list. Some helpers use a
    // destructured object parameter, whose opening brace is not the body.
    const parametersStart = source.indexOf('(', start);
    let parametersDepth = 0;
    let parametersEnd = -1;
    for (let index = parametersStart; index < source.length; index += 1) {
        if (source[index] === '(') parametersDepth += 1;
        if (source[index] === ')') parametersDepth -= 1;
        if (parametersDepth === 0) {
            parametersEnd = index;
            break;
        }
    }
    const bodyStart = source.indexOf('{', parametersEnd);
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

test('in-place dropdown panels reuse the shared chevron SVG', () => {
    const dropdownSource = readFrontendSource(path.join(__dirname, '..', 'common', 'dropdown.js'), 'utf8');
    const iconsSource = readFrontendSource(path.join(__dirname, '..', 'common', 'icons.js'), 'utf8');
    const chevronMarkup = iconsSource.match(/chatFilesChevron:\s*'([^']+)'/)?.[1];
    assert.ok(chevronMarkup, 'expected the shared chat-files chevron icon');
    assert.equal((chevronMarkup.match(/<svg\b/g) || []).length, 1);
    assert.doesNotMatch(chevronMarkup, /<svg\b[^>]*>[\s\S]*<svg\b/);
    assert.match(chevronMarkup, /width="14" height="14" viewBox="0 0 16 16"/);

    const addChevron = extractFunction(dropdownSource, 'addTriggerChevron');
    assert.match(addChevron, /window\.Icons\?\.chatFilesChevron/);
    assert.doesNotMatch(addChevron, /<svg/);
});

test('composer attachment and mention designs use dedicated stylesheets and in-card panels', () => {
    const frontendRoot = path.join(__dirname, '..', '..');
    const index = readFrontendSource(path.join(frontendRoot, 'index.html'), 'utf8');
    const chatBoxStyles = readFrontendSource(path.join(frontendRoot, 'css', 'chat', 'chatBox', 'chatBox.css'), 'utf8');
    const attachmentStyles = readFrontendSource(path.join(frontendRoot, 'css', 'chat', 'chatBox', 'chatBoxFileDropdown.css'), 'utf8');
    const mentionStyles = readFrontendSource(path.join(frontendRoot, 'css', 'chat', 'chatBoxMentionMenu.css'), 'utf8');
    const commonDropdownSource = readFrontendSource(path.join(frontendRoot, 'js', 'common', 'dropdown.js'), 'utf8');
    const commonStyles = readFrontendSource(path.join(frontendRoot, 'css', 'common', 'elements.css'), 'utf8');

    assert.match(index, /chatBox\/chatBoxFileDropdown\.css/);
    assert.match(index, /chatBoxMentionMenu\.css/);
    assert.match(index, /data-dropdown-panel="main"/);
    assert.match(index, /data-dropdown-panel="connections"/);
    assert.match(index, /data-dropdown-panel="chats"/);
    assert.match(index, /data-dropdown-panel="files"/);
    assert.match(index, /class="[^"]*chatbox-attachment-menu[^"]*" id="chatBoxFilesDropdown"/);
    [
        'chatBoxUploadFromComputerButton',
        'chatBoxAddMeetingButton',
        'chatBoxQuickScreenCaptureButton',
        'chatBoxOpenConnectionsButton',
        'chatBoxChooseChatReferencesButton',
        'chatBoxChooseUploadedFilesButton',
        'chatBoxAddGoogleDriveButton',
    ].forEach((buttonId) => {
        assert.match(index, new RegExp(`class="select-dropdown-button" id="${buttonId}"`));
    });
    assert.doesNotMatch(index, /chatbox-attachment-(?:item|row|connection-row)/);
    assert.doesNotMatch(index, /chatbox-attachment-footer|chatBoxAddChatsButton|chatBoxAddFilesButton/);
    assert.match(index, /id="chatBoxFilesQuickpickScroll"/);
    assert.match(index, /id="chatBoxChatReferencesQuickpickScroll"/);
    assert.doesNotMatch(index, /id="chatBoxFilesQuickpick"/);
    assert.doesNotMatch(index, /id="chatBoxChatReferencesQuickpick"/);
    assert.match(attachmentStyles, /\.select-dropdown\.chatbox-attachment-menu/);
    const attachmentShellRule = attachmentStyles.match(/\.select-dropdown\.chatbox-attachment-menu\s*\{([^}]*)\}/s)?.[1] || '';
    assert.match(commonStyles, /--select-dropdown-height-duration:\s*360ms/);
    assert.doesNotMatch(attachmentShellRule, /\bheight\s*:/);
    assert.doesNotMatch(attachmentShellRule, /\b(?:background|border|box-shadow|opacity|pointer-events|visibility)\s*:/);
    assert.doesNotMatch(attachmentStyles, /\.chatbox-attachment-menu\.open\s*\{/);
    assert.doesNotMatch(attachmentStyles, /\.chatbox-attachment-row(?:\W|$)/);
    assert.doesNotMatch(attachmentStyles, /\.select-dropdown-panel(?:\W|$)/);
    assert.match(commonStyles, /\.select-dropdown-panel\[data-dropdown-panel="main"\] > \.select-dropdown-panel-scroll\s*\{[^}]*overflow-y:\s*hidden;/s);
    assert.doesNotMatch(attachmentStyles, /\.chatbox-attachment-footer/);
    assert.match(mentionStyles, /\.mention-menu__intro/);
    assert.match(mentionStyles, /\.mention-menu\s*\{[^}]*width:\s*100%;/s);
    assert.doesNotMatch(chatBoxStyles, /\.mention-menu\s*\{/);
    assert.doesNotMatch(chatBoxStyles, /#chatBoxFilesDropdown\s*\{/);

    const chatBoxSource = readFrontendSource(path.join(frontendRoot, 'js', 'chat', 'chatBox.js'), 'utf8');
    const streamSource = readStreamMessagesSource();
    const modelSelectSource = readFrontendSource(path.join(frontendRoot, 'js', 'chat', 'modelSelect.js'), 'utf8');
    const modelSelectStyles = readFrontendSource(path.join(frontendRoot, 'css', 'chat', 'modelSelect.css'), 'utf8');
    const modalMarkupSource = readFrontendSource(path.join(frontendRoot, 'js', 'chat', 'deleteWarningModals.js'), 'utf8');
    assert.match(chatBoxSource, /window\.createDropdownPanelNavigator\?\.\(/);
    assert.match(chatBoxSource, /chatBoxAttachmentPanelNavigator\?\.reset\(\{ focus: false \}\)/);
    assert.doesNotMatch(chatBoxSource, /ChatBoxSubmenu|chatBoxConnectionsSubmenu|canShowConnectionsSubmenu/);
    const generatedMenuBuilder = extractFunction(chatBoxSource, 'createChatFilesMenuElement');
    assert.match(generatedMenuBuilder, /mainPanel\.dataset\.dropdownPanel = 'main'/);
    assert.match(generatedMenuBuilder, /name: 'chats'/);
    assert.match(generatedMenuBuilder, /name: 'files'/);
    assert.match(generatedMenuBuilder, /connectionsPanel\.dataset\.dropdownPanel = 'connections'/);
    assert.match(generatedMenuBuilder, /button\.dataset\.dropdownOpenPanel = 'connections'/);
    assert.match(generatedMenuBuilder, /button\.dataset\.dropdownOpenPanel = 'chats'/);
    assert.match(generatedMenuBuilder, /button\.dataset\.dropdownOpenPanel = 'files'/);
    assert.match(generatedMenuBuilder, /chatbox-attachment-menu js-chat-files-menu/);
    assert.match(generatedMenuBuilder, /window\.createDropdownPanelNavigator\?\.\(/);
    assert.match(generatedMenuBuilder, /dropdown\.setAttribute\('aria-label', getChatI18nString\('chat_files_menu_aria', 'Add to chat'\)\)/);
    assert.doesNotMatch(generatedMenuBuilder, /has-submenu|select-submenu|mouseenter|mouseleave/);
    assert.match(streamSource, /getDropdownPanelNavigator\?\.\(uploadDropdown\)\?\.reset\(\{ focus: false \}\)/);
    assert.match(streamSource, /uploadDropdown\.getAttribute\('role'\) === 'dialog' \? 'dialog' : 'menu'/);
    assert.doesNotMatch(modelSelectSource, /msAlphaModels|msExperimentalModels|model-select-portal-dropdown|setupNestedHoverHandlers|openMobileSubPanel/);
    assert.doesNotMatch(modelSelectStyles, /model-select-portal-dropdown/);
    assert.doesNotMatch(commonStyles, /\.select-submenu|\.has-submenu/);
    const quickpickCheckboxBuilder = extractFunction(chatBoxSource, 'createChatFilesQuickpickCheckbox');
    assert.match(quickpickCheckboxBuilder, /checkbox\.className = 'form-checkbox'/);
    assert.match(streamSource, /ChatFilesMenu\.createQuickpickCheckbox\(checked\)/);
    assert.match(attachmentStyles, /\.chatbox-files-quickpick__item > \.form-checkbox/);
    assert.doesNotMatch(attachmentStyles, /chatbox-files-quickpick__item-check/);
    assert.doesNotMatch(chatBoxSource, /chatbox-files-quickpick__item-check/);
    assert.doesNotMatch(streamSource, /chatbox-files-quickpick__item-check/);
    const attachmentMeasureSource = extractFunction(commonDropdownSource, 'measureDropdownPanelContent');
    assert.match(attachmentMeasureSource, /child\.offsetTop \|\| 0\) \+ \(child\.offsetHeight \|\| 0/);
    assert.doesNotMatch(attachmentMeasureSource, /getBoundingClientRect/);
    assert.match(commonDropdownSource, /const borderHeight =/);
    assert.match(commonDropdownSource, /header\?\.offsetHeight/);
    assert.match(commonDropdownSource, /Math\.max\(minimumHeight, resolvePanelHeight/);
    assert.match(index, /data-dropdown-panel="(?:chats|files)" data-dropdown-panel-height="420"/);
    assert.doesNotMatch(chatBoxSource, /function (?:open|measure|sync)ChatBoxAttachmentPanel/);
    assert.doesNotMatch(chatBoxSource, /const panelHeights = \{ main: 328/);
    assert.doesNotMatch(attachmentStyles, /height:\s*328px/);
    assert.doesNotMatch(chatBoxSource, /chatBoxAttachmentSelectionSnapshot|updateChatBoxAttachmentPanelSelection|data-chatbox-attachment-(cancel|add)/);
    assert.match(chatBoxSource, /maybeLoadMoreUploadedFiles\('quickpick'\)/);
    assert.match(chatBoxSource, /function maybeLoadMoreChatReferenceQuickpick\(/);
    assert.match(chatBoxSource, /params\.set\('offset', String\(options\.offset \|\| 0\)\)/);
    assert.match(chatBoxSource, /params\.set\('offset', String\(requestedOffset\)\)/);
    assert.doesNotMatch(chatBoxSource, /openChatFilesModal|openChatReferencesModal|chatBoxChooseFilesOverlay|chatBoxChooseChatsOverlay/);
    assert.doesNotMatch(modalMarkupSource, /chatBoxChooseFilesOverlay|chatBoxChooseChatsOverlay/);
    assert.match(chatBoxSource, /function renderMentionCategoryDetail\(/);
    assert.match(chatBoxSource, /dropdown\.setAttribute\('role', 'dialog'\)/);
    const mentionResultBuilder = extractFunction(chatBoxSource, 'buildMentionResultItem');
    assert.match(mentionResultBuilder, /addIcon\.innerHTML = Icons\.plus/);
});
