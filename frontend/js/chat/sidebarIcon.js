function getDefaultSidebarIconSvg() {
    if (typeof Icons === 'object' && Icons?.omlorix) {
        return Icons.omlorix;
    }

    return Icons.omlorix;
}

function getSidebarIconHost(button) {
    return document.getElementById('sidebarHeaderLogo') || button;
}

function updateSidebarInstanceName(applicationName) {
    const nameElement = document.getElementById('sidebarHeaderInstanceName');
    if (!nameElement) {
        return;
    }

    const resolvedName = typeof applicationName === 'string' && applicationName.trim()
        ? applicationName.trim()
        : 'Omlorix';
    nameElement.textContent = resolvedName;
    nameElement.title = resolvedName;
}

function applyDefaultSidebarIcon(button) {
    if (!button) {
        return;
    }
    button.innerHTML = getDefaultSidebarIconSvg();
}

/**
 * Insert a server-sanitized SVG into the document so `currentColor` inherits
 * the application's active theme instead of the operating-system theme seen
 * by an isolated SVG loaded through an `<img>` element.
 */
function applyInlineSidebarSvg(button, svgText) {
    if (!button || !svgText || typeof DOMParser !== 'function') {
        return false;
    }

    const parser = new DOMParser();
    const svgDocument = parser.parseFromString(svgText, 'image/svg+xml');
    const parserError = svgDocument.querySelector('parsererror');
    const svgElement = svgDocument.documentElement;
    if (parserError || !svgElement || svgElement.localName !== 'svg') {
        return false;
    }

    // The icon endpoint only returns SVG markup after backend sanitization.
    // Importing that response inline lets the existing sidebar color rule drive
    // currentColor while keeping the button's accessible label authoritative.
    const importedSvg = document.importNode(svgElement, true);
    importedSvg.setAttribute('aria-hidden', 'true');
    importedSvg.removeAttribute('width');
    importedSvg.removeAttribute('height');
    button.replaceChildren(importedSvg);
    return true;
}

/**
 * Render a raster icon response without losing support for PNG, JPEG, or WebP
 * uploads. The backend currently normalizes those formats to PNG derivatives,
 * but accepting any browser-decodable raster response keeps this UI boundary
 * independent from that storage detail.
 */
function applyRasterSidebarIcon(button, blob) {
    return new Promise((resolve) => {
        const objectUrl = URL.createObjectURL(blob);
        const customIcon = new Image();
        customIcon.alt = '';
        customIcon.onload = () => {
            button.replaceChildren(customIcon);
            URL.revokeObjectURL(objectUrl);
            resolve(true);
        };
        customIcon.onerror = () => {
            URL.revokeObjectURL(objectUrl);
            resolve(false);
        };
        customIcon.src = objectUrl;
    });
}

async function fetchSidebarIcon() {
    const sidebarIconButton = document.getElementById("sidebarHeaderLogoButton");
    if (!sidebarIconButton) {
        return;
    }
    const sidebarIconHost = getSidebarIconHost(sidebarIconButton);

    // Show the default icon while the custom icon loads or when unavailable.
    applyDefaultSidebarIcon(sidebarIconHost);

    try {
        const response = await fetch('/api/v1/settings/icon/get', {
            cache: 'no-cache',
            credentials: 'same-origin',
        });
        if (!response.ok) {
            return;
        }

        const contentType = String(response.headers.get('Content-Type') || '')
            .split(';', 1)[0]
            .trim()
            .toLowerCase();

        if (contentType === 'image/svg+xml') {
            const svgText = await response.text();
            if (!applyInlineSidebarSvg(sidebarIconHost, svgText)) {
                applyDefaultSidebarIcon(sidebarIconHost);
            }
            return;
        }

        const blob = await response.blob();
        if (!blob || blob.size === 0 || !await applyRasterSidebarIcon(sidebarIconHost, blob)) {
            applyDefaultSidebarIcon(sidebarIconHost);
        }
    } catch {
        // Branding is optional. Keep the built-in, theme-aware icon when the
        // custom asset cannot be fetched or decoded.
        applyDefaultSidebarIcon(sidebarIconHost);
    }
}

function initializeSidebarBrand() {
    const applicationName = typeof window.getApplicationName === 'function'
        ? window.getApplicationName()
        : window.applicationName;
    updateSidebarInstanceName(applicationName);
    return fetchSidebarIcon();
}

window.addEventListener('app:applicationNameUpdated', (event) => {
    updateSidebarInstanceName(event?.detail?.applicationName);
});

// Ensure the DOM is fully loaded before trying to find the button.
document.addEventListener('DOMContentLoaded', initializeSidebarBrand);
