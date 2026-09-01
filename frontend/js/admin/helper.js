/**
 * Public exports for the shared admin helpers.
 *
 * Load these classic scripts before this orchestrator, in order:
 * core.js, generatedMarkup.js, schemaMetadata.js, uiState.js, fieldLayout.js,
 * selectControls.js, fieldControls.js, api.js, settingsController.js,
 * fieldValidation.js.
 */
if (typeof window !== 'undefined') {
    window.PROVIDER_LABEL_MAP = window.PROVIDER_LABEL_MAP || PROVIDER_LABEL_MAP;
    window.DEFAULT_PROVIDER_ICON_KEYS = window.DEFAULT_PROVIDER_ICON_KEYS || DEFAULT_PROVIDER_ICON_KEYS;
    window.CUSTOM_PROVIDER_ICON_KEYS = window.CUSTOM_PROVIDER_ICON_KEYS || CUSTOM_PROVIDER_ICON_KEYS;
    window.PROVIDER_DEFAULT_ICON_MAP = window.PROVIDER_DEFAULT_ICON_MAP || PROVIDER_DEFAULT_ICON_MAP;
    window.providerSupportsCustomIcon = window.providerSupportsCustomIcon || providerSupportsCustomIcon;
    window.getDefaultProviderIconKey = window.getDefaultProviderIconKey || getDefaultProviderIconKey;
    window.adminT = window.adminT || helperT;
    window.adminFormatT = window.adminFormatT || helperFormatT;
    window.adminEscapeHtml = window.adminEscapeHtml || helperEscapeHtml;
    window.createAdminEmptyPlaceholder = window.createAdminEmptyPlaceholder || createAdminEmptyPlaceholder;
    window.createAdminLoadingPlaceholder = window.createAdminLoadingPlaceholder || createAdminLoadingPlaceholder;
    window.resolveAdminSchemaOptionLabel = window.resolveAdminSchemaOptionLabel || resolveAdminSchemaOptionLabel;
    window.getFieldPlaceholder = window.getFieldPlaceholder || getFieldPlaceholder;
    if (!window.formatProviderLabel) {
        window.formatProviderLabel = formatProviderLabel;
    }

    window.formatProviderLabel = window.formatProviderLabel || formatProviderLabel;
    window.setButtonLabel = window.setButtonLabel || setButtonLabel;
    window.setButtonLoadingState = window.setButtonLoadingState || setButtonLoadingState;
    window.createAdminExportJobsController = window.createAdminExportJobsController || createAdminExportJobsController;
    window.adminSetRefreshButtonLoadingState = window.adminSetRefreshButtonLoadingState || setRefreshButtonLoadingState;
    window.adminShowRefreshButtonSuccessState = window.adminShowRefreshButtonSuccessState || showRefreshButtonSuccessState;
    window.adminResetRefreshButtonState = window.adminResetRefreshButtonState || resetRefreshButtonState;
    window.createAdminLoadingPlaceholder = window.createAdminLoadingPlaceholder || createAdminLoadingPlaceholder;
    window.syncSchemaSectionVisibility = window.syncSchemaSectionVisibility || syncSchemaSectionVisibility;
    window.syncSectionBodyLastVisibleRow = window.syncSectionBodyLastVisibleRow || syncSectionBodyLastVisibleRow;
    window.getMaskedFieldSubmissionMarker = window.getMaskedFieldSubmissionMarker || getMaskedFieldSubmissionMarker;
}
