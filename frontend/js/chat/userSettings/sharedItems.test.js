const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const SHARED_ITEMS_PATH = path.join(__dirname, 'sharedItems.js');
const USER_SETTINGS_CSS_PATH = path.join(__dirname, '../../../css/userSettings/style.css');

test('shared item settings reuse the chat-share modal shell', () => {
    const source = fs.readFileSync(SHARED_ITEMS_PATH, 'utf8');

    assert.match(source, /className = 'cs-overlay shared-modal-overlay si-manage-overlay'/);
    assert.match(source, /class="cs-modal shared-modal shared-modal--fit si-manage-modal"/);
    assert.match(source, /class="cs-header shared-modal-header shared-modal-header--main"/);
    assert.match(source, /class="cs-body shared-modal-body"/);
    assert.match(source, /class="cs-footer shared-modal-footer"/);
    assert.match(source, /class="cs-link-card"/);
    assert.match(source, /function closeManageModal\(\)[\s\S]*overlay\.inert = true;[\s\S]*overlay\.setAttribute\('aria-hidden', 'true'\)/);
});

test('shared item settings use the chat-share summary actions and trash icon', () => {
    const source = fs.readFileSync(SHARED_ITEMS_PATH, 'utf8');
    const css = fs.readFileSync(USER_SETTINGS_CSS_PATH, 'utf8');

    assert.match(source, /data-manage-action="copy-link".*Icons\.copy/s);
    assert.match(source, /data-manage-action="open-link".*Icons\.open_window/s);
    assert.match(source, /data-manage-action="edit".*Icons\.create/s);
    assert.match(source, /data-manage-action="unshare".*Icons\.trash/s);
    assert.match(source, /class="si-action-btn si-unshare-btn".*Icons\.trash/s);
    assert.match(css, /\.si-item-actions\s*\{[^}]*transform:\s*none;[^}]*transition:\s*none;/s);
});

test('shared item edit controls follow the chat-share form pattern', () => {
    const source = fs.readFileSync(SHARED_ITEMS_PATH, 'utf8');
    const css = fs.readFileSync(USER_SETTINGS_CSS_PATH, 'utf8');

    assert.match(source, /class="cs-radio-group"/);
    assert.match(source, /id="siManagePasswordToggle"/);
    assert.match(source, /id="siManageExpiryToggle"/);
    assert.match(source, /saveManagedSettings/);
    assert.match(source, /overlay\.hidden = false;[\s\S]*overlay\.setAttribute\('aria-hidden', 'false'\)/);
    assert.match(css, /\.si-manage-overlay\s*\{[^}]*--shared-modal-z-index: 1600;/s);
});

test('partial managed-setting failures refresh the current backend item', () => {
    const source = fs.readFileSync(SHARED_ITEMS_PATH, 'utf8');
    const saveBody = source.slice(
        source.indexOf('async function saveManagedSettings'),
        source.indexOf('async function handleManageAction'),
    );

    assert.match(saveBody, /catch \(error\)[\s\S]*await refreshManagedItem\(\(candidate\) => getItemKey\(candidate\) === getItemKey\(item\)\)[\s\S]*notifyError\(message\)/);
});
