// Shared IANA timezone helpers for user and administrator forms.
//
// Keeping timezone discovery and labels here ensures every form presents the
// same browser-supported identifiers and preserves an existing stored value
// even when that value is an older IANA alias.
(() => {
    'use strict';

    const UTC_TIMEZONE = 'UTC';

    /**
     * Return the browser's current IANA timezone, falling back to UTC when the
     * runtime cannot resolve one.
     */
    function getBrowserTimeZone() {
        try {
            return Intl.DateTimeFormat().resolvedOptions().timeZone || UTC_TIMEZONE;
        } catch (_) {
            return UTC_TIMEZONE;
        }
    }

    /**
     * Check an extra value before adding it to a select. Intl accepts both
     * canonical identifiers and supported IANA aliases.
     */
    function isValidTimeZone(timeZone) {
        const normalized = String(timeZone || '').trim();
        if (!normalized) {
            return false;
        }
        try {
            new Intl.DateTimeFormat('en-US', { timeZone: normalized }).format();
            return true;
        } catch (_) {
            return false;
        }
    }

    /**
     * Return every IANA timezone supported by the current JavaScript runtime.
     * The browser timezone and UTC are pinned to the top for convenience.
     */
    function getSupportedTimeZoneValues(extraValues = []) {
        const browserTimeZone = getBrowserTimeZone();
        let supportedValues = [];

        if (typeof Intl !== 'undefined' && typeof Intl.supportedValuesOf === 'function') {
            try {
                supportedValues = Intl.supportedValuesOf('timeZone');
            } catch (_) {
                supportedValues = [];
            }
        }

        const validExtraValues = (Array.isArray(extraValues) ? extraValues : [extraValues])
            .map((value) => String(value || '').trim())
            .filter(isValidTimeZone);
        const values = Array.from(new Set([
            UTC_TIMEZONE,
            browserTimeZone,
            ...validExtraValues,
            ...(Array.isArray(supportedValues) ? supportedValues : []),
        ])).filter(Boolean);

        return values.sort((left, right) => {
            if (left === browserTimeZone && right !== browserTimeZone) {
                return -1;
            }
            if (right === browserTimeZone && left !== browserTimeZone) {
                return 1;
            }
            if (left === UTC_TIMEZONE && right !== UTC_TIMEZONE) {
                return -1;
            }
            if (right === UTC_TIMEZONE && left !== UTC_TIMEZONE) {
                return 1;
            }
            return left.localeCompare(right);
        });
    }

    /**
     * Format the timezone's current UTC offset. The current date is used
     * intentionally so daylight-saving changes are reflected in the label.
     */
    function getTimeZoneOffsetLabel(timeZone) {
        try {
            const formatter = new Intl.DateTimeFormat('en-US', {
                timeZone,
                hour: '2-digit',
                minute: '2-digit',
                timeZoneName: 'shortOffset',
            });
            const zoneName = formatter
                .formatToParts(new Date())
                .find((part) => part.type === 'timeZoneName')?.value || UTC_TIMEZONE;
            return zoneName.replace(/^GMT$/i, UTC_TIMEZONE).replace(/^GMT/i, UTC_TIMEZONE);
        } catch (_) {
            return UTC_TIMEZONE;
        }
    }

    /**
     * Build the consistent reader-facing label used by all timezone selects.
     */
    function formatTimeZoneLabel(timeZone) {
        return `${timeZone} (${getTimeZoneOffsetLabel(timeZone)})`;
    }

    /**
     * Return select-ready option objects for the shared custom-select adapter.
     */
    function getSupportedTimeZoneOptions(extraValues = []) {
        return getSupportedTimeZoneValues(extraValues).map((timeZone) => ({
            value: timeZone,
            label: formatTimeZoneLabel(timeZone),
        }));
    }

    window.OmlorixTimeZones = Object.freeze({
        UTC_TIMEZONE,
        formatTimeZoneLabel,
        getBrowserTimeZone,
        getSupportedTimeZoneOptions,
        getSupportedTimeZoneValues,
        getTimeZoneOffsetLabel,
        isValidTimeZone,
    });
})();
