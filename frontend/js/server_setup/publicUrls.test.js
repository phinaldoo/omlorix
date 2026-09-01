const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function loadPublicUrlModule() {
    const window = {};
    const context = { URL, window };
    vm.createContext(context);
    vm.runInContext(
        fs.readFileSync(path.join(__dirname, 'publicUrls.js'), 'utf8'),
        context
    );
    return window.serverSetupPublicUrls;
}

test('normalizes setup URLs to origins using backend-compatible rules', () => {
    const publicUrls = loadPublicUrlModule();

    assert.equal(
        publicUrls.normalizePublicUrl(' HTTPS//Primary.Example:443/path '),
        'https://primary.example'
    );
    assert.equal(
        publicUrls.normalizePublicUrl('http://localhost:3000/setup'),
        'http://localhost:3000'
    );
});

test('validates ordered unique public URL lists', () => {
    const publicUrls = loadPublicUrlModule();
    const valid = publicUrls.validatePublicUrls([
        'https://primary.example/path',
        'http://localhost:3000',
    ]);
    const duplicate = publicUrls.validatePublicUrls([
        'https://primary.example',
        'https://PRIMARY.example/other',
    ]);

    assert.deepEqual(Array.from(valid.urls), [
        'https://primary.example',
        'http://localhost:3000',
    ]);
    assert.equal(duplicate.valid, false);
    assert.equal(duplicate.messageKey, 'error_public_url_duplicate');
});

test('detects the current origin and redirects through the primary URL', () => {
    const publicUrls = loadPublicUrlModule();

    assert.equal(
        publicUrls.isOriginConfigured(
            ['https://primary.example', 'http://localhost:3000'],
            'http://localhost:3000'
        ),
        true
    );
    assert.equal(
        publicUrls.isOriginConfigured(
            ['https://primary.example'],
            'http://localhost:3000'
        ),
        false
    );
    assert.equal(
        publicUrls.buildRedirectUrl(
            'https://primary.example',
            '/account?from=setup'
        ),
        'https://primary.example/account?from=setup'
    );
    assert.equal(
        publicUrls.buildRedirectUrl(
            'https://primary.example',
            '//untrusted.example/path'
        ),
        'https://primary.example/'
    );
    assert.equal(
        publicUrls.buildRedirectUrl(
            'https://primary.example',
            '/\\evil.com/path'
        ),
        'https://primary.example/'
    );
});
