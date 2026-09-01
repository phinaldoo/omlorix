/**
 * Translate structured admin API errors into actionable notifications.
 *
 * Keep the code-to-key mapping explicit so translation keys remain stable and
 * discoverable by the project's i18n validation.
 */
function getAdminApiErrorMessage(detail) {
    if (typeof detail === 'string') {
        return detail.trim();
    }
    if (!detail || typeof detail !== 'object') {
        return '';
    }

    const fallback = typeof detail.message === 'string' ? detail.message.trim() : '';
    const provider = typeof detail.provider === 'string' ? detail.provider : '';
    const countryCode = typeof detail.country_code === 'string' ? detail.country_code : '';
    const ipAddress = typeof detail.ip_address === 'string' ? detail.ip_address : '';
    switch (detail.code) {
        case 'ip_address_invalid':
            return helperFormatT(
                'security_ip_address_invalid_error',
                fallback || 'Invalid IP address: {ipAddress}. Use a valid IPv4 or IPv6 address, such as 203.0.113.10 or 2001:db8::1.',
                { ipAddress }
            );
        case 'ip_country_code_invalid':
            return helperFormatT(
                'security_ip_country_code_invalid_error',
                fallback || 'Invalid country code: {countryCode}. Use a two-letter ISO 3166-1 code such as DE or US.',
                { countryCode }
            );
        case 'ip_country_provider_not_configured':
            return helperT(
                'security_ip_country_provider_not_configured_error',
                fallback || 'Cannot save country-based IP restrictions because no IP location provider is configured. Select an IP location provider first.'
            );
        case 'ip_country_provider_api_key_missing':
            return helperFormatT(
                'security_ip_country_provider_api_key_missing_error',
                fallback || 'Cannot save country-based IP restrictions because {provider} is selected, but its API key is not configured. Enter the {provider} API key first.',
                { provider }
            );
        case 'ip_country_lookup_failed':
            return helperFormatT(
                'security_ip_country_lookup_failed_error',
                fallback || 'Omlorix could not resolve your current admin IP country using {provider}. Verify the provider configuration, API key, network access, and trusted proxy settings, or allow IPs without a country match.',
                { provider }
            );
        case 'admin_settings_validation_failed':
            return helperT(
                'admin_validation_failed',
                fallback || 'Validation failed.'
            );
        default:
            return fallback;
    }
}

async function fetchAdminJson(path, { method = 'GET', body, signal, headers } = {}, errorMessage = 'Request failed') {
    try {
        const init = {
            method,
            signal,
        };

        if (body !== undefined) {
            init.body = typeof body === 'string' ? body : JSON.stringify(body);
        }

        if (headers) {
            init.headers = headers;
        }

        const response = await window.authedFetch(path, init);

        if (!response.ok) {
            let detailMessage = '';
            try {
                const payload = await response.json();
                detailMessage = getAdminApiErrorMessage(payload?.detail);
            } catch (_) {}
            notifyError(detailMessage || errorMessage);
            return null;
        }

        return await response.json();
    } catch (error) {
        notifyError(errorMessage);
        return null;
    }
}

const helperT = (key, fallback) => {
    if (typeof window.getTranslation === 'function') {
        return window.getTranslation(key, fallback);
    }
    return fallback !== undefined ? fallback : key;
};

const helperFormatT = (key, fallback, vars) => {
    if (typeof window.formatTranslation === 'function') {
        return window.formatTranslation(key, fallback, vars);
    }
    const template = helperT(key, fallback);
    return String(template).replace(/\{(\w+)\}/g, (_, token) => {
        const value = vars?.[token];
        return value === undefined || value === null ? '' : String(value);
    });
};

const helperEscapeHtml = (value) => {
    const div = document.createElement('div');
    div.textContent = value == null ? '' : String(value);
    return div.innerHTML;
};

