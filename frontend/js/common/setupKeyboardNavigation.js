(() => {
    const DEFAULT_ENTER_KEY_NAV_INPUT_TYPES = [
        '',
        'text',
        'email',
        'search',
        'url',
        'tel',
        'number',
        'password',
        'date',
        'datetime-local',
        'time',
        'month',
        'week'
    ];

    const INTERACTIVE_TAGS = new Set(['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON', 'A']);
    const INTERACTIVE_ROLES = new Set([
        'button',
        'checkbox',
        'combobox',
        'link',
        'menuitem',
        'option',
        'radio',
        'slider',
        'spinbutton',
        'switch',
        'tab',
        'textbox'
    ]);

    function hasDisallowedKeyModifiers(event, expectedKey) {
        return (
            event.key !== expectedKey ||
            event.altKey ||
            event.ctrlKey ||
            event.metaKey ||
            event.repeat
        );
    }

    function isNavigationVisible(navigationSelector) {
        const navigation = document.querySelector(navigationSelector);
        if (!navigation || navigation.classList.contains('hidden')) {
            return false;
        }
        return true;
    }

    function isElementInsideActiveModal(element, modalSelector) {
        if (!modalSelector || !element) {
            return false;
        }
        return getActiveModals(modalSelector).some((activeModal) => activeModal.contains(element));
    }

    function getActiveModals(modalSelector) {
        if (!modalSelector) {
            return [];
        }

        return Array.from(document.querySelectorAll(modalSelector)).filter((modal) => {
            if (!modal || modal.hidden || modal.getAttribute('aria-hidden') === 'true') {
                return false;
            }

            const style = window.getComputedStyle(modal);
            return style.display !== 'none' && style.visibility !== 'hidden' && style.visibility !== 'collapse';
        });
    }

    function isModalBlockingPageShortcuts(modalSelector) {
        return getActiveModals(modalSelector).length > 0;
    }

    function isInteractiveTarget(element) {
        if (!element) {
            return false;
        }

        if (element.isContentEditable) {
            return true;
        }

        if (INTERACTIVE_TAGS.has(element.tagName)) {
            return true;
        }

        const role = (element.getAttribute('role') || '').toLowerCase();
        return INTERACTIVE_ROLES.has(role);
    }

    function isNavigationButton(element, navigationSelector) {
        return Boolean(
            element &&
            element.closest?.(navigationSelector) &&
            element.matches?.('button, [role="button"]')
        );
    }

    function createEnterKeyInputTypeSet(values = DEFAULT_ENTER_KEY_NAV_INPUT_TYPES) {
        return new Set(values.map((value) => String(value || '').toLowerCase()));
    }

    function shouldHandleDirectionalNavigation(event, {
        navigationSelector = '.navigation',
        nextButtonSelector = '.navigation .om-button.border.submit:not(.hidden)',
        backButtonSelector = '.navigation .om-button.border.cancel:not(.hidden)',
        modalSelector = null,
    } = {}) {
        if (
            (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') ||
            event.altKey ||
            event.ctrlKey ||
            event.metaKey ||
            event.repeat
        ) {
            return false;
        }

        if (!isNavigationVisible(navigationSelector)) {
            return false;
        }

        const activeElement = document.activeElement;
        if (!activeElement) {
            return true;
        }

        if (isModalBlockingPageShortcuts(modalSelector) || isElementInsideActiveModal(activeElement, modalSelector)) {
            return false;
        }

        if (isInteractiveTarget(activeElement) && !isNavigationButton(activeElement, navigationSelector)) {
            return false;
        }

        const buttonSelector = event.key === 'ArrowRight'
            ? nextButtonSelector
            : backButtonSelector;

        return Boolean(document.querySelector(buttonSelector));
    }

    function shouldHandleSplashStart(event) {
        if (hasDisallowedKeyModifiers(event, 'Enter')) {
            return false;
        }

        const activeElement = document.activeElement;
        if (!activeElement || activeElement === document.body || activeElement === document.documentElement) {
            return true;
        }

        return !isInteractiveTarget(activeElement);
    }

    function shouldHandleNextNavigation(event, {
        modalSelector = null,
        enterKeyNavInputTypes = createEnterKeyInputTypeSet(),
    } = {}) {
        if (hasDisallowedKeyModifiers(event, 'Enter')) {
            return false;
        }

        const activeElement = document.activeElement;
        if (!activeElement) {
            return true;
        }

        if (isModalBlockingPageShortcuts(modalSelector) || isElementInsideActiveModal(activeElement, modalSelector)) {
            return false;
        }

        if (activeElement.isContentEditable) {
            return false;
        }

        const tagName = activeElement.tagName;
        if (tagName === 'TEXTAREA' || tagName === 'SELECT' || tagName === 'BUTTON' || tagName === 'A') {
            return false;
        }

        const role = (activeElement.getAttribute('role') || '').toLowerCase();
        if (INTERACTIVE_ROLES.has(role)) {
            return false;
        }

        if (tagName === 'INPUT') {
            const type = (activeElement.getAttribute('type') || '').toLowerCase();
            if (!enterKeyNavInputTypes.has(type)) {
                return false;
            }
        }

        return true;
    }

    function initializeSetupKeyboardNavigation({
        getCurrentStep,
        startSetup,
        shouldHandleSplashStart: shouldHandleSplashStartFn,
        shouldHandleDirectionalNavigation: shouldHandleDirectionalNavigationFn,
        shouldHandleNextNavigation: shouldHandleNextNavigationFn,
        navigationSelector = '.navigation',
        visibleNextButtonSelector = '.navigation .om-button.border.submit:not(.hidden)',
        visibleBackButtonSelector = '.navigation .om-button.border.cancel:not(.hidden)',
        nextButtonSelector = '.om-button.border.submit',
        isNextButtonDisabled,
    } = {}) {
        if (
            typeof getCurrentStep !== 'function' ||
            typeof startSetup !== 'function' ||
            typeof shouldHandleSplashStartFn !== 'function' ||
            typeof shouldHandleDirectionalNavigationFn !== 'function' ||
            typeof shouldHandleNextNavigationFn !== 'function'
        ) {
            return;
        }

        document.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' && getCurrentStep() === 0 && shouldHandleSplashStartFn(event)) {
                event.preventDefault();
                startSetup();
                return;
            }

            if (shouldHandleDirectionalNavigationFn(event)) {
                event.preventDefault();
                if (event.key === 'ArrowRight') {
                    document.querySelector(visibleNextButtonSelector)?.click();
                } else if (event.key === 'ArrowLeft') {
                    document.querySelector(visibleBackButtonSelector)?.click();
                }
                return;
            }

            if (!shouldHandleNextNavigationFn(event)) {
                return;
            }

            const navigation = document.querySelector(navigationSelector);
            const nextBtn = navigation?.querySelector(nextButtonSelector);

            if (!navigation || navigation.classList.contains('hidden')) {
                return;
            }

            const disabled = typeof isNextButtonDisabled === 'function'
                ? isNextButtonDisabled(nextBtn)
                : Boolean(nextBtn?.disabled);
            if (!nextBtn || nextBtn.classList.contains('hidden') || disabled) {
                return;
            }

            event.preventDefault();
            nextBtn.click();
        });
    }

    window.setupKeyboardNavigationFilters = {
        createEnterKeyInputTypeSet,
        shouldHandleDirectionalNavigation,
        shouldHandleSplashStart,
        shouldHandleNextNavigation,
        initializeSetupKeyboardNavigation,
        isModalBlockingPageShortcuts,
    };
})();
