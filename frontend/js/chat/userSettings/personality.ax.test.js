const assert = require('node:assert/strict');
const { execFile } = require('node:child_process');
const path = require('node:path');
const test = require('node:test');
const { promisify } = require('node:util');

const execFileAsync = promisify(execFile);

test('personality presets keep Chrome accessibility value synchronized through persistence', {
    timeout: 60_000,
}, async () => {
    const electronPath = require('electron');
    const runnerPath = path.resolve(
        __dirname,
        '..',
        '..',
        '..',
        '..',
        'electron',
        'tests',
        'fixtures',
        'personality-combobox-ax-runner.js',
    );
    const { stdout } = await execFileAsync(electronPath, [
        '--headless',
        '--disable-gpu',
        runnerPath,
    ], {
        cwd: path.resolve(__dirname, '..', '..', '..', '..'),
        env: {
            ...process.env,
            ELECTRON_DISABLE_SECURITY_WARNINGS: 'true',
        },
        timeout: 50_000,
    });

    assert.deepEqual(JSON.parse(stdout.trim()), {
        presets: 9,
        status: 'passed',
    });
});
