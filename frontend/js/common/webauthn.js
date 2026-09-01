// WebAuthn helpers (base64url + credential conversion)

function _bufferToBase64url(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = '';
    bytes.forEach(b => { binary += String.fromCharCode(b); });
    const base64 = btoa(binary);
    return base64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
}

function _base64urlToBuffer(base64url) {
    const base64 = (base64url || '').replace(/-/g, '+').replace(/_/g, '/');
    const padded = base64 + '='.repeat((4 - (base64.length % 4)) % 4);
    const binary = atob(padded);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
        bytes[i] = binary.charCodeAt(i);
    }
    return bytes.buffer;
}

function _publicKeyCredentialToJSON(pubKeyCred) {
    // Prefer the browser-provided toJSON() when available.
    // Some browsers expose PublicKeyCredential.toJSON as an instance method that must be
    // called with the credential object as its receiver.
    if (pubKeyCred && typeof pubKeyCred.toJSON === 'function') {
        try {
            return _publicKeyCredentialToJSON(pubKeyCred.toJSON());
        } catch (e) {
            // Fall through to manual conversion
        }
    }

    if (pubKeyCred instanceof Array) {
        return pubKeyCred.map(i => _publicKeyCredentialToJSON(i));
    }

    if (pubKeyCred instanceof ArrayBuffer) {
        return _bufferToBase64url(pubKeyCred);
    }

    if (pubKeyCred && typeof pubKeyCred === 'object') {
        const obj = {};
        for (const key in pubKeyCred) {
            if (key === 'toJSON') continue;
            obj[key] = _publicKeyCredentialToJSON(pubKeyCred[key]);
        }
        return obj;
    }

    return pubKeyCred;
}

function _preformatCreateOptions(opts) {
    if (!opts || !opts.publicKey) return opts;
    const publicKey = { ...opts.publicKey };
    if (publicKey.challenge) {
        publicKey.challenge = _base64urlToBuffer(publicKey.challenge);
    }
    if (publicKey.user && publicKey.user.id) {
        publicKey.user = { ...publicKey.user, id: _base64urlToBuffer(publicKey.user.id) };
    }
    if (Array.isArray(publicKey.excludeCredentials)) {
        publicKey.excludeCredentials = publicKey.excludeCredentials.map(cred => ({
            ...cred,
            id: _base64urlToBuffer(cred.id),
        }));
    }
    return { ...opts, publicKey };
}

function _preformatGetOptions(opts) {
    if (!opts || !opts.publicKey) return opts;
    const publicKey = { ...opts.publicKey };
    if (publicKey.challenge) {
        publicKey.challenge = _base64urlToBuffer(publicKey.challenge);
    }
    if (Array.isArray(publicKey.allowCredentials)) {
        publicKey.allowCredentials = publicKey.allowCredentials.map(cred => ({
            ...cred,
            id: _base64urlToBuffer(cred.id),
        }));
    }
    return { ...opts, publicKey };
}

function _normalizeHostname(hostname) {
    return String(hostname || '').trim().toLowerCase().replace(/\.$/, '');
}

function _extractRpIdFromOptions(opts) {
    if (!opts || typeof opts !== 'object' || !opts.publicKey) return '';
    const publicKey = opts.publicKey;
    if (typeof publicKey.rpId === 'string' && publicKey.rpId.trim()) {
        return _normalizeHostname(publicKey.rpId);
    }
    if (publicKey.rp && typeof publicKey.rp.id === 'string' && publicKey.rp.id.trim()) {
        return _normalizeHostname(publicKey.rp.id);
    }
    return '';
}

function _isRpIdCompatibleWithHostname(rpId, hostname) {
    const normalizedRpId = _normalizeHostname(rpId);
    const normalizedHost = _normalizeHostname(hostname);
    if (!normalizedRpId || !normalizedHost) return true;
    if (normalizedHost === normalizedRpId) return true;
    return normalizedHost.endsWith(`.${normalizedRpId}`);
}

function _buildRpIdMismatchMessage({ actionLabel, rpId, currentHost, expectedOrigin }) {
    const translate = typeof window !== 'undefined' && typeof window.getTranslation === 'function'
        ? window.getTranslation
        : (_key, fallback) => fallback;
    const format = typeof window !== 'undefined' && typeof window.formatTranslation === 'function'
        ? window.formatTranslation
        : (key, fallback, vars = {}) => Object.entries(vars).reduce(
            (text, [name, value]) => text.replaceAll(`{${name}}`, String(value)),
            translate(key, fallback),
        );
    const effectiveAction = String(actionLabel || translate('passkey_action_authentication', 'authentication')).trim();
    const targetOrigin = String(expectedOrigin || '').trim()
        || (rpId ? `https://${rpId}` : translate('passkey_configured_public_url', 'the configured public URL'));
    return format(
        'passkey_rp_mismatch_message',
        'Passkey {action} is not available on this URL. You are on "{currentHost}", but passkeys are configured for "{rpId}". Open {targetOrigin} and try again. If this is your server, update Settings -> General -> Public URL.',
        {
            action: effectiveAction,
            currentHost,
            rpId,
            targetOrigin,
        },
    );
}

function _getRpIdMismatchMessage(opts, context = {}) {
    const rpId = _extractRpIdFromOptions(opts);
    const currentHost = _normalizeHostname(window?.location?.hostname || '');
    if (!rpId || !currentHost) return '';
    if (_isRpIdCompatibleWithHostname(rpId, currentHost)) return '';

    return _buildRpIdMismatchMessage({
        actionLabel: context.actionLabel,
        rpId,
        currentHost,
        expectedOrigin: context.expectedOrigin,
    });
}

