// ===== Extensible Tool Configuration =====
// Maps tool names to display text for headers during generation and after completion
function safeGetLocalStorageItem(key) {
    try {
        return localStorage.getItem(key);
    } catch (_error) {
        return null;
    }
}

function safeSetLocalStorageItem(key, value) {
    try {
        localStorage.setItem(key, value);
    } catch (_error) {
        // Ignore storage failures and keep the current session state.
    }
}

function shouldReduceMotionForStreamMessages() {
    try {
        return typeof window.matchMedia === 'function'
            && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    } catch (_error) {
        return false;
    }
}

function getStreamText(key, fallback) {
    if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}

var chatTitleUtils = window.ChatTitleUtils || {};

function getStreamTextFormatted(key, fallback, vars = {}) {
    if (typeof window !== 'undefined' && typeof window.formatTranslation === 'function') {
        return window.formatTranslation(key, fallback, vars);
    }
    return String(getStreamText(key, fallback)).replace(/\{(\w+)\}/g, (_, token) => {
        const value = vars[token];
        return value === undefined || value === null ? '' : String(value);
    });
}

function escapeStreamHtml(value) {
    if (typeof escapeHtml === 'function') {
        return escapeHtml(value);
    }
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

