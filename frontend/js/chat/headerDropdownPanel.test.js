const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const frontendRoot = path.join(__dirname, '..', '..');

test('chat header icon actions have localized accessible names', () => {
    const index = fs.readFileSync(path.join(frontendRoot, 'index.html'), 'utf8');
    const shareButton = index.match(/<button[^>]+id="headerShareButton"[^>]*>[\s\S]*?<\/button>/)?.[0] || '';
    const actionsButton = index.match(/<button[^>]+id="headerDotsButton"[^>]*>[\s\S]*?<\/button>/)?.[0] || '';

    assert.match(shareButton, /type="button"/);
    assert.match(shareButton, /aria-label="Share chat"/);
    assert.match(shareButton, /data-i18n-attr="aria-label:chat_share_modal_title"/);
    assert.match(shareButton, /data-omlorix-icon="upload"[^>]+aria-hidden="true"/);

    assert.match(actionsButton, /aria-label="More options"/);
    assert.match(actionsButton, /data-i18n-attr="aria-label:chat_more_options"/);
    assert.match(actionsButton, /data-omlorix-icon="ellipsis"[^>]+aria-hidden="true"/);

    const localeRoot = path.join(frontendRoot, 'i18n');
    const locales = fs.readdirSync(localeRoot).filter((name) => (
        fs.existsSync(path.join(localeRoot, name, 'index.json'))
    ));

    locales.forEach((locale) => {
        const translations = JSON.parse(fs.readFileSync(path.join(localeRoot, locale, 'index.json'), 'utf8'));
        ['chat_share_modal_title', 'chat_more_options'].forEach((key) => {
            assert.equal(typeof translations[key], 'string', `${locale} is missing ${key}`);
            assert.ok(translations[key].trim(), `${locale} has an empty ${key}`);
        });
    });

    const german = JSON.parse(fs.readFileSync(path.join(localeRoot, 'de', 'index.json'), 'utf8'));
    assert.equal(german.chat_share_modal_title, 'Chat teilen');
    assert.equal(german.chat_more_options, 'Weitere Optionen');
});

test('header chat export formats use shared in-place dropdown panels', () => {
    const index = fs.readFileSync(path.join(frontendRoot, 'index.html'), 'utf8');
    const headerSource = fs.readFileSync(path.join(__dirname, 'header.js'), 'utf8');
    const downloadSource = fs.readFileSync(path.join(__dirname, 'chatDownload.js'), 'utf8');
    const dropdownSource = fs.readFileSync(path.join(frontendRoot, 'js', 'common', 'dropdown.js'), 'utf8');

    assert.match(index, /class="select-dropdown select-dropdown-panel-menu" id="headerDotsButtonDropdown"/);
    assert.match(index, /id="headerDotsButton"[^>]+aria-haspopup="dialog"[^>]+aria-controls="headerDotsButtonDropdown"/);
    assert.match(index, /id="headerDotsButtonDropdown"[^>]+role="dialog"[^>]+aria-modal="false"[^>]+data-i18n-attr="aria-label:chat_more_options"/);
    assert.match(index, /data-dropdown-panel="main"/);
    assert.match(index, /id="downloadChatMenuParent"[^>]+data-dropdown-open-panel="formats"/);
    assert.match(index, /id="downloadChatFormatsPanel" data-dropdown-panel="formats"/);
    assert.match(index, /data-dropdown-panel-back[^>]+data-i18n-attr="aria-label:dropdown_back_aria"/);
    assert.doesNotMatch(index, /downloadChatFormatsSubmenu|id="downloadChatMenuItem"[^>]+has-submenu/);

    assert.match(headerSource, /window\.createDropdownPanelNavigator\?\.\(/);
    assert.doesNotMatch(headerSource, /toggleSubmenu|pointerenter|pointerleave/);
    assert.match(downloadSource, /window\.closeHeaderDropdown\?\.\(\)/);
    assert.match(dropdownSource, /window\.createDropdownPanelNavigator = createDropdownPanelNavigator/);
});

test('shared dropdown back label is translated in every supported locale', () => {
    const localeRoot = path.join(frontendRoot, 'i18n');
    const locales = fs.readdirSync(localeRoot).filter((name) => (
        fs.existsSync(path.join(localeRoot, name, 'index.json'))
    ));

    locales.forEach((locale) => {
        const translations = JSON.parse(fs.readFileSync(path.join(localeRoot, locale, 'index.json'), 'utf8'));
        assert.equal(typeof translations.dropdown_back_aria, 'string', `${locale} is missing dropdown_back_aria`);
        assert.ok(translations.dropdown_back_aria.trim(), `${locale} has an empty dropdown_back_aria`);
    });
});

test('panel-based chat menus retain the shared dropdown opening animation', () => {
    const commonStyles = fs.readFileSync(path.join(frontendRoot, 'css', 'common', 'elements.css'), 'utf8');
    const sidebarStyles = fs.readFileSync(path.join(frontendRoot, 'css', 'chat', 'sidebar.css'), 'utf8');
    const attachmentStyles = fs.readFileSync(path.join(frontendRoot, 'css', 'chat', 'chatBox', 'chatBoxFileDropdown.css'), 'utf8');

    assert.match(commonStyles, /\.select-dropdown\s*\{[\s\S]*?--select-dropdown-closed-transform:[^;]+;[\s\S]*?transition:[\s\S]*?opacity 0\.16s ease,[\s\S]*?transform 0\.18s cubic-bezier\(0\.2, 0\.8, 0\.2, 1\)/);
    assert.doesNotMatch(commonStyles, /#headerDotsButtonDropdown\s*\{[^}]*--select-dropdown-/s);
    assert.doesNotMatch(sidebarStyles, /--sidebar-chat-dropdown-|\.sidebar-element > \.select-dropdown\.open/);
    assert.doesNotMatch(attachmentStyles, /\.chatbox-attachment-menu\s*\{[^}]*--select-dropdown-(?:closed|open|transform-origin)/s);
});
