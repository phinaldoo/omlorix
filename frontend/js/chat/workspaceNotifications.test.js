const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');


const SOURCE_PATH = path.join(__dirname, 'workspaceNotifications.js');
const I18N_ROOT = path.resolve(__dirname, '../../i18n');
const ASSET_TRANSLATION_KEYS = [
    'workspace_notifications_category_canvas_assets',
    'workspace_notifications_canvas_asset_request_message',
    'workspace_notifications_canvas_asset_public_request_message',
    'workspace_notifications_canvas_asset_approve',
    'workspace_notifications_canvas_asset_approve_public',
    'workspace_notifications_canvas_asset_reject',
];


test('Canvas asset approval notifications expose translated owner actions', () => {
    const source = fs.readFileSync(SOURCE_PATH, 'utf8');

    assert.match(source, /details\?\.type === 'canvas_asset_approval'/);
    assert.match(source, /\/api\/v1\/files\/canvas\/assets\/decision/);
    assert.match(source, /decision === 'approve'/);
    assert.match(source, /notification_id: String\(notification\.id/);
});


test('Canvas asset approval controls are translated in every locale', () => {
    const locales = fs.readdirSync(I18N_ROOT, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name);

    locales.forEach((locale) => {
        const dictionary = JSON.parse(
            fs.readFileSync(path.join(I18N_ROOT, locale, 'index.json'), 'utf8'),
        );
        ASSET_TRANSLATION_KEYS.forEach((key) => {
            assert.ok(
                Object.prototype.hasOwnProperty.call(dictionary, key),
                `${locale} is missing ${key}`,
            );
        });
    });
});
