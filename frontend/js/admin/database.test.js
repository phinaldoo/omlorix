const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

test('backup schedule retention inputs have visible translated labels', () => {
    const adminHtml = fs.readFileSync(path.join(__dirname, '..', '..', 'admin.html'), 'utf8');
    const localeRoot = path.join(__dirname, '..', '..', 'i18n');
    const fields = [
        {
            inputId: 'backupScheduleRetentionCountInput',
            translationKey: 'db_schedule_retention_count_label',
        },
        {
            inputId: 'backupScheduleRetentionDaysInput',
            translationKey: 'db_schedule_retention_days_label',
        },
    ];

    for (const field of fields) {
        // Match the actual label element associated with each number input.
        // The regression occurred because these labels used the sr-only class,
        // leaving sighted users with two indistinguishable populated fields.
        const labelTag = adminHtml.match(new RegExp(
            `<label[^>]*for="${field.inputId}"[^>]*>`,
        ))?.[0];
        assert.ok(labelTag, `missing label for ${field.inputId}`);
        assert.match(labelTag, /class="[^"]*\bform-label\b[^"]*"/);
        assert.doesNotMatch(labelTag, /\bsr-only\b/);
        assert.match(labelTag, new RegExp(`data-i18n="${field.translationKey}"`));
    }

    // User-facing labels must remain available in every supported language.
    for (const locale of fs.readdirSync(localeRoot, { withFileTypes: true })) {
        if (!locale.isDirectory()) continue;
        const translations = JSON.parse(
            fs.readFileSync(path.join(localeRoot, locale.name, 'index.json'), 'utf8'),
        );
        for (const field of fields) {
            assert.equal(
                typeof translations[field.translationKey],
                'string',
                `${locale.name} is missing ${field.translationKey}`,
            );
            assert.ok(
                translations[field.translationKey].trim(),
                `${locale.name} has an empty ${field.translationKey}`,
            );
        }
    }
});

test('backup custom selects persist their visible labels as accessible names', () => {
    const source = fs.readFileSync(path.join(__dirname, 'database.js'), 'utf8');
    const accessibilityHelper = source.match(
        /function applyBackupSelectAccessibility[\s\S]*?(?=\n    function upgradeBackupSelect)/,
    )?.[0];

    assert.ok(accessibilityHelper, 'missing backup select accessibility helper');
    assert.match(
        accessibilityHelper,
        /select\.setAttribute\('aria-labelledby', labelId\);/,
        'the native source select must retain the label used by the generated combobox',
    );
    assert.match(
        accessibilityHelper,
        /trigger\.setAttribute\('aria-labelledby', labelId\);/,
        'the focusable combobox must reference the visible field label',
    );

    for (const selectName of ['destinationProviderSelect', 'scheduleFrequencySelect']) {
        assert.match(
            source,
            new RegExp(`backupModalSelects = \\[([\\s\\S]*?)dom\\.${selectName}`),
            `${selectName} must use the labelled custom-select path`,
        );
    }
});

function backupJobPage(items = [], {
    page = 1,
    pageSize = 10,
    total = items.length,
    totalPages = total ? Math.ceil(total / pageSize) : 0,
} = {}) {
    return {
        items,
        page,
        page_size: pageSize,
        total,
        total_pages: totalPages,
    };
}

