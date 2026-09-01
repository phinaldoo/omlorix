/**
 * Generate and render a QR code for the provided TOTP secret.
 * @param {string} secret - The base32 encoded secret returned by the backend.
 * @param {string} account - The user account (typically the email) to embed in the label.
 * @param {string} issuer - Application name shown in authenticator apps.
 */
function generateQrCode(secret, account, issuer = '') {
    try {
        const container = document.getElementById('tfaQrCode');
        if (!container) {
            return;
        }

        // Clear any previous code
        container.innerHTML = '';

        const resolvedIssuer = String(
            issuer || (typeof window.getApplicationName === 'function' ? window.getApplicationName() : '') || 'Omlorix'
        ).trim() || 'Omlorix';

        // Build otpauth URI (TOTP)
        const label = encodeURIComponent(`${resolvedIssuer}:${account}`);
        const uri = `otpauth://totp/${label}?secret=${secret}&issuer=${encodeURIComponent(resolvedIssuer)}&algorithm=SHA1&digits=6&period=30`;
        container.dataset.tfaQrPayload = uri;
        const normalizedSecret = String(secret || '').replace(/\s+/g, '').trim();
        if (normalizedSecret) {
            container.dataset.tfaSecret = normalizedSecret;
        } else {
            delete container.dataset.tfaSecret;
        }
        notifyTfaSetupUiRefresh(container);

        if (typeof QRCode === 'undefined') {
            return;
        }

        // Determine size based on container – fallback to 180px
        const size = Math.min(container.clientWidth, container.clientHeight) || 180;

        // Render QR code
        new QRCode(container, {
            text: uri,
            width: size,
            height: size,
            colorDark: '#000000',
            colorLight: '#ffffff',
            correctLevel: QRCode.CorrectLevel.H
        });
    } catch (err) {
    }
}

/**
 * Render a QR code directly from a provided URI text (e.g., otpauth URI).
 * @param {string} uri - The full text/URI to encode in the QR code.
 */
function renderQrCode(uri) {
    const container = document.getElementById('tfaQrCode');
    if (!container) {
        return;
    }
    const qrPayload = String(uri || '').trim();
    container.dataset.tfaQrPayload = qrPayload;
    const secretFromUri = extractTotpSecret(qrPayload);
    if (secretFromUri) {
        container.dataset.tfaSecret = secretFromUri;
    } else {
        delete container.dataset.tfaSecret;
    }
    container.innerHTML = '';
    notifyTfaSetupUiRefresh(container);

    if (!qrPayload || typeof QRCode === 'undefined') {
        return;
    }
    const size = Math.min(container.clientWidth, container.clientHeight) || 180;
    new QRCode(container, {
        text: qrPayload,
        width: size,
        height: size,
        colorDark: '#000000',
        colorLight: '#ffffff',
        correctLevel: QRCode.CorrectLevel.H
    });
}

function notifyTfaSetupUiRefresh(container) {
    if (typeof window.refresh2FASetupCopyState !== 'function') {
        return;
    }
    window.refresh2FASetupCopyState({
        secret: container?.dataset?.tfaSecret || '',
        otpauthUri: container?.dataset?.tfaQrPayload || '',
    });
}

function extractTotpSecret(uri) {
    if (!uri || typeof uri !== 'string') {
        return '';
    }
    try {
        const normalized = uri.startsWith('otpauth://')
            ? `https://${uri.slice('otpauth://'.length)}`
            : uri;
        const url = new URL(normalized);
        return String(url.searchParams.get('secret') || '').replace(/\s+/g, '').trim();
    } catch (error) {
        const match = uri.match(/[?&]secret=([^&]+)/i);
        if (!match || !match[1]) {
            return '';
        }
        try {
            return decodeURIComponent(match[1]).replace(/\s+/g, '').trim();
        } catch (_error) {
            return match[1].replace(/\s+/g, '').trim();
        }
    }
}
