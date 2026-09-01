const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');


const source = fs.readFileSync(path.join(__dirname, 'assistantSpeech.js'), 'utf8');
const actionsSource = fs.readFileSync(path.join(__dirname, 'messages', 'actions.js'), 'utf8');


function loadAssistantSpeech(chatSetup) {
    let browserSpeakCount = 0;
    let nextTimerId = 1;
    const timers = new Map();
    const context = {
        AbortController,
        setTimeout(callback, delay) {
            const timerId = nextTimerId;
            nextTimerId += 1;
            timers.set(timerId, { callback, delay });
            return timerId;
        },
        clearTimeout(timerId) {
            timers.delete(timerId);
        },
        document: {
            documentElement: { lang: 'en-US' },
            addEventListener() {},
        },
        localStorage: {
            getItem() {
                return null;
            },
            setItem() {},
        },
        window: {
            chatSetup,
            addEventListener() {},
            getTranslation(_key, fallback) {
                return fallback;
            },
            speechSynthesis: {
                cancel() {},
                speak(utterance) {
                    browserSpeakCount += 1;
                    utterance.onstart?.();
                },
            },
            SpeechSynthesisUtterance: class {
                constructor(text) {
                    this.text = text;
                }
            },
            authedFetch() {},
        },
    };
    context.window.document = context.document;
    context.window.localStorage = context.localStorage;

    vm.runInNewContext(source, context, { filename: 'assistantSpeech.js' });

    return {
        assistantSpeech: context.window.AssistantSpeech,
        window: context.window,
        getBrowserSpeakCount: () => browserSpeakCount,
        runAllTimers() {
            const callbacks = [...timers.values()].map((timer) => timer.callback);
            timers.clear();
            callbacks.forEach((callback) => callback());
        },
        getTimerCount: () => timers.size,
        getTimerDelays: () => [...timers.values()].map((timer) => timer.delay),
    };
}


test('a configured provider remains selected even when it is not ready yet', () => {
    const harness = loadAssistantSpeech({
        read_aloud_provider_id: 'provider-1',
        read_aloud_ready: false,
        read_aloud_uses_browser_native: false,
    });
    const customCalls = [];

    harness.assistantSpeech.registerProvider('custom', {
        canSpeak: () => true,
        speak: () => customCalls.push('custom'),
        stop() {},
    });

    harness.assistantSpeech.speakMessage({ messageId: 'message-1', text: 'Hello' });

    assert.equal(harness.assistantSpeech.getProvider(), 'custom');
    assert.deepEqual(customCalls, ['custom']);
    assert.equal(harness.getBrowserSpeakCount(), 0);
});


test('a configured provider never falls back to browser-native speech', () => {
    const harness = loadAssistantSpeech({
        read_aloud_provider_id: 'provider-1',
        read_aloud_ready: true,
        read_aloud_uses_browser_native: false,
    });

    harness.assistantSpeech.registerProvider('custom', {
        canSpeak: () => false,
        speak() {},
        stop() {},
    });

    assert.throws(
        () => harness.assistantSpeech.speakMessage({ messageId: 'message-1', text: 'Hello' }),
        /Speech playback is not available in this browser\./,
    );
    assert.equal(harness.getBrowserSpeakCount(), 0);
});


test('browser-native speech is used when the native provider is explicitly selected', () => {
    const harness = loadAssistantSpeech({
        read_aloud_provider_id: 'browser_native',
        read_aloud_ready: true,
        read_aloud_uses_browser_native: true,
    });

    harness.assistantSpeech.speakMessage({ text: 'Hello' });

    assert.equal(harness.assistantSpeech.getProvider(), 'browser');
    assert.equal(harness.getBrowserSpeakCount(), 1);
    assert.equal(harness.assistantSpeech.getState().isPlaying, true);
    assert.equal(harness.getTimerCount(), 0);
});


