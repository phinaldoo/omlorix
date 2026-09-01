function revealChatAppShell() {
    const body = document.body;
    if (body) {
        body.style.display = 'flex';
    }
}

function safeSetLocalStorageItem(key, value) {
    try {
        localStorage.setItem(key, value);
    } catch (error) {
        console.warn(`Failed to persist ${key} to localStorage:`, error);
    }
}

async function initChatSetup() {
    if (typeof window !== 'undefined' && typeof window.__omlorixInitialAuthBootstrap?.then === 'function') {
        const isAuthed = await window.__omlorixInitialAuthBootstrap.catch(() => false);
        if (!isAuthed) {
            return;
        }
    }
    revealChatAppShell();
    let chatSetup = null;
    const res = await window.authedFetch(`/api/v1/settings/chat/setup`);
    if (res.ok) {
        chatSetup = await res.json();

        // Browser locale signals replace the removed setup wizard. The server
        // applies them only to blank fields, so later User Settings choices
        // always remain authoritative.
        if (typeof window.applyDetectedLocaleDefaults === 'function') {
            try {
                chatSetup = await window.applyDetectedLocaleDefaults(chatSetup);
            } catch (error) {
                // Locale persistence is best effort. Keep the setup response so
                // a transient update failure cannot prevent chat initialization.
                console.warn('Unable to apply detected locale defaults:', error);
            }
        }

        if (typeof window !== 'undefined') {
            window.chatSetup = chatSetup;
        }
        // The chat bootstrap is an account-bound fallback for locale
        // synchronization. auth.js normally publishes this earlier from the
        // refresh response, but keeping this boundary authoritative also makes
        // direct page loads and future bootstrap changes safe.
        if (chatSetup.language && typeof window.applyAuthenticatedLanguage === 'function') {
            void window.applyAuthenticatedLanguage(chatSetup.language);
        }
        if (typeof window.BYOK?.setPolicy === 'function') {
            window.BYOK.setPolicy(chatSetup);
        }

        initUserProfileUI(chatSetup.first_name, chatSetup.last_name, chatSetup.email); // First name, last name, email
        initProjectsSidebar(chatSetup.enable_projects, chatSetup.allow_project_share); // If projects are enabled
        initAutomationsSidebar(chatSetup.enable_automations); // If automations are enabled
        initWorkspaceTodos(chatSetup.enable_todo, chatSetup.allow_todo_list_share); // If todos are enabled
        initWorkspaceNotes(chatSetup.enable_notes, chatSetup.allow_notes_share); // If notes are enabled
        initWorkspaceMemories(chatSetup.enable_memories); // If memories are enabled
        initWorkspaceBookmarks(chatSetup.enable_bookmarks, chatSetup.allow_bookmark_share); // If bookmarks are enabled
        // The workspace remains available when any connection family is
        // allowed; each family applies its own policy within the page.
        initWorkspaceConnections(chatSetup);
        initWorkspaceSkills(chatSetup.enable_skills, chatSetup.allow_skill_share); // If skills are enabled
        initWorkspaceAgents(chatSetup.allow_agents, chatSetup.allow_agent_share); // Custom agents are first-class user models
        initWorkspacePrompts(chatSetup.enable_prompts, chatSetup.allow_prompt_share); // If prompt library is enabled
        initChatFullWidth(chatSetup.chat_full_width); // If chat is full width
        initChatBoxWarning(chatSetup.show_chat_box_warning, chatSetup.chat_box_warning_message); // If chat box warning is enabled, and the message
        initProfilePicture(chatSetup);
        setTheme(chatSetup.theme_mode);
        setColorTheme(chatSetup.color_theme);
        initializeThemeSettings(chatSetup.theme_mode, chatSetup.color_theme);

        initWelcomeMessage(chatSetup);
        initNotificationBadge(chatSetup.has_new_notifications);

        safeSetLocalStorageItem('show_model_settings', chatSetup.show_model_settings ? 'true' : 'false');
        safeSetLocalStorageItem('show_message_nav', chatSetup.show_message_nav ? 'true' : 'false');
        safeSetLocalStorageItem('show_assistant_message_metadata', chatSetup.show_assistant_message_metadata ? 'true' : 'false');
        safeSetLocalStorageItem('render_user_messages_markdown', chatSetup.render_user_messages_markdown);
        safeSetLocalStorageItem('render_assistant_messages_markdown', chatSetup.render_assistant_messages_markdown);
        safeSetLocalStorageItem('ctrl_enter_to_send', chatSetup.ctrl_enter_to_send ? 'true' : 'false');
        safeSetLocalStorageItem('always_use_temporary_chat', chatSetup.always_use_temporary_chat ? 'true' : 'false');
        safeSetLocalStorageItem('chat_box_show_call_input', chatSetup.chat_box_show_call_input ? 'true' : 'false');
        safeSetLocalStorageItem('allow_file_uploads', chatSetup.allow_file_uploads);
        safeSetLocalStorageItem('realtime_call_ready', chatSetup.realtime_call_ready ? 'true' : 'false');
        safeSetLocalStorageItem('file_transcription_ready', chatSetup.file_transcription_ready ? 'true' : 'false');
        safeSetLocalStorageItem('live_transcription_ready', chatSetup.live_transcription_ready ? 'true' : 'false');
        safeSetLocalStorageItem('allow_regenerate_response', chatSetup.allow_regenerate_response ? 'true' : 'false');
        safeSetLocalStorageItem('allow_rate_response', chatSetup.allow_rate_response ? 'true' : 'false');
        safeSetLocalStorageItem('allow_delete_messages', chatSetup.allow_delete_messages ? 'true' : 'false');
        const rawSpeechSpeed = Number(chatSetup.speech_playback_speed);
        const speechPlaybackSpeed = Number.isFinite(rawSpeechSpeed)
            ? Math.min(2, Math.max(0.5, rawSpeechSpeed))
            : 1;
        safeSetLocalStorageItem('speech_playback_speed', String(speechPlaybackSpeed));
        if (window.AssistantSpeech && typeof window.AssistantSpeech.setPreferredSpeed === 'function') {
            window.AssistantSpeech.setPreferredSpeed(speechPlaybackSpeed);
        }
        safeSetLocalStorageItem('allow_chat_deletion', chatSetup.allow_chat_deletion);
        safeSetLocalStorageItem('shadow_chat_deletion', chatSetup.shadow_chat_deletion ? 'true' : 'false');
        safeSetLocalStorageItem('privacy_policy_notice', JSON.stringify(chatSetup.privacy_policy_notice || {}));
        const complianceSettings = chatSetup.compliance || {};
        const complianceWatermarkText = typeof complianceSettings.watermark === 'string' ? complianceSettings.watermark : '';
        safeSetLocalStorageItem('compliance_enable_watermark', complianceSettings.enable_watermark ? 'true' : 'false');
        safeSetLocalStorageItem('compliance_watermark', complianceWatermarkText);
        if (chatSetup.font_family) {
            safeSetLocalStorageItem('font-family', chatSetup.font_family);
        } 
        if (typeof window.applyFontPreferences === 'function') {
            window.applyFontPreferences();
        }


        if (typeof window !== 'undefined') {
            window.hasLeaderboardAccess = !!chatSetup.has_leaderboard_access;
        }

        // Strip background colors from clipboard to prevent them from being pasted
        document.addEventListener('copy', (e) => {
            const selection = window.getSelection();
            if (selection.rangeCount > 0) {
                const range = selection.getRangeAt(0);
                const fragment = range.cloneContents();
                const div = document.createElement('div');

                const ancestor = range.commonAncestorContainer;
                const styleSource = ancestor instanceof Element ? ancestor : ancestor?.parentElement;
                if (styleSource instanceof Element) {
                    const computedStyle = window.getComputedStyle(styleSource);
                    div.style.fontFamily = computedStyle.fontFamily;
                    div.style.fontSize = computedStyle.fontSize;
                    div.style.fontWeight = computedStyle.fontWeight;
                    div.style.fontStyle = computedStyle.fontStyle;
                    div.style.lineHeight = computedStyle.lineHeight;
                }
                
                div.appendChild(fragment);
                
                // Remove background-color from all elements
                const allElements = div.querySelectorAll('*');
                allElements.forEach(el => {
                    el.style.backgroundColor = '';
                    el.style.background = '';
                });
                
                // Get plain text and modified HTML
                const plainText = selection.toString();
                const modifiedHtml = div.innerHTML;
                
                // Set clipboard data with modified HTML (no background)
                e.preventDefault();
                e.clipboardData.setData('text/plain', plainText);
                e.clipboardData.setData('text/html', modifiedHtml);
            }
        });

        document.dispatchEvent(new CustomEvent('chatSetupReady', { detail: chatSetup }));
        if (typeof window.syncTemporaryChatModeWithPreference === 'function') {
            window.syncTemporaryChatModeWithPreference();
        }
    }
}
initChatSetup();
