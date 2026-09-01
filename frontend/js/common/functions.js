// authentication
function commonT(key, fallback) {
    if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}

function commonFormatT(key, fallback, vars = {}) {
    if (typeof window !== 'undefined' && typeof window.formatTranslation === 'function') {
        return window.formatTranslation(key, fallback, vars);
    }
    return String(commonT(key, fallback)).replace(/\{(\w+)\}/g, (_, token) => {
        const value = vars[token];
        return value === undefined || value === null ? '' : String(value);
    });
}

function parseDeviceInfo(userAgent) {
    // Browser detection with version
    let browser = commonT('device_unknown_browser', 'Unknown Browser');
    let browserVersion = "";
    
    // More comprehensive browser detection
    if (userAgent.includes("Firefox/")) {
        browser = "Firefox";
        const match = userAgent.match(/Firefox\/(\d+(\.\d+)?)/);
        if (match) browserVersion = match[1];
    } else if (userAgent.includes("Edg/")) {
        browser = "Edge";
        const match = userAgent.match(/Edg\/(\d+(\.\d+)?)/);
        if (match) browserVersion = match[1];
    } else if (userAgent.includes("OPR/") || userAgent.includes("Opera/")) {
        browser = "Opera";
        const match = userAgent.match(/OPR\/(\d+(\.\d+)?)/);
        if (match) browserVersion = match[1];
    } else if (userAgent.includes("SamsungBrowser/")) {
        browser = "Samsung Browser";
        const match = userAgent.match(/SamsungBrowser\/(\d+(\.\d+)?)/);
        if (match) browserVersion = match[1];
    } else if (userAgent.includes("Chrome/") && !userAgent.includes("Chromium/")) {
        browser = "Chrome";
        const match = userAgent.match(/Chrome\/(\d+(\.\d+)?)/);
        if (match) browserVersion = match[1];
    } else if (userAgent.includes("Safari/")) {
        browser = "Safari";
        const match = userAgent.match(/Version\/(\d+(\.\d+)?)/);
        if (match) browserVersion = match[1];
        
        // Special case for Chrome on iOS which includes Safari in UA
        if (userAgent.includes("CriOS/")) {
            browser = "Chrome";
            const chromeMatch = userAgent.match(/CriOS\/(\d+(\.\d+)?)/);
            if (chromeMatch) browserVersion = chromeMatch[1];
        }
        // Special case for Firefox on iOS which includes Safari in UA
        else if (userAgent.includes("FxiOS/")) {
            browser = "Firefox";
            const ffMatch = userAgent.match(/FxiOS\/(\d+(\.\d+)?)/);
            if (ffMatch) browserVersion = ffMatch[1];
        }
    } else if (userAgent.includes("MSIE ") || userAgent.includes("Trident/")) {
        browser = "Internet Explorer";
        const match = userAgent.match(/MSIE (\d+(\.\d+)?)/);
        if (match) browserVersion = match[1];
        else {
            const tridentMatch = userAgent.match(/Trident\/(\d+(\.\d+)?)/);
            if (tridentMatch) {
                // Convert Trident version to IE version
                const tridentToIE = { '4.0': '8.0', '5.0': '9.0', '6.0': '10.0', '7.0': '11.0' };
                browserVersion = tridentToIE[tridentMatch[1]] || '';
            }
        }
    }
    
    // OS detection with version when available
    let os = commonT('device_unknown_os', 'Unknown OS');
    let osVersion = "";
    let deviceType = 'desktop';
    
    // --- Reordered OS detection: iOS → Android → Windows → macOS → Linux ---
    // iOS detection with version and device type
    if (/iPhone|iPad|iPod/i.test(userAgent) ||
        (/AppleWebKit/i.test(userAgent) && /Mobile/i.test(userAgent) && /Safari/i.test(userAgent))) {
        os = "iOS";
        const match = userAgent.match(/OS (\d+[._]\d+([._]\d+)?)/);
        if (match) osVersion = match[1].replace(/_/g, '.');

        if (/iPad/i.test(userAgent) || (/AppleWebKit/i.test(userAgent) && /Mobile/i.test(userAgent) && /Macintosh/i.test(userAgent))) {
            deviceType = 'tablet';
        } else {
            deviceType = 'mobile';
        }
    }
    // Android detection with version
    else if (/Android/i.test(userAgent)) {
        os = "Android";
        deviceType = 'mobile';
        const match = userAgent.match(/Android (\d+(\.\d+)?)/);
        if (match) osVersion = match[1];

        // Detect if tablet
        if (/tablet|SM-T/i.test(userAgent) || (!/Mobile/i.test(userAgent) && /Android/i.test(userAgent))) {
            deviceType = 'tablet';
        }
    }
    // Windows detection with version
    else if (/Windows NT/i.test(userAgent)) {
        os = "Windows";
        const ntVersions = {
            '10.0': '10/11', // Windows 10 and 11 share the same NT version
            '6.3': '8.1',
            '6.2': '8',
            '6.1': '7',
            '6.0': 'Vista',
            '5.2': 'XP x64',
            '5.1': 'XP',
            '5.0': '2000'
        };
        const match = userAgent.match(/Windows NT (\d+\.\d+)/);
        if (match && ntVersions[match[1]]) {
            osVersion = ntVersions[match[1]];
        }
    }
    // macOS detection with version (execute AFTER iOS detection)
    else if (/Macintosh|Mac OS X/i.test(userAgent)) {
        os = "macOS";
        const match = userAgent.match(/Mac OS X (\d+[._]\d+([._]\d+)?)/);
        if (match) {
            osVersion = match[1].replace(/_/g, '.');
        }
    }
    // Linux detection (runs only if none of the above matched)
    else if (/Linux/i.test(userAgent) && !/Android/i.test(userAgent)) {
        os = "Linux";
        if (/Ubuntu/i.test(userAgent)) os = "Ubuntu";
        else if (/Fedora/i.test(userAgent)) os = "Fedora";
        else if (/Debian/i.test(userAgent)) os = "Debian";
    }
    
    // Device model detection for popular devices
    let deviceModel = "";
    
    // First check if this is a mobile Apple device
    if (os === "iOS") {
        if (/iPhone/i.test(userAgent)) {
            deviceModel = "iPhone";
        } else if (/iPad/i.test(userAgent)) {
            deviceModel = "iPad";
        } else if (/iPod/i.test(userAgent)) {
            deviceModel = "iPod";
        } else if (/Mobile/i.test(userAgent)) {
            deviceModel = "iPhone"; // Most likely an iPhone if Mobile Safari on iOS
        }
    } 
    // Then check for other devices only if we haven't identified an iOS device
    else if (/SM-G|Galaxy S/i.test(userAgent)) {
        deviceModel = "Samsung Galaxy";
    } else if (/Pixel/i.test(userAgent)) {
        deviceModel = "Google Pixel";
    }
    
    // --- Remove version numbers from name formatting ---
    let name = browser;
    // Version numbers intentionally omitted
    
    if (os === "iOS" && deviceModel) {
        name = commonFormatT('device_name_on', '{browser} on {device}', {
            browser,
            device: deviceModel,
        });
    } else {
        name = commonFormatT('device_name_on', '{browser} on {device}', {
            browser,
            device: os,
        });
        if (deviceModel) name += ` (${deviceModel})`;
    }
    
    return { 
        name: name,
        deviceType: deviceType,
        browser: browser,
        browserVersion: browserVersion,
        os: os,
        osVersion: osVersion,
        deviceModel: deviceModel
    };
}