test('speech startup times out, cancels the provider, and exposes a retryable error state', () => {
    const harness = loadAssistantSpeech({
        read_aloud_provider_id: 'provider-1',
        read_aloud_ready: true,
        read_aloud_uses_browser_native: false,
    });
    let stopCount = 0;

    harness.assistantSpeech.registerProvider('custom', {
        canSpeak: () => true,
        speak() {},
        stop() {
            stopCount += 1;
        },
    });

    harness.assistantSpeech.speakMessage({ messageId: 'message-1', text: 'Hello' });
    assert.equal(harness.assistantSpeech.getState().isLoading, true);
    assert.equal(harness.getTimerCount(), 1);
    assert.deepEqual(harness.getTimerDelays(), [10_000]);

    const stopCountBeforeTimeout = stopCount;
    harness.runAllTimers();

    assert.equal(stopCount, stopCountBeforeTimeout + 1);
    const timedOutState = harness.assistantSpeech.getState();
    assert.equal(timedOutState.providerId, 'custom');
    assert.equal(timedOutState.activeMessageId, null);
    assert.equal(timedOutState.isLoading, false);
    assert.equal(timedOutState.isPlaying, false);
    assert.equal(timedOutState.preferredSpeed, 1);
    assert.equal(timedOutState.lastError, 'Audio could not be prepared in time. Please try again.');
    assert.equal(timedOutState.errorMessageId, 'message-1');
});


test('a timed-out custom speech request aborts the in-flight authenticated fetch', () => {
    const harness = loadAssistantSpeech({
        read_aloud_provider_id: 'provider-1',
        read_aloud_ready: true,
        read_aloud_uses_browser_native: false,
    });
    let requestSignal = null;
    harness.window.authedFetch = (_url, options) => {
        requestSignal = options.signal;
        return new Promise(() => {});
    };

    harness.assistantSpeech.speakMessage({ messageId: 'message-1', text: 'Hello' });
    assert.equal(requestSignal.aborted, false);

    harness.runAllTimers();

    assert.equal(requestSignal.aborted, true);
    assert.equal(harness.assistantSpeech.getState().isLoading, false);
    assert.equal(harness.assistantSpeech.getState().errorMessageId, 'message-1');
});


test('a successful retry clears the timeout error and ignores callbacks from the failed attempt', () => {
    const harness = loadAssistantSpeech({
        read_aloud_provider_id: 'provider-1',
        read_aloud_ready: true,
        read_aloud_uses_browser_native: false,
    });
    const attempts = [];
    let startRetry = false;

    harness.assistantSpeech.registerProvider('custom', {
        canSpeak: () => true,
        speak(callbacks) {
            attempts.push(callbacks);
            if (startRetry) {
                callbacks.onStart();
            }
        },
        stop() {},
    });

    harness.assistantSpeech.speakMessage({ messageId: 'message-1', text: 'Hello' });
    harness.runAllTimers();
    assert.equal(harness.assistantSpeech.getState().errorMessageId, 'message-1');

    startRetry = true;
    harness.assistantSpeech.speakMessage({ messageId: 'message-1', text: 'Hello' });
    assert.equal(harness.assistantSpeech.getState().isPlaying, true);
    assert.equal(harness.assistantSpeech.getState().lastError, null);
    assert.equal(harness.assistantSpeech.getState().errorMessageId, null);
    assert.equal(harness.getTimerCount(), 0);

    attempts[0].onStart();
    assert.equal(harness.assistantSpeech.getState().isPlaying, true);
});


test('read-aloud UI surfaces failures and turns the failed message action into Retry', () => {
    assert.match(actionsSource, /notifyError\?\.\(currentError\)/);
    assert.match(actionsSource, /speechState\.errorMessageId/);
    assert.match(actionsSource, /assistant_speech_retry/);
    assert.match(actionsSource, /speedRange\.disabled = isLoading/);
    assert.match(actionsSource, /setAttribute\('aria-busy', isLoading \? 'true' : 'false'\)/);
});


test('per-message read-aloud speed control has a localized accessible name', () => {
    assert.match(
        actionsSource,
        /speedRange\.setAttribute\(\s*'aria-label',\s*getStreamText\('user_settings_speech_speed_aria', 'Assistant speech playback speed'\)\s*\)/,
    );
});


test('read-aloud timeout and retry copy exists in every supported locale', () => {
    const i18nRoot = path.join(__dirname, '..', '..', 'i18n');
    const locales = fs.readdirSync(i18nRoot, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name);

    for (const locale of locales) {
        const translations = JSON.parse(fs.readFileSync(path.join(i18nRoot, locale, 'index.json'), 'utf8'));
        assert.ok(translations.assistant_speech_start_timeout, `${locale} lacks the read-aloud timeout error`);
        assert.ok(translations.assistant_speech_retry, `${locale} lacks the read-aloud retry label`);
        assert.ok(translations.user_settings_speech_speed_aria, `${locale} lacks the read-aloud speed accessible name`);
    }
});