const ADMIN_ICON_ALIASES = {
    dashboard: 'dashboard',
    settings: 'settings',
    users: 'groups',
    groups: 'groups',
    login: 'signin',
    customization: 'clock',
    social: 'connections',
    enterprise: 'enterprise',
    ldap: 'server',
    chats: 'chatFilesChooseChats',
    messages: 'chatFilesChooseChats',
    providers: 'server',
    providerGroups: 'dashboard',
    serviceConnections: 'connections',
    models: 'llmModels',
    rateLimits: 'rateLimits',
    tools: 'tool',
    skills: 'lightning',
    feedback: 'thumbUp',
    statistics: 'statistics',
    userStatistics: 'groups',
    security: 'security',
    auditLogs: 'textLines',
    database: 'database',
    about: 'info',
    codeExecution: 'code',
    automation: 'clock',
    searchPlus: 'magnifyingGlass',
    weather: 'sun',
    image: 'image_gen',
    video: 'video_gen',
    audio: 'audio_gen',
    music: 'music',
    presentation: 'desktop',
    mcp: 'connections',
    chevronLeft: 'chevronLeft',
    arrowUpRight: 'arrow_top_right',
};

function getSharedAdminIcons() {
    if (typeof Icons !== 'undefined' && Icons) {
        return Icons;
    }
    if (typeof window !== 'undefined' && window.Icons) {
        return window.Icons;
    }
    if (typeof globalThis !== 'undefined' && globalThis.Icons) {
        return globalThis.Icons;
    }
    return {};
}

function getAdminIconMarkup(name) {
    return getSharedAdminIcons()?.[name] || '';
}

function getAdminRegistryIconMarkup(name) {
    const iconName = ADMIN_ICON_ALIASES[name] || name;
    return getAdminIconMarkup(iconName);
}

const ADMIN_NAV_CONFIG = [
    { items: [{ page: 'dashboard', icon: 'dashboard', labelKey: 'nav_dashboard', label: 'Dashboard' }] },
    {
        labelKey: 'nav_group_settings',
        label: 'Settings',
        items: [{ page: 'general', icon: 'settings', labelKey: 'nav_general', label: 'General' }],
    },
    {
        labelKey: 'nav_group_access',
        label: 'Access',
        items: [
            { page: 'users', icon: 'users', labelKey: 'nav_users', label: 'Users' },
            { page: 'groups', icon: 'groups', labelKey: 'nav_groups', label: 'Groups' },
        ],
    },
    {
        labelKey: 'nav_group_authentication',
        label: 'Authentication',
        items: [
            { page: 'login', icon: 'login', labelKey: 'nav_login', label: 'Login' },
            { page: 'login-customization', icon: 'customization', labelKey: 'nav_customization', label: 'Customization' },
            { page: 'login-social', icon: 'social', labelKey: 'nav_social_login', label: 'OAuth' },
            { page: 'login-enterprise-sso', icon: 'enterprise', labelKey: 'nav_enterprise_sso', label: 'Enterprise SSO' },
            { page: 'login-ldap', icon: 'ldap', labelKey: 'nav_ldap', label: 'LDAP' },
        ],
    },
    {
        labelKey: 'nav_group_conversations',
        label: 'Conversations',
        items: [
            { page: 'chat', icon: 'chats', labelKey: 'nav_chats', label: 'Chats' },
        ],
    },
    {
        labelKey: 'nav_group_ai_models',
        label: 'AI Models',
        items: [
            { page: 'providers', icon: 'providers', labelKey: 'nav_providers', label: 'Providers' },
            { page: 'provider-groups', icon: 'providerGroups', labelKey: 'nav_provider_groups', label: 'Provider Groups' },
            { page: 'models', icon: 'models', labelKey: 'nav_models', label: 'Models' },
            { page: 'rate-limits', icon: 'rateLimits', labelKey: 'nav_rate_limits', label: 'Rate Limits' },
        ],
    },
    {
        labelKey: 'nav_group_capabilities',
        label: 'Capabilities',
        items: [
            { page: 'tools', icon: 'tools', labelKey: 'nav_tools', label: 'Tools' },
            { page: 'skills', icon: 'skills', labelKey: 'nav_skills', label: 'Skills' },
        ],
    },
    {
        labelKey: 'nav_group_analytics',
        label: 'Analytics',
        items: [
            { page: 'model-feedback', icon: 'feedback', labelKey: 'nav_feedback', label: 'Feedback' },
            { page: 'model-statistics', icon: 'statistics', labelKey: 'nav_statistics', label: 'Statistics' },
            { page: 'user-statistics', icon: 'userStatistics', labelKey: 'nav_user_statistics', label: 'User Statistics' },
            { page: 'file-storage', icon: 'database', labelKey: 'nav_file_storage', label: 'File Storage' },
        ],
    },
    {
        labelKey: 'nav_group_system',
        label: 'System',
        items: [
            { page: 'security', icon: 'security', labelKey: 'nav_security', label: 'Security' },
            { page: 'audit-logs', icon: 'auditLogs', labelKey: 'nav_audit_logs', label: 'Audit Logs' },
            { page: 'database', icon: 'database', labelKey: 'nav_database', label: 'Database' },
            { page: 'about', icon: 'about', labelKey: 'nav_about', label: 'About' },
        ],
    },
];

