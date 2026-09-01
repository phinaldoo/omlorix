const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function extractSnippet(source, marker, endMarker) {
    const start = source.indexOf(marker);
    assert.notEqual(start, -1, `expected snippet starting with ${marker}`);
    const end = source.indexOf(endMarker, start);
    assert.notEqual(end, -1, `expected snippet ending before ${endMarker}`);
    return source.slice(start, end);
}

function loadHelpers(translations = {}) {
    const source = fs.readFileSync(path.join(__dirname, 'adminUserNotifications.js'), 'utf8');
    const context = {
        window: {
            getTranslation(key, fallback) {
                return Object.prototype.hasOwnProperty.call(translations, key) ? translations[key] : fallback;
            },
            formatTranslation(key, fallback, vars = {}) {
                const template = this.getTranslation(key, fallback);
                return String(template).replace(/\{(\w+)\}/g, (_, token) => String(vars[token] ?? ''));
            },
        },
    };

    vm.runInNewContext(
        [
            extractSnippet(source, 'const adminUserNotifT =', 'function notifyAdmin('),
            `this.helpers = {
                getAdminShareItemTypeLabel,
                getAdminNotificationMessage,
            };`,
        ].join('\n\n'),
        context,
        { filename: 'adminUserNotifications.js' },
    );

    return context.helpers;
}

test('admin notifications translate share invitation messages from details', () => {
    const { getAdminNotificationMessage } = loadHelpers({
        workspace_notifications_item_file_folder: 'dossier partagé',
        workspace_notifications_inviter_unknown: 'Quelqu’un',
        workspace_notifications_item_untitled: 'Élément sans titre',
        workspace_notifications_invitation_message: '{inviter} vous a invité à {itemType} : {title}',
    });

    const message = getAdminNotificationMessage({
        message: 'Alice invited you to a file folder: Budget',
        details: {
            type: 'share_invitation',
            inviter_name: 'Alice',
            item_type: 'file_folder',
            item_title: 'Budget',
        },
    });

    assert.equal(message, 'Alice vous a invité à dossier partagé : Budget');
});

test('admin notifications fall back to stored message for non-share notifications', () => {
    const { getAdminNotificationMessage } = loadHelpers();

    assert.equal(
        getAdminNotificationMessage({
            message: 'Plain notification',
            details: { type: 'general' },
        }),
        'Plain notification',
    );
});
