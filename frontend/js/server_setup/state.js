// State management for server_setup

// Keep the donation page in the wizard so it can be restored for a later
// release without rebuilding its content. Change this single flag to `true`
// when the page should be part of server setup again.
const SHOW_DONATION_STEP = false;

const state = {
    currentStep: 0,
    // Steps 1-5 are configuration pages; step 6 is the completion screen.
    totalSteps: 6,
    serverData: {
        applicationName: '',
        // Public URLs are ordered. The first entry is the canonical URL used
        // for generated links and as the post-setup redirect destination.
        publicUrls: [],
        defaultUserRole: 'pending',
        logoLight: null,
        logoDark: null,
        icon: null
    }
};

// Expose state globally
window.state = state;
