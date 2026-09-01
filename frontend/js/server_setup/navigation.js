// Navigation and validation for server_setup
const stepErrorDisplay = {};

const FIELD_TARGETS = {
    applicationName: () => document.getElementById('appNameInput'),
    publicUrls: () => document.querySelectorAll('.public-url-input'),
    brandAssets: () => [
        document.getElementById('logoLightUpload'),
        document.getElementById('logoDarkUpload')
    ],
    defaultUserRole: () => window.getSetupSelectTrigger?.('defaultUserRole')
};

let lastRenderedStep = -1;

/** Return the ordered configuration steps that users can currently visit. */
function getVisibleConfigurationSteps() {
    const steps = [2, 3, 4, 5];
    return SHOW_DONATION_STEP ? [1, ...steps] : steps;
}

/** Find the next visible step while preserving the stable step IDs in markup. */
function getNextVisibleStep(currentStep) {
    const visibleSteps = getVisibleConfigurationSteps();
    const currentIndex = visibleSteps.indexOf(currentStep);
    return currentIndex >= 0 ? visibleSteps[currentIndex + 1] : visibleSteps[0];
}

/** Find the previous visible step, falling back to the welcome screen. */
function getPreviousVisibleStep(currentStep) {
    const visibleSteps = getVisibleConfigurationSteps();
    const currentIndex = visibleSteps.indexOf(currentStep);
    return currentIndex > 0 ? visibleSteps[currentIndex - 1] : 0;
}

function startSetup() {
    state.currentStep = getVisibleConfigurationSteps()[0];
    updateStep();
}

async function nextStep() {
    if (state.currentStep >= state.totalSteps) {
        return;
    }

    const isCurrentStepValid = updateValidation({ showErrors: true });
    if (!isCurrentStepValid) {
        focusFirstErrorField();
        return;
    }

    // Persist normalized origins before leaving this step. If the browser's
    // current origin is absent, make the resulting lockout an explicit choice.
    if (state.currentStep === 3) {
        window.serverSetupPublicUrls?.commitNormalizedValues();
        const shouldContinue =
            await window.serverSetupPublicUrls?.confirmUnlistedCurrentOrigin();
        if (shouldContinue === false) {
            return;
        }
    }

    const visibleSteps = getVisibleConfigurationSteps();
    const finalConfigStep = visibleSteps[visibleSteps.length - 1];
    if (state.currentStep === finalConfigStep) {
        completeSetup();
        return;
    }

    state.currentStep = getNextVisibleStep(state.currentStep);
    updateStep();
}

function previousStep() {
    if (state.currentStep > 0) {
        state.currentStep = getPreviousVisibleStep(state.currentStep);
        updateStep();
    }
}

function updateStep() {
    const stepChanged = lastRenderedStep !== state.currentStep;
    const visibleSteps = getVisibleConfigurationSteps();
    const currentVisibleStepIndex = visibleSteps.indexOf(state.currentStep);
    const completedVisibleSteps = currentVisibleStepIndex >= 0
        ? currentVisibleStepIndex + 1
        : 0;
    const progress = (completedVisibleSteps / visibleSteps.length) * 100;
    const clampedProgress = Math.max(0, Math.min(100, Math.round(progress)));
    const progressFill = document.querySelector('.progress-fill');
    if (progressFill) {
        progressFill.style.width = `${clampedProgress}%`;
    }

    const progressContainer = document.querySelector('.progress-container');
    const progressBar = document.getElementById('setupProgressBar');
    if (progressBar) {
        progressBar.setAttribute('aria-valuenow', String(clampedProgress));
        progressBar.setAttribute('aria-valuetext', `${clampedProgress}%`);
    }

    if (progressContainer) {
        if (state.currentStep === 0 || state.currentStep === state.totalSteps) {
            progressContainer.classList.add('hidden');
            progressContainer.hidden = true;
            progressContainer.setAttribute('aria-hidden', 'true');
        } else {
            progressContainer.classList.remove('hidden');
            progressContainer.hidden = false;
            progressContainer.setAttribute('aria-hidden', 'false');
        }
    }

    const header = document.querySelector('.header');
    if (header) {
        header.classList.toggle('hidden', state.currentStep === state.totalSteps);
    }

    const finalConfigStep = visibleSteps[visibleSteps.length - 1];
    const firstConfigStep = visibleSteps[0];

    document.querySelectorAll('.step').forEach((step) => {
        const stepNum = parseInt(step.getAttribute('data-step'));
        if (stepNum === state.currentStep) {
            step.hidden = false;
            void step.offsetWidth; // force reflow for animation
            step.classList.add('active');
            step.classList.remove('exit-left');
            step.setAttribute('aria-hidden', 'false');
            step.inert = false;
        } else if (stepNum < state.currentStep) {
            step.classList.remove('active');
            step.classList.add('exit-left');
            step.setAttribute('aria-hidden', 'true');
            step.inert = true;
            setTimeout(() => {
                if (!step.classList.contains('active')) step.hidden = true;
            }, 500);
        } else {
            step.classList.remove('active', 'exit-left');
            step.setAttribute('aria-hidden', 'true');
            step.inert = true;
            setTimeout(() => {
                if (!step.classList.contains('active')) step.hidden = true;
            }, 500);
        }
    });

    const backBtn = document.querySelector('.om-button.border.cancel');
    const nextBtn = document.querySelector('.om-button.border.submit');
    const navContainer = document.querySelector('.navigation');
    const hideNav = state.currentStep === 0 || state.currentStep === state.totalSteps;

    if (navContainer) {
        navContainer.classList.toggle('hidden', hideNav);
        navContainer.hidden = hideNav;
        navContainer.setAttribute('aria-hidden', hideNav ? 'true' : 'false');
    }

    if (!backBtn || !nextBtn) {
        updateValidation();
        return;
    }

    if (hideNav) {
        backBtn.classList.add('hidden');
        nextBtn.classList.add('hidden');
        backBtn.hidden = true;
        nextBtn.hidden = true;
    } else {
        backBtn.classList.toggle('hidden', state.currentStep === firstConfigStep);
        backBtn.hidden = state.currentStep === firstConfigStep;
        nextBtn.classList.remove('hidden');
        nextBtn.hidden = false;
        if (state.currentStep === finalConfigStep) {
            setNavButtonLabel(nextBtn, 'finish');
        } else {
            setNavButtonLabel(nextBtn, 'next');
        }
    }

    updateValidation();

    if (stepChanged && (lastRenderedStep !== -1 || state.currentStep !== 0)) {
        announceCurrentStep();
        focusCurrentStepHeading();
    }
    lastRenderedStep = state.currentStep;
}