function createHarness() {
    const fetchCalls = [];
    const elements = new Map();

    const document = {
        getElementById(id) {
            if (!elements.has(id)) {
                elements.set(id, {
                    children: [],
                    checked: false,
                    dataset: {},
                    disabled: false,
                    hidden: false,
                    innerHTML: '',
                    listeners: {},
                    scrollIntoViewCalls: [],
                    textContent: '',
                    value: '',
                    addEventListener(type, listener) {
                        this.listeners[type] = listener;
                    },
                    appendChild(child) {
                        this.children.push(child);
                        return child;
                    },
                    focus() {},
                    replaceChildren(...children) {
                        this.children = children;
                    },
                    scrollIntoView(options) {
                        this.scrollIntoViewCalls.push(options);
                    },
                    closest() {
                        return null;
                    },
                    getAttribute() {
                        return null;
                    },
                    removeEventListener() {},
                    classList: {
                        add() {},
                        remove() {},
                        toggle() {},
                    },
                    querySelector() {
                        return null;
                    },
                    querySelectorAll() {
                        return [];
                    },
                    setAttribute() {},
                    removeAttribute() {},
                    toggleAttribute() {},
                });
            }
            return elements.get(id);
        },
        addEventListener() {},
        createElement(tagName) {
            return {
                tagName,
                children: [],
                dataset: {},
                style: {},
                addEventListener() {},
                appendChild(child) {
                    this.children.push(child);
                    return child;
                },
                replaceChildren(...children) {
                    this.children = children;
                },
                classList: {
                    add() {},
                    remove() {},
                    toggle() {},
                },
                setAttribute() {},
            };
        },
    };

    const context = {
        AbortController,
        Blob: class Blob {},
        clearTimeout,
        console,
        document,
        fetch: async (url) => {
            fetchCalls.push(url);
            return {
                ok: true,
                status: 200,
                json: async () => [],
                text: async () => '[]',
            };
        },
        FormData: class FormData {},
        Intl,
        JSON,
        Map,
        Promise,
        Set,
        URL,
        URLSearchParams,
        window: {
            addEventListener() {},
            authedFetch: async (url) => {
                fetchCalls.push(url);
                return {
                    ok: true,
                    status: 200,
                    json: async () => [],
                    text: async () => '[]',
                };
            },
            formatTranslation(_key, fallback, variables = {}) {
                return String(fallback).replace(/\{(\w+)\}/g, (_match, token) => (
                    variables[token] === undefined ? '' : String(variables[token])
                ));
            },
            getTranslation(_key, fallback) {
                return fallback;
            },
            registerEscapeHandler() {},
        },
        setTimeout,
    };
    context.globalThis = context;

    const source = fs.readFileSync(path.join(__dirname, 'database.js'), 'utf8');
    vm.runInNewContext(source, context, { filename: 'database.js' });

    return {
        elements,
        fetchCalls,
        window: context.window,
    };
}

test('database admin data loads only when the database page initializer runs', async () => {
    const harness = createHarness();

    assert.deepEqual(harness.fetchCalls, []);

    await harness.window.initDatabasePage();

    assert.ok(
        harness.fetchCalls.includes('/api/v1/admin/backups/destinations'),
        'backup destinations should load when the database page opens'
    );
});

test('backup form requires encryption when plaintext archives are disabled', async () => {
    const harness = createHarness();
    const createRequests = [];
    const response = (payload) => ({
        ok: true,
        status: 200,
        json: async () => payload,
        text: async () => JSON.stringify(payload),
    });

    harness.window.authedFetch = async (url, init = {}) => {
        if (url.endsWith('/capabilities')) {
            return response({
                archive_encryption_available: true,
                plaintext_archives_allowed: false,
            });
        }
        if (url.endsWith('/jobs?page=1&page_size=10')) {
            return response(backupJobPage());
        }
        if (url.endsWith('/create')) {
            createRequests.push({ url, init });
            return response({ id: 'must-not-be-created' });
        }
        return response([]);
    };

    await harness.window.initDatabasePage();
    const checkbox = harness.elements.get('backupNowEncryptionEnabled');
    const policyWarning = harness.elements.get('backupNowPlaintextPolicyWarning');
    policyWarning.hidden = true;
    harness.elements.get('openBackupNowModalButton').listeners.click();

    assert.equal(checkbox.checked, true);
    assert.equal(checkbox.disabled, true);
    assert.equal(policyWarning.hidden, false);
    assert.equal(harness.elements.get('backupNowCreateButton').disabled, false);

    // A stale script or DOM mutation must still be stopped before the request.
    checkbox.disabled = false;
    checkbox.checked = false;
    await harness.elements.get('backupNowCreateButton').listeners.click();

    assert.deepEqual(createRequests, []);
    assert.equal(checkbox.checked, true);
    assert.equal(checkbox.disabled, true);
});

