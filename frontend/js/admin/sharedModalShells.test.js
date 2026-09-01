const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const frontendRoot = path.join(__dirname, '../..');
const adminHtml = fs.readFileSync(path.join(frontendRoot, 'admin.html'), 'utf8');

function openingTagWithId(source, id) {
    const match = source.match(new RegExp(`<[^>]+\\bid="${id}"[^>]*>`));
    assert.ok(match, `missing #${id}`);
    return match[0];
}

function dialogTagInside(source, overlayId) {
    const idIndex = source.indexOf(`id="${overlayId}"`);
    assert.notEqual(idIndex, -1, `missing #${overlayId}`);
    const overlayEnd = source.indexOf('>', idIndex);
    const match = source.slice(overlayEnd + 1).match(/<[^>]+role="dialog"[^>]*>/);
    assert.ok(match, `missing dialog inside #${overlayId}`);
    return match[0];
}

function ruleBody(source, selector) {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    return source.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`))?.[1] || '';
}

test('every static admin modal uses the shared accessible shell', () => {
    const overlays = [
        'auditLogsExportOverlay',
        'resetTwofaConfirmOverlay',
        'backupNowOverlay',
        'backupDestinationOverlay',
        'backupScheduleOverlay',
        'databaseActionConfirmOverlay',
        'deleteWebsearchProviderOverlay',
        'userNotificationFormOverlay',
        'editUserReasonOverlay',
        'adminChatModal',
        'viewReasonOverlay',
    ];

    for (const id of overlays) {
        const overlay = openingTagWithId(adminHtml, id);
        const dialog = dialogTagInside(adminHtml, id);
        assert.match(overlay, /class="[^"]*\bshared-modal-overlay\b/);
        assert.match(overlay, /\bhidden\b/);
        assert.match(overlay, /aria-hidden="true"/);
        assert.match(dialog, /class="[^"]*\bshared-modal\b/);
        assert.match(dialog, /aria-modal="true"/);
        assert.match(dialog, /aria-labelledby="[^"]+"/);
        assert.match(dialog, /tabindex="-1"/);
    }

    assert.ok(
        adminHtml.indexOf('/css/common/searchModal.css') > adminHtml.indexOf('/css/admin_chats/style.css'),
        'the shared shell must load after feature styles',
    );
});

test('custom generated admin dialogs compose the shared header body close and footer', () => {
    const sources = [
        'adminSkills.js',
        'deleteWarningModals.js',
        'mcpServers.js',
        'securityIps.js',
        'serviceConnections.js',
    ].map((name) => fs.readFileSync(path.join(__dirname, name), 'utf8'));

    for (const source of sources) {
        assert.match(source, /shared-modal-header shared-modal-header--main/);
        assert.match(source, /shared-modal-body/);
    }

    for (const source of sources.slice(1)) {
        assert.match(source, /shared-modal-footer|actions:/);
    }

    for (const name of ['mcpServers.js', 'securityIps.js', 'serviceConnections.js']) {
        const source = fs.readFileSync(path.join(__dirname, name), 'utf8');
        assert.match(source, /shared-modal-close/);
    }
});

test('admin feature styles no longer redefine shared modal frames', () => {
    const notificationCss = fs.readFileSync(path.join(frontendRoot, 'css/admin/adminUserNotifications.css'), 'utf8');
    const serviceCss = fs.readFileSync(path.join(frontendRoot, 'css/admin/serviceConnections.css'), 'utf8');
    const chatCss = fs.readFileSync(path.join(frontendRoot, 'css/admin_chats/style.css'), 'utf8');

    assert.equal(ruleBody(notificationCss, '.user-notification-form-overlay'), '');
    assert.equal(ruleBody(notificationCss, '.user-notification-form-modal'), '');
    assert.equal(ruleBody(serviceCss, '.service-connection-modal'), '');
    assert.equal(ruleBody(serviceCss, '.service-connection-dialog'), '');
    assert.equal(ruleBody(chatCss, '.admin-chat-modal-overlay'), '');
    assert.equal(ruleBody(chatCss, '.admin-chat-modal'), '');
});