const ADMIN_TOOL_PAGE_CONFIG = [
    {
        key: 'service-connections',
        backButtonId: 'serviceConnectionsBack',
        content: [{ id: 'serviceConnectionsRoot' }],
        titleKey: 'page_service_connections',
        title: 'Service Connections',
        subtitleKey: 'page_service_connections_subtitle',
        subtitle: 'Load balance shared execution and rendering services with weighted routing.',
    },
    {
        key: 'code-execution-settings',
        backButtonId: 'codeExecutionSettingsBack',
        statusId: 'codeExecutionSettingsStatus',
        content: [{ id: 'codeExecutionServiceConnectionsLink' }, { id: 'codeExecutionSettingsFields' }],
        titleKey: 'page_code_execution_settings',
        title: 'Code Execution Settings',
        subtitleKey: 'page_code_execution_settings_subtitle',
        subtitle: 'Configure execution defaults for the Python sandbox.',
    },
    {
        key: 'deep-research-settings',
        backButtonId: 'deepResearchSettingsBack',
        statusId: 'deepResearchSettingsStatus',
        content: [{ id: 'deepResearchSettingsFields' }],
        titleKey: 'page_deep_research_settings',
        title: 'Deep Research Settings',
        subtitleKey: 'page_deep_research_settings_subtitle',
        subtitle: 'Configure the provider and model used for deep research execution.',
    },
    {
        key: 'weather-tool-settings',
        backButtonId: 'weatherToolSettingsBack',
        statusId: 'weatherToolSettingsStatus',
        content: [{ id: 'weatherToolSettingsFields' }],
        titleKey: 'page_weather_tool_settings',
        title: 'Weather Settings',
        subtitleKey: 'page_weather_tool_settings_subtitle',
        subtitle: 'Configure the weather data provider for fetching real-time weather forecasts.',
    },
    {
        key: 'image-generation-settings',
        backButtonId: 'imageGenSettingsBack',
        statusId: 'imageGenSettingsStatus',
        content: [{ id: 'imageGenCurrentConfig' }, { id: 'imageGenWizard' }],
        titleKey: 'page_image_gen_settings',
        title: 'Image Generation Settings',
        subtitleKey: 'page_image_gen_settings_subtitle',
        subtitle: 'Configure the AI provider and model for generating images in chats.',
    },
    {
        key: 'video-generation-settings',
        backButtonId: 'videoGenerationSettingsBack',
        statusId: 'videoGenerationSettingsStatus',
        content: [{ id: 'videoGenerationSettingsFields' }],
        titleKey: 'page_video_generation_settings',
        title: 'Video Generation Settings',
        subtitleKey: 'page_video_generation_settings_subtitle',
        subtitle: 'Configure provider, model, and default generation parameters for chat video generation.',
    },
    {
        key: 'audio-generation-settings',
        backButtonId: 'audioGenerationSettingsBack',
        statusId: 'audioGenerationSettingsStatus',
        content: [{ id: 'audioGenerationSettingsFields' }],
        titleKey: 'page_audio_generation_settings',
        title: 'Audio Generation Settings',
        subtitleKey: 'page_audio_generation_settings_subtitle',
        subtitle: 'Configure provider, model, and default generation parameters for chat audio generation.',
    },
    {
        key: 'music-generation-settings',
        backButtonId: 'musicGenerationSettingsBack',
        statusId: 'musicGenerationSettingsStatus',
        content: [{ id: 'musicGenerationSettingsFields' }],
        titleKey: 'page_music_generation_settings',
        title: 'Music Generation Settings',
        subtitleKey: 'page_music_generation_settings_subtitle',
        subtitle: 'Configure provider, model, and default generation parameters for chat music generation.',
    },
    {
        key: 'create-slide-presentation-settings',
        backButtonId: 'createSlidePresentationSettingsBack',
        statusId: 'createSlidePresentationSettingsStatus',
        content: [{ id: 'createSlidePresentationServiceConnectionsLink' }, { id: 'createSlidePresentationSettingsFields' }],
        titleKey: 'page_slide_presentation_settings',
        title: 'Slide Presentation Settings',
        subtitleKey: 'page_slide_presentation_settings_subtitle',
        subtitle: 'Configure models and research defaults for the presentation generation pipeline.',
    },
];