test('backup form preserves plaintext creation when server policy allows it', async () => {
    const harness = createHarness();
    const createRequests = [];
    const response = (payload) => ({
        ok: true,
        status: 200,
        json: async () => payload,
        text: async () => JSON.stringify(payload),
    });

    harness.window.authedFetch = async (url, init = {}) => {
        if (url.endsWith('/capabilities')) {
            return response({
                archive_encryption_available: true,
                plaintext_archives_allowed: true,
            });
        }
        if (url.endsWith('/jobs?page=1&page_size=10')) {
            return response(backupJobPage());
        }
        if (url.endsWith('/create')) {
            createRequests.push({ url, init });
            return response({ id: 'plaintext-job' });
        }
        return response([]);
    };

    await harness.window.initDatabasePage();
    const checkbox = harness.elements.get('backupNowEncryptionEnabled');
    harness.elements.get('openBackupNowModalButton').listeners.click();
    checkbox.checked = false;
    checkbox.listeners.change();
    await harness.elements.get('backupNowCreateButton').listeners.click();

    assert.equal(checkbox.disabled, false);
    assert.equal(createRequests.length, 1);
    assert.deepEqual(JSON.parse(createRequests[0].init.body), {
        destination_id: null,
        encryption_enabled: false,
    });
});

test('rapid database page re-entry restarts an aborted backup-history request', async () => {
    const harness = createHarness();
    const response = (payload) => ({
        ok: true,
        status: 200,
        json: async () => payload,
        text: async () => JSON.stringify(payload),
    });
    let finishDestinations;
    const destinationsResponse = new Promise((resolve) => {
        finishDestinations = resolve;
    });
    let jobsRequestCount = 0;

    harness.window.authedFetch = async (url, init = {}) => {
        if (url.endsWith('/destinations')) {
            return destinationsResponse;
        }
        if (url.endsWith('/jobs?page=1&page_size=10')) {
            jobsRequestCount += 1;
            if (jobsRequestCount === 1) {
                return new Promise((_resolve, reject) => {
                    init.signal.addEventListener('abort', () => {
                        const error = new Error('Request aborted');
                        error.name = 'AbortError';
                        reject(error);
                    }, { once: true });
                });
            }
            return response(backupJobPage([{
                id: 'reloaded-job',
                status: 'success',
                trigger_type: 'manual',
                created_at: '2026-07-30T12:00:00Z',
                artifacts: [],
            }]));
        }
        if (url.endsWith('/capabilities')) {
            return response({});
        }
        return response([]);
    };

    const firstInitialization = harness.window.initDatabasePage();
    harness.window.teardownDatabasePage();
    await harness.window.initDatabasePage();

    assert.equal(jobsRequestCount, 2);
    assert.match(harness.elements.get('backupJobsList').innerHTML, /reloaded-job/);

    finishDestinations(response([]));
    await firstInitialization;
});

test('backup destination tests retain actionable structured diagnostics', () => {
    const source = fs.readFileSync(path.join(__dirname, 'database.js'), 'utf8');

    assert.match(source, /function localizedDestinationTestFailure/);
    assert.match(source, /backup_destination_tls_certificate_invalid/);
    assert.match(source, /backup_destination_authentication_failed/);
    assert.match(source, /backup_destination_permission_denied/);
    assert.match(source, /backup_destination_connection_timeout/);
    assert.match(source, /backup_destination_unreachable/);
    assert.match(source, /backup_destination_path_not_found/);
    assert.match(source, /backup_destination_protocol_unsupported/);
    assert.match(source, /const errorCode = result\?\.details\?\.error_code/);
    assert.match(source, /localizedDestinationTestFailure\(errorCode\)/);
});