function updateValidation(options = {}) {
    const { showErrors = false } = options;
    const nextBtn = document.querySelector('.om-button.border.submit');
    const { isValid, fieldResults } = evaluateCurrentStepValidation();
    const shouldShowErrors = showErrors || stepErrorDisplay[state.currentStep] === true;

    if (!isValid && (showErrors || shouldShowErrors)) {
        stepErrorDisplay[state.currentStep] = true;
    } else if (isValid) {
        stepErrorDisplay[state.currentStep] = false;
    }

    applyFieldErrorDisplay(fieldResults, shouldShowErrors);
    setNextButtonState(nextBtn, isValid);

    return isValid;
}

function hasUploadedLogos() {
    return Boolean(state.serverData.logoLight || state.serverData.logoDark);
}

function handleBrandingAssetsUpdated() {
    updateValidation();
    if (!hasUploadedLogos() && state.currentStep === state.totalSteps - 1) {
        return;
    }
    updateStep();
}

function setNavButtonLabel(button, mode) {
    if (!button) return;
    let label = 'Next';
    if (mode === 'finish') {
        button.setAttribute('data-i18n', 'btn_finish');
        label =
            typeof window !== 'undefined' && typeof window.getTranslation === 'function'
                ? window.getTranslation('btn_finish', 'Finish')
                : 'Finish';
    } else {
        button.setAttribute('data-i18n', 'btn_next');
        label =
            typeof window !== 'undefined' && typeof window.getTranslation === 'function'
                ? window.getTranslation('btn_next', 'Next')
                : 'Next';
    }
    button.textContent = label;
}

function evaluateCurrentStepValidation() {
    const fieldResults = {};
    let isValid = true;

    switch(state.currentStep) {
        case 2: {
            const hasName = (state.serverData.applicationName || '').trim() !== '';
            fieldResults.applicationName = buildFieldResult(
                hasName,
                'error_application_name_required',
                'Enter an application name to continue.'
            );
            isValid = fieldResults.applicationName.valid;
            break;
        }
        case 3: {
            fieldResults.publicUrls =
                window.serverSetupPublicUrls?.validatePublicUrls(state.serverData.publicUrls)
                || buildFieldResult(
                    false,
                    'error_public_url_required',
                    'Add at least one public URL to continue.'
                );
            isValid = fieldResults.publicUrls.valid;
            break;
        }
        case 4: {
            fieldResults.brandAssets = buildFieldResult(
                hasUploadedLogos(),
                'error_brand_assets_required',
                'Upload at least one logo to continue.'
            );
            isValid = fieldResults.brandAssets.valid;
            break;
        }
        case 5: {
            fieldResults.defaultUserRole = buildFieldResult(
                !!state.serverData.defaultUserRole,
                'error_default_role_required',
                'Select a default user role.'
            );
            isValid = fieldResults.defaultUserRole.valid;
            break;
        }
        default:
            isValid = true;
            break;
    }

    return { isValid, fieldResults };
}

function buildFieldResult(condition, messageKey, fallback) {
    if (condition) {
        return { valid: true };
    }
    return {
        valid: false,
        messageKey,
        fallback
    };
}

