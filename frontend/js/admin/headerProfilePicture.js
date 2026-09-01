(function () {
    const CHAT_SETUP_ENDPOINT = '/api/v1/settings/chat/setup';
    const PROFILE_PICTURE_ENDPOINT = '/api/v1/users/profile-picture/get';

    let cachedBlobUrl = null;

    const profileImageEl = document.getElementById('adminHeaderProfileImage');
    const profileInitialsEl = document.getElementById('adminHeaderProfileInitials');

    if (!profileImageEl || !profileInitialsEl) {
        return;
    }

    function revokeBlobUrl() {
        if (cachedBlobUrl) {
            URL.revokeObjectURL(cachedBlobUrl);
            cachedBlobUrl = null;
        }
    }

    function getInitials(firstName, lastName) {
        const first = (firstName || '').trim();
        const last = (lastName || '').trim();
        if (!first && !last) {
            return 'U';
        }
        if (!last) {
            return first.charAt(0).toUpperCase();
        }
        return (first.charAt(0) + last.charAt(0)).toUpperCase();
    }

    function showInitials(firstName, lastName) {
        revokeBlobUrl();
        const initials = getInitials(firstName, lastName);
        profileInitialsEl.textContent = initials;
        profileInitialsEl.style.display = 'flex';
        profileImageEl.hidden = true;
    }

    function showProfilePicture(blobUrl) {
        profileImageEl.src = blobUrl;
        profileImageEl.hidden = false;
        profileInitialsEl.style.display = 'none';
    }

    async function fetchChatSetup() {
        try {
            const response = await window.authedFetch?.(CHAT_SETUP_ENDPOINT);
            if (!response?.ok) {
                throw new Error(`chat setup request failed: ${response?.status}`);
            }
            return response.json();
        } catch (error) {
            console.error('Failed to fetch admin chat setup', error);
            throw error;
        }
    }

    async function fetchProfilePictureUrl() {
        try {
            const response = await window.authedFetch?.(PROFILE_PICTURE_ENDPOINT, {
                method: 'GET',
                headers: {
                    'Cache-Control': 'no-cache',
                    'Content-Type': null,
                },
            });
            if (!response?.ok) {
                throw new Error(`profile picture request failed: ${response?.status}`);
            }
            const blob = await response.blob();
            if (!blob || blob.size === 0) {
                return null;
            }
            revokeBlobUrl();
            cachedBlobUrl = URL.createObjectURL(blob);
            return cachedBlobUrl;
        } catch (error) {
            console.error('Failed to fetch admin profile picture', error);
            return null;
        }
    }

    async function initAdminHeaderProfilePicture() {
        try {
            const setup = await fetchChatSetup();
            const { first_name: firstName, last_name: lastName, has_profile_picture: hasProfilePicture } = setup || {};

            if (hasProfilePicture) {
                const blobUrl = await fetchProfilePictureUrl();
                if (blobUrl) {
                    showProfilePicture(blobUrl);
                    return;
                }
            }

            showInitials(firstName, lastName);
        } catch (error) {
            showInitials();
        }
    }

    if (document.readyState === 'complete' || document.readyState === 'interactive') {
        initAdminHeaderProfilePicture();
    } else {
        document.addEventListener('DOMContentLoaded', initAdminHeaderProfilePicture, { once: true });
    }

    window.addEventListener('beforeunload', revokeBlobUrl);
})();