test('backup destinations render provider-aware cards without raw config or secrets', async () => {
    const harness = createHarness();
    const response = (payload) => ({
        ok: true,
        status: 200,
        json: async () => payload,
        text: async () => JSON.stringify(payload),
    });
    const destination = {
        id: 'destination-1',
        name: 'WebDAV Storage',
        provider: 'webdav',
        enabled: true,
        config: {
            url: 'https://admin:secret@nas.example.test:5556/webdav?token=private',
            username: 'omlorix-backup',
            password: '***redacted***',
            prefix: 'Omlorix-Backups',
            verify_ssl: false,
            timeout: 60,
        },
    };

    harness.window.authedFetch = async (url) => {
        if (url.endsWith('/destinations')) return response([destination]);
        if (url.endsWith('/capabilities')) return response({});
        return response([]);
    };

    await harness.window.initDatabasePage();

    const html = harness.elements.get('backupDestinationList').innerHTML;
    assert.match(html, /class="db-destination-card"/);
    assert.match(html, />WebDAV Storage</);
    assert.match(html, />WebDAV</);
    assert.match(html, />\s*Active\s*</);
    assert.match(html, /https:\/\/nas\.example\.test:5556\/webdav/);
    assert.match(html, />Omlorix-Backups</);
    assert.match(html, />60 seconds</);
    assert.match(html, /TLS certificate verification is off/);
    assert.match(html, /aria-label="Delete"/);
    assert.doesNotMatch(html, /\*\*\*redacted\*\*\*/);
    assert.doesNotMatch(html, /admin:secret/);
    assert.doesNotMatch(html, /token=private/);
    assert.doesNotMatch(html, /"password"/);
    assert.doesNotMatch(html, /"url"/);
});

test('backup destination connection tests report their result inside the card', async () => {
    const harness = createHarness();
    const notifications = [];
    const response = (payload) => ({
        ok: true,
        status: 200,
        json: async () => payload,
        text: async () => JSON.stringify(payload),
    });
    const destination = {
        id: 'destination-1',
        name: 'Local backups',
        provider: 'local',
        enabled: true,
        config: {},
    };
    let testRequests = 0;

    harness.window.authedFetch = async (url) => {
        if (url.endsWith('/destinations/destination-1/test')) {
            testRequests += 1;
            return response({
                status: 'success',
                details: {
                    status: 'ok',
                    probe_content: 'internal provider response must not be rendered',
                },
            });
        }
        if (url.endsWith('/destinations')) return response([destination]);
        if (url.endsWith('/capabilities')) return response({});
        return response([]);
    };
    harness.window.notifySuccess = (message) => notifications.push(message);

    await harness.window.initDatabasePage();
    const destinationList = harness.elements.get('backupDestinationList');
    const testButton = {
        dataset: {
            destinationAction: 'test',
            destinationId: 'destination-1',
        },
    };
    await destinationList.listeners.click({
        target: {
            closest() {
                return testButton;
            },
        },
    });

    assert.equal(testRequests, 1);
    assert.deepEqual(notifications, ['Connection to “Local backups” verified.']);
    assert.match(destinationList.innerHTML, /Connection verified/);
    assert.match(destinationList.innerHTML, /class="db-destination-test-result is-success"/);
    assert.doesNotMatch(notifications.join(' '), /probe_content|internal provider response/);
    assert.doesNotMatch(destinationList.innerHTML, /probe_content/);
    assert.doesNotMatch(destinationList.innerHTML, /internal provider response/);
});