const ADMIN_TOOL_CARD_CONFIG = [
    {
        targetPage: 'service-connections',
        icon: 'connections',
        badgeKey: 'tool_label_infrastructure',
        badge: 'Infrastructure',
        titleKey: 'tool_title_service_connections',
        title: 'Service Connections',
        descriptionKey: 'tool_service_connections_desc',
        description: 'Manage shared execution and rendering services with weighted routing.',
        ctaKey: 'tool_manage_connections',
        cta: 'Manage connections',
    },
    {
        targetPage: 'code-execution-settings',
        icon: 'codeExecution',
        badgeKey: 'tool_label_code',
        badge: 'Code',
        titleKey: 'tool_title_code_execution',
        title: 'Code Execution',
        descriptionKey: 'tool_code_execution_desc',
        description: 'Configure Python sandbox defaults for tool execution.',
        ctaKey: 'tool_configure_defaults',
        cta: 'Configure defaults',
    },
    {
        targetPage: 'websearch-providers',
        icon: 'automation',
        badgeKey: 'tool_label_automation',
        badge: 'Automation',
        titleKey: 'tool_title_websearch',
        title: 'WebSearch',
        descriptionKey: 'tool_websearch_desc',
        description: 'Manage search and scraping providers that enable real-time browsing inside chats.',
        ctaKey: 'tool_open_provider_list',
        cta: 'Open provider list',
    },
    {
        targetPage: 'deep-research-settings',
        icon: 'searchPlus',
        badgeKey: 'tool_label_research',
        badge: 'Research',
        titleKey: 'tool_title_deep_research',
        title: 'Deep Research',
        descriptionKey: 'tool_deep_research_desc',
        description: 'Configure the provider and model used by the deep research tool.',
        ctaKey: 'tool_configure_tool',
        cta: 'Configure tool',
    },
    {
        targetPage: 'weather-tool-settings',
        icon: 'weather',
        badgeKey: 'tool_label_weather',
        badge: 'Weather',
        titleKey: 'tool_title_weather',
        title: 'Weather',
        descriptionKey: 'tool_weather_desc',
        description: 'Configure the weather data provider for fetching real-time weather forecasts.',
        ctaKey: 'tool_configure_provider',
        cta: 'Configure provider',
    },
    {
        targetPage: 'image-generation-settings',
        icon: 'image',
        badgeKey: 'tool_label_ai_generation',
        badge: 'AI Generation',
        titleKey: 'tool_title_image_generation',
        title: 'Image Generation',
        descriptionKey: 'tool_image_gen_desc',
        description: 'Configure the AI image generation provider and model for creating images in chats.',
        ctaKey: 'tool_configure_model',
        cta: 'Configure model',
    },
    {
        targetPage: 'video-generation-settings',
        icon: 'video',
        badgeKey: 'tool_label_ai_generation',
        badge: 'AI Generation',
        titleKey: 'tool_title_video_generation',
        title: 'Video Generation',
        descriptionKey: 'tool_video_generation_desc',
        description: 'Configure the AI provider and model for generating videos in chats.',
        ctaKey: 'tool_configure_model',
        cta: 'Configure model',
    },
    {
        targetPage: 'audio-generation-settings',
        icon: 'audio',
        badgeKey: 'tool_label_ai_generation',
        badge: 'AI Generation',
        titleKey: 'tool_title_audio_generation',
        title: 'Audio Generation',
        descriptionKey: 'tool_audio_generation_desc',
        description: 'Configure the AI provider and model for generating speech audio in chats.',
        ctaKey: 'tool_configure_model',
        cta: 'Configure model',
    },
    {
        targetPage: 'music-generation-settings',
        icon: 'music',
        badgeKey: 'tool_label_ai_generation',
        badge: 'AI Generation',
        titleKey: 'tool_title_music_generation',
        title: 'Music Generation',
        descriptionKey: 'tool_music_generation_desc',
        description: 'Configure the AI provider and model for generating music tracks in chats.',
        ctaKey: 'tool_configure_model',
        cta: 'Configure model',
    },
    {
        targetPage: 'create-slide-presentation-settings',
        icon: 'presentation',
        badgeKey: 'tool_label_presentations',
        badge: 'Presentations',
        titleKey: 'tool_title_slide_presentation',
        title: 'Slide Presentation',
        descriptionKey: 'tool_slide_presentation_desc',
        description: 'Configure AI models and research defaults for the presentation pipeline.',
        ctaKey: 'tool_configure_pipeline',
        cta: 'Configure pipeline',
    },
    {
        targetPage: 'mcp-settings',
        icon: 'mcp',
        badgeKey: 'tool_label_integrations',
        badge: 'Integrations',
        titleKey: 'tool_title_mcp_servers',
        title: 'MCP Servers',
        descriptionKey: 'tool_mcp_servers_desc',
        description: 'Connect admin-managed Model Context Protocol servers and preview the tools they expose.',
        ctaKey: 'tool_configure_servers',
        cta: 'Configure servers',
    },
    {
        targetPage: 'custom-python-tools',
        icon: 'codeExecution',
        badgeKey: 'tool_label_developer_tools',
        badge: 'Developer Tools',
        titleKey: 'tool_title_custom_python_tools',
        title: 'Custom Python Tools',
        descriptionKey: 'tool_custom_python_tools_desc',
        description: 'Register admin-managed Python tools with their own schemas so models can call them like any built-in tool.',
        ctaKey: 'tool_open_tool_registry',
        cta: 'Open tool registry',
    },
];

