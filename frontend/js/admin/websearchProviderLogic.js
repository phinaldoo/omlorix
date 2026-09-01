/**
 * Websearch Provider Selection Logic
 * 
 * Handles the smart selection of websearch providers based on their capabilities:
 * - "search" only: Show only in search dropdown
 * - "scrape" only: Show only in scrape dropdown  
 * - "search" + "scrape": Show in both dropdowns (usable separately)
 * - "combined": Show in search dropdown only, auto-hide scrape when selected
 * - "combined" + "scrape": Show in search (as combined) and scrape (separately)
 * - "combined" + "search": Show in search, when selected as combined hide scrape
 */
(function () {
    if (window.WebsearchProviderLogic) {
        return;
    }

    const SEARCH_PROVIDER_KEY = 'settings.websearch_search_provider';
    const SCRAPE_PROVIDER_KEY = 'settings.websearch_scrape_provider';
    const BYOK_SEARCH_PROVIDER_KEY = 'settings.chat.byok_default_search_provider';
    const BYOK_SCRAPE_PROVIDER_KEY = 'settings.chat.byok_default_scrape_provider';
    const DEFAULT_PAIR_KEYS = Object.freeze({
        searchFieldKey: SEARCH_PROVIDER_KEY,
        scrapeFieldKey: SCRAPE_PROVIDER_KEY,
        searchValueKey: 'websearch_search_provider',
        scrapeValueKey: 'websearch_scrape_provider',
    });
    const BYOK_PAIR_KEYS = Object.freeze({
        searchFieldKey: BYOK_SEARCH_PROVIDER_KEY,
        scrapeFieldKey: BYOK_SCRAPE_PROVIDER_KEY,
        searchValueKey: 'byok_default_search_provider',
        scrapeValueKey: 'byok_default_scrape_provider',
    });
    const SEARCH_PROVIDER_KEYS = new Set([SEARCH_PROVIDER_KEY, BYOK_SEARCH_PROVIDER_KEY]);
    const SCRAPE_PROVIDER_KEYS = new Set([SCRAPE_PROVIDER_KEY, BYOK_SCRAPE_PROVIDER_KEY]);
    const t = window.adminT || ((key, fallback) =>
        (typeof window.getTranslation === 'function'
            ? window.getTranslation(key, fallback ?? key)
            : fallback ?? key));
    const formatT = window.adminFormatT || ((key, fallback, vars) => {
        if (typeof window.formatTranslation === 'function') {
            return window.formatTranslation(key, fallback, vars);
        }
        return String(t(key, fallback)).replace(/\{(\w+)\}/g, (_, token) => {
            const value = vars?.[token];
            return value === undefined || value === null ? '' : String(value);
        });
    });
    const resolveOptionLabel = (option = {}) => (
        typeof window.resolveAdminSchemaOptionLabel === 'function'
            ? window.resolveAdminSchemaOptionLabel(option, t)
            : (option.i18n_label ? t(option.i18n_label, option.label || option.value || option.id || '') : (option.label || option.value || option.id || ''))
    );
    const compareOptionsByLabel = (left = {}, right = {}) => {
        const labelCompare = resolveOptionLabel(left).localeCompare(resolveOptionLabel(right), undefined, {
            sensitivity: 'base',
            numeric: true,
        });
        if (labelCompare !== 0) {
            return labelCompare;
        }

        return String(left.value || left.id || '').localeCompare(String(right.value || right.id || ''), undefined, {
            sensitivity: 'base',
            numeric: true,
        });
    };

    const sortedProviderOptions = (options) => (
        Array.isArray(options) ? [...options].sort(compareOptionsByLabel) : []
    );

    /**
     * Extract metadata from an option element or field option object.
     */
    const getOptionMetadata = (option) => {
        if (!option) return null;
        
        // Handle native option element with data attributes
        if (option instanceof HTMLOptionElement) {
            const metaStr = option.dataset.metadata;
            if (metaStr) {
                try {
                    return JSON.parse(metaStr);
                } catch {
                    return null;
                }
            }
            return null;
        }
        
        // Handle field option object from schema
        return option.metadata || null;
    };

    /**
     * Check if a provider option has combined capability.
     */
    const isCombinedProvider = (option) => {
        const meta = getOptionMetadata(option);
        return meta?.has_combined === true;
    };

    /**
     * Check if a provider has explicit search capability (not just combined).
     */
    const hasExplicitSearch = (option) => {
        const meta = getOptionMetadata(option);
        return meta?.has_search === true;
    };

    /**
     * Check if a provider has explicit scrape capability.
     */
    const hasExplicitScrape = (option) => {
        const meta = getOptionMetadata(option);
        return meta?.has_scrape === true;
    };

    /**
     * Check if selecting this provider in search should hide the scrape field.
     * Only combined providers that also expose direct URL scraping should hide and
     * synchronize the scrape field.
     */
    const shouldHideScrapeWhenSelected = (option) => {
        const meta = getOptionMetadata(option);
        if (!meta) return false;
        return meta.has_combined === true && meta.has_scrape === true;
    };

    /**
     * Create a select control with metadata stored in data attributes.
     */
    const createSelectWithMetadata = (field, selectedValue) => {
        const select = document.createElement('select');
        select.className = 'select';
        
        // Add empty option for optional fields
        if (!field.required) {
            const emptyOpt = document.createElement('option');
            emptyOpt.value = '';
            emptyOpt.textContent = t('websearch_provider_select_placeholder', '— Select —');
            select.appendChild(emptyOpt);
        }
        
        const options = sortedProviderOptions(field.options);
        options.forEach((option) => {
            const opt = document.createElement('option');
            opt.value = option.value;
            opt.textContent = resolveOptionLabel(option);
            
            // Store metadata in data attribute
            if (option.metadata) {
                opt.dataset.metadata = JSON.stringify(option.metadata);
                
                // Add visual indicator for combined providers
                if (option.metadata.has_combined) {
                    const label = resolveOptionLabel(option);
                    opt.textContent = formatT('websearch_provider_combined_suffix', '{label} (combined)', { label });
                }
            }
            
            if (String(option.value) === String(selectedValue)) {
                opt.selected = true;
            }
            
            select.appendChild(opt);
        });
        
        return select;
    };

    /**
     * Get the currently selected option element from a select.
     */
    const getSelectedOption = (select) => {
        if (!select) return null;
        const selectedIndex = select.selectedIndex;
        if (selectedIndex < 0) return null;
        return select.options[selectedIndex] || null;
    };

    /**
     * Find the scrape field row and control from schema controls array.
     */
    const findFieldByKey = (schemaControls, fieldKey) => {
        if (!Array.isArray(schemaControls) || !fieldKey) {
            return null;
        }
        return schemaControls.find((entry) => entry && entry.field && entry.field.key === fieldKey) || null;
    };

    const findScrapeField = (schemaControls, scrapeFieldKey = SCRAPE_PROVIDER_KEY) => {
        return findFieldByKey(schemaControls, scrapeFieldKey);
    };

    /**
     * Find the search field row and control from schema controls array.
     */
    const findSearchField = (schemaControls, searchFieldKey = SEARCH_PROVIDER_KEY) => {
        return findFieldByKey(schemaControls, searchFieldKey);
    };

    /**
     * Update the visibility and value of the scrape field based on search selection.
     */
    const updateScrapeFieldStateForKeys = (schemaControls, keys = DEFAULT_PAIR_KEYS, forceValue = null) => {
        const searchEntry = findSearchField(schemaControls, keys.searchFieldKey);
        const scrapeEntry = findScrapeField(schemaControls, keys.scrapeFieldKey);
        
        if (!searchEntry || !scrapeEntry) {
            return;
        }
        
        const searchSelect = searchEntry.control;
        const scrapeSelect = scrapeEntry.control;
        const scrapeRow = scrapeSelect?.closest?.('.settings-row');
        
        if (!searchSelect || !scrapeSelect || !scrapeRow) {
            return;
        }
        
        const selectedOption = getSelectedOption(searchSelect);
        const searchValue = searchSelect.value;
        
        // Check if the selected search provider should hide scrape
        const hideAndSync = selectedOption && searchValue && shouldHideScrapeWhenSelected(selectedOption);
        
        if (hideAndSync) {
            // Hide scrape field
            scrapeRow.hidden = true;
            scrapeRow.style.display = 'none';
            scrapeRow.dataset.hiddenByCombined = 'true';
            
            // Sync scrape value to match search (for combined providers)
            scrapeSelect.value = searchValue;
            scrapeSelect.dispatchEvent(new Event('change', { bubbles: true }));
        } else {
            // Show scrape field (if not hidden by other dependencies)
            if (scrapeRow.dataset.hiddenByCombined === 'true') {
                scrapeRow.hidden = false;
                scrapeRow.style.display = '';
                delete scrapeRow.dataset.hiddenByCombined;
            }
            
            // If forceValue is provided, set it
            if (forceValue !== null) {
                scrapeSelect.value = forceValue;
            }
        }
        
        // Update single select UI if present
        if (scrapeSelect._singleSelect?.syncFromSelect) {
            scrapeSelect._singleSelect.syncFromSelect();
        }
        window.syncSectionBodyLastVisibleRow?.(scrapeRow.closest?.('.settings-section-body') || scrapeRow.parentElement || null);
    };

    /**
     * Re-run scrape field visibility logic. Useful after dependency-driven UI changes.
     */
    const refreshScrapeFieldStateForKeys = (schemaControls, keys = DEFAULT_PAIR_KEYS) => {
        if (!Array.isArray(schemaControls) || !schemaControls.length) {
            return;
        }
        updateScrapeFieldStateForKeys(schemaControls, keys);
    };

    /**
     * Attach the websearch provider logic to schema controls.
     * Call this after rendering the schema to set up the combined provider behavior.
     */
    const attachProviderPairLogic = (schemaControls, keys = DEFAULT_PAIR_KEYS) => {
        const searchEntry = findSearchField(schemaControls, keys.searchFieldKey);
        
        if (!searchEntry) {
            return;
        }
        
        const searchSelect = searchEntry.control;
        if (!searchSelect) {
            return;
        }
        
        // Initial state update
        updateScrapeFieldStateForKeys(schemaControls, keys);
        
        // Listen for changes to search provider
        searchSelect.addEventListener('change', () => {
            updateScrapeFieldStateForKeys(schemaControls, keys);
        });
    };

    const updateScrapeFieldState = (schemaControls, forceValue = null) => {
        updateScrapeFieldStateForKeys(schemaControls, DEFAULT_PAIR_KEYS, forceValue);
    };

    const refreshScrapeFieldState = (schemaControls) => {
        refreshScrapeFieldStateForKeys(schemaControls, DEFAULT_PAIR_KEYS);
    };

    const attachWebsearchProviderLogic = (schemaControls) => {
        attachProviderPairLogic(schemaControls, DEFAULT_PAIR_KEYS);
    };

    /**
     * Process schema values before sending to API.
     * Ensures scrape provider is set correctly for combined providers.
     */
    const processProviderPairValuesForSubmit = (values, schemaControls, keys = DEFAULT_PAIR_KEYS) => {
        const searchEntry = findSearchField(schemaControls, keys.searchFieldKey);
        
        if (!searchEntry) {
            return values;
        }
        
        const searchSelect = searchEntry.control;
        if (!searchSelect) {
            return values;
        }
        
        const selectedOption = getSelectedOption(searchSelect);
        const searchValue = searchSelect.value;
        
        // If combined provider selected, ensure scrape = search
        if (selectedOption && searchValue && shouldHideScrapeWhenSelected(selectedOption)) {
            // Navigate to settings object and set scrape provider
            if (values[keys.searchValueKey]) {
                values[keys.scrapeValueKey] = values[keys.searchValueKey];
            }
        }
        
        return values;
    };

    const processWebsearchValuesForSubmit = (values, schemaControls) => {
        return processProviderPairValuesForSubmit(values, schemaControls, DEFAULT_PAIR_KEYS);
    };

    /**
     * Check if a field is the websearch search provider field.
     */
    const isSearchProviderField = (field) => {
        return SEARCH_PROVIDER_KEYS.has(field?.key);
    };

    /**
     * Check if a field is the websearch scrape provider field.
     */
    const isScrapeProviderField = (field) => {
        return SCRAPE_PROVIDER_KEYS.has(field?.key);
    };

    /**
     * Check if a field is either websearch provider field.
     */
    const isWebsearchProviderField = (field) => {
        return isSearchProviderField(field) || isScrapeProviderField(field);
    };

    /**
     * Validate websearch provider requirements before form submission.
     * 
     * If web_search tools is enabled and native_websearch is not enabled,
     * both websearch_scrape_provider and websearch_search_provider must be set.
     * 
     * @param {Object} params - Validation parameters
     * @param {Array} params.tools - List of enabled tools
     * @param {Object} params.settings - Model settings object
     * @param {Array} params.schemaControls - Schema controls array (for checking combined provider state)
     * @returns {Object} - { valid: boolean, error: string|null }
     */
    const validateWebsearchProviders = ({ tools, settings, schemaControls }) => {
        // Check if web_search tool is enabled
        const websearchTools = ['web_search'];
        const hasWebsearchTools = Array.isArray(tools) && 
            tools.some(tool => websearchTools.includes(tool));
        
        if (!hasWebsearchTools) {
            return { valid: true, error: null };
        }
        
        settings = settings || {};
        
        // Check if native websearch is enabled
        const nativeWebsearch = settings.native_websearch === true || 
            settings.native_websearch === 'true' || 
            settings.native_websearch === '1';
        
        if (nativeWebsearch) {
            // Native websearch handles everything, no external providers needed
            return { valid: true, error: null };
        }
        
        // Get provider values
        let scrapeProvider = settings.websearch_scrape_provider;
        let searchProvider = settings.websearch_search_provider;
        
        // Check if a combined provider is selected (scrape is auto-set to search)
        if (schemaControls) {
            const searchEntry = findSearchField(schemaControls, DEFAULT_PAIR_KEYS.searchFieldKey);
            if (searchEntry?.control) {
                const selectedOption = getSelectedOption(searchEntry.control);
                if (selectedOption && shouldHideScrapeWhenSelected(selectedOption)) {
                    // Combined provider - scrape will be set to search value
                    scrapeProvider = searchProvider;
                }
            }
        }
        
        // Validate that both providers are set
        const missing = [];
        if (!scrapeProvider || (typeof scrapeProvider === 'string' && !scrapeProvider.trim())) {
            missing.push(t('websearch_scrape_provider_label', 'Web search scrape provider'));
        }
        if (!searchProvider || (typeof searchProvider === 'string' && !searchProvider.trim())) {
            missing.push(t('websearch_search_provider_label', 'Web search provider'));
        }

        if (missing.length > 0) {
            return {
                valid: false,
                error: formatT(
                    'websearch_provider_validation_error',
                    'Web search or URL content tools require websearch providers to be configured. Missing: {missing}. Either select providers or enable native web search.',
                    { missing: missing.join(', ') }
                )
            };
        }
        
        return { valid: true, error: null };
    };

    // Export the utility functions
    window.WebsearchProviderLogic = {
        SEARCH_PROVIDER_KEY,
        SCRAPE_PROVIDER_KEY,
        BYOK_SEARCH_PROVIDER_KEY,
        BYOK_SCRAPE_PROVIDER_KEY,
        BYOK_PAIR_KEYS,
        getOptionMetadata,
        isCombinedProvider,
        hasExplicitSearch,
        hasExplicitScrape,
        shouldHideScrapeWhenSelected,
        compareOptionsByLabel,
        sortedProviderOptions,
        createSelectWithMetadata,
        getSelectedOption,
        findScrapeField,
        findSearchField,
        findFieldByKey,
        attachProviderPairLogic,
        updateScrapeFieldState,
        updateScrapeFieldStateForKeys,
        refreshScrapeFieldState,
        refreshScrapeFieldStateForKeys,
        attachWebsearchProviderLogic,
        processProviderPairValuesForSubmit,
        processWebsearchValuesForSubmit,
        isSearchProviderField,
        isScrapeProviderField,
        isWebsearchProviderField,
        validateWebsearchProviders,
    };
})();