function applyFieldErrorDisplay(fieldResults, shouldShowErrors) {
    Object.entries(fieldResults).forEach(([field, result]) => {
        if (!result) {
            return;
        }
        const targets = getFieldTargets(field);
        const hasError = shouldShowErrors && result.valid === false;
        targets.forEach(target => {
            target.classList.toggle('input-error', hasError);
        });

        const errorEl = document.querySelector(`.field-error[data-error-for="${field}"]`);
        if (!errorEl) {
            return;
        }

        const errorId = errorEl.id || '';
        targets.forEach((target) => {
            setFieldAccessibilityState(target, hasError, errorId);
        });

        if (hasError) {
            errorEl.textContent = result.messageKey
                ? translateServerSetupMessage(result.messageKey, result.fallback)
                : result.fallback || '';
            errorEl.classList.add('visible');
            errorEl.setAttribute('aria-hidden', 'false');
        } else {
            errorEl.textContent = '';
            errorEl.classList.remove('visible');
            errorEl.setAttribute('aria-hidden', 'true');
        }
    });
}

function setFieldAccessibilityState(target, hasError, errorId) {
    if (!target || typeof target.setAttribute !== 'function') {
        return;
    }

    if (supportsAriaInvalid(target)) {
        target.setAttribute('aria-invalid', hasError ? 'true' : 'false');
    } else {
        target.removeAttribute('aria-invalid');
    }

    if (!errorId) {
        return;
    }

    const existing = (target.getAttribute('aria-describedby') || '')
        .split(/\s+/)
        .filter(Boolean)
        .filter(id => id !== errorId);

    if (hasError) {
        existing.push(errorId);
    }

    if (existing.length > 0) {
        target.setAttribute('aria-describedby', [...new Set(existing)].join(' '));
    } else {
        target.removeAttribute('aria-describedby');
    }
}

function supportsAriaInvalid(target) {
    if (!target || !target.tagName) {
        return false;
    }

    const tagName = target.tagName.toUpperCase();
    if (tagName === 'INPUT' || tagName === 'SELECT' || tagName === 'TEXTAREA') {
        return true;
    }

    const role = (target.getAttribute('role') || '').toLowerCase();
    return ['textbox', 'combobox', 'searchbox', 'spinbutton'].includes(role);
}

function getFieldTargets(field) {
    const resolver = FIELD_TARGETS[field];
    if (!resolver) {
        return [];
    }
    const resolved = resolver();
    if (!resolved) {
        return [];
    }
    if (Array.isArray(resolved)) {
        return resolved.filter(Boolean);
    }
    if (resolved instanceof NodeList || resolved instanceof HTMLCollection) {
        return Array.from(resolved).filter(Boolean);
    }
    return [resolved];
}

function translateServerSetupMessage(key, fallback) {
    if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback;
}

function setNextButtonState(nextBtn, isValid) {
    if (!nextBtn) {
        return;
    }
    nextBtn.classList.toggle('is-disabled', !isValid);
    nextBtn.setAttribute('aria-disabled', (!isValid).toString());
    nextBtn.removeAttribute('disabled');
}

function focusCurrentStepHeading() {
    const activeStep = document.querySelector(`.step[data-step="${state.currentStep}"]`);
    if (!activeStep) {
        return;
    }

    const heading = activeStep.querySelector('.step-title, .splash-title, .complete-title');
    if (!heading || typeof heading.focus !== 'function') {
        return;
    }

    heading.setAttribute('tabindex', '-1');
    heading.focus();
    heading.addEventListener('blur', () => {
        heading.removeAttribute('tabindex');
    }, { once: true });
}

function announceCurrentStep() {
    const liveRegion = document.getElementById('stepAnnouncement');
    if (!liveRegion) {
        return;
    }

    if (state.currentStep === 0) {
        liveRegion.textContent = translateServerSetupMessage('step_welcome_announcement', 'Welcome step');
        return;
    }

    if (state.currentStep === state.totalSteps) {
        liveRegion.textContent = translateServerSetupMessage('step_complete_announcement', 'Setup complete');
        return;
    }

    const activeStep = document.querySelector(`.step[data-step="${state.currentStep}"]`);
    const title = activeStep?.querySelector('.step-title')?.textContent?.trim() || '';
    const visibleSteps = getVisibleConfigurationSteps();
    const currentVisibleStep = visibleSteps.indexOf(state.currentStep) + 1;
    if (typeof window !== 'undefined' && typeof window.formatTranslation === 'function') {
        liveRegion.textContent = window.formatTranslation(
            'step_announcement',
            'Step {current} of {total}: {title}',
            {
                current: currentVisibleStep,
                total: visibleSteps.length,
                title
            }
        );
        return;
    }

    liveRegion.textContent = `Step ${currentVisibleStep} of ${visibleSteps.length}: ${title}`;
}

function focusFirstErrorField() {
    const firstError = document.querySelector('.field-error.visible[data-error-for]');
    if (!firstError) {
        return;
    }

    const field = firstError.getAttribute('data-error-for');
    if (!field) {
        return;
    }

    const targets = getFieldTargets(field);
    const firstTarget = targets.find(target => target && typeof target.focus === 'function');
    firstTarget?.focus();
}
