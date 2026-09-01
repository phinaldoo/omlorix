const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

test('uploads branding before activating setup and redirects to the primary URL', async () => {
    const events = [];
    const buttons = {
        '.om-button.border.cancel': { disabled: false },
        '.om-button.border.submit': { disabled: false },
    };
    const context = {
        console,
        document: {
            querySelector(selector) {
                return buttons[selector] || null;
            },
        },
        state: {
            currentStep: 5,
            totalSteps: 6,
            serverData: {
                applicationName: 'Omlorix',
                publicUrls: [
                    'https://primary.example',
                    'http://localhost:3000',
                ],
                defaultUserRole: 'pending',
            },
        },
        updateStep() {
            events.push('complete-screen');
        },
        getTranslation(_key, fallback) {
            return fallback;
        },
        setTimeout(callback) {
            callback();
        },
    };
    context.window = context;
    context.location = {
        replace(destination) {
            events.push(`redirect:${destination}`);
        },
    };
    context.uploadBrandingAssets = async () => {
        events.push('upload');
    };
    context.authedFetch = async (_url, options) => {
        events.push('setup');
        assert.deepEqual(JSON.parse(options.body), {
            application_name: 'Omlorix',
            public_url: ['https://primary.example', 'http://localhost:3000'],
            default_user_role: 'pending',
        });
        return {
            ok: true,
            async json() {
                return {
                    status: 'success',
                    public_urls: [
                        'https://primary.example',
                        'http://localhost:3000',
                    ],
                    primary_public_url: 'https://primary.example',
                };
            },
        };
    };
    context.getAccountReturnUrl = () => '/account?from=setup';
    context.serverSetupPublicUrls = {
        buildRedirectUrl(primary, returnPath) {
            return new URL(returnPath, `${primary}/`).href;
        },
    };

    vm.createContext(context);
    vm.runInContext(
        fs.readFileSync(path.join(__dirname, 'api.js'), 'utf8'),
        context
    );

    await context.completeSetup();

    assert.deepEqual(events, [
        'upload',
        'setup',
        'complete-screen',
        'redirect:https://primary.example/account?from=setup',
    ]);
});
