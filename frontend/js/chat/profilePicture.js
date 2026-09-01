/**
 * Profile Picture Manager
 * Handles profile picture upload, display, and deletion across multiple UI components.
 */

const ProfilePictureState = {
    blobUrl: null,

    revokeBlobUrl() {
        if (this.blobUrl) {
            URL.revokeObjectURL(this.blobUrl);
            this.blobUrl = null;
        }
    },

    createBlobUrl(blob) {
        this.revokeBlobUrl();
        this.blobUrl = URL.createObjectURL(blob);
        return this.blobUrl;
    },
};

const PROFILE_PICTURE_ENDPOINT = '/api/v1/users/profile-picture/get';
const CHAT_SETUP_ENDPOINT = '/api/v1/settings/chat/setup';

function translateProfilePictureText(key, fallback) {
    if (typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}

function formatProfilePictureText(key, fallback, vars = {}) {
    let text = translateProfilePictureText(key, fallback);
    Object.entries(vars).forEach(([name, value]) => {
        text = text.replace(new RegExp(`\\{${name}\\}`, 'g'), String(value));
    });
    return text;
}

const ELEMENT_IDS = {
    // All profile surfaces use the same resolved initials when an image is not
    // available. The sidebar element starts hidden behind its loading skeleton,
    // which prevents the fallback from flashing before setup has completed.
    initials: ['sidebarProfileInitials', 'profileInitials'],
    images: ['sidebarProfilePicture', 'profilePicture'],
    controls: {
        input: 'profilePictureInput',
        deleteBtn: 'deleteProfilePicBtn',
        deleteIcon: 'deleteProfilePicIcon',
        editBtn: 'editProfilePicture',
        sourceNote: 'profilePictureSourceNote',
    },
};

function getInitials(firstName, lastName) {
    const first = (firstName || '').trim();
    const last = (lastName || '').trim();

    if (!first && !last) return 'U';
    if (!last) return first.charAt(0).toUpperCase();
    return (first.charAt(0) + last.charAt(0)).toUpperCase();
}

function getActiveProfileCache() {
    if (typeof window !== 'undefined' && window.activeUserProfile && typeof window.activeUserProfile === 'object') {
        return window.activeUserProfile;
    }
    return {};
}

function normalizeProfileSetup(profileSetupOrLegacy, firstName = '', lastName = '') {
    const cachedProfile = getActiveProfileCache();
    const cachedFirstName = typeof cachedProfile.first_name === 'string' ? cachedProfile.first_name : '';
    const cachedLastName = typeof cachedProfile.last_name === 'string' ? cachedProfile.last_name : '';

    if (profileSetupOrLegacy && typeof profileSetupOrLegacy === 'object') {
        return {
            first_name: profileSetupOrLegacy.first_name || firstName || cachedFirstName || '',
            last_name: profileSetupOrLegacy.last_name || lastName || cachedLastName || '',
            has_profile_picture: profileSetupOrLegacy.has_profile_picture === true,
            has_custom_profile_picture: profileSetupOrLegacy.has_custom_profile_picture === true,
            profile_picture_source: profileSetupOrLegacy.profile_picture_source || (profileSetupOrLegacy.has_custom_profile_picture ? 'custom' : 'initials'),
            profile_picture_provider: profileSetupOrLegacy.profile_picture_provider || '',
        };
    }

    return {
        first_name: firstName || cachedFirstName || '',
        last_name: lastName || cachedLastName || '',
        has_profile_picture: profileSetupOrLegacy === true,
        has_custom_profile_picture: profileSetupOrLegacy === true,
        profile_picture_source: profileSetupOrLegacy === true ? 'custom' : 'initials',
        profile_picture_provider: '',
    };
}

function updateUIElements(showIds, hideIds, updates = {}) {
    hideIds.forEach((id) => {
        const el = document.getElementById(id);
        if (!el) return;

        // Use the semantic hidden state as well as an inline display value. The
        // HTML starts profile images hidden, so the browser can never lay out an
        // empty image beside the initials while user data is still loading.
        el.hidden = true;
        el.style.display = 'none';
    });

    showIds.forEach((id) => {
        const el = document.getElementById(id);
        if (!el) return;

        el.hidden = false;
        el.style.display = el.tagName === 'IMG' ? 'block' : 'flex';
    });

    Object.entries(updates).forEach(([id, props]) => {
        const el = document.getElementById(id);
        if (!el) return;
        Object.entries(props).forEach(([prop, value]) => {
            if (prop === 'innerHTML' || prop === 'src') {
                el[prop] = value;
            }
        });
    });
}

/**
 * Load a profile-picture URL without exposing an incomplete image element.
 *
 * The detached image warms the browser cache and confirms that the blob can be
 * decoded. The visible image elements are updated only after this succeeds, so
 * the initials remain a stable fallback during both network and decode time.
 *
 * @param {string} blobUrl - Object URL returned by the profile-picture API.
 * @returns {Promise<boolean>} Whether the browser loaded the image successfully.
 */
function preloadProfilePicture(blobUrl) {
    return new Promise((resolve) => {
        const image = new Image();
        image.onload = () => resolve(true);
        image.onerror = () => resolve(false);
        image.src = blobUrl;
    });
}

/**
 * Replace the sidebar loading skeletons with the resolved profile content.
 *
 * The name is populated earlier by initUserProfileUI but remains hidden until
 * the avatar request has either loaded or definitively fallen back to no image.
 * This keeps the two skeletons synchronized and avoids a partially hydrated row.
 */
function finishSidebarProfileLoading() {
    ['sidebarProfileAvatarSkeleton', 'sidebarProfileNameSkeleton'].forEach((id) => {
        const skeleton = document.getElementById(id);
        if (!skeleton) return;
        skeleton.hidden = true;
        skeleton.style.display = 'none';
    });

    const sidebarName = document.getElementById('sidebarName');
    if (sidebarName) {
        sidebarName.hidden = false;
        sidebarName.style.display = 'block';
    }
}

async function fetchProfilePicture() {
    try {
        const response = await window.authedFetch(PROFILE_PICTURE_ENDPOINT, {
            method: 'GET',
            headers: {
                'Cache-Control': 'no-cache',
                'Content-Type': null,
            },
        });

        if (!response?.ok) {
            return null;
        }

        const blob = await response.blob();
        if (!blob || blob.size === 0) {
            return null;
        }

        return ProfilePictureState.createBlobUrl(blob);
    } catch (error) {
        console.error('Failed to fetch profile picture:', error);
        return null;
    }
}

function displayProfilePicture(blobUrl) {
    const updates = {};
    ELEMENT_IDS.images.forEach((id) => {
        updates[id] = { src: blobUrl };
    });

    updateUIElements(
        ELEMENT_IDS.images,
        ELEMENT_IDS.initials,
        updates,
    );
    finishSidebarProfileLoading();
}

function displayInitialsProfile(firstName, lastName) {
    const initials = getInitials(firstName, lastName);
    const updates = {};

    ELEMENT_IDS.initials.forEach((id) => {
        updates[id] = { innerHTML: initials };
    });
    ELEMENT_IDS.images.forEach((id) => {
        updates[id] = { src: '' };
    });

    updateUIElements(
        ELEMENT_IDS.initials,
        ELEMENT_IDS.images,
        updates,
    );
    finishSidebarProfileLoading();
}

function updateProfilePictureControls(profileSetup) {
    const deleteButton = document.getElementById(ELEMENT_IDS.controls.deleteBtn);
    if (deleteButton) {
        deleteButton.style.display = ['custom', 'oauth'].includes(profileSetup.profile_picture_source) ? '' : 'none';
    }

    const sourceNote = document.getElementById(ELEMENT_IDS.controls.sourceNote);
    if (!sourceNote) return;

    if (profileSetup.profile_picture_source === 'oauth') {
        const rawProvider = String(profileSetup.profile_picture_provider || '').trim();
        const providerLabel = rawProvider ? `${rawProvider.charAt(0).toUpperCase()}${rawProvider.slice(1)}` : 'OAuth';
        sourceNote.textContent = formatProfilePictureText(
            'profile_picture_oauth_source_note',
            'Using your {provider} profile picture. Remove it to use initials and stop future imports.',
            { provider: providerLabel },
        );
        sourceNote.style.display = 'block';
        return;
    }

    sourceNote.textContent = '';
    sourceNote.style.display = 'none';
}

function applyProfileSetupToWindow(profileSetup) {
    if (!window.chatSetup || typeof window.chatSetup !== 'object') {
        window.chatSetup = { ...profileSetup };
        return;
    }

    window.chatSetup = {
        ...window.chatSetup,
        ...profileSetup,
    };
}

async function applyProfilePictureSetup(profileSetupOrLegacy, firstName = '', lastName = '') {
    const profileSetup = normalizeProfileSetup(profileSetupOrLegacy, firstName, lastName);
    applyProfileSetupToWindow(profileSetup);
    updateProfilePictureControls(profileSetup);

    if (profileSetup.has_profile_picture) {
        const blobUrl = await fetchProfilePicture();
        if (blobUrl && await preloadProfilePicture(blobUrl)) {
            displayProfilePicture(blobUrl);
            return;
        }
    }

    ProfilePictureState.revokeBlobUrl();
    displayInitialsProfile(profileSetup.first_name, profileSetup.last_name);
}

async function fetchChatSetup() {
    const response = await window.authedFetch(CHAT_SETUP_ENDPOINT);
    if (!response?.ok) {
        throw new Error(`Failed to fetch chat setup (${response?.status})`);
    }
    return response.json();
}

async function refreshProfilePictureFromServer() {
    const chatSetup = await fetchChatSetup();
    await applyProfilePictureSetup(chatSetup);
    return chatSetup;
}

async function refreshBrowserAccountAvatars() {
    if (typeof window.fetchBrowserAccounts !== 'function') {
        return null;
    }

    try {
        const payload = await window.fetchBrowserAccounts({ silent: true });
        window.dispatchEvent(new CustomEvent('profile-picture:changed'));
        return payload;
    } catch (error) {
        console.error('Failed to refresh browser accounts after profile picture update:', error);
        window.dispatchEvent(new CustomEvent('profile-picture:changed'));
        return null;
    }
}

async function getProfilePictureUploadErrorMessage(response) {
    if (!response) {
        return '';
    }

    try {
        const payload = await response.clone().json();
        const detail = typeof payload?.detail === 'string' ? payload.detail.trim() : '';
        if (!detail) {
            return '';
        }
        if (typeof window.translateBackendDetail === 'function') {
            return window.translateBackendDetail(detail, detail);
        }
        return detail;
    } catch (_error) {
        return '';
    }
}

async function uploadProfilePicture(file) {
    try {
        const formData = new FormData();
        formData.append('file', file);

        const response = await window.authedFetch('/api/v1/users/profile-picture/upload', {
            method: 'POST',
            headers: {
                'Content-Type': null,
                'Cache-Control': 'no-cache',
            },
            body: formData,
        });

        if (!response?.ok) {
            const error = new Error(`HTTP error! status: ${response?.status}`);
            error.userMessage = await getProfilePictureUploadErrorMessage(response);
            throw error;
        }

        ProfilePictureState.revokeBlobUrl();
        await refreshProfilePictureFromServer();
        await refreshBrowserAccountAvatars();
    } catch (error) {
        console.error('Failed to upload profile picture:', error);
        notifyError(error?.userMessage || translateProfilePictureText(
            'profile_picture_upload_failed',
            'Failed to upload profile picture',
        ));
    }
}

async function deleteProfilePicture() {
    try {
        const response = await window.authedFetch('/api/v1/users/profile-picture/delete', {
            method: 'DELETE',
            headers: {
                'Cache-Control': 'no-cache',
            },
        });

        if (!response?.ok) {
            throw new Error(`HTTP error! status: ${response?.status}`);
        }

        ProfilePictureState.revokeBlobUrl();
        await refreshProfilePictureFromServer();
        await refreshBrowserAccountAvatars();
    } catch (error) {
        console.error('Failed to delete profile picture:', error);
        notifyError(translateProfilePictureText(
            'profile_picture_delete_failed',
            'Failed to delete profile picture',
        ));
    }
}

/**
 * Render the profile-picture delete icon from the application's shared icon set.
 *
 * Keeping the HTML as an empty placeholder prevents this settings page from
 * introducing a second trash-can design and leaves the button text available
 * to the translation system as a direct text node.
 */
function renderDeleteProfilePictureIcon() {
    const iconContainer = document.getElementById(ELEMENT_IDS.controls.deleteIcon);
    if (!iconContainer || typeof Icons === 'undefined' || !Icons.trash) {
        return;
    }

    iconContainer.innerHTML = Icons.trash;
}

function initProfilePictureEventListeners() {
    renderDeleteProfilePictureIcon();

    const profilePictureInput = document.getElementById(ELEMENT_IDS.controls.input);
    if (!profilePictureInput) {
        console.warn('Profile picture input element not found');
        return;
    }

    profilePictureInput.addEventListener('change', (event) => {
        const file = event.target.files?.[0];
        event.target.value = '';
        if (file) {
            uploadProfilePicture(file);
        }
    });

    const triggerProfilePictureDialog = (event) => {
        event.preventDefault();
        profilePictureInput.click();
    };

    const editProfilePicture = document.getElementById(ELEMENT_IDS.controls.editBtn);
    editProfilePicture?.addEventListener('click', triggerProfilePictureDialog);

    const deleteProfilePicBtn = document.getElementById(ELEMENT_IDS.controls.deleteBtn);
    deleteProfilePicBtn?.addEventListener('click', deleteProfilePicture);
}

window.initProfilePicture = applyProfilePictureSetup;
window.ProfilePictureManager = {
    applySetup: applyProfilePictureSetup,
    refreshFromServer: refreshProfilePictureFromServer,
};

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initProfilePictureEventListeners);
} else {
    initProfilePictureEventListeners();
}

window.addEventListener('beforeunload', () => {
    ProfilePictureState.revokeBlobUrl();
});
