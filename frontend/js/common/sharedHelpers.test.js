const assert = require('node:assert/strict');
const { execFile } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const { promisify } = require('node:util');
const { createElectronSpawnEnvironment, createElectronTestArguments } = require('../../../electron/scripts/dev-electron-runtime.cjs');

test('shared frontend helpers preserve form, picker, and preview behavior in Chromium', { timeout: 60_000 }, async () => {
    const root = path.resolve(__dirname, '../../..');
    const { stdout } = await promisify(execFile)(require('electron'), createElectronTestArguments([
        '--headless', '--disable-gpu', path.join(root, 'electron/tests/fixtures/frontend-shared-helpers-runner.js'),
    ]), {
        cwd: root,
        env: createElectronSpawnEnvironment({ ...process.env, ELECTRON_DISABLE_SECURITY_WARNINGS: 'true' }),
        timeout: 50_000,
    });
    assert.equal(JSON.parse(stdout.trim()).status, 'passed');
});

test('entry pages load extracted helpers before their consumers', () => {
    for (const [page, helper, consumer] of [
        ['index.html', 'common/publicUsers.js', 'chat/todos.js'],
        ['index.html', 'common/textPreview.js', 'chat/files.js'],
        ['chat_share.html', 'common/textPreview.js', 'chat-share.js'],
        ...['index.html', 'admin.html', 'server_setup.html'].map((page) => [page, 'common/dependencyUtils.js', 'admin/helper/settingsController.js']),
        ['admin.html', 'admin/helper/fieldLayout.js', 'admin/modelsCreate.js'],
        ['admin.html', 'admin/helper/selectControls.js', 'admin/websearchProviders.js'],
        ['admin.html', 'admin/mediaGenerationCommon.js', 'admin/imageGeneration.js'],
    ]) {
        const html = fs.readFileSync(path.join(__dirname, '../..', page), 'utf8');
        const helperPosition = html.indexOf(`/js/${helper}`);
        assert.ok(helperPosition >= 0 && helperPosition < html.indexOf(`/js/${consumer}`), `${page}: ${helper} before ${consumer}`);
    }
});