const ADMIN_MODEL_SUBPAGE_CONFIG = [
    {
        key: 'models-dictation-settings',
        titleKey: 'models_dictation_settings_page_title',
        title: 'Dictation settings',
        subtitleKey: 'models_dictation_settings_page_subtitle',
        subtitle: 'Configure microphone dictation transcription provider and model.',
        statusId: 'modelsDictationSettingsStatus',
        content: [{ id: 'modelsDictationSettingsFields' }],
    },
    {
        key: 'models-read-aloud-settings',
        titleKey: 'models_read_aloud_settings_page_title',
        title: 'Read aloud settings',
        subtitleKey: 'models_read_aloud_settings_page_subtitle',
        subtitle: 'Configure text-to-speech read aloud provider, model, voice, and format.',
        statusId: 'modelsReadAloudSettingsStatus',
        content: [{ id: 'modelsReadAloudSettingsFields' }],
    },
    {
        key: 'models-realtime-settings',
        titleKey: 'models_realtime_settings_page_title',
        title: 'Realtime call settings',
        subtitleKey: 'models_realtime_settings_page_subtitle',
        subtitle: 'Configure realtime speech call provider, model, voice, and advanced behavior.',
        statusId: 'modelsRealtimeSettingsStatus',
        content: [{ id: 'modelsRealtimeSettingsFields' }],
    },
];

