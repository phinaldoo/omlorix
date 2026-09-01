(function initializeLocaleDefaults(root) {
    'use strict';

    const SUPPORTED_LANGUAGES = new Set([
        'en', 'de', 'es', 'fr', 'hi', 'ar', 'zh', 'ja', 'it', 'pt', 'ru',
    ]);
    const SUPPORTED_COUNTRIES = new Set([
        'ar', 'au', 'ca', 'cn', 'de', 'es', 'fr', 'gb', 'in', 'it', 'jp', 'us',
    ]);
    const LEGACY_REGION_ALIASES = { UK: 'GB' };
    const FALLBACK_REGIONS = {
        de: 'DE', en: 'US', es: 'ES', fr: 'FR', hi: 'IN',
        it: 'IT', ja: 'JP', pt: 'BR', zh: 'CN',
    };

    function getLocaleCandidates() {
        const candidates = [];
        const addCandidate = (value) => {
            const candidate = typeof value === 'string' ? value.trim() : '';
            if (candidate && !candidates.includes(candidate)) {
                candidates.push(candidate);
            }
        };

        try {
            Array.from(root.navigator?.languages || []).forEach(addCandidate);
        } catch (error) {
            // Privacy-focused browsers may restrict locale properties.
        }
        addCandidate(root.navigator?.language);
        try {
            addCandidate(root.Intl.DateTimeFormat().resolvedOptions().locale);
        } catch (error) {
            // Navigator signals remain sufficient when Intl locale lookup fails.
        }
        return candidates;
    }

    function getLocaleLanguage(locale) {
        const match = String(locale || '').match(/^([a-z]{2,3})(?:[-_]|$)/i);
        return match ? match[1].toLowerCase() : '';
    }

    function getExplicitRegion(locale) {
        const normalized = String(locale || '').replace(/_/g, '-');
        if (!normalized) return '';
        try {
            if (typeof root.Intl.Locale === 'function') {
                return new root.Intl.Locale(normalized).region?.toUpperCase() || '';
            }
        } catch (error) {
            // Fall through to a parser that also supports older webviews.
        }
        return normalized.match(/-([a-z]{2})(?:-|$)/i)?.[1]?.toUpperCase() || '';
    }

    function inferRegion(locale) {
        const normalized = String(locale || '').replace(/_/g, '-');
        if (!normalized) return '';
        try {
            if (typeof root.Intl.Locale === 'function') {
                return new root.Intl.Locale(normalized).maximize().region?.toUpperCase() || '';
            }
        } catch (error) {
            // The explicit fallback map covers supported language-only locales.
        }
        return FALLBACK_REGIONS[getLocaleLanguage(normalized)] || '';
    }

    function normalizeSupportedCountry(region) {
        const normalizedRegion = LEGACY_REGION_ALIASES[region] || region;
        const country = String(normalizedRegion || '').toLowerCase();
        return SUPPORTED_COUNTRIES.has(country) ? country : '';
    }

    /** Detect supported account locale defaults without changing browser state. */
    function detectUserLocaleDefaults() {
        const candidates = getLocaleCandidates();
        const language = candidates
            .map(getLocaleLanguage)
            .find((candidate) => SUPPORTED_LANGUAGES.has(candidate)) || 'en';
        // Region and language must come from the same locale preference. For
        // example, de-CH followed by en-US means German with an unsupported CH
        // country, not German with a US country borrowed from the fallback.
        const matchingCandidates = candidates.filter(
            (candidate) => getLocaleLanguage(candidate) === language,
        );
        const explicitRegion = matchingCandidates.map(getExplicitRegion).find(Boolean) || '';
        const inferredRegion = explicitRegion
            ? ''
            : inferRegion(matchingCandidates[0] || language);
        // An explicit unsupported region intentionally stays blank instead of
        // being overwritten with the language's likely/default territory.
        const country = normalizeSupportedCountry(explicitRegion || inferredRegion);
        let timezone = '';
        try {
            timezone = root.Intl.DateTimeFormat().resolvedOptions().timeZone || '';
        } catch (error) {
            // The backend keeps timezone blank when the browser cannot expose it.
        }
        return { language, country, timezone };
    }

    /** Persist only missing locale fields and merge the accepted values into bootstrap data. */
    async function applyDetectedLocaleDefaults(chatSetup = {}) {
        const missingFields = ['language', 'country', 'timezone']
            .filter((field) => !String(chatSetup?.[field] || '').trim());
        if (missingFields.length === 0 || typeof root.authedFetch !== 'function') {
            return chatSetup;
        }

        const detected = detectUserLocaleDefaults();
        const payload = Object.fromEntries(
            missingFields
                .filter((field) => detected[field])
                .map((field) => [field, detected[field]]),
        );
        if (Object.keys(payload).length === 0) {
            return chatSetup;
        }

        try {
            const response = await root.authedFetch('/api/v1/users/settings/locale-defaults', {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!response.ok) {
                return chatSetup;
            }
            const result = await response.json();
            return { ...chatSetup, ...(result?.updated?.general || {}) };
        } catch (error) {
            console.warn('Unable to initialize account locale defaults:', error);
            return chatSetup;
        }
    }

    root.detectUserLocaleDefaults = detectUserLocaleDefaults;
    root.applyDetectedLocaleDefaults = applyDetectedLocaleDefaults;
})(typeof window !== 'undefined' ? window : globalThis);
