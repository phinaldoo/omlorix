(() => {
    'use strict';

    const SPEED_KEY = 'speech_playback_speed';
    const DEFAULT_SPEED = 1.0;
    const MIN_SPEED = 0.5;
    const MAX_SPEED = 2.0;
    const START_TIMEOUT_MS = 10_000;

    const providers = {};
    const listeners = new Set();

    const state = {
        providerId: 'browser',
        activeMessageId: null,
        activeText: '',
        isLoading: false,
        isPlaying: false,
        lastError: null,
        errorMessageId: null,
        playbackToken: 0,
    };

    let preferredSpeed = DEFAULT_SPEED;
    let activeStartTimeout = null;

    const t = (key, fallback) => {
        if (typeof window !== 'undefined' && typeof window.getTranslation === 'function') {
            return window.getTranslation(key, fallback);
        }
        return fallback;
    };

    const formatT = (key, fallback, vars = {}) => {
        if (typeof window !== 'undefined' && typeof window.formatTranslation === 'function') {
            return window.formatTranslation(key, fallback, vars);
        }
        return String(t(key, fallback)).replace(/\{(\w+)\}/g, (_, token) => {
            const value = vars?.[token];
            return value === undefined || value === null ? '' : String(value);
        });
    };

    const clampSpeed = (value) => {
        const numeric = Number(value);
        if (!Number.isFinite(numeric)) return DEFAULT_SPEED;
        return Math.min(MAX_SPEED, Math.max(MIN_SPEED, numeric));
    };

    const formatError = (error) => {
        if (!error) return t('assistant_speech_playback_failed', 'Speech playback failed.');
        if (typeof error === 'string') return error;
        if (error.message) return String(error.message);
        return t('assistant_speech_playback_failed', 'Speech playback failed.');
    };

    const emitState = () => {
        const snapshot = {
            providerId: state.providerId,
            activeMessageId: state.activeMessageId,
            isLoading: state.isLoading,
            isPlaying: state.isPlaying,
            preferredSpeed,
            lastError: state.lastError,
            errorMessageId: state.errorMessageId,
        };
        listeners.forEach((listener) => {
            try {
                listener(snapshot);
            } catch (_) {
                // Ignore subscriber errors
            }
        });
    };

    const readPreferredSpeed = () => {
        try {
            const raw = localStorage.getItem(SPEED_KEY);
            preferredSpeed = raw === null ? DEFAULT_SPEED : clampSpeed(raw);
        } catch (_) {
            preferredSpeed = DEFAULT_SPEED;
        }
    };

    const writePreferredSpeed = (value) => {
        try {
            localStorage.setItem(SPEED_KEY, String(value));
        } catch (_) {
            // Ignore localStorage failures
        }
    };

    const sanitizeSpeechText = (input) => {
        if (input === null || typeof input === 'undefined') {
            return '';
        }
        return String(input)
            .replace(/```[\s\S]*?```/g, ' ')
            .replace(/`([^`]+)`/g, '$1')
            .replace(/\s+/g, ' ')
            .trim();
    };

    const clearStartTimeout = () => {
        if (activeStartTimeout !== null) {
            clearTimeout(activeStartTimeout);
            activeStartTimeout = null;
        }
    };

    const stopInternal = ({ keepError = false } = {}) => {
        clearStartTimeout();
        state.playbackToken += 1;
        const provider = providers[state.providerId];
        if (provider && typeof provider.stop === 'function') {
            try {
                provider.stop();
            } catch (_) {
                // Ignore provider stop errors
            }
        }

        state.activeMessageId = null;
        state.activeText = '';
        state.isLoading = false;
        state.isPlaying = false;
        if (!keepError) {
            state.lastError = null;
            state.errorMessageId = null;
        }
        emitState();
    };

    const finishPlayback = ({ clearError = true } = {}) => {
        clearStartTimeout();
        state.activeMessageId = null;
        state.activeText = '';
        state.isLoading = false;
        state.isPlaying = false;
        if (clearError) {
            state.lastError = null;
            state.errorMessageId = null;
        }
        emitState();
    };

    const registerProvider = (providerId, provider) => {
        if (!providerId || typeof providerId !== 'string') {
            throw new Error(t('assistant_speech_provider_id_required', 'Provider ID is required.'));
        }
        if (!provider || typeof provider.speak !== 'function' || typeof provider.stop !== 'function') {
            throw new Error(t('assistant_speech_provider_contract_required', 'Provider must implement speak() and stop().'));
        }
        providers[providerId] = provider;
    };

    registerProvider('browser', {
        canSpeak: () => (
            typeof window !== 'undefined'
            && 'speechSynthesis' in window
            && typeof window.SpeechSynthesisUtterance !== 'undefined'
        ),
        speak: ({ text, speed, lang, onStart, onEnd, onError }) => {
            const utterance = new window.SpeechSynthesisUtterance(text);
            utterance.rate = speed;
            utterance.lang = lang || document.documentElement.lang || 'en-US';
            utterance.onstart = () => onStart?.();
            utterance.onend = () => onEnd?.();
            utterance.onerror = (event) => {
                const message = event && event.error
                    ? formatT('assistant_speech_browser_error_detail', 'Speech error: {error}', { error: event.error })
                    : t('assistant_speech_browser_error', 'Speech error');
                onError?.(new Error(message));
            };

            window.speechSynthesis.cancel();
            window.speechSynthesis.speak(utterance);
        },
        stop: () => {
            if (typeof window !== 'undefined' && window.speechSynthesis) {
                window.speechSynthesis.cancel();
            }
        },
    });

    let activeAudio = null;
    let activeObjectUrl = null;
    let activeAbortController = null;

    const cleanupCustomAudio = () => {
        if (activeAbortController) {
            activeAbortController.abort();
            activeAbortController = null;
        }
        if (activeAudio) {
            try {
                activeAudio.pause();
            } catch (_) {
                // Ignore pause failures
            }
            activeAudio.src = '';
            activeAudio = null;
        }
        if (activeObjectUrl) {
            try {
                URL.revokeObjectURL(activeObjectUrl);
            } catch (_) {
                // Ignore object URL cleanup failures
            }
            activeObjectUrl = null;
        }
    };

    const readResponseError = async (response) => {
        try {
            const payload = await response.json();
            if (payload && typeof payload === 'object') {
                return payload.detail || payload.message || formatT('assistant_speech_request_failed_status', 'Speech request failed ({status})', { status: response.status });
            }
        } catch (_) {
            // Ignore JSON parsing failures
        }
        return formatT('assistant_speech_request_failed_status', 'Speech request failed ({status})', { status: response.status });
    };

    registerProvider('custom', {
        canSpeak: () => typeof window !== 'undefined' && typeof window.authedFetch === 'function',
        speak: async ({ messageId, text, speed, onLoadingStart, onStart, onEnd, onError }) => {
            cleanupCustomAudio();

            const abortController = new AbortController();
            activeAbortController = abortController;
            onLoadingStart?.();

            let response;
            try {
                response = await window.authedFetch('/api/v1/chats/messages/read-aloud', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        message_id: String(messageId || '').trim() || undefined,
                        text,
                    }),
                    signal: abortController.signal,
                });
            } catch (error) {
                if (abortController.signal.aborted) {
                    return;
                }
                onError?.(error);
                return;
            }

            if (!response.ok) {
                if (abortController.signal.aborted) {
                    return;
                }
                onError?.(new Error(await readResponseError(response)));
                return;
            }

            let blob;
            try {
                blob = await response.blob();
            } catch (error) {
                if (abortController.signal.aborted) {
                    return;
                }
                onError?.(error);
                return;
            }

            if (abortController.signal.aborted) {
                return;
            }
            if (!blob || blob.size <= 0) {
                onError?.(new Error(t('assistant_speech_audio_empty', 'Read aloud audio was empty.')));
                return;
            }

            const objectUrl = URL.createObjectURL(blob);
            activeObjectUrl = objectUrl;

            const audio = new Audio();
            activeAudio = audio;
            audio.preload = 'auto';
            audio.src = objectUrl;
            audio.playbackRate = clampSpeed(speed);
            audio.preservesPitch = true;

            let playbackStarted = false;
            const startPlayback = async () => {
                if (playbackStarted || abortController.signal.aborted) {
                    return;
                }
                playbackStarted = true;
                try {
                    await audio.play();
                } catch (error) {
                    if (abortController.signal.aborted) {
                        return;
                    }
                    onError?.(error);
                }
            };

            audio.addEventListener('canplay', () => {
                startPlayback();
            }, { once: true });
            audio.addEventListener('play', () => {
                onStart?.();
            }, { once: true });
            audio.addEventListener('ended', () => {
                cleanupCustomAudio();
                onEnd?.();
            }, { once: true });
            audio.addEventListener('error', () => {
                cleanupCustomAudio();
                onError?.(new Error(t('assistant_speech_audio_playback_failed', 'Audio playback failed.')));
            }, { once: true });
            audio.load();
        },
        stop: () => {
            cleanupCustomAudio();
        },
    });

    const getProvider = (providerId) => providers[providerId] || null;

    const setProvider = (providerId) => {
        if (!providers[providerId]) {
            throw new Error(formatT('assistant_speech_unknown_provider', 'Unknown TTS provider: {provider}', { provider: providerId }));
        }
        state.providerId = providerId;
        emitState();
    };

    const getPlaybackProvider = (preferredProviderId) => {
        const explicit = preferredProviderId && providers[preferredProviderId] ? preferredProviderId : null;
        const selectedProviderId = explicit || state.providerId;
        // Keep the configured provider authoritative. In particular, a custom
        // provider must never silently fall back to browser-native speech: the
        // two engines can produce different voices, languages, and billing
        // behavior, and a fallback would make the admin setting ineffective.
        const selectedProvider = providers[selectedProviderId] || null;

        if (selectedProvider && typeof selectedProvider.canSpeak === 'function' && selectedProvider.canSpeak()) {
            return { providerId: selectedProviderId, provider: selectedProvider };
        }

        return { providerId: selectedProviderId, provider: null };
    };

    const speakMessage = ({ messageId, text, speed, providerId } = {}) => {
        const cleanedText = sanitizeSpeechText(text);
        if (!cleanedText) {
            throw new Error(t('assistant_speech_no_text_error', 'No assistant text available to read aloud.'));
        }

        const resolvedSpeed = clampSpeed(typeof speed === 'number' ? speed : preferredSpeed);
        const { providerId: activeProviderId, provider } = getPlaybackProvider(providerId);
        const normalizedMessageId = String(messageId || '').trim();

        if (!provider) {
            throw new Error(t('assistant_speech_unavailable_error', 'Speech playback is not available in this browser.'));
        }
        if (activeProviderId === 'custom' && !normalizedMessageId) {
            throw new Error(t('assistant_speech_saved_message_required', 'Read aloud requires a saved assistant message.'));
        }

        stopInternal({ keepError: true });

        state.playbackToken += 1;
        const playbackToken = state.playbackToken;
        const resolvedMessageId = normalizedMessageId || 'assistant-message';

        state.providerId = activeProviderId;
        state.activeMessageId = resolvedMessageId;
        state.activeText = cleanedText;
        state.isLoading = true;
        state.isPlaying = false;
        state.lastError = null;
        state.errorMessageId = null;
        emitState();

        const failPlayback = (error) => {
            if (playbackToken !== state.playbackToken) return;
            clearStartTimeout();
            const failedMessageId = state.activeMessageId;
            state.playbackToken += 1;
            try {
                provider.stop();
            } catch (_) {
                // Ignore cleanup failures while reporting the original error.
            }
            state.activeMessageId = null;
            state.activeText = '';
            state.isLoading = false;
            state.isPlaying = false;
            state.lastError = formatError(error);
            state.errorMessageId = failedMessageId;
            emitState();
        };

        activeStartTimeout = setTimeout(() => {
            failPlayback(new Error(t(
                'assistant_speech_start_timeout',
                'Audio could not be prepared in time. Please try again.'
            )));
        }, START_TIMEOUT_MS);

        const callbacks = {
            messageId: resolvedMessageId,
            text: cleanedText,
            speed: resolvedSpeed,
            lang: document.documentElement.lang,
            onLoadingStart: () => {
                if (playbackToken !== state.playbackToken) return;
                state.isLoading = true;
                emitState();
            },
            onStart: () => {
                if (playbackToken !== state.playbackToken) return;
                clearStartTimeout();
                state.isLoading = false;
                state.isPlaying = true;
                emitState();
            },
            onEnd: () => {
                if (playbackToken !== state.playbackToken) return;
                finishPlayback({ clearError: true });
            },
            onError: (error) => {
                failPlayback(error);
            },
        };

        try {
            const result = provider.speak(callbacks);
            if (result && typeof result.catch === 'function') {
                result.catch(failPlayback);
            }
        } catch (error) {
            failPlayback(error);
        }
    };

    const stop = () => {
        stopInternal({ keepError: true });
    };

    const setPreferredSpeed = (value, { restartIfPlaying = false } = {}) => {
        preferredSpeed = clampSpeed(value);
        writePreferredSpeed(preferredSpeed);
        emitState();

        if (restartIfPlaying && (state.isPlaying || state.isLoading) && state.activeText) {
            const messageId = state.activeMessageId;
            const text = state.activeText;
            const providerId = state.providerId;
            speakMessage({ messageId, text, speed: preferredSpeed, providerId });
        }

        return preferredSpeed;
    };

    const getPreferredSpeed = () => preferredSpeed;

    const getState = () => ({
        providerId: state.providerId,
        activeMessageId: state.activeMessageId,
        isLoading: state.isLoading,
        isPlaying: state.isPlaying,
        preferredSpeed,
        lastError: state.lastError,
        errorMessageId: state.errorMessageId,
    });

    const isPlayingMessage = (messageId) => (
        state.isPlaying
        && state.activeMessageId !== null
        && String(messageId || '') === String(state.activeMessageId)
    );

    const subscribe = (listener) => {
        if (typeof listener !== 'function') {
            return () => {};
        }
        listeners.add(listener);
        try {
            listener({
                providerId: state.providerId,
                activeMessageId: state.activeMessageId,
                isLoading: state.isLoading,
                isPlaying: state.isPlaying,
                preferredSpeed,
                lastError: state.lastError,
                errorMessageId: state.errorMessageId,
            });
        } catch (_) {
            // Ignore subscriber errors
        }
        return () => {
            listeners.delete(listener);
        };
    };

    const extractTextFromContainer = (container) => {
        if (!container) return '';

        const chunks = [];
        const contentNodes = container.querySelectorAll('.assistant-message .assistant-message-content');
        contentNodes.forEach((node) => {
            if (!node) return;
            const raw = node.getAttribute('data-raw-content');
            const value = typeof raw === 'string' && raw.length ? raw : (node.innerText || node.textContent || '');
            const cleaned = sanitizeSpeechText(value);
            if (cleaned) {
                chunks.push(cleaned);
            }
        });

        return sanitizeSpeechText(chunks.join(' '));
    };

    if (typeof window !== 'undefined') {
        const syncConfiguredProvider = () => {
            const chatSetup = window.chatSetup || {};
            // `read_aloud_ready` describes whether the provider has enough
            // configuration to generate audio. It must not decide which
            // engine is selected: otherwise an explicitly configured provider
            // falls back to browser speech whenever its credentials/model are
            // incomplete. Native speech is selected only for the explicit
            // browser-native provider.
            const configuredProviderId = String(chatSetup.read_aloud_provider_id || '').trim();
            const shouldUseCustomProvider = Boolean(
                configuredProviderId
                && configuredProviderId !== 'browser_native'
            );
            state.providerId = shouldUseCustomProvider ? 'custom' : 'browser';
            emitState();
        };

        if (window.chatSetup) {
            syncConfiguredProvider();
        }
        document.addEventListener('chatSetupReady', syncConfiguredProvider);
        window.addEventListener('beforeunload', () => {
            try {
                stopInternal({ keepError: true });
            } catch (_) {
                // Ignore unload cancellation errors
            }
        });
    }

    readPreferredSpeed();

    window.AssistantSpeech = {
        MIN_SPEED,
        MAX_SPEED,
        DEFAULT_SPEED,
        START_TIMEOUT_MS,
        registerProvider,
        setProvider,
        getProvider: () => state.providerId,
        getProviderConfig: getProvider,
        speakMessage,
        stop,
        subscribe,
        getState,
        isPlayingMessage,
        setPreferredSpeed,
        getPreferredSpeed,
        extractTextFromContainer,
    };
})();
