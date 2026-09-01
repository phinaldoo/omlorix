(() => {
    const initProfileDropdown = () => {
        const profileButton = document.getElementById('adminHeaderProfileButton');
        const dropdown = document.getElementById('adminProfileDropdown');

        if (!profileButton || !dropdown) {
            return;
        }

        const logoutButton = document.getElementById('adminHeaderLogoutButton');

        const dropdownController = window.createDropdownController({
            id: 'admin-profile-dropdown',
            group: 'admin-header-dropdowns',
            trigger: profileButton,
            dropdown,
            root: profileButton.closest('.admin-profile-toggle'),
            escapePriority: 100,
            inert: true,
            manageFocusable: true,
            closeOnFocusOutside: true,
            outsideEvents: ['click', 'touchstart'],
        });

        const toggleDropdown = (forceState) => {
            const willOpen = forceState ?? !dropdownController.isOpen();
            dropdownController[willOpen ? 'open' : 'close']({ reason: 'api' });
        };

        toggleDropdown(false);

        document.addEventListener('adminCloseProfileDropdown', () => toggleDropdown(false));

        if (logoutButton) {
            logoutButton.addEventListener('click', () => {
                toggleDropdown(false);
                window.logout();
            });
        }
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initProfileDropdown);
    } else {
        initProfileDropdown();
    }
})();
