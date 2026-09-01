// Main initializer for server_setup

document.addEventListener('DOMContentLoaded', async () => {
    // Initialize input handlers
    if (typeof window.initializeSetupAdminSelects === 'function') {
        window.initializeSetupAdminSelects();
    }
    if (typeof window.serverSetupPublicUrls?.initializeEditor === 'function') {
        window.serverSetupPublicUrls.initializeEditor();
    }
    initializeInputs();
    initializeNavigationButtons();
    initializeKeyboardNavigation();
    
    // Initialize upload handlers
    initializeUploads();

    if (typeof window.loadSavedBrandingAssets === 'function') {
        await window.loadSavedBrandingAssets();
    }
    
    window.__serverSetupBooted = true;
    document.body.style.display = 'flex';

    // Initial rendering and validation
    updateStep();
});

function initializeInputs() {
    // Application name input
    const appNameInput = document.getElementById('appNameInput');
    if (appNameInput) {
        appNameInput.addEventListener('input', (e) => {
            state.serverData.applicationName = e.target.value;
            updateValidation();
        });
        if (state.serverData.applicationName) {
            appNameInput.value = state.serverData.applicationName;
        }
    }
    
    // Default user role select
    const defaultUserRoleInput = document.getElementById('defaultUserRoleInput');
    if (defaultUserRoleInput) {
        const defaultRole = state.serverData?.defaultUserRole || 'pending';
        state.serverData.defaultUserRole = defaultRole;
        if (typeof window.setCustomSelectValue === 'function') {
            window.setCustomSelectValue('defaultUserRole', defaultRole);
        }
    }
}

function initializeNavigationButtons() {
    const startButton = document.getElementById('startSetupButton');
    const backButton = document.getElementById('serverSetupBackButton');
    const nextButton = document.getElementById('serverSetupNextButton');

    startButton?.addEventListener('click', startSetup);
    backButton?.addEventListener('click', previousStep);
    nextButton?.addEventListener('click', nextStep);
}

const ENTER_KEY_NAV_INPUT_TYPES = window.setupKeyboardNavigationFilters
    ? window.setupKeyboardNavigationFilters.createEnterKeyInputTypeSet()
    : new Set([
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
    ]);

let keyboardNavigationInitialized = false;

function initializeKeyboardNavigation() {
    if (keyboardNavigationInitialized) {
        return;
    }
    keyboardNavigationInitialized = true;

    const filters = window.setupKeyboardNavigationFilters;
    if (!filters) {
        return;
    }

    filters.initializeSetupKeyboardNavigation({
        getCurrentStep: () => state.currentStep,
        startSetup,
        shouldHandleSplashStart,
        shouldHandleDirectionalNavigation,
        shouldHandleNextNavigation,
        navigationSelector: '.navigation',
        visibleNextButtonSelector: '.navigation .om-button.border.submit:not(.hidden)',
        visibleBackButtonSelector: '.navigation .om-button.border.cancel:not(.hidden)',
        nextButtonSelector: '.om-button.border.submit',
        isNextButtonDisabled: (nextBtn) => !nextBtn || nextBtn.disabled,
    });
}

function shouldHandleDirectionalNavigation(event) {
    const filters = window.setupKeyboardNavigationFilters;
    if (!filters) {
        return false;
    }
    return filters.shouldHandleDirectionalNavigation(event, {
        navigationSelector: '.navigation',
        nextButtonSelector: '.navigation .om-button.border.submit:not(.hidden)',
        backButtonSelector: '.navigation .om-button.border.cancel:not(.hidden)',
        modalSelector: '.warning-overlay.active, .delete-warning-overlay:not([hidden])',
    });
}

function shouldHandleSplashStart(event) {
    const filters = window.setupKeyboardNavigationFilters;
    return filters ? filters.shouldHandleSplashStart(event) : false;
}

function shouldHandleNextNavigation(event) {
    const filters = window.setupKeyboardNavigationFilters;
    if (!filters) {
        return false;
    }
    return filters.shouldHandleNextNavigation(event, {
        modalSelector: '.warning-overlay.active, .delete-warning-overlay:not([hidden])',
        enterKeyNavInputTypes: ENTER_KEY_NAV_INPUT_TYPES,
    });
}
