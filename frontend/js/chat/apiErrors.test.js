const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

class FakeClassList {
    constructor(element) {
        this.element = element;
    }

    add(className) {
        const classes = new Set(String(this.element.className || '').split(/\s+/).filter(Boolean));
        classes.add(className);
        this.element.className = Array.from(classes).join(' ');
    }
}

class FakeElement {
    constructor(tagName, id = '') {
        this.tagName = tagName;
        this.id = id;
        this.children = [];
        this.parentElement = null;
        this.dataset = {};
        this.attributes = {};
        this.className = '';
        this.textContent = '';
        this.innerHTML = '';
        this.classList = new FakeClassList(this);
    }

    appendChild(child) {
        child.parentElement = this;
        this.children.push(child);
        return child;
    }

    insertAdjacentElement(position, child) {
        if (position !== 'afterend' || !this.parentElement) {
            this.appendChild(child);
            return child;
        }
        const siblings = this.parentElement.children;
        const index = siblings.indexOf(this);
        child.parentElement = this.parentElement;
        siblings.splice(index + 1, 0, child);
        return child;
    }

    remove() {
        if (!this.parentElement) return;
        this.parentElement.children = this.parentElement.children.filter((child) => child !== this);
        this.parentElement = null;
    }

    setAttribute(name, value) {
        this.attributes[name] = String(value);
    }

    querySelectorAll(selector) {
        if (!selector.startsWith('.')) {
            return [];
        }
        const className = selector.slice(1);
        const matches = [];
        const visit = (element) => {
            const classes = String(element.className || '').split(/\s+/).filter(Boolean);
            if (classes.includes(className)) {
                matches.push(element);
            }
            element.children.forEach(visit);
        };
        this.children.forEach(visit);
        return matches;
    }

    closest() {
        return null;
    }
}

function loadApiErrors(translations = {}) {
    const source = fs.readFileSync(path.join(__dirname, 'apiErrors.js'), 'utf8');
    const chatAreaContainer = new FakeElement('div', 'chatAreaContainer');
    const notifications = {
        errors: [],
        warnings: [],
    };
    const context = {
        document: {
            createElement(tagName) {
                return new FakeElement(tagName);
            },
            getElementById(id) {
                return id === 'chatAreaContainer' ? chatAreaContainer : null;
            },
        },
        window: {
            getTranslation(key, fallback) {
                return Object.prototype.hasOwnProperty.call(translations, key)
                    ? translations[key]
                    : fallback;
            },
            notifyError(message) {
                notifications.errors.push(message);
            },
            notifyWarning(message) {
                notifications.warnings.push(message);
            },
        },
        Icons: {
            clock: '<svg aria-hidden="true"></svg>',
        },
        Intl,
        Date,
        Number,
        String,
        console,
        requestAnimationFrame(callback) {
            callback();
        },
    };

    vm.runInNewContext(
        `${source}
this.showRateLimitCard = showRateLimitCard;
this.omlorixClassifyTranscriptionLimit = omlorixClassifyTranscriptionLimit;
this.omlorixFormatTranscriptionErrorMessage = omlorixFormatTranscriptionErrorMessage;
this.resolveApiErrorMessage = resolveApiErrorMessage;`,
        context,
        { filename: 'apiErrors.js' },
    );

    return {
        chatAreaContainer,
        notifications,
        showRateLimitCard: context.showRateLimitCard,
        classifyTranscriptionLimit: context.omlorixClassifyTranscriptionLimit,
        formatTranscriptionErrorMessage: context.omlorixFormatTranscriptionErrorMessage,
        resolveApiErrorMessage: context.resolveApiErrorMessage,
    };
}

test('missing-model API errors use translated user-facing copy', () => {
    const { resolveApiErrorMessage } = loadApiErrors();

    assert.equal(
        resolveApiErrorMessage(
            { detail: { code: 'chat_model_required' } },
            'Generic failure',
        ),
        'No model is available for your account. Ask an administrator for access, or add your own model if your account allows it.',
    );
});

test('disabled transcription API errors use translated user-facing copy', () => {
    const translatedMessage = 'Die Transkription ist nicht aktiviert.';
    const { formatTranscriptionErrorMessage } = loadApiErrors({
        chat_transcription_not_enabled: translatedMessage,
    });

    assert.equal(
        formatTranscriptionErrorMessage(
            { detail: { code: 'transcription_not_enabled' } },
            'Meeting transcription failed. Please try again.',
            400,
        ),
        translatedMessage,
    );
});

test('every locale translates the disabled transcription error', () => {
    const localeRoot = path.join(__dirname, '..', '..', 'i18n');
    for (const locale of fs.readdirSync(localeRoot)) {
        const localePath = path.join(localeRoot, locale, 'index.json');
        if (!fs.existsSync(localePath)) continue;
        const translations = JSON.parse(fs.readFileSync(localePath, 'utf8'));
        assert.ok(
            translations.chat_transcription_not_enabled?.trim(),
            `${locale}/index.json is missing chat_transcription_not_enabled`,
        );
    }
});

test('transcription limits distinguish active reservations from consumed quota', () => {
    const { classifyTranscriptionLimit } = loadApiErrors();

    assert.deepEqual(
        JSON.parse(JSON.stringify(classifyTranscriptionLimit({
            detail: {
                code: 'user_dictation_rate_limited',
                reason: 'active_reservation',
            },
        }))),
        {
            code: 'user_dictation_rate_limited',
            reason: 'active_reservation',
            isDictationInProgress: true,
            isDictationRateLimit: false,
        },
    );
    assert.deepEqual(
        JSON.parse(JSON.stringify(classifyTranscriptionLimit({
            detail: {
                code: 'user_dictation_rate_limited',
                reason: 'quota_exceeded',
            },
        }))),
        {
            code: 'user_dictation_rate_limited',
            reason: 'quota_exceeded',
            isDictationInProgress: false,
            isDictationRateLimit: true,
        },
    );
});

test('rate limit card renders without warning or error notification', () => {
    const { chatAreaContainer, notifications, showRateLimitCard } = loadApiErrors();

    const card = showRateLimitCard({
        container: chatAreaContainer,
        errorData: {
            detail: {
                code: 'user_model_rate_limited',
                message: 'You have exceeded your usage limit for this model.',
                model_id: 'gpt-test',
                model_name: 'GPT Test',
                period_label: 'daily',
                quota_unit: 'requests',
                quota_value: 5,
                current_usage: 5,
            },
        },
        fallbackDetail: 'Rate limit reached.',
        showToast: true,
    });

    assert.ok(card);
    assert.equal(chatAreaContainer.querySelectorAll('.chat-rate-limit-card').length, 1);
    assert.deepEqual(notifications.errors, []);
    assert.deepEqual(notifications.warnings, []);
});