function _parseUtcDate(isoString) {
    if (!isoString) return null;

    let normalized = typeof isoString === 'string' ? isoString.trim() : '';
    if (!normalized) return null;

    if (!normalized.includes('T') && normalized.includes(' ')) {
        normalized = normalized.replace(' ', 'T');
    }

    const hasExplicitTimezone = /([zZ]|[+-]\d{2}:?\d{2})$/.test(normalized);
    if (!hasExplicitTimezone) {
        normalized += 'Z';
    }

    const date = new Date(normalized);
    return Number.isNaN(date.getTime()) ? null : date;
}

function formatRelativeTime(isoString) {
    const date = _parseUtcDate(isoString);
    if (!date) return "";

    const now = new Date();
    const diffMs = Math.max(0, now - date);
    const seconds = Math.floor(diffMs / 1000);
    const minutes = Math.floor(seconds / 60);
    const hours = Math.floor(minutes / 60);
    const days = Math.floor(hours / 24);

    const language = (typeof document !== 'undefined' && document.documentElement?.lang) ||
        (typeof navigator !== 'undefined' && navigator.language) ||
        'en';
    if (typeof Intl !== 'undefined' && typeof Intl.RelativeTimeFormat === 'function') {
        const relativeFormatter = new Intl.RelativeTimeFormat(language, { numeric: 'auto' });
        if (seconds < 60) return relativeFormatter.format(0, 'second');
        if (minutes < 60) return relativeFormatter.format(-minutes, 'minute');
        if (hours < 24) return relativeFormatter.format(-hours, 'hour');
        if (days < 7) return relativeFormatter.format(-days, 'day');
    }

    if (seconds < 60) return commonT('relative_time_now', 'just now');
    if (minutes < 60) return commonFormatT('relative_time_minutes_ago', '{count} min ago', { count: minutes });
    if (hours < 24) return commonFormatT('relative_time_hours_ago', '{count} hr ago', { count: hours });
    if (days === 1) return commonT('relative_time_yesterday', 'yesterday');
    if (days < 7) return commonFormatT('relative_time_days_ago', '{count} days ago', { count: days });

    return date.toLocaleDateString(language, { day: '2-digit', month: '2-digit', year: 'numeric' });
}




function isValidEmail(email) {
    const re = /^(([^<>()\[\]\\.,;:\s@"]+(\.[^<>()\[\]\\.,;:\s@"]+)*)|(".+"))@((\[[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}])|(([a-zA-Z\-0-9]+\.)+[a-zA-Z]{2,}))$/;
    return re.test(String(email).toLowerCase());
}


function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}


function formatEnumLabel(value, fallback = '') {
    if (!value) return fallback;
    return String(value).replace(/_/g, ' ').trim().toUpperCase();
}