const ADMIN_MODELS_ACTION_ROWS = [
    {
        titleKey: 'models_dictation_settings_title',
        title: 'Dictation settings',
        descKey: 'models_dictation_settings_desc',
        description: 'Configure transcription provider and model for microphone dictation.',
        buttonLabelKey: 'models_dictation_settings_btn',
        buttonLabel: 'Manage dictation',
        targetPage: 'models-dictation-settings',
        ariaKey: 'models_dictation_settings_nav_aria',
        ariaLabel: 'Dictation settings navigation',
        buttonAriaKey: 'models_dictation_settings_btn_aria',
        buttonAriaLabel: 'Manage dictation',
    },
    {
        titleKey: 'models_read_aloud_settings_title',
        title: 'Read aloud settings',
        descKey: 'models_read_aloud_settings_desc',
        description: 'Configure text-to-speech provider, model, voice, and audio format.',
        buttonLabelKey: 'models_read_aloud_settings_btn',
        buttonLabel: 'Manage read aloud',
        targetPage: 'models-read-aloud-settings',
        ariaKey: 'models_read_aloud_settings_nav_aria',
        ariaLabel: 'Read aloud settings navigation',
        buttonAriaKey: 'models_read_aloud_settings_btn_aria',
        buttonAriaLabel: 'Manage read aloud',
    },
    {
        titleKey: 'models_realtime_settings_title',
        title: 'Realtime call settings',
        descKey: 'models_realtime_settings_desc',
        description: 'Configure provider, model, voice, and advanced options for realtime calls.',
        buttonLabelKey: 'models_realtime_settings_btn',
        buttonLabel: 'Manage realtime calls',
        targetPage: 'models-realtime-settings',
        ariaKey: 'models_realtime_settings_nav_aria',
        ariaLabel: 'Realtime call settings navigation',
        buttonAriaKey: 'models_realtime_settings_btn_aria',
        buttonAriaLabel: 'Manage realtime calls',
    },
];

const ADMIN_SECURITY_ACTION_SECTIONS = [
    {
        className: 'settings-section settings-section--spaced',
        titleId: 'security-blocked-ips-title',
        titleKey: 'security_blocked_ips_title',
        title: 'IP bans',
        descriptionKey: 'security_blocked_ips_desc',
        description: 'Review and manage IP addresses blocked from accessing Omlorix.',
        rows: [
            {
                titleKey: 'security_manage_ips_title',
                title: 'Manage IP bans',
                descKey: 'security_manage_ips_desc',
                description: 'Open the IP bans page to inspect entries or add new blocks.',
                buttonLabelKey: 'security_go_to_ips_btn',
                buttonLabel: 'Manage bans',
                targetPage: 'security-ips',
                buttonId: 'securityBlockedIpsButton',
                ariaKey: 'blocked_ip_navigation_aria',
                ariaLabel: 'Blocked IP navigation',
            },
            {
                titleKey: 'security_view_ip_analytics_title',
                title: 'View IP analytics',
                descKey: 'security_view_ip_analytics_desc',
                description: 'Open country trends, blocked attempts, and recent IP security events.',
                buttonLabelKey: 'security_view_ip_analytics_btn',
                buttonLabel: 'View analytics',
                targetPage: 'security-ip-analytics',
                buttonId: 'securityIpAnalyticsButton',
                ariaKey: 'ip_analytics_navigation_aria',
                ariaLabel: 'IP analytics navigation',
            },
        ],
    },
    {
        titleId: 'security-privacy-policy-title',
        titleKey: 'security_privacy_policy_title',
        title: 'Privacy Policy',
        descriptionKey: 'security_privacy_policy_desc',
        description: 'Manage the privacy policy content visible to users.',
        rows: [{
            titleKey: 'security_edit_policy_title',
            title: 'Edit Privacy Policy',
            descKey: 'security_edit_policy_desc',
            description: 'Update the markdown content of the privacy policy page.',
            buttonLabelKey: 'security_edit_policy_btn',
            buttonLabel: 'Edit Policy',
            targetPage: 'privacy-policy',
            ariaKey: 'privacy_policy_navigation_aria',
            ariaLabel: 'Privacy Policy navigation',
        }],
    },
    {
        className: 'settings-section settings-section--compact-spaced',
        titleId: 'security-terms-of-service-title',
        titleKey: 'security_terms_of_service_title',
        title: 'Terms of Service',
        descriptionKey: 'security_terms_of_service_desc',
        description: 'Manage the terms of service content visible to users.',
        rows: [{
            titleKey: 'security_edit_terms_title',
            title: 'Edit Terms of Service',
            descKey: 'security_edit_terms_desc',
            description: 'Update the markdown content of the terms of service page.',
            buttonLabelKey: 'security_edit_terms_btn',
            buttonLabel: 'Edit Terms',
            targetPage: 'terms-of-service',
            ariaKey: 'terms_of_service_navigation_aria',
            ariaLabel: 'Terms of Service navigation',
        }],
    },
];
