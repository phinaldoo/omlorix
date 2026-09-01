const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function createBrowserContext({
    languages = ['en-US'],
    language = languages[0],
    resolvedLocale = language,
    timezone = 'America/New_York',
    authedFetch,
} = {}) {
    const context = {
        console,
        Intl: {
            Locale: Intl.Locale,
            DateTimeFormat() {
                return {
                    resolvedOptions() {
                        return { locale: resolvedLocale, timeZone: timezone };
                    },
                };
            },
        },
        navigator: { language, languages },
        authedFetch,
    };
    context.window = context;
    vm.createContext(context);
    vm.runInContext(
        fs.readFileSync(path.join(__dirname, 'localeDefaults.js'), 'utf8'),
        context,
    );
    return context;
}

test('detects supported language, explicit country, and IANA timezone', () => {
    const context = createBrowserContext({
        languages: ['en'],
        language: 'en-GB',
        resolvedLocale: 'en-US',
        timezone: 'Europe/London',
    });

    assert.deepEqual(
        { ...context.detectUserLocaleDefaults() },
        { language: 'en', country: 'gb', timezone: 'Europe/London' },
    );
});

test('does not replace an explicit unsupported region with a language default', () => {
    const context = createBrowserContext({
        languages: ['de-CH'],
        language: 'de-CH',
        resolvedLocale: 'de-CH',
        timezone: 'Europe/Zurich',
    });

    assert.equal(context.detectUserLocaleDefaults().country, '');
});

test('does not borrow a country from a fallback locale in another language', () => {
    const context = createBrowserContext({
        languages: ['de-CH', 'en-US'],
        language: 'de-CH',
        resolvedLocale: 'de-CH',
        timezone: 'Europe/Zurich',
    });

    assert.deepEqual(
        { ...context.detectUserLocaleDefaults() },
        { language: 'de', country: '', timezone: 'Europe/Zurich' },
    );
});

test('persists and merges only locale fields missing from chat setup', async () => {
    const requests = [];
    const context = createBrowserContext({
        languages: ['en-GB'],
        timezone: 'Europe/London',
        authedFetch: async (url, options) => {
            requests.push({ url, options });
            return {
                ok: true,
                json: async () => ({
                    status: 'success',
                    updated: { general: { country: 'gb', timezone: 'Europe/London' } },
                }),
            };
        },
    });

    const result = await context.applyDetectedLocaleDefaults({ language: 'fr' });

    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, '/api/v1/users/settings/locale-defaults');
    assert.equal(requests[0].options.method, 'PATCH');
    assert.deepEqual(JSON.parse(requests[0].options.body), {
        country: 'gb',
        timezone: 'Europe/London',
    });
    assert.deepEqual(
        { ...result },
        { language: 'fr', country: 'gb', timezone: 'Europe/London' },
    );
});

test('skips persistence when every locale preference is already configured', async () => {
    let requests = 0;
    const context = createBrowserContext({
        authedFetch: async () => {
            requests += 1;
            throw new Error('request should not be made');
        },
    });
    const configured = { language: 'de', country: 'de', timezone: 'Europe/Berlin' };

    const result = await context.applyDetectedLocaleDefaults(configured);

    assert.equal(requests, 0);
    assert.equal(result, configured);
});