test('backup destination card copy is translated in every supported locale', () => {
    const localeRoot = path.join(__dirname, '..', '..', 'i18n');
    const requiredKeys = [
        'db_destination_add_btn',
        'db_destination_status_active',
        'db_destination_status_inactive',
        'db_destination_summary_storage',
        'db_destination_summary_local_server',
        'db_destination_summary_credentials',
        'db_destination_summary_credentials_configured',
        'db_destination_tls_unverified',
        'db_destination_tls_verified',
        'db_destination_timeout_seconds',
        'db_destination_tls_warning',
        'db_destination_test_in_progress',
        'db_destination_test_verified',
        'db_destination_test_success_named',
    ];

    for (const locale of fs.readdirSync(localeRoot, { withFileTypes: true })) {
        if (!locale.isDirectory()) continue;
        const translations = JSON.parse(
            fs.readFileSync(path.join(localeRoot, locale.name, 'admin.json'), 'utf8'),
        );
        for (const key of requiredKeys) {
            assert.equal(typeof translations[key], 'string', `${locale.name} is missing ${key}`);
            assert.ok(translations[key].trim(), `${locale.name} has an empty ${key}`);
        }
        assert.ok(
            translations.db_destination_test_success_named.includes('{name}'),
            `${locale.name} destination test success copy is missing {name}`,
        );
        for (const token of ['{start}', '{end}', '{total}']) {
            assert.ok(
                translations.db_history_pagination_showing.includes(token),
                `${locale.name} pagination copy is missing ${token}`,
            );
        }
        assert.ok(
            translations.db_history_page_aria.includes('{page}'),
            `${locale.name} page label is missing {page}`,
        );
    }
});