function _looksLikeRpDomainSecurityError(error) {
    if (String(error?.name || '') !== 'SecurityError') return false;
    const message = String(error?.message || '').toLowerCase();
    return (
        message.includes('effective domain')
        || message.includes('valid domain')
        || message.includes('rp id')
        || message.includes('rpid')
    );
}

function _buildPasskeyOriginSecurityMessage() {
    const translate = typeof window !== 'undefined' && typeof window.getTranslation === 'function'
        ? window.getTranslation
        : (_key, fallback) => fallback;
    return translate(
        'passkey_origin_security_error_message',
        'Passkeys are not available because this browser rejected the current URL or configured passkey domain. Use Omlorix from a configured HTTPS public URL with a valid domain name, then try again. If this is your server, update Settings -> General -> Public URLs.',
    );
}

function _getWebAuthnErrorMessage(error, opts, context = {}) {
    const mismatchMessage = _getRpIdMismatchMessage(opts, context);
    if (mismatchMessage) {
        return mismatchMessage;
    }

    if (_looksLikeRpDomainSecurityError(error)) {
        return _buildPasskeyOriginSecurityMessage();
    }

    return '';
}

const _PASSKEY_AUTO_PROMPT_HINTS_KEY = 'omlorix.passkeyAutoPromptHints.v1';
const _PASSKEY_AUTO_PROMPT_HINT_LIMIT = 50;

function _normalizePasskeyHintIdentifier(identifier) {
    return String(identifier || '').trim().toLowerCase();
}

function _readPasskeyAutoPromptHints() {
    try {
        const parsed = JSON.parse(localStorage.getItem(_PASSKEY_AUTO_PROMPT_HINTS_KEY) || '{}');
        return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
    } catch (_error) {
        return {};
    }
}

function _writePasskeyAutoPromptHints(hints) {
    try {
        const entries = Object.entries(hints || {})
            .filter(([, value]) => value && typeof value === 'object' && value.enabled === true)
            .sort(([, left], [, right]) => Number(right.lastSeenAt || 0) - Number(left.lastSeenAt || 0))
            .slice(0, _PASSKEY_AUTO_PROMPT_HINT_LIMIT);
        localStorage.setItem(_PASSKEY_AUTO_PROMPT_HINTS_KEY, JSON.stringify(Object.fromEntries(entries)));
    } catch (_error) {
        // Storage is a best-effort UX hint. Authentication never depends on it.
    }
}

async function _hashPasskeyHintIdentifier(identifier) {
    const normalizedIdentifier = _normalizePasskeyHintIdentifier(identifier);
    if (!normalizedIdentifier || typeof crypto === 'undefined' || !crypto.subtle || typeof TextEncoder === 'undefined') {
        return '';
    }

    try {
        const origin = typeof window !== 'undefined' ? String(window.location?.origin || '') : '';
        const digest = await crypto.subtle.digest(
            'SHA-256',
            new TextEncoder().encode(`omlorix-passkey-hint:${origin}:${normalizedIdentifier}`),
        );
        return _bufferToBase64url(digest);
    } catch (_error) {
        return '';
    }
}

async function _hasPasskeyAutoPromptHint(identifier) {
    const hintKey = await _hashPasskeyHintIdentifier(identifier);
    if (!hintKey) return false;

    const hints = _readPasskeyAutoPromptHints();
    const hint = hints[hintKey];
    if (!hint || hint.enabled !== true) {
        return false;
    }

    hint.lastSeenAt = Date.now();
    hints[hintKey] = hint;
    _writePasskeyAutoPromptHints(hints);
    return true;
}

async function _markPasskeyAutoPromptHint(identifier) {
    const hintKey = await _hashPasskeyHintIdentifier(identifier);
    if (!hintKey) return false;

    const now = Date.now();
    const hints = _readPasskeyAutoPromptHints();
    hints[hintKey] = {
        enabled: true,
        createdAt: Number(hints[hintKey]?.createdAt || now),
        lastSeenAt: now,
    };
    _writePasskeyAutoPromptHints(hints);
    return true;
}

async function _clearPasskeyAutoPromptHint(identifier) {
    const hintKey = await _hashPasskeyHintIdentifier(identifier);
    if (!hintKey) return false;

    const hints = _readPasskeyAutoPromptHints();
    if (!Object.prototype.hasOwnProperty.call(hints, hintKey)) {
        return false;
    }

    delete hints[hintKey];
    _writePasskeyAutoPromptHints(hints);
    return true;
}

function _getPasskeyHintIdentifierFromCreateOptions(opts) {
    const userName = opts?.publicKey?.user?.name;
    return _normalizePasskeyHintIdentifier(userName);
}

if (typeof window !== 'undefined') {
    window.WebAuthnHelpers = {
        bufferToBase64url: _bufferToBase64url,
        base64urlToBuffer: _base64urlToBuffer,
        publicKeyCredentialToJSON: _publicKeyCredentialToJSON,
        preformatCreateOptions: _preformatCreateOptions,
        preformatGetOptions: _preformatGetOptions,
        getRpIdMismatchMessage: _getRpIdMismatchMessage,
        getWebAuthnErrorMessage: _getWebAuthnErrorMessage,
        normalizePasskeyHintIdentifier: _normalizePasskeyHintIdentifier,
        getPasskeyHintIdentifierFromCreateOptions: _getPasskeyHintIdentifierFromCreateOptions,
        hasPasskeyAutoPromptHint: _hasPasskeyAutoPromptHint,
        markPasskeyAutoPromptHint: _markPasskeyAutoPromptHint,
        clearPasskeyAutoPromptHint: _clearPasskeyAutoPromptHint,
    };
}
