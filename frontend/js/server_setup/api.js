// API calls for server_setup

async function completeSetup() {
    const backBtn = document.querySelector('.om-button.border.cancel');
    const nextBtn = document.querySelector('.om-button.border.submit');
    
    // Disable buttons during submission
    if (backBtn) backBtn.disabled = true;
    if (nextBtn) nextBtn.disabled = true;
    
    try {
        // Upload while setup mode still permits access through the address that
        // opened the wizard. Completing setup can immediately enforce a public
        // URL list that excludes this origin.
        if (typeof window.uploadBrandingAssets === 'function') {
            try {
                await window.uploadBrandingAssets();
            } catch (uploadError) {
                console.error('Brand asset upload error:', uploadError);
                if (typeof notifyError === 'function') {
                    notifyError(
                        uploadError instanceof Error && uploadError.message
                            ? uploadError.message
                            : getTranslation('error_logo_upload_failed', 'Failed to upload branding assets. Please try again.')
                    );
                }
                if (backBtn) backBtn.disabled = false;
                if (nextBtn) nextBtn.disabled = false;
                return;
            }
        }

        const payload = {
            application_name: state.serverData.applicationName,
            public_url: state.serverData.publicUrls,
            default_user_role: state.serverData.defaultUserRole,
        };
        
        const res = await window.authedFetch('/api/v1/settings/server/setup', {
            method: 'POST',
            body: JSON.stringify(payload)
        });
        
        if (!res.ok) {
            const errorData = await res.json().catch(() => ({}));
            const errorMsg = errorData.detail || getTranslation('error_complete_setup_failed', 'Failed to complete server setup. Please try again.');
            if (typeof notifyError === 'function') {
                notifyError(errorMsg);
            }
            if (backBtn) backBtn.disabled = false;
            if (nextBtn) nextBtn.disabled = false;
            return;
        }

        const setupResult = await res.json();
        
        // Move to complete screen
        state.currentStep = state.totalSteps;
        updateStep();
        
        // Redirect after showing completion
        setTimeout(() => {
            const returnUrl = typeof window.getAccountReturnUrl === 'function'
                ? window.getAccountReturnUrl()
                : '';
            const primaryPublicUrl =
                setupResult.primary_public_url || state.serverData.publicUrls[0];
            const destination = window.serverSetupPublicUrls.buildRedirectUrl(
                primaryPublicUrl,
                returnUrl || '/'
            );
            window.location.replace(destination);
        }, 2000);
        
    } catch (err) {
        console.error('Server setup error:', err);
        if (typeof notifyError === 'function') {
            notifyError(getTranslation('error_complete_setup_unexpected', 'An unexpected error occurred. Please try again.'));
        }
        if (backBtn) backBtn.disabled = false;
        if (nextBtn) nextBtn.disabled = false;
    }
}