test('backup history pagination is accessible and translated in every supported locale', () => {
    const adminHtml = fs.readFileSync(path.join(__dirname, '..', '..', 'admin.html'), 'utf8');
    const paginationCss = fs.readFileSync(
        path.join(__dirname, '..', '..', 'css', 'admin', 'adminUserNotifications.css'),
        'utf8',
    );
    const localeRoot = path.join(__dirname, '..', '..', 'i18n');
    const requiredIds = [
        'backupJobsPagination',
        'backupJobsPaginationInfo',
        'backupJobsPaginationPrev',
        'backupJobsPaginationPages',
        'backupJobsPaginationNext',
    ];
    const requiredKeys = [
        'db_history_loading',
        'db_history_load_failed',
        'db_history_pagination_showing',
        'db_history_page_aria',
    ];

    for (const id of requiredIds) {
        assert.match(adminHtml, new RegExp(`id="${id}"`), `missing #${id}`);
    }
    assert.match(
        adminHtml,
        /id="backupJobsPagination"[^>]+data-i18n-attr="aria-label:pagination_navigation_aria"/,
    );
    assert.match(
        paginationCss,
        /\.user-notifications-pagination\[hidden\]\s*\{\s*display:\s*none;/,
    );

    for (const locale of fs.readdirSync(localeRoot, { withFileTypes: true })) {
        if (!locale.isDirectory()) continue;
        const translations = JSON.parse(
            fs.readFileSync(path.join(localeRoot, locale.name, 'admin.json'), 'utf8'),
        );
        for (const key of requiredKeys) {
            assert.equal(typeof translations[key], 'string', `${locale.name} is missing ${key}`);
            assert.ok(translations[key].trim(), `${locale.name} has an empty ${key}`);
        }
    }
});

test('destination save availability uses explicit in-flight state', () => {
    const source = fs.readFileSync(path.join(__dirname, 'database.js'), 'utf8');

    assert.match(source, /destinationSaveInProgress: false/);
    assert.match(source, /disabled = state\.destinationSaveInProgress \|\| !parseAdditionalConfig\(\)\.valid/);
    assert.match(
        source,
        /state\.destinationSaveInProgress = true;[\s\S]*?finally \{\s*state\.destinationSaveInProgress = false;/,
    );
});

test('backup history fetches and renders only the selected backend page', async () => {
    const harness = createHarness();
    const jobsForPage = (prefix, start) => Array.from({ length: 10 }, (_, index) => ({
        id: `${prefix}-${start + index}`,
        status: 'success',
        trigger_type: 'manual',
        created_at: `2026-07-${String(20 - index).padStart(2, '0')}T12:00:00Z`,
        artifacts: [],
    }));
    const firstPage = jobsForPage('first', 1);
    const secondPage = jobsForPage('second', 11);
    let finishSecondPage;
    const secondPageResponse = new Promise((resolve) => {
        finishSecondPage = resolve;
    });
    const response = (payload) => ({
        ok: true,
        status: 200,
        json: async () => payload,
        text: async () => JSON.stringify(payload),
    });

    harness.window.authedFetch = async (url) => {
        harness.fetchCalls.push(url);
        if (url.endsWith('/jobs?page=1&page_size=10')) {
            return response(backupJobPage(firstPage, {
                page: 1,
                total: 21,
                totalPages: 3,
            }));
        }
        if (url.endsWith('/jobs?page=2&page_size=10')) {
            return secondPageResponse;
        }
        if (url.endsWith('/capabilities')) {
            return response({});
        }
        return response([]);
    };

    await harness.window.initDatabasePage();

    const jobsList = harness.elements.get('backupJobsList');
    const pagination = harness.elements.get('backupJobsPagination');
    const paginationInfo = harness.elements.get('backupJobsPaginationInfo');
    const nextButton = harness.elements.get('backupJobsPaginationNext');

    assert.equal(pagination.hidden, false);
    assert.equal(paginationInfo.textContent, 'Showing 1–10 of 21 backups');
    assert.equal(nextButton.disabled, false);
    assert.match(jobsList.innerHTML, /first-1/);
    assert.doesNotMatch(jobsList.innerHTML, /second-11/);

    const pageChange = nextButton.listeners.click();

    // The old cards must stay mounted until page two arrives. Collapsing them
    // into a short loading row is what caused the scroll position to jump.
    assert.match(jobsList.innerHTML, /first-1/);
    assert.doesNotMatch(jobsList.innerHTML, /Loading backup history/);
    assert.deepEqual(
        JSON.parse(JSON.stringify(jobsList.scrollIntoViewCalls)),
        [{ block: 'start' }],
    );

    finishSecondPage(response(backupJobPage(secondPage, {
        page: 2,
        total: 21,
        totalPages: 3,
    })));
    await pageChange;

    assert.equal(paginationInfo.textContent, 'Showing 11–20 of 21 backups');
    assert.match(jobsList.innerHTML, /second-11/);
    assert.doesNotMatch(jobsList.innerHTML, /first-1</);
    assert.deepEqual(
        harness.fetchCalls.filter((url) => url.includes('/jobs?')),
        [
            '/api/v1/admin/backups/jobs?page=1&page_size=10',
            '/api/v1/admin/backups/jobs?page=2&page_size=10',
        ],
    );
    assert.equal(jobsList.scrollIntoViewCalls.length, 1, 'page navigation should scroll once');
});

test('backup history pagination reports refresh failures without rejecting', async () => {
    const harness = createHarness();
    const errors = [];
    const firstPage = [{
        id: 'first-1',
        status: 'success',
        trigger_type: 'manual',
        created_at: '2026-07-20T12:00:00Z',
        artifacts: [],
    }];
    const response = (payload, { ok = true, status = 200 } = {}) => ({
        ok,
        status,
        clone() {
            return response(payload, { ok, status });
        },
        json: async () => payload,
        text: async () => JSON.stringify(payload),
    });

    harness.window.notifyError = (message) => errors.push(message);
    harness.window.authedFetch = async (url) => {
        if (url.endsWith('/jobs?page=1&page_size=10')) {
            return response(backupJobPage(firstPage, {
                page: 1,
                total: 11,
                totalPages: 2,
            }));
        }
        if (url.endsWith('/jobs?page=2&page_size=10')) {
            return response(
                { detail: 'Backup history unavailable.' },
                { ok: false, status: 503 },
            );
        }
        if (url.endsWith('/capabilities')) {
            return response({});
        }
        return response([]);
    };

    await harness.window.initDatabasePage();
    await harness.elements.get('backupJobsPaginationNext').listeners.click();

    assert.deepEqual(errors, ['Backup history unavailable.']);
});

test('backup verification remains disabled and busy until its request finishes', async () => {
    const harness = createHarness();
    const backupJob = {
        id: 'job-1',
        status: 'success',
        trigger_type: 'manual',
        created_at: '2026-07-27T12:00:00Z',
        artifacts: [{ id: 'artifact-1', storage: { scheme: 'local' } }],
    };
    let finishVerification;
    let verificationRequests = 0;
    const verificationResponse = new Promise((resolve) => {
        finishVerification = resolve;
    });
    const response = (payload) => ({
        ok: true,
        status: 200,
        json: async () => payload,
        text: async () => JSON.stringify(payload),
    });

    harness.window.authedFetch = async (url) => {
        if (url.endsWith('/jobs/job-1/verify')) {
            verificationRequests += 1;
            return verificationResponse;
        }
        if (url.includes('/jobs?page=1&page_size=10') && !url.includes('/restore/')) {
            return response(backupJobPage([backupJob]));
        }
        if (url.endsWith('/capabilities')) {
            return response({});
        }
        return response([]);
    };

    await harness.window.initDatabasePage();

    const jobsList = harness.elements.get('backupJobsList');
    const attributes = new Map();
    const classes = new Set(['om-button', 'border', 'db-action-button']);
    const label = { textContent: 'Verify' };
    const verifyButton = {
        dataset: { jobAction: 'verify', jobId: 'job-1' },
        disabled: false,
        isConnected: true,
        classList: {
            add(...names) {
                names.forEach((name) => classes.add(name));
            },
            remove(...names) {
                names.forEach((name) => classes.delete(name));
            },
            toggle(name, force) {
                if (force) classes.add(name);
                else classes.delete(name);
            },
        },
        closest(selector) {
            return selector === '[data-job-action]' ? this : null;
        },
        get offsetWidth() {
            return 0;
        },
        querySelector(selector) {
            return selector === 'span' ? label : null;
        },
        removeAttribute(name) {
            attributes.delete(name);
        },
        setAttribute(name, value) {
            attributes.set(name, value);
        },
        toggleAttribute(name, force) {
            if (force) attributes.set(name, '');
            else attributes.delete(name);
        },
    };

    const firstClick = jobsList.listeners.click({ target: verifyButton });
    const secondClick = jobsList.listeners.click({ target: verifyButton });

    assert.equal(verificationRequests, 1, 'only one verification request should run per job');
    assert.equal(verifyButton.disabled, true);
    assert.equal(attributes.get('aria-busy'), 'true');
    assert.equal(classes.has('loading'), true);
    assert.equal(label.textContent, 'Verifying backup…');

    // A refresh reconstructs the action buttons, so the in-progress state must
    // be rendered from application state instead of living only on the old node.
    await harness.window.initDatabasePage();
    assert.match(jobsList.innerHTML, /db-action-button loading/);
    assert.match(jobsList.innerHTML, /aria-busy="true"/);
    assert.match(jobsList.innerHTML, /Verifying backup…/);

    finishVerification(response({ valid: true }));
    await Promise.all([firstClick, secondClick]);

    assert.equal(verifyButton.disabled, false);
    assert.equal(attributes.has('aria-busy'), false);
    assert.equal(classes.has('loading'), false);
    assert.equal(label.textContent, 'Verify');
});

test('backup downloads preflight with authenticated HEAD before native navigation', async () => {
    const harness = createHarness();
    const notifications = [];
    const downloadRequests = [];
    const backupJob = {
        id: 'job/one',
        status: 'success',
        trigger_type: 'manual',
        created_at: '2026-07-27T12:00:00Z',
        artifacts: [{ id: 'artifact-1', storage: { scheme: 'local' } }],
    };
    const response = (payload) => ({
        ok: true,
        status: 200,
        json: async () => payload,
        text: async () => JSON.stringify(payload),
    });

    harness.window.notifySuccess = (message) => notifications.push(message);
    harness.window.authedFetch = async (url, init = {}) => {
        harness.fetchCalls.push(url);
        if (url.includes('/jobs?page=1&page_size=10') && !url.includes('/restore/')) {
            return response(backupJobPage([backupJob]));
        }
        if (url.endsWith('/capabilities')) {
            return response({});
        }
        if (url.endsWith('/jobs/job%2Fone/download')) {
            downloadRequests.push({ url, method: init.method });
            return response(null);
        }
        return response([]);
    };

    await harness.window.initDatabasePage();

    const jobsList = harness.elements.get('backupJobsList');
    assert.match(
        jobsList.innerHTML,
        /<a[^>]+href="\/api\/v1\/admin\/backups\/jobs\/job%2Fone\/download"[^>]+download[^>]+data-native-backup-download/,
    );
    assert.doesNotMatch(jobsList.innerHTML, /data-job-action="download"/);
    assert.equal(
        harness.fetchCalls.some((url) => url.endsWith('/jobs/job%2Fone/download')),
        false,
        'rendering the download must not fetch and buffer the archive',
    );

    let nativeClicks = 0;
    let defaultPrevented = false;
    const classes = new Set(['om-button', 'border', 'cancel', 'db-action-button']);
    const label = { textContent: 'Download' };
    const downloadLink = {
        href: '/api/v1/admin/backups/jobs/job%2Fone/download',
        dataset: { jobId: 'job/one' },
        click() {
            nativeClicks += 1;
        },
        classList: {
            add(...names) {
                names.forEach((name) => classes.add(name));
            },
            remove(...names) {
                names.forEach((name) => classes.delete(name));
            },
            toggle(name, force) {
                if (force) classes.add(name);
                else classes.delete(name);
            },
        },
        closest(selector) {
            return selector === '[data-native-backup-download]' ? this : null;
        },
        querySelector(selector) {
            return selector === 'span' ? label : null;
        },
        removeAttribute() {},
        setAttribute() {},
    };

    await jobsList.listeners.click({
        target: downloadLink,
        preventDefault() {
            defaultPrevented = true;
        },
    });

    assert.equal(defaultPrevented, true);
    assert.deepEqual(downloadRequests, [{
        url: '/api/v1/admin/backups/jobs/job%2Fone/download',
        method: 'HEAD',
    }]);
    assert.equal(nativeClicks, 1);
    assert.deepEqual(notifications, [
        "Download started. Track its progress in your browser's downloads.",
    ]);
});

test('backup download preflight reports server errors without native navigation', async () => {
    const harness = createHarness();
    const errors = [];
    const response = (payload, { ok = true, status = 200 } = {}) => ({
        ok,
        status,
        clone() {
            return response(payload, { ok, status });
        },
        json: async () => payload,
        text: async () => JSON.stringify(payload),
    });

    harness.window.notifyError = (message) => errors.push(message);
    harness.window.authedFetch = async (url, init = {}) => {
        if (url.endsWith('/jobs/job-1/download') && init.method === 'HEAD') {
            // Browsers intentionally expose no response body for HEAD, even
            // when the server generated a JSON error response.
            return response(null, { ok: false, status: 400 });
        }
        if (url.includes('/jobs?page=1&page_size=10') && !url.includes('/restore/')) {
            return response(backupJobPage());
        }
        if (url.endsWith('/capabilities')) {
            return response({});
        }
        return response([]);
    };

    await harness.window.initDatabasePage();

    let nativeClicks = 0;
    const label = { textContent: 'Download' };
    const downloadLink = {
        href: '/api/v1/admin/backups/jobs/job-1/download',
        dataset: { jobId: 'job-1' },
        click() {
            nativeClicks += 1;
        },
        classList: { add() {}, remove() {}, toggle() {} },
        closest(selector) {
            return selector === '[data-native-backup-download]' ? this : null;
        },
        querySelector(selector) {
            return selector === 'span' ? label : null;
        },
        removeAttribute() {},
        setAttribute() {},
    };

    await harness.elements.get('backupJobsList').listeners.click({
        target: downloadLink,
        preventDefault() {},
    });

    assert.equal(nativeClicks, 0);
    assert.deepEqual(errors, ['Failed to download backup artifact.']);
});
