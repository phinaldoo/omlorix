let userLocationDebounceTimer = null;
let lastSubmittedLocation = null;
let isInitializingUserLocation = false;

function userLocationT(key, fallback) {
    if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}

async function initUserLocation(location) {
    const userLocationInput = document.getElementById('userLocation');

    isInitializingUserLocation = true;
    try {
        const value = typeof location === 'string' ? location : '';
        userLocationInput.value = value;
        lastSubmittedLocation = value;
        attachUserLocationListener(userLocationInput);
    } finally {
        window.setTimeout(() => {
            isInitializingUserLocation = false;
        }, 0);
    }
}

function attachUserLocationListener(input) {
    if (!input || typeof input.addEventListener !== 'function') {
        return;
    }

    if (input.dataset.userLocationInitialized === 'true') {
        return;
    }

    input.addEventListener('input', handleUserLocationInput, { passive: true });
    input.dataset.userLocationInitialized = 'true';
}

function handleUserLocationInput(event) {
    if (isInitializingUserLocation) {
        return;
    }

    const { value } = event.target;

    if (userLocationDebounceTimer) {
        window.clearTimeout(userLocationDebounceTimer);
    }

    userLocationDebounceTimer = window.setTimeout(() => {
        saveUserLocation(value);
    }, 1500);
}

async function saveUserLocation(value) {
    const trimmedValue = typeof value === 'string' ? value.trim() : '';

    if (trimmedValue === lastSubmittedLocation) {
        return;
    }

    try {
        const response = await window.authedFetch('/api/v1/users/location', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ location: trimmedValue })
        });

        if (!response.ok) {
            notifyError(userLocationT('us_general_location_save_failed', 'Failed to save location'));
            return;
        }

        lastSubmittedLocation = trimmedValue;
    } catch (error) {
        console.error('[userLocation] Error updating location', error);
    }
}
